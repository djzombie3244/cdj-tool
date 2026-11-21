"""Naprawa tytulow i artystow: reguly na przykladach z prawdziwych nazw plikow (Edmpacks, WIELKIE LITERY)."""
import naprawa


def test_adres_i_domena_w_nawiasach():
    t, a, z = naprawa.napraw("Heart Of Glass (Original Mix) [EdmPacks.com]", "Blondie")
    assert t == "Heart Of Glass (Original Mix)" and a == "Blondie" and z


def test_www_w_tytule():
    t, a, _ = naprawa.napraw("Tequila www.example.com", "VERO")
    assert t == "Tequila" and a == "VERO"


def test_numer_na_poczatku():
    t, a, z = naprawa.napraw("01 - Heart Of Glass", "Blondie")
    assert t == "Heart Of Glass" and z


def test_rok_nie_jest_numerem():
    t, a, z = naprawa.napraw("1999", "Prince")
    assert t == "1999" and not z


def test_artysta_z_tytulu_gdy_pusty():
    t, a, z = naprawa.napraw("Fernando Campo - Baby Music", "")
    assert a == "Fernando Campo" and t == "Baby Music" and "artysta wzięty z tytułu" in z


def test_artysta_nie_jest_nadpisywany():
    t, a, _ = naprawa.napraw("Foo - Bar", "Ktos")
    assert t == "Foo - Bar" and a == "Ktos"


def test_wielkie_litery():
    t, a, z = naprawa.napraw("HEART OF GLASS", "BLONDIE")
    assert t == "Heart Of Glass" and a == "BLONDIE" and len(z) == 1


def test_skrot_artysty_bez_zmian():
    t, a, z = naprawa.napraw("Tequila www.example.com", "VERO")
    assert a == "VERO" and z == ["usunięto adres lub dopisek z tytułu"]


def test_nic_do_zmiany():
    assert naprawa.napraw("Heart Of Glass", "Blondie") == ("Heart Of Glass", "Blondie", [])


def test_pusty_tytul_zostaje():
    t, a, z = naprawa.napraw("[EdmPacks.com]", "X")
    assert t == "[EdmPacks.com]" and z == []
