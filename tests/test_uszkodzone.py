"""Uszkodzone ramki w pliku audio: ffmpeg dekoduje dalej, ale utwor dostaje ostrzezenie w dzienniku."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "cdjtool"))

import gen_dat as g
import silnik


def test_liczenie_bledow_dekodowania():
    assert g._licz_bledy_dekodowania(b"") == 0
    assert g._licz_bledy_dekodowania(None) == 0
    err = (b"[mp3float @ 0x1] Header missing\n"
           b"Error while decoding stream #0:0: Invalid data found when processing input\n")
    assert g._licz_bledy_dekodowania(err) == 1
    assert g._licz_bledy_dekodowania(err * 3) == 3
    assert g._licz_bledy_dekodowania(b"[mp3float @ 0x1] Header missing\n") == 1


def test_blad_na_koncu_pliku_nie_jest_ostrzezeniem(tmp_path, monkeypatch):
    """Bardzo wiele zwyklych MP3 ma uszkodzona ostatnia ramke; odtwarzacz tego nie slyszy."""
    monkeypatch.setattr(g, "_bledy_w_ogonie", lambda s: 1)
    wywolania = []

    class R:
        stderr = (b"[mp3float @ 0x1] Header missing\n"
                  b"Error while decoding stream #0:0: Invalid data found when processing input\n")

    def falszywy_run(cmd, **kw):
        wywolania.append(cmd)
        open(cmd[-1], "wb").write(b"\x00" * 16)
        return R()

    monkeypatch.setattr(g.subprocess, "run", falszywy_run)
    g.wczytaj(str(tmp_path / "x.mp3"))
    assert g.BLEDY_DEKODOWANIA == 0, "blad tylko na koncu pliku nie moze dawac ostrzezenia"

    monkeypatch.setattr(g, "_bledy_w_ogonie", lambda s: 0)
    g.wczytaj(str(tmp_path / "x.mp3"))
    assert g.BLEDY_DEKODOWANIA == 1, "blad w srodku utworu musi dawac ostrzezenie"


def test_brak_sprawdzenia_ogona_nie_daje_ostrzezenia(tmp_path, monkeypatch):
    """Gdy ffmpeg nie potrafi przewinac do konca pliku, milczymy zamiast ostrzegac przy kazdym pliku."""
    monkeypatch.setattr(g, "_bledy_w_ogonie", lambda s: None)

    class R:
        stderr = b"Error while decoding stream #0:0: Invalid data found when processing input\n"

    def falszywy_run(cmd, **kw):
        open(cmd[-1], "wb").write(b"\x00" * 16)
        return R()

    monkeypatch.setattr(g.subprocess, "run", falszywy_run)
    g.wczytaj(str(tmp_path / "x.mp3"))
    assert g.BLEDY_DEKODOWANIA == 0


def test_opis_utworu_z_ostrzezeniem():
    u = {"meta": {"artysta": "A", "tytul": "T"}}
    czysty = silnik._opis_utworu({"bpm": 128.0}, u)
    assert "uszkodzony" not in czysty
    zly = silnik._opis_utworu({"bpm": 128.0, "uszkodzony": 2}, u)
    assert "uszkodzony plik (błędy dekodowania: 2)" in zly
