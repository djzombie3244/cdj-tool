"""Klucz (Camelot / Open Key) i tempo na poczatku nazwy pliku nie trafiaja do pola wykonawcy."""
import okladki


def test_key_and_bpm_prefix_is_not_the_artist():
    assert okladki.z_nazwy_pliku("12A - Doja Cat - Paint The Town Red.mp3") == ("Doja Cat", "Paint The Town Red")
    assert okladki.z_nazwy_pliku("1A - 128 - Artist X - Title (Mix).mp3") == ("Artist X", "Title (Mix)")
    assert okladki.z_nazwy_pliku("8A - 6 - Artist - Title.mp3") == ("Artist", "Title")
    assert okladki.z_nazwy_pliku("[8A] Artist - Title.mp3") == ("Artist", "Title")
    assert okladki.z_nazwy_pliku("03. Kękę - Myślę o tym.mp3") == ("Kękę", "Myślę o tym")
    assert okladki.z_nazwy_pliku("Artist - Title - Remix.mp3") == ("Artist", "Title - Remix")
