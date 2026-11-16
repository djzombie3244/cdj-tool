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


def test_opis_utworu_z_ostrzezeniem():
    u = {"meta": {"artysta": "A", "tytul": "T"}}
    czysty = silnik._opis_utworu({"bpm": 128.0}, u)
    assert "uszkodzony" not in czysty
    zly = silnik._opis_utworu({"bpm": 128.0, "uszkodzony": 2}, u)
    assert "uszkodzony plik (błędy dekodowania: 2)" in zly
