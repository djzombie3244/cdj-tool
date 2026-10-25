"""Tests of the export.pdb writer/reader. Standard library only."""
import os
import struct

import pdb_writer as pw

TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "cdjtool", "wzorzec", "export.pdb")


def _template():
    return open(TEMPLATE, "rb").read()


def _track(i, title):
    data = _template()
    w = pw.Wzorzec(data)
    page = w.page(w.chain(0)[-1])
    n, offs = pw.czytaj_wiersze(page)
    end = struct.unpack("<H", page[30:32])[0]
    f, s = pw.parse_track_row(page[pw.HDR + offs[0]:pw.HDR + end])
    f = dict(f, id=i, artist=1, album=1)
    s = dict(s, title=title, filename="t%d.mp3" % i, file_path="/Contents/A/B/t%d.mp3" % i)
    return f, s


def _build(titles):
    rows = []
    for i, t in enumerate(titles, 1):
        f, s = _track(i, t)
        rows.append((f, s))
    tmp = [pw.track_row(0, f, s) for f, s in rows]
    out, k = [], 0
    for group in pw.rows_per_pages(tmp):
        for j in range(len(group)):
            out.append(pw.track_row(0x20 * j, *rows[k]))
            k += 1
    return pw.zbuduj_pdb(_template(), {
        0: out,
        2: [pw.artist_row(0, 1, "Artist")],
        3: [pw.album_row(0, 1, 0, "Album")],
        7: [pw.playlist_tree_row(0, 0, 1, False, "Tracks")],
        8: [pw.playlist_entry_row(i + 1, i + 1, 1) for i in range(len(titles))],
    })


def test_template_is_neutral():
    lib = pw.czytaj_biblioteke(_template())
    assert len(lib["utwory"]) == 1
    assert lib["utwory"][0][1]["title"] == "Template"


def test_roundtrip_many_tracks():
    titles = ["Track %d" % i for i in range(1, 201)]
    lib = pw.czytaj_biblioteke(_build(titles))
    assert [s["title"] for _, s in lib["utwory"]] == titles
    assert len(lib["wpisy"]) == 200


def test_long_strings_are_4_byte_aligned():
    """Regression: unaligned UTF-16 strings made the CDJ-2000 fail with E-8709."""
    titles = ["Edplacid Patiño %d" % i for i in range(30)] + ["x" * 200]
    data = _build(titles)
    w = pw.Wzorzec(data)
    for pg in w.chain(0)[1:]:
        page = w.page(pg)
        if page[27] not in (0x24, 0x34):
            continue
        n, offs = pw.czytaj_wiersze(page)
        for o in offs:
            row = page[pw.HDR + o:]
            for so in struct.unpack("<21H", row[94:136]):
                if row[so] in (0x90, 0x40):
                    assert so % 4 == 0


def test_unicode_survives():
    lib = pw.czytaj_biblioteke(_build(["Żółć – ありがとう"]))
    assert lib["utwory"][0][1]["title"] == "Żółć – ありがとう"


def test_index_capacity_guard():
    """More tracks than the page index can hold must fail loudly, not write a broken database."""
    import pytest
    f, st = _track(1, "x")
    groups = pw.rows_per_pages([pw.track_row(0, f, st)] * 30000)
    rows = [pw.track_row(0x20 * j, f, st) for g in groups for j in range(len(g))]
    with pytest.raises(ValueError):
        pw.zbuduj_pdb(_template(), {0: rows})
