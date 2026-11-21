"""Raport przed graniem: czysty pendrive daje ok, a celowe uszkodzenia sa nazwane."""
import os
import shutil

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")
if shutil.which(os.environ.get("CDJ_FFMPEG", "ffmpeg")) is None:
    pytest.skip("ffmpeg not available", allow_module_level=True)

import pdb_writer as pw  # noqa: E402
import raport  # noqa: E402
import silnik  # noqa: E402
from test_engine import _click_track  # noqa: E402


def _pendrive(tmp_path):
    a, usb = tmp_path / "a", tmp_path / "usb"
    a.mkdir(); usb.mkdir()
    _click_track(str(a / "one.wav"), 128.0, seconds=30)
    ev = []
    silnik.przetworz({"folder_mp3": str(a), "pendrive": str(usb), "playlista": "T", "kopiuj_mp3": True,
                      "zastap_pioneer": False, "nie_usypiaj": False, "okladki": False}, ev.append)
    assert [e for e in ev if e.get("typ") == "koniec"][0]["ok"]
    return usb


def _kody(w):
    return {p["kod"] for p in w["problemy"]}


def test_czysty_pendrive(tmp_path):
    w = raport.sprawdz(str(_pendrive(tmp_path)))
    assert w["ok"] and w["utworow"] == 1 and "hot_a_nie_na_beacie" not in _kody(w)


def test_brak_pliku_audio_i_rozmiar(tmp_path):
    usb = _pendrive(tmp_path)
    lib = pw.czytaj_biblioteke((usb / "PIONEER" / "rekordbox" / "export.pdb").read_bytes())
    (f, s), = lib["utwory"]
    audio = usb / s["file_path"].lstrip("/")
    with open(audio, "ab") as fh:
        fh.write(b"x" * 10)
    assert "rozmiar" in _kody(raport.sprawdz(str(usb))) and not raport.sprawdz(str(usb))["ok"]
    os.remove(audio)
    assert "brak_audio" in _kody(raport.sprawdz(str(usb)))


def test_brak_analizy_i_smieci(tmp_path):
    usb = _pendrive(tmp_path)
    for p in (usb / "PIONEER" / "USBANLZ").rglob("*.EXT"):
        p.unlink()
    (usb / "._x").write_bytes(b"1")
    k = _kody(raport.sprawdz(str(usb)))
    assert "analiza" in k and "apple_double" in k


def test_brak_bazy(tmp_path):
    w = raport.sprawdz(str(tmp_path))
    assert not w["ok"] and w["problemy"][0]["kod"] == "baza"
