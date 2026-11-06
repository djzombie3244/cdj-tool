"""Okladki: obraz z tagow (ID3 w MP3/WAV/AIFF, covr w M4A), miniatury 80/240 px i tabela 13 w export.pdb."""
import os
import shutil
import struct
import subprocess

import pytest

import okladki
import pdb_writer as pw

OBRAZ = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 4 + b"\xff\xd9"


def _ramka(fid, dane, wersja=3):
    n = struct.pack(">I", len(dane)) if wersja == 3 else bytes(
        [(len(dane) >> 21) & 127, (len(dane) >> 14) & 127, (len(dane) >> 7) & 127, len(dane) & 127])
    return fid + n + b"\x00\x00" + dane


def _id3(ramki, wersja=3):
    tresc = b"".join(ramki)
    n = len(tresc)
    return b"ID3" + bytes([wersja, 0, 0]) + bytes([(n >> 21) & 127, (n >> 14) & 127, (n >> 7) & 127, n & 127]) + tresc


def _apic(typ, obraz, enc=0, opis=b""):
    if enc == 1:
        return bytes([1]) + b"image/jpeg\x00" + bytes([typ]) + "﻿".encode("utf-16-le") + opis + b"\x00\x00" + obraz
    return bytes([enc]) + b"image/jpeg\x00" + bytes([typ]) + opis + b"\x00" + obraz


def test_id3_front_cover_is_preferred(tmp_path):
    inny = b"\xff\xd8inny\xff\xd9"
    for wersja in (3, 4):
        p = tmp_path / ("a%d.mp3" % wersja)
        p.write_bytes(_id3([_ramka(b"TIT2", b"\x00Tytul", wersja),
                            _ramka(b"APIC", _apic(0, inny), wersja),
                            _ramka(b"APIC", _apic(3, OBRAZ, enc=1, opis="o".encode("utf-16-le")), wersja)], wersja)
                      + b"\xff\xfb" + bytes(400))
        assert okladki.wyciagnij(str(p)) == OBRAZ
    p = tmp_path / "bez.mp3"
    p.write_bytes(_id3([_ramka(b"TIT2", b"\x00x")]) + bytes(100))
    assert okladki.wyciagnij(str(p)) is None


def test_wav_and_aiff_id3_chunk(tmp_path):
    tag = _id3([_ramka(b"APIC", _apic(3, OBRAZ))])
    fmt = b"fmt " + struct.pack("<I", 16) + struct.pack("<HHIIHH", 1, 1, 44100, 88200, 2, 16)
    dane = b"data" + struct.pack("<I", 4) + bytes(4)
    id3 = b"id3 " + struct.pack("<I", len(tag)) + tag + (b"\x00" if len(tag) & 1 else b"")
    tresc = b"WAVE" + fmt + dane + id3
    (tmp_path / "a.wav").write_bytes(b"RIFF" + struct.pack("<I", len(tresc)) + tresc)
    assert okladki.wyciagnij(str(tmp_path / "a.wav")) == OBRAZ
    id3b = b"ID3 " + struct.pack(">I", len(tag)) + tag + (b"\x00" if len(tag) & 1 else b"")
    tresc = b"AIFF" + b"COMM" + struct.pack(">I", 18) + bytes(18) + id3b
    (tmp_path / "a.aiff").write_bytes(b"FORM" + struct.pack(">I", len(tresc)) + tresc)
    assert okladki.wyciagnij(str(tmp_path / "a.aiff")) == OBRAZ


def _atom(typ, dane):
    return struct.pack(">I", 8 + len(dane)) + typ + dane


def test_m4a_covr(tmp_path):
    covr = _atom(b"covr", _atom(b"data", struct.pack(">II", 13, 0) + OBRAZ))
    meta = _atom(b"meta", bytes(4) + _atom(b"hdlr", bytes(25)) + _atom(b"ilst", covr))
    plik = _atom(b"ftyp", b"M4A \x00\x00\x00\x00") + _atom(b"mdat", bytes(64)) + _atom(b"moov", _atom(b"udta", meta))
    (tmp_path / "a.m4a").write_bytes(plik)
    assert okladki.wyciagnij(str(tmp_path / "a.m4a")) == OBRAZ


