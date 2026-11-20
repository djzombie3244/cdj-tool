"""Scenariusz z calym pendrive'em: filtr przed analiza, playlisty inteligentne, sortowanie, propozycje i kopia.
Na symulowanym pendrive'ie (katalog), bez sprzetu."""
import os

import edytor
import pdb_writer as pw
import silnik
from test_engine import _click_track


def _run(folder, usb, name, **kw):
    ev = []
    opcje = {"folder_mp3": folder, "pendrive": usb, "playlista": name, "kopiuj_mp3": True,
             "zastap_pioneer": False, "okladki": False, "test_dysku": False}
    opcje.update(kw)
    silnik.przetworz(opcje, ev.append)
    return ev


def test_scenariusz_filtr_playlisty_sortowanie_kopia(tmp_path):
    muz, usb, cel = tmp_path / "muz", tmp_path / "usb", tmp_path / "kopie"
    for d in (muz, usb, cel):
        d.mkdir()
    _click_track(str(muz / "wolny.wav"), 100.0)
    _click_track(str(muz / "szybki.wav"), 140.0)
    pdb = usb / "PIONEER" / "rekordbox" / "export.pdb"

    # 1. filtr gatunku odrzuca wszystko PRZED analiza: nie ma zdarzen "utwor", pendrive bez bazy
    try:
        _run(str(muz), str(usb), "Test", filtr={"gatunek": "nieistniejacy-gatunek"})
        raise AssertionError("pusty wynik filtra powinien byc bledem")
    except ValueError as e:
        assert "Filtr" in str(e)
    assert not pdb.exists()

    # 2. filtr BPM (po analizie) + playlista inteligentna + sortowanie po BPM
    ev = _run(str(muz), str(usb), "Test", filtr={"bpm_min": 130.0},
              inteligentne_playlisty=[{"nazwa": "Szybkie", "bpm_min": 130.0}], sortuj_playlisty="bpm")
    assert any(e.get("typ") == "koniec" and e.get("ok") for e in ev)
    lib = pw.czytaj_biblioteke(pdb.read_bytes())
    assert len(lib["utwory"]) == 1, "po filtrze BPM na pendrive ma trafic tylko szybki utwor"
    assert "Szybkie" in {n for _, _, _, _, n in lib["drzewo"]}

    # 3. propozycje memory cue: funkcja dziala na zapisanym utworze (krotki utwor moze nie miec propozycji)
    tid = lib["utwory"][0][0]["id"]
    prop = edytor.propozycje(str(usb), tid)
    assert isinstance(prop, list)

    # 4. kopia z poprawkami: oryginal na pendrive'ie bez zmian, kopia z plikami analizy i baza
    przed = pdb.read_bytes()
    u = edytor.utwor(str(usb), tid)
    wynik = edytor.kopia(str(usb), tid, {"beaty": u["beaty"], "hot": u["hot"], "pamiec": u["pamiec"]}, str(cel))
    assert wynik["ok"] and os.path.realpath(wynik["folder"]) == os.path.realpath(str(cel))
    assert pdb.read_bytes() == przed, "oryginalna baza na pendrive'ie zmieniona przez kopie"
    assert (cel / "PIONEER" / "rekordbox" / "export.pdb").exists()
    assert any(p.suffix.upper() == ".DAT" for p in (cel / "PIONEER").rglob("*"))
