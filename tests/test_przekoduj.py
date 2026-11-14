"""Przycisk "Przekoduj": utwor oznaczony w edytorze idzie na pendrive jako WAV i zajmuje miejsce oryginalu w bazie."""
import os
import shutil
import subprocess
import wave

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")
FFMPEG = os.environ.get("CDJ_FFMPEG", "ffmpeg")
if shutil.which(FFMPEG) is None:
    pytest.skip("ffmpeg not available", allow_module_level=True)

import edytor  # noqa: E402
import pdb_writer as pw  # noqa: E402
import silnik  # noqa: E402
from test_engine import _click_track  # noqa: E402


def _aiff(wav, aiff):
    r = subprocess.run([FFMPEG, "-v", "error", "-y", "-i", wav, "-ac", "2", "-c:a", "pcm_s16be", aiff], capture_output=True)
    if r.returncode != 0:
        pytest.skip("this ffmpeg cannot write AIFF")
    os.remove(wav)


def _zapis(folder, usb, **opcje):
    ev = []
    silnik.przetworz(dict({"folder_mp3": str(folder), "pendrive": str(usb), "playlista": "T", "kopiuj_mp3": True,
                           "zastap_pioneer": False, "nie_usypiaj": False, "okladki": False, "import_cue": False},
                          **opcje), ev.append)
    assert [e for e in ev if e.get("typ") == "koniec"][0]["ok"]
    return ev


def _baza(usb):
    return pw.czytaj_biblioteke((usb / "PIONEER" / "rekordbox" / "export.pdb").read_bytes())


def test_reencode_from_the_editor_and_on_the_next_write(tmp_path, monkeypatch):
    monkeypatch.setenv("CDJ_PRZEKODUJ", str(tmp_path / "przekoduj.json"))
    monkeypatch.setenv("CDJ_KOREKTY", str(tmp_path / "korekty.json"))
    monkeypatch.setenv("CDJ_IMPORT", str(tmp_path / "import.json"))
    a, usb = tmp_path / "a", tmp_path / "usb"
    a.mkdir(); usb.mkdir()
    _click_track(str(a / "one.wav"), 128.0, seconds=20)
    _aiff(str(a / "one.wav"), str(a / "one.aiff"))
    _click_track(str(a / "two.wav"), 120.0, seconds=20)
    _zapis(a, usb, nazwa_folderu="Muzyka")
    lib = _baza(usb)
    stare = {s["file_path"]: f["id"] for f, s in lib["utwory"]}
    assert set(stare) == {"/Muzyka/one.aiff", "/Muzyka/two.wav"}
    wpisy = sorted(lib["wpisy"])

    # przycisk w edytorze: oznaczenie i zadanie dla jednego pliku na pendrive (bez kopiowania)
    folder, nazwa = edytor.do_przekodowania(str(usb), stare["/Muzyka/one.aiff"])
    assert nazwa == "one.aiff" and len(edytor.wczytaj_przekoduj()) == 1
    ev = _zapis(folder, usb, kopiuj_mp3=False, tylko_pliki=[nazwa], bez_playlisty=True, test_dysku=False)
    assert any("WAV" in e.get("tekst", "") for e in ev if e.get("typ") == "log")
    lib = _baza(usb)
    nowe = {s["file_path"]: f for f, s in lib["utwory"]}
    assert set(nowe) == {"/Muzyka/one.wav", "/Muzyka/two.wav"}
    f = nowe["/Muzyka/one.wav"]
    assert f["id"] == stare["/Muzyka/one.aiff"] and f["unk90"] == 11 and sorted(lib["wpisy"]) == wpisy
    with wave.open(str(usb / "Muzyka" / "one.wav")) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (2, 2, 44100) and w.getnframes() > 44100 * 19
    assert f["file_size"] == (usb / "Muzyka" / "one.wav").stat().st_size
    assert (usb / "Muzyka" / "one.aiff").exists()            # oryginal zostaje na pendrive
    t = edytor.utwor(str(usb), f["id"])
    assert abs(t["bpm"] - 128.0) < 0.2 and len(t["beaty"]) > 30
    try:
        edytor.do_przekodowania(str(usb), f["id"])
    except ValueError:
        pass
    else:
        raise AssertionError("WAV nie powinien dac sie przekodowac")

    # kolejny zapis z folderu zrodlowego: oryginal znowu idzie jako WAV, bez drugiego wpisu
    ev = _zapis(a, usb, nazwa_folderu="Muzyka")
    lib = _baza(usb)
    assert sorted(s["file_path"] for _, s in lib["utwory"]) == ["/Muzyka/one.wav", "/Muzyka/two.wav"]
    assert {s["file_path"]: f["id"] for f, s in lib["utwory"]} == {"/Muzyka/one.wav": stare["/Muzyka/one.aiff"],
                                                                 "/Muzyka/two.wav": stare["/Muzyka/two.wav"]}
    assert sorted(lib["wpisy"]) == wpisy

    # zdjecie oznaczenia: lista pusta
    edytor.ustaw_przekoduj(None, None)
    assert edytor.wczytaj_przekoduj() == {}
