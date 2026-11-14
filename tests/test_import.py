"""Import siatek i cue: rekordbox XML, Traktor NML, tagi Serato; zastosowanie przy zapisie pendrive'a."""
import base64
import os
import shutil
import struct

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")
if shutil.which(os.environ.get("CDJ_FFMPEG", "ffmpeg")) is None:
    pytest.skip("ffmpeg not available", allow_module_level=True)

import edytor  # noqa: E402
import importcue  # noqa: E402
import silnik  # noqa: E402
from test_engine import _click_track  # noqa: E402

RB_XML = """<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0"><PRODUCT Name="rekordbox" Version="6.8.5"/>
<COLLECTION Entries="1">
<TRACK TrackID="1" Name="One" Artist="A" Location="file://localhost%s" AverageBpm="128.00">
<TEMPO Inizio="0.150" Bpm="128.00" Metro="4/4" Battito="3"/>
<TEMPO Inizio="15.150" Bpm="64.00" Metro="4/4" Battito="1"/>
<POSITION_MARK Name="" Type="0" Start="1.500" Num="-1"/>
<POSITION_MARK Name="Drop" Type="0" Start="12.345" Num="4"/>
<POSITION_MARK Name="" Type="0" Start="0.150" Num="0"/>
</TRACK></COLLECTION></DJ_PLAYLISTS>"""

