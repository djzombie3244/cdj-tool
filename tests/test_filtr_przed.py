"""Filtr kolekcji przed analiza: data pliku i gatunek sprawdzane przed analiza, BPM dopiero po niej."""
import os

import silnik


def test_filtr_daty_przed_analiza(tmp_path):
    stary = tmp_path / "stary.mp3"
    nowy = tmp_path / "nowy.mp3"
    stary.write_bytes(b"x")
    nowy.write_bytes(b"x")
    os.utime(stary, (1000, 1000))
    os.utime(nowy, (5000, 5000))
    pliki = ["stary.mp3", "nowy.mp3"]
    assert silnik._filtr_przed_analiza(str(tmp_path), pliki, {"data_od": 3000}) == ["nowy.mp3"]
    assert silnik._filtr_przed_analiza(str(tmp_path), pliki, {"data_do": 3000}) == ["stary.mp3"]


def test_nieczytelny_plik_zostaje_dla_analizy(tmp_path):
    zly = tmp_path / "zly.mp3"
    zly.write_bytes(b"to nie jest mp3")
    assert silnik._filtr_przed_analiza(str(tmp_path), ["zly.mp3"], {"gatunek": "house"}) == ["zly.mp3"]
