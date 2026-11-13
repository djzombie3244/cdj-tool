"""Edytor siatki i cue: te same bajty co gen_dat, zapis na pendrive i poprawka uzyta przy kolejnym zapisie."""
import os
import shutil
import struct

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")
if shutil.which(os.environ.get("CDJ_FFMPEG", "ffmpeg")) is None:
    pytest.skip("ffmpeg not available", allow_module_level=True)

import edytor  # noqa: E402
import gen_dat as g  # noqa: E402
import pdb_writer as pw  # noqa: E402
import silnik  # noqa: E402
from test_engine import _click_track  # noqa: E402


def test_tag_builders_match_gen_dat():
    czasy = g.siatka_ms(127.5, 0.31, 40.0, 1)
    tempa = [127.5] * len(czasy)
    assert edytor.tag_pqtz(list(czasy), list(czasy.numery), tempa) == g.tag_pqtz(czasy, 127.5)
    assert edytor.tag_pqt2(list(czasy), list(czasy.numery), tempa) == g.tag_pqt2(czasy, 127.5)
    wpisy = [(1, 120), (2, 15000), (3, 30500)]
    assert edytor.tag_pcob(1, wpisy) == g.tag_pcob(1, wpisy)
    assert edytor.tag_pco2(1, [(1, 120, "Start"), (2, 15000, "")]) == g.tag_pco2(1, [(1, 120, "Start"), (2, 15000, "")])


def _zapis(folder, usb):
    ev = []
    silnik.przetworz({"folder_mp3": str(folder), "pendrive": str(usb), "playlista": "T", "kopiuj_mp3": True,
                      "zastap_pioneer": False, "nie_usypiaj": False, "okladki": False}, ev.append)
    assert [e for e in ev if e.get("typ") == "koniec"][0]["ok"]
    return ev


def test_edit_save_and_reapply(tmp_path, monkeypatch):
    monkeypatch.setenv("CDJ_KOREKTY", str(tmp_path / "korekty.json"))
    a, usb = tmp_path / "a", tmp_path / "usb"
    a.mkdir(); usb.mkdir()
    _click_track(str(a / "one.wav"), 128.0, seconds=30)
    _zapis(a, usb)
    (u,) = edytor.lista(str(usb))
    t = edytor.utwor(str(usb), u["id"])
    assert len(t["beaty"]) > 50 and len(t["hot"]) >= 1 and t["waveform"]["typ"] == "pwv5" and not t["korekta"]

    # bez zmian: te same bajty po przebudowie
    lib = pw.czytaj_biblioteke((usb / "PIONEER" / "rekordbox" / "export.pdb").read_bytes())
    (f, s), = lib["utwory"]
    dat_p = usb / s["analyze_path"].lstrip("/")
    ext_p = dat_p.with_suffix(".EXT")
    dat, ext = dat_p.read_bytes(), ext_p.read_bytes()
    assert edytor.zbuduj_pliki(dat, ext, {"beaty": t["beaty"], "hot": t["hot"], "pamiec": t["pamiec"]}) == (dat, ext)

    # przesuniecie siatki o +20 ms, tempo 64 (polowa), hot A w innym miejscu, jeden memory cue
    beaty = [[b[0] + 0.02, b[1], 64.0] for b in t["beaty"][::2]]
    hot = [[n, ms] for n, ms in t["hot"] if n != 1] + [[1, 5000]]
    r = edytor.zapisz(str(usb), u["id"], {"beaty": beaty, "hot": hot, "pamiec": [5000]})
    assert r["bpm"] == 64.0
    t2 = edytor.utwor(str(usb), u["id"])
    assert t2["korekta"] and abs(t2["beaty"][0][0] - beaty[0][0]) < 1e-6 and t2["beaty"][0][2] == 64.0
    assert dict(t2["hot"])[1] == 5000 and t2["pamiec"] == [5000]
    assert edytor.lista(str(usb))[0]["bpm"] == 64.0
    assert [n for n, _ in g.hot_z_pliku(dat_p.read_bytes())] == sorted(n for n, _ in hot if n <= 3)  # DAT: tylko A-C

    # kolejny zapis pendrive'a (analiza od nowa / z pamieci) dostaje poprawke zamiast automatu
    ev = _zapis(a, usb)
    assert any("poprawka" in e.get("tekst", "") for e in ev if e.get("typ") == "log")
    t3 = edytor.utwor(str(usb), u["id"])
    assert abs(t3["beaty"][0][0] - beaty[0][0]) < 1e-6 and dict(t3["hot"])[1] == 5000
    assert pw.czytaj_biblioteke((usb / "PIONEER" / "rekordbox" / "export.pdb").read_bytes())["utwory"][0][0]["tempo"] == 6400

    # zapomnienie poprawki: nastepny zapis znow automatyczny
    edytor.zapisz_korekte(edytor.klucz_korekty(s["file_path"], f["file_size"]), None)
    _zapis(a, usb)
    t4 = edytor.utwor(str(usb), u["id"])
    assert t4["beaty"][0][2] == 128.0 and not t4["korekta"]


def test_audio_path_stays_inside_the_stick(tmp_path):
    (tmp_path / "a.mp3").write_bytes(b"x")
    assert edytor.plik_audio(str(tmp_path), "/a.mp3").endswith("a.mp3")
    for zle in ("/../etc/passwd", "/PIONEER/rekordbox/export.pdb", "/brak.mp3"):
        try:
            edytor.plik_audio(str(tmp_path), zle)
        except ValueError:
            continue
        raise AssertionError(zle)
