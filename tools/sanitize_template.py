#!/usr/bin/env python3
"""Tworzy neutralny wzorzec export.pdb: usuwa z niego dane cudzej biblioteki.

Z oryginalnego pliku (wygenerowanego przez Rekordbox) zostaje struktura i ustawienia,
a wiersze utworów, artystów, albumów i playlist są zastępowane jednym neutralnym wierszem.
Użycie:  python3 sanitize_template.py wejscie.pdb wyjscie.pdb
"""
import os, struct, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "cdjtool"))
import pdb_writer as pw

NEUTRALNE = {"title": "Template", "filename": "template.mp3", "file_path": "/Contents/Template/template.mp3",
             "analyze_path": "/PIONEER/USBANLZ/P000/00000000/ANLZ0000.DAT", "comment": "",
             "date_added": "2000-01-01", "analyze_date": "2000-01-01", "release_date": "",
             "isrc": "", "lyricist": "", "mix_name": "", "message": "", "kuvo": "ON", "autoload": "ON"}


def main(wej, wyj):
    d = open(wej, "rb").read()
    w = pw.Wzorzec(d)
    p = w.page(w.chain(0)[-1])
    n, offs = pw.czytaj_wiersze(p)
    gr = sorted(set(offs)) + [struct.unpack("<H", p[30:32])[0]]
    o = offs[0]
    nxt = [b for b in gr if b > o][0]
    f, s = pw.parse_track_row(p[pw.HDR + o:pw.HDR + nxt])
    f = dict(f, id=1, artist=1, album=1, key=0, label=0, genre=0, artwork=0, composer=0, orig_artist=0, remixer=0)
    s = dict(s)
    for k, v in NEUTRALNE.items():
        s[k] = v
    wyj_bytes = pw.zbuduj_pdb(d, {
        0: [pw.track_row(0, f, s)],
        2: [pw.artist_row(0, 1, "Template Artist")],
        3: [pw.album_row(0, 1, 0, "Template Album")],
        7: [pw.playlist_tree_row(0, 0, 1, False, "Tracks")],
        8: [pw.playlist_entry_row(1, 1, 1)],
    })
    # wyzeruj strony, do ktorych nie prowadzi zaden lancuch (resztki po starych wierszach)
    uzywane = {0}
    for t, (pusty, pierwszy, ostatni) in pw.Wzorzec(wyj_bytes).tabs.items():
        uzywane.update(pw.Wzorzec(wyj_bytes).chain(t))
    b = bytearray(wyj_bytes)
    for i in range(1, len(b) // pw.PL):
        if i not in uzywane:
            b[i * pw.PL:(i + 1) * pw.PL] = bytes(pw.PL)
    open(wyj, "wb").write(bytes(b))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