def test_artwork_row_matches_rekordbox():
    # wiersz z eksportu rekordboxa (PIONEER/rekordbox/export.pdb, tabela 13)
    assert pw.art_row(1, "/PIONEER/Artwork/00001/a1.jpg") == bytes.fromhex(
        "010000003d2f50494f4e4545522f417274776f726b2f30303030312f61312e6a70670000")


def test_cover_on_stick(tmp_path):
    np = pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    Image = pytest.importorskip("PIL.Image")
    if shutil.which(os.environ.get("CDJ_FFMPEG", "ffmpeg")) is None:
        pytest.skip("ffmpeg not available")
    import io
    import silnik
    from test_engine import _click_track
    a, usb = tmp_path / "a", tmp_path / "usb"
    a.mkdir(); usb.mkdir()
    b = io.BytesIO()
    Image.new("RGB", (500, 400), (200, 30, 60)).save(b, "JPEG")
    jpg = b.getvalue()
    for nm in ("one", "two"):
        _click_track(str(tmp_path / (nm + ".wav")), 128.0, seconds=20)
        subprocess.run([os.environ.get("CDJ_FFMPEG", "ffmpeg"), "-v", "error", "-y", "-i", str(tmp_path / (nm + ".wav")),
                        "-c:a", "libmp3lame", "-b:a", "128k", str(tmp_path / (nm + ".mp3"))], check=True)
        dane = (tmp_path / (nm + ".mp3")).read_bytes()
        if nm == "one":
            dane = _id3([_ramka(b"APIC", _apic(3, jpg))]) + dane
        (a / (nm + ".mp3")).write_bytes(dane)
    zd = []
    silnik.przetworz({"folder_mp3": str(a), "pendrive": str(usb), "playlista": "T", "kopiuj_mp3": True,
                      "zastap_pioneer": False, "nie_usypiaj": False}, zd.append)
    assert [e for e in zd if e.get("typ") == "koniec"][0]["ok"]
    lib = pw.czytaj_biblioteke((usb / "PIONEER" / "rekordbox" / "export.pdb").read_bytes())
    art = {s["filename"]: f["artwork"] for f, s in lib["utwory"]}
    assert art["two.mp3"] == 0 and art["one.mp3"] > 0
    assert lib["okladki"] == [(art["one.mp3"], "/PIONEER/Artwork/00001/a%d.jpg" % art["one.mp3"])]
    for nazwa, r in (("a%d.jpg", 80), ("a%d_m.jpg", 240)):
        im = Image.open(usb / "PIONEER" / "Artwork" / "00001" / (nazwa % art["one.mp3"]))
        assert im.size == (r, r) and im.format == "JPEG"

    # ponowny zapis: ta sama okladka pod tym samym id; bez opcji tabela i id zostaja
    silnik.przetworz({"folder_mp3": str(a), "pendrive": str(usb), "playlista": "T", "kopiuj_mp3": True,
                      "zastap_pioneer": False, "nie_usypiaj": False}, lambda e: None)
    lib2 = pw.czytaj_biblioteke((usb / "PIONEER" / "rekordbox" / "export.pdb").read_bytes())
    assert lib2["okladki"] == lib["okladki"]
    assert {s["filename"]: f["artwork"] for f, s in lib2["utwory"]} == art


def test_flac_picture_block(tmp_path):
    def blok(typ, dane, ostatni=False):
        return bytes([typ | (0x80 if ostatni else 0)]) + len(dane).to_bytes(3, "big") + dane
    mime = b"image/jpeg"
    pic = struct.pack(">II", 3, len(mime)) + mime + struct.pack(">I", 0) + bytes(16) + struct.pack(">I", len(OBRAZ)) + OBRAZ
    (tmp_path / "a.flac").write_bytes(b"fLaC" + blok(0, bytes(34)) + blok(6, pic, True) + bytes(50))
    assert okladki.wyciagnij(str(tmp_path / "a.flac")) == OBRAZ


def _deezer(wpisy):
    import json
    return json.dumps({"data": [{"title": t, "artist": {"name": a},
                                 "album": {"title": "LP", "cover_xl": "https://cdn/" + u}} for a, t, u in wpisy]}).encode()