NML = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<NML VERSION="19"><COLLECTION ENTRIES="1">
<ENTRY TITLE="One" ARTIST="A"><LOCATION DIR="/:Users/:dj/:Music/:Zażółć/:" FILE="one.mp3" VOLUME="Macintosh HD" VOLUMEID="x"></LOCATION>
<TEMPO BPM="126.500000" BPM_QUALITY="100.000000"></TEMPO>
<CUE_V2 NAME="AutoGrid" DISPL_ORDER="0" TYPE="4" START="52.5" LEN="0" REPEATS="-1" HOTCUE="0"></CUE_V2>
<CUE_V2 NAME="n.n." DISPL_ORDER="0" TYPE="0" START="30123.4" LEN="0" REPEATS="-1" HOTCUE="2"></CUE_V2>
<CUE_V2 NAME="n.n." DISPL_ORDER="0" TYPE="0" START="61000.0" LEN="0" REPEATS="-1" HOTCUE="-1"></CUE_V2>
</ENTRY></COLLECTION></NML>"""


def test_rekordbox_xml(tmp_path):
    p = tmp_path / "rb.xml"
    p.write_text(RB_XML % "/Users/dj/Music/Za%C5%BC%C3%B3%C5%82%C4%87/One%20Track.mp3", encoding="utf-8")
    typ, m = importcue.rozpoznaj_i_czytaj(str(p), "rb.xml")
    assert typ == "rekordbox"
    (k, w), = m.items()
    assert k == importcue.klucz_sciezki("/Users/dj/Music/Zażółć/One Track.mp3")
    assert w["siatka"] == [[0.15, 128.0, 3], [15.15, 64.0, 1]]
    assert w["hot"] == [(1, 150), (5, 12345)] and w["pamiec"] == [1500]
    assert importcue._sciezka_z_url("file://localhost/C:/Music/a%20b.mp3") == "C:/Music/a b.mp3"


def test_traktor_nml(tmp_path):
    p = tmp_path / "collection.nml"
    p.write_text(NML, encoding="utf-8")
    typ, m = importcue.rozpoznaj_i_czytaj(str(p), "collection.nml")
    assert typ == "traktor"
    w = m[importcue.klucz_sciezki("/Users/dj/Music/Zażółć/one.mp3")]
    assert w["siatka"] == [[0.0525, 126.5, 1]]
    assert w["hot"] == [(1, 52), (3, 30123)] and w["pamiec"] == [61000]
    assert importcue._sciezka_traktora({"DIR": "/:Music/:", "FILE": "a.mp3", "VOLUME": "D:"}) == "D:/Music/a.mp3"
    assert importcue._sciezka_traktora({"DIR": "/:Sets/:", "FILE": "a.mp3", "VOLUME": "USB"}) == "/Volumes/USB/Sets/a.mp3"


def _geob(opis, dane):
    tresc = b"\x00application/octet-stream\x00\x00" + opis.encode("latin-1") + b"\x00" + dane
    return b"GEOB" + struct.pack(">I", len(tresc)) + b"\x00\x00" + tresc


def test_serato_tags(tmp_path):
    cue = lambda i, ms: b"CUE\x00" + struct.pack(">I", 13) + b"\x00" + bytes([i]) + struct.pack(">I", ms) + b"\x00\xcc\x00\x00\x00\x00\x00"  # noqa: E731
    markers = b"\x01\x01" + cue(0, 1000) + cue(3, 65432) + b"BPMLOCK\x00" + struct.pack(">I", 1) + b"\x00" + b"\x00"
    m2 = b"\x01\x01" + base64.b64encode(markers).rstrip(b"=")
    grid = b"\x01\x00" + struct.pack(">I", 2) + struct.pack(">fI", 0.25, 64) + struct.pack(">ff", 30.25, 120.0) + b"\x00"
    ramki = _geob("Serato Markers2", m2) + _geob("Serato BeatGrid", grid)
    p = tmp_path / "s.mp3"
    p.write_bytes(b"ID3\x03\x00\x00" + bytes([(len(ramki) >> 21) & 127, (len(ramki) >> 14) & 127,
                                              (len(ramki) >> 7) & 127, len(ramki) & 127]) + ramki + b"\xff\xfb\x90\x00" * 10)
    w = importcue.serato_z_pliku(str(p))
    assert w["hot"] == [(1, 1000), (4, 65432)]
    assert w["siatka"][0][0] == 0.25 and abs(w["siatka"][0][1] - 128.0) < 1e-6 and w["siatka"][1][:2] == [30.25, 120.0]
    assert importcue.serato_z_pliku(str(tmp_path / "brak.mp3")) is None


def test_edit_from_import_keeps_missing_hot_cues():
    beaty = [[0.5 + 0.5 * i, i % 4 + 1, 120.0] for i in range(100)]
    w = {"zrodlo": "rekordbox", "siatka": [[10.0, 100.0, 1]], "hot": [[2, 5000]], "pamiec": []}
    e = importcue.edycja(w, beaty, [(1, 100), (2, 200), (3, 300), (4, 400), (5, 500), (6, 600)], [700], 50.0)
    t = [b[0] for b in e["beaty"]]
    assert abs(t[0] - 0.4) < 1e-9 and all(abs((b - a) - 0.6) < 1e-9 for a, b in zip(t, t[1:])) and t[-1] < 50.0
    assert [b for b in e["beaty"] if abs(b[0] - 10.0) < 1e-9][0][1] == 1
    assert dict(e["hot"]) == {1: 100, 2: 5000, 3: 300, 4: 400, 5: 500, 6: 600} and e["pamiec"] == [700]
    przes = importcue.edycja(w, beaty, [], [], 50.0, -0.026)
    assert abs(min(abs(b[0] - 9.974) for b in przes["beaty"])) < 1e-9 and dict(przes["hot"])[2] == 4974


def test_import_is_applied_when_writing_the_stick(tmp_path, monkeypatch):
    monkeypatch.setenv("CDJ_IMPORT", str(tmp_path / "import.json"))
    monkeypatch.setenv("CDJ_KOREKTY", str(tmp_path / "korekty.json"))
    monkeypatch.setenv("CDJ_PRZEKODUJ", str(tmp_path / "przekoduj.json"))
    a, usb = tmp_path / "a", tmp_path / "usb"
    a.mkdir(); usb.mkdir()
    _click_track(str(a / "one.wav"), 128.0, seconds=30)
    xml = tmp_path / "rb.xml"
    xml.write_text(RB_XML % str(a / "one.wav"), encoding="utf-8")
    assert importcue.dodaj(str(xml), "rb.xml") == {"typ": "rekordbox", "utworow": 1}
    assert importcue.stan()[0]["utworow"] == 1

    def zapis(**opcje):
        ev = []
        silnik.przetworz(dict({"folder_mp3": str(a), "pendrive": str(usb), "playlista": "T", "kopiuj_mp3": True,
                               "zastap_pioneer": False, "nie_usypiaj": False, "okladki": False}, **opcje), ev.append)
        assert [e for e in ev if e.get("typ") == "koniec"][0]["ok"]
        return ev

    ev = zapis()
    assert any("rekordbox" in e.get("tekst", "") for e in ev if e.get("typ") == "log")
    (u,) = edytor.lista(str(usb))
    t = edytor.utwor(str(usb), u["id"])
    assert abs(t["beaty"][0][0] - 0.15) < 1e-3 and t["beaty"][0][1] == 3 and t["beaty"][0][2] == 128.0
    assert t["beaty"][-1][2] == 64.0 and t["dynamiczna"]
    assert dict(t["hot"])[1] == 150 and dict(t["hot"])[5] == 12345 and t["pamiec"] == [1500]
    # wylaczony import: automatyczna siatka
    zapis(import_cue=False)
    t = edytor.utwor(str(usb), u["id"])
    assert not t["dynamiczna"] and dict(t["hot"]).get(5) != 12345
    # poprawka z edytora ma pierwszenstwo przed importem
    edytor.zapisz(str(usb), u["id"], {"beaty": [[0.3 + 0.5 * i, i % 4 + 1, 120.0] for i in range(50)],
                                      "hot": [[1, 999]], "pamiec": []})
    zapis()
    t = edytor.utwor(str(usb), u["id"])
    assert t["beaty"][0][2] == 120.0 and dict(t["hot"]) == {1: 999}
    importcue.usun()
    assert importcue.stan() == []
