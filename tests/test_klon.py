"""Kopia pendrive'a: zawartosc identyczna, export.pdb ostatni, pliki systemowe pominiete, bledy zrozumiale."""
import os

import pytest

import klon


def _pendrive(korzen, z_pdb=True):
    os.makedirs(os.path.join(korzen, "PIONEER", "rekordbox"))
    os.makedirs(os.path.join(korzen, "PIONEER", "USBANLZ", "P001"))
    os.makedirs(os.path.join(korzen, "Muzyka"))
    if z_pdb:
        open(os.path.join(korzen, klon.PDB), "wb").write(os.urandom(5000))
    open(os.path.join(korzen, "PIONEER", "USBANLZ", "P001", "ANLZ0000.DAT"), "wb").write(os.urandom(3000))
    open(os.path.join(korzen, "Muzyka", "a.mp3"), "wb").write(os.urandom(9 * 1024 * 1024))
    open(os.path.join(korzen, "Muzyka", "._a.mp3"), "wb").write(b"x")  # AppleDouble
    os.makedirs(os.path.join(korzen, ".Spotlight-V100"))
    open(os.path.join(korzen, ".Spotlight-V100", "x"), "wb").write(b"x")


def test_kopia_identyczna(tmp_path):
    a, b = str(tmp_path / "a"), str(tmp_path / "b")
    os.makedirs(a); os.makedirs(b)
    _pendrive(a)
    klon.sprawdz(a, b)
    zgl = []
    w = klon.kopiuj(a, b, lambda *x: zgl.append(x))
    assert w["ok"] and w["plikow"] == 3 and not w["bledy"]
    for rel, _ in klon.lista_plikow(a):
        assert open(os.path.join(a, rel), "rb").read() == open(os.path.join(b, rel), "rb").read()
    assert not os.path.exists(os.path.join(b, "Muzyka", "._a.mp3"))
    assert not os.path.exists(os.path.join(b, ".Spotlight-V100"))
    assert {z[0] for z in zgl} == {"kopiowanie", "weryfikacja"}


def test_pdb_na_koncu(tmp_path):
    a = str(tmp_path / "a")
    os.makedirs(a)
    _pendrive(a)
    assert klon.lista_plikow(a)[-1][0] == klon.PDB


def test_wykrywa_uszkodzenie(tmp_path, monkeypatch):
    a, b = str(tmp_path / "a"), str(tmp_path / "b")
    os.makedirs(a); os.makedirs(b)
    _pendrive(a)
    oryg = klon._odczyt
    def zepsuty(sciezka, bez_cache):
        for i, d in enumerate(oryg(sciezka, bez_cache)):
            yield (b"\x00" + d[1:]) if bez_cache and sciezka.endswith("a.mp3") and i == 0 else d
    monkeypatch.setattr(klon, "_odczyt", zepsuty)
    w = klon.kopiuj(a, b)
    assert not w["ok"] and os.path.join("Muzyka", "a.mp3") in w["bledy"]


def test_bledy_wejscia(tmp_path):
    a, b = str(tmp_path / "a"), str(tmp_path / "b")
    os.makedirs(a); os.makedirs(b)
    with pytest.raises(ValueError, match="biblioteki"):
        klon.sprawdz(a, b)
    _pendrive(a)
    with pytest.raises(ValueError, match="ten sam"):
        klon.sprawdz(a, a)


def test_cel_pusty_i_wyczysc(tmp_path):
    b = str(tmp_path / "b")
    os.makedirs(os.path.join(b, ".Spotlight-V100"))
    assert klon.cel_pusty(b)
    os.makedirs(os.path.join(b, "Stare"))
    open(os.path.join(b, "Stare", "x"), "wb").write(b"1")
    assert not klon.cel_pusty(b)
    klon.wyczysc(b)
    assert klon.cel_pusty(b) and os.path.isdir(os.path.join(b, ".Spotlight-V100"))