def test_net_lookup_matches_artist_and_title_and_caches(tmp_path):
    zapytania = []

    def pobierz(url):
        zapytania.append(url)
        if url.startswith("https://cdn/"):
            return b"\xff\xd8" + url.encode() + b"\xff\xd9"
        if "api.deezer.com/search?" in url:
            return _deezer([("Other Guy", "Forever", "zly"), ("GLF", "Forever (Radio Edit)", "radio"),
                            ("GLF", "Forever (Original Mix)", "dobry")])
        return b'{"data": [], "results": []}'

    s = okladki.Siec(str(tmp_path / "c"), pobierz)
    assert s.szukaj("GLF", "Forever (Original Mix)", "Unknown Album") == b"\xff\xd8https://cdn/dobry\xff\xd9"
    n = len(zapytania)
    assert okladki.Siec(str(tmp_path / "c"), pobierz).szukaj("GLF", "Forever (Original Mix)") is not None
    assert len(zapytania) == n                                    # drugi raz z pamieci podrecznej
    # bez wykonawcy w tagach: z nazwy pliku; inny wykonawca w wynikach -> brak
    assert s.szukaj("Unknown Artist", "x", "", "03. Nobody - Forever (Original Mix).mp3") is None
    assert okladki.Siec(str(tmp_path / "c"), lambda u: 1 / 0).szukaj("Nobody", "Forever") is None  # "brak" zapamietany


def test_net_lookup_stops_without_network(tmp_path):
    proby = []

    def bez_sieci(url):
        proby.append(url)
        raise OSError("no route")

    s = okladki.Siec(str(tmp_path / "c"), bez_sieci)
    for i in range(10):
        assert s.szukaj("A%d" % i, "T") is None
    assert s.wylaczone and len(proby) <= 4
    assert not list((tmp_path / "c").iterdir())                  # brak sieci nie zapisuje "nie znaleziono"


def test_net_cover_on_stick_only_with_option(tmp_path, monkeypatch):
    pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    Image = pytest.importorskip("PIL.Image")
    if shutil.which(os.environ.get("CDJ_FFMPEG", "ffmpeg")) is None:
        pytest.skip("ffmpeg not available")
    import io
    import silnik
    from test_engine import _click_track
    b = io.BytesIO()
    Image.new("RGB", (600, 600), (10, 200, 60)).save(b, "JPEG")
    jpg = b.getvalue()
    monkeypatch.setattr(silnik, "_cache_katalog", lambda: str(tmp_path / "cache" / "analizy"))
    monkeypatch.setattr(okladki, "_pobierz", lambda url: jpg if url.startswith("https://cdn/")
                        else _deezer([("Germont", "Dyary (Extended Mix)", "x")]))
    a, usb = tmp_path / "a", tmp_path / "usb"
    a.mkdir(); usb.mkdir()
    _click_track(str(a / "12. Germont - Dyary (Extended Mix).wav"), 128.0, seconds=20)
    for siec in (False, True):
        silnik.przetworz({"folder_mp3": str(a), "pendrive": str(usb), "playlista": "T", "kopiuj_mp3": True,
                          "zastap_pioneer": False, "nie_usypiaj": False, "okladki_siec": siec}, lambda e: None)
        lib = pw.czytaj_biblioteke((usb / "PIONEER" / "rekordbox" / "export.pdb").read_bytes())
        (f, s), = lib["utwory"]
        assert (f["artwork"] > 0) == siec


def test_missing_tags_are_filled_from_file_and_folder_names():
    import unicodedata
    import silnik
    d = unicodedata.normalize("NFD", "/x/Hemp Gru - Lojalność 2011/")
    u = silnik._uzupelnij_z_nazw(d + unicodedata.normalize("NFD", "Hemp Gru - Dwa Spojrzenia ft. Kali_ Marta.mp3"),
                                 None, None, None)
    assert u == {"tytul": "Dwa Spojrzenia ft. Kali, Marta", "artysta": "Hemp Gru", "album": "Lojalność", "rok": 2011}
    # tytul z tagu w formie "Wykonawca - Tytul" tez jest rozdzielany; pelne tagi zostaja bez zmian
    assert silnik._uzupelnij_z_nazw("/x/a.mp3", "Hemp Gru - Daj Żyć", None, None)["artysta"] == "Hemp Gru"
    assert silnik._uzupelnij_z_nazw(d + "Hemp Gru - X.mp3", "Song", "Artist", "LP") == \
        {"tytul": "Song", "artysta": "Artist", "album": "LP", "rok": 0}
