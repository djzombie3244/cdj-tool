"""Gatunek i komentarz z tagow: odczyt z pliku (ffmpeg) i zapis komentarza do wiersza utworu."""
import os
import shutil
import subprocess

import pytest

import pdb_writer as pw
import silnik

TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "cdjtool", "wzorzec", "export.pdb")


@pytest.mark.skipif(not shutil.which("ffmpeg") and not os.environ.get("CDJ_FFMPEG"), reason="brak ffmpeg")
def test_gatunek_i_komentarz_z_tagow(tmp_path):
    plik = tmp_path / "t.mp3"
    subprocess.run([silnik.FFMPEG, "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                    "-metadata", "genre=Techno", "-metadata", "comment=Ciemny klub",
                    "-y", str(plik)], check=True)
    meta = silnik.metadane(str(plik))
    assert meta["gatunek"] == "Techno"
    assert meta["komentarz"] == "Ciemny klub"


def test_komentarz_w_wierszu_utworu():
    data = open(TEMPLATE, "rb").read()
    w = pw.Wzorzec(data)
    page = w.page(w.chain(0)[-1])
    n, offs = pw.czytaj_wiersze(page)
    end = int.from_bytes(page[30:32], "little")
    f, s = pw.parse_track_row(page[pw.HDR + offs[0]:pw.HDR + end])
    s = dict(s, comment="Ciemny klub", title="T", filename="t.mp3", file_path="/Contents/t.mp3")
    row = pw.track_row(0, dict(f, id=1, artist=1, album=1), s)
    assert "Ciemny klub".encode("utf-16-le") in row or b"Ciemny klub" in row
