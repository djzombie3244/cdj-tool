"""Pod mod RaftTone v1.1: hot cue D-F w EXT, Hot Cue Auto Load "ON", plik .2EX,
pliki moda (MODGRID.BIN, MODHCUE.BIN) zostaja na pendrive."""
import os
import shutil
import struct

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")
if shutil.which(os.environ.get("CDJ_FFMPEG", "ffmpeg")) is None:
    pytest.skip("ffmpeg not available", allow_module_level=True)

import gen_dat as g  # noqa: E402
import pdb_writer as pw  # noqa: E402
import silnik  # noqa: E402
from test_engine import _click_track  # noqa: E402


def test_dat_keeps_three_hot_cues_and_ext_has_up_to_six():
    cue = {"hot": [(1000 * i, "c%d" % i) for i in range(1, 7)], "pamiec": [(1000, "Start")]}
    y = np.zeros(44100 * 20, dtype=np.float32)
    czasy = np.arange(0.5, 19, 0.5)
    dat = g.zbuduj_dat("/a.mp3", 120.0, czasy, y, 20.0, 44100 * 20, cue=cue)
    ext = g.zbuduj_ext("/a.mp3", y, 20.0, czasy, 120.0, cue=cue)
    assert [n for n, _ in g.hot_z_pliku(dat)] == [1, 2, 3]          # firmware CDJ-2000: A-C
    assert g.hot_z_pliku(ext) == [(i, 1000 * i) for i in range(1, 7)]  # mod v1.1: bank D-F z EXT


def test_network_beat_cluster_ignores_offbeat_detections():
    okres = 60 / 140
    rng = np.random.default_rng(1)
    na_beat = 0.030 + 0.003 * rng.standard_normal(70)
    posrodku = ((0.030 + okres / 2 + okres / 2) % okres) - okres / 2 + 0.003 * rng.standard_normal(70)
    przes, zg = g._skupienie_beatow(np.r_[na_beat, posrodku], okres)
    assert abs(przes - 0.030) < 0.004 and 0.4 < zg < 0.6
    przes, zg = g._skupienie_beatow(na_beat, okres)
    assert abs(przes - 0.030) < 0.002 and zg > 0.95


def _run(folder, usb, **opcje):
    ev = []
    silnik.przetworz(dict({"folder_mp3": str(folder), "pendrive": str(usb), "playlista": "T", "kopiuj_mp3": True,
                           "zastap_pioneer": False, "nie_usypiaj": False}, **opcje), ev.append)
    koniec = [e for e in ev if e.get("typ") == "koniec"]
    assert koniec and koniec[0]["ok"]
    return ev, koniec[0]


def _lib(usb):
    return pw.czytaj_biblioteke((usb / "PIONEER" / "rekordbox" / "export.pdb").read_bytes())


def test_autoload_option_and_mod_files_are_kept(tmp_path, monkeypatch):
    a, usb = tmp_path / "a", tmp_path / "usb"
    a.mkdir(); usb.mkdir()
    _click_track(str(a / "one.wav"), 128.0, seconds=30)
    monkeypatch.setattr(silnik, "_na_wolumenie", lambda root: True)   # jak prawdziwy pendrive: kopia + czyszczenie
    monkeypatch.setattr(silnik, "kopia_zapasowa", lambda pioneer, root: str(tmp_path / "kopia"))
    monkeypatch.setattr(silnik, "kopia_zapasowa_pdb", lambda p, root: str(tmp_path / "kopia_pdb"))

    _run(a, usb)
    (f, s), = _lib(usb)["utwory"]
    assert s["autoload"] == ""
    dat_p = usb / s["analyze_path"].lstrip("/")
    oryginal = dat_p.read_bytes()

    # deck (mod v1.1) zapisuje wlasne pliki i nadpisuje DAT przy ladowaniu (np. obcina siatke)
    (usb / "PIONEER" / "MODGRID.BIN").write_bytes(b"MGR1" + bytes(28))
    (usb / "PIONEER" / "MODHCUE.BIN").write_bytes(b"MHC1" + bytes(16))
    dat_p.write_bytes(oryginal[:200])

    # ponowny zapis: DAT znow pelny, Auto Load wedlug opcji, pliki moda nietkniete
    _run(a, usb, autoload=True)
    (f, s), = _lib(usb)["utwory"]
    assert s["autoload"] == "ON"
    assert dat_p.read_bytes() == oryginal
    assert (usb / "PIONEER" / "MODGRID.BIN").read_bytes() == b"MGR1" + bytes(28)

    # zastapienie zawartosci pendrive'a tez zostawia pliki moda (przypisane do sciezek utworow)
    ev, _ = _run(a, usb, zastap_pioneer=True)
    assert (usb / "PIONEER" / "MODHCUE.BIN").read_bytes() == b"MHC1" + bytes(16)
    assert any(e.get("typ") == "log" and "MODGRID.BIN" in e["tekst"] for e in ev)
    assert len(_lib(usb)["utwory"]) == 1


def test_three_band_file_is_written_only_with_the_option(tmp_path):
    a, usb = tmp_path / "a", tmp_path / "usb"
    a.mkdir(); usb.mkdir()
    _click_track(str(a / "one.wav"), 128.0, seconds=30)

    _run(a, usb, trzy_pasma=True)
    (f, s), = _lib(usb)["utwory"]
    dat_p = usb / s["analyze_path"].lstrip("/")
    ex2 = dat_p.with_suffix(".2EX").read_bytes()
    assert ex2[:4] == b"PMAI" and struct.unpack(">I", ex2[8:12])[0] == len(ex2)
    tagi = {t: (o, dl) for o, t, dl in g.tagi_anlz(ex2)}
    assert list(tagi) == [b"PPTH", b"PWV7", b"PWV6", b"PWVC", b"PVDI"]
    o, dl = tagi[b"PWV7"]
    n7 = struct.unpack(">I", ex2[o + 16:o + 20])[0]
    assert dl == 24 + 3 * n7 and struct.unpack(">I", ex2[o + 20:o + 24])[0] == 0x00960000
    # tyle wpisow, ile ma kolorowy waveform w EXT (150 na sekunde)
    ext = dat_p.with_suffix(".EXT").read_bytes()
    o5 = [o for o, t, _ in g.tagi_anlz(ext) if t == b"PWV5"][0]
    assert struct.unpack(">I", ext[o5 + 16:o5 + 20])[0] == n7
    assert tagi[b"PWV6"][1] == 3620
    assert ex2[tagi[b"PPTH"][0] + 16:tagi[b"PWV7"][0]].decode("utf-16-be").rstrip("\x00") == s["file_path"]

    # z pamieci podrecznej i bez opcji: stary .2EX znika
    _run(a, usb)
    assert not dat_p.with_suffix(".2EX").exists()
    _run(a, usb, trzy_pasma=True)
    assert dat_p.with_suffix(".2EX").read_bytes() == ex2
