"""Edytor siatki beatow i punktow cue dla utworow zapisanych juz na pendrive.

Czyta i zapisuje pliki analizy (ANLZ0000.DAT/.EXT) oraz tempo w export.pdb bez numpy (dziala w procesie okna).
Budowa tagow PQTZ, PQT2, PCOB i PCO2 jest tu powtorzona 1:1 z gen_dat (test pilnuje, ze bajty sa identyczne):
zmiana gen_dat kasowalaby zapamietane analizy calej biblioteki.

Poprawki zapisuje tez na komputerze (korekty.json): przy kolejnym zapisie pendrive'a ten sam plik dostaje
poprawiona siatke i cue zamiast automatycznych.
"""
import base64
import json
import math
import os
import struct
import sys
import threading
import unicodedata

import pdb_writer as pw

OFFSET_DAT = 0x1C  # dlugosc naglowka PMAI w naszych plikach i w rekordboxie
ROZSZERZENIA = (".mp3", ".m4a", ".aac", ".wav", ".aif", ".aiff", ".flac")
_LOCK = threading.Lock()


# ------------------------------------------------------------------ tagi --
def tagi_anlz(dane):
    o = struct.unpack(">I", dane[4:8])[0]
    while o + 12 <= len(dane):
        dl = struct.unpack(">I", dane[o + 8:o + 12])[0]
        if dl < 12 or o + dl > len(dane):
            break
        yield o, dane[o:o + 4], dl
        o += dl


def _ms_calk(t_s):
    return int(math.floor(t_s * 1000.0 + 1e-6))


def tag_pqtz(czasy_s, numery, tempa):
    body = b"".join(struct.pack(">HHI", int(nr), int(round(b * 100)), _ms_calk(t))
                    for t, nr, b in zip(czasy_s, numery, tempa))
    n = len(czasy_s)
    return b"PQTZ" + struct.pack(">III", 24, 24 + len(body), 0) + struct.pack(">II", 0x80000, n) + body


def tag_pqt2(czasy_s, numery, tempa):
    n = len(czasy_s)
    nr = [int(x) for x in numery]
    b100 = [int(round(b * 100)) for b in tempa]
    ms = [_ms_calk(t) for t in czasy_s]
    ulamki = [min(999, max(0, int(round((t * 1000.0 - m) * 1000.0)))) for t, m in zip(czasy_s, ms)]
    suma = sum(nr[i] + b100[i] + ms[i] for i in range(n)) & 0xFFFFFFFF
    body = b"".join(struct.pack(">H", u) for u in ulamki)
    hdr = struct.pack(">11I", 0, 0x02000002, 0,
                      (nr[0] << 16) | b100[0], ms[0], (nr[-1] << 16) | b100[-1], ms[-1], n, suma, 0, 0)
    return b"PQT2" + struct.pack(">II", 56, 56 + len(body)) + hdr + body


def _pcpt(k, n, hot, ms):
    return (b"PCPT" + struct.pack(">III", 0x1C, 0x38, hot) + struct.pack(">II", 0, 0x00010000)
            + struct.pack(">HH", 0xFFFF if k == 0 else k - 1, 0xFFFF if k == n - 1 else k + 1)
            + b"\x01\x00\x03\xe8" + struct.pack(">II", int(ms), 0xFFFFFFFF) + b"\x00" * 16)


def tag_pcob(tryb, wpisy):
    n = len(wpisy)
    body = b"".join(_pcpt(k, n, hot, ms) for k, (hot, ms) in enumerate(wpisy))
    return (b"PCOB" + struct.pack(">III", 0x18, 0x18 + len(body), tryb) + struct.pack(">HH", 0, n)
            + struct.pack(">I", 0xFFFFFFFF) + body)


def _pcp2(hot, ms, nazwa):
    kom = (nazwa + "\x00").encode("utf-16-be") if nazwa else b""
    wnetrze = (b"\x01\x00\x03\xe8" + struct.pack(">II", int(ms), 0xFFFFFFFF) + b"\x00" + b"\x01" + b"\x00" * 6
               + struct.pack(">HH", 0, 0) + struct.pack(">I", len(kom)) + kom + b"\x00" * 44)
    return b"PCP2" + struct.pack(">II", 0x10, 0x10 + len(wnetrze)) + struct.pack(">I", hot) + wnetrze


def tag_pco2(tryb, wpisy):
    body = b"".join(_pcp2(hot, ms, nazwa) for hot, ms, nazwa in wpisy)
    return b"PCO2" + struct.pack(">III", 0x14, 0x14 + len(body), tryb) + struct.pack(">HH", len(wpisy), 0) + body


# --------------------------------------------------------------- odczyt --
def czytaj_siatke(dat, ext=None):
    """[(czas_s, numer, bpm)] z PQTZ; ulamki ms z PQT2 pliku EXT, gdy sa."""
    beaty = []
    for o, t, dl in tagi_anlz(dat):
        if t == b"PQTZ":
            n = struct.unpack(">I", dat[o + 20:o + 24])[0]
            for i in range(n):
                nr, b100, ms = struct.unpack(">HHI", dat[o + 24 + 8 * i:o + 32 + 8 * i])
                beaty.append([ms / 1000.0, nr, b100 / 100.0])
    if ext:
        for o, t, dl in tagi_anlz(ext):
            if t == b"PQT2":
                n = struct.unpack(">I", ext[o + 12 + 28:o + 12 + 32])[0]
                if n == len(beaty):
                    for i in range(n):
                        beaty[i][0] += struct.unpack(">H", ext[o + 56 + 2 * i:o + 58 + 2 * i])[0] / 1e6
    return beaty


def czytaj_cue(dane):
    """(hot [(numer, ms)], pamiec [ms], nazwy {(tryb, numer, ms): nazwa})."""
    hot, pam, nazwy = [], [], {}
    for o, t, dl in tagi_anlz(dane):
        if t == b"PCOB":
            tryb = struct.unpack(">I", dane[o + 12:o + 16])[0]
            p = o + struct.unpack(">I", dane[o + 4:o + 8])[0]
            while p + 36 <= o + dl and dane[p:p + 4] == b"PCPT":
                nr = struct.unpack(">I", dane[p + 12:p + 16])[0]
                ms = struct.unpack(">I", dane[p + 32:p + 36])[0]
                (hot.append((nr, ms)) if tryb == 1 and nr > 0 else pam.append(ms))
                p += struct.unpack(">I", dane[p + 8:p + 12])[0]
        elif t == b"PCO2":
            tryb = struct.unpack(">I", dane[o + 12:o + 16])[0]
            p = o + 0x14
            while p + 16 <= o + dl and dane[p:p + 4] == b"PCP2":
                dlw = struct.unpack(">I", dane[p + 8:p + 12])[0]
                nr = struct.unpack(">I", dane[p + 12:p + 16])[0]
                ms = struct.unpack(">I", dane[p + 20:p + 24])[0]
                k = struct.unpack(">I", dane[p + 0x28:p + 0x2C])[0]
                if k:
                    nazwy[(tryb, nr, ms)] = dane[p + 0x2C:p + 0x2C + k].decode("utf-16-be", "replace").rstrip("\x00")
                p += dlw
    return sorted(hot), sorted(set(pam)), nazwy


def waveform(ext):
    """Kolorowy waveform PWV5 (150 kolumn/s, 2 bajty) jako base64 albo niebieski PWV3 (1 bajt)."""
    for o, t, dl in tagi_anlz(ext):
        if t == b"PWV5":
            return {"typ": "pwv5", "dane": base64.b64encode(ext[o + 24:o + dl]).decode()}
    for o, t, dl in tagi_anlz(ext):
        if t == b"PWV3":
            return {"typ": "pwv3", "dane": base64.b64encode(ext[o + 24:o + dl]).decode()}
    return {"typ": "brak", "dane": ""}


# ------------------------------------------------------------- zapis --
def _podmien(dane, nowe, cue_tagi):
    """Plik ANLZ z podmienionymi tagami: nowe = {nazwa: bajty}; wszystkie PCOB/PCO2 zastepuje cue_tagi
    (w miejscu pierwszego z nich). Naglowek PMAI z nowa dlugoscia."""
    start = struct.unpack(">I", dane[4:8])[0]
    czesci, cue_wstawione = [], False
    for o, t, dl in tagi_anlz(dane):
        if t in (b"PCOB", b"PCO2"):
            if not cue_wstawione:
                czesci.append(b"".join(cue_tagi))
                cue_wstawione = True
            continue
        czesci.append(nowe.get(t, dane[o:o + dl]))
    calosc = dane[:start] + b"".join(czesci)
    return calosc[:8] + struct.pack(">I", len(calosc)) + calosc[12:]


def zbuduj_pliki(dat, ext, edycja):
    """(dat, ext) z siatka i cue z edycji: {"beaty": [[czas_s, numer, bpm]], "hot": [[numer, ms]],
    "pamiec": [ms]}. Nazwy cue zostaja, gdy punkt sie nie przesunal."""
    beaty = sorted(edycja["beaty"], key=lambda b: b[0])
    if len(beaty) < 2:
        raise ValueError("Siatka musi mieć co najmniej dwa beaty.")
    czasy = [max(0.0, float(b[0])) for b in beaty]
    numery = [int(b[1]) if 1 <= int(b[1]) <= 4 else 1 for b in beaty]
    tempa = [min(655.0, max(1.0, float(b[2]))) for b in beaty]
    hot = sorted({int(n): int(ms) for n, ms in edycja.get("hot", []) if 1 <= int(n) <= 8 and int(ms) >= 0}.items())
    pam = sorted({int(ms) for ms in edycja.get("pamiec", []) if int(ms) >= 0})[:10]
    _, _, nazwy = czytaj_cue(ext)
    hot_n = [(n, ms, nazwy.get((1, n, ms), "")) for n, ms in hot]
    pam_n = [(0, ms, nazwy.get((0, 0, ms), "")) for ms in pam]
    cue_dat = [tag_pcob(1, [(n, ms) for n, ms, _ in hot_n[:3]]), tag_pcob(0, [(0, ms) for _, ms, _ in pam_n])]
    cue_ext = [tag_pcob(1, [(n, ms) for n, ms, _ in hot_n]), tag_pcob(0, [(0, ms) for _, ms, _ in pam_n]),
               tag_pco2(1, hot_n), tag_pco2(0, pam_n)]
    nowy_dat = _podmien(dat, {b"PQTZ": tag_pqtz(czasy, numery, tempa)}, cue_dat)
    nowy_ext = _podmien(ext, {b"PQT2": tag_pqt2(czasy, numery, tempa)}, cue_ext)
    return nowy_dat, nowy_ext


def bpm_utworu(edycja):
    """Tempo do bazy: najczestsze tempo beatow (siatka dynamiczna: tempo, ktore trwa najdluzej)."""
    licz = {}
    for b in edycja["beaty"]:
        licz[round(float(b[2]), 2)] = licz.get(round(float(b[2]), 2), 0) + 1
    return max(licz.items(), key=lambda x: x[1])[0]


def ustaw_tempo_w_bazie(pdb, track_id, bpm):
    """export.pdb z tempem utworu o danym id (pole tempo wiersza utworu, w setnych BPM)."""
    w = pw.Wzorzec(pdb)
    dane = bytearray(pdb)
    for pg in w.chain(pw.TYPE_TRACKS)[1:]:
        strona = w.page(pg)
        if strona[27] not in (0x24, 0x34):
            continue
        _, offs = pw.czytaj_wiersze(strona)
        for o in offs:
            r = pg * pw.PL + pw.HDR + o
            if struct.unpack("<I", dane[r + 72:r + 76])[0] == track_id:
                struct.pack_into("<I", dane, r + 56, int(round(bpm * 100)))
                return bytes(dane)
    raise ValueError("Nie znaleziono utworu %s w bazie." % track_id)


# --------------------------------------------------- korekty na komputerze --
def plik_korekt():
    if sys.platform == "win32":
        baza = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "CDJ-Tool")
    else:
        baza = os.path.expanduser("~/Library/Application Support/CDJ-Tool")
    return os.environ.get("CDJ_KOREKTY") or os.path.join(baza, "korekty.json")


def klucz_korekty(sciezka_na_pendrive, rozmiar):
    return "%s|%d" % (unicodedata.normalize("NFC", sciezka_na_pendrive), int(rozmiar))


def wczytaj_korekty():
    try:
        with open(plik_korekt(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def zapisz_korekte(klucz, edycja):
    with _LOCK:
        k = wczytaj_korekty()
        if edycja is None:
            k.pop(klucz, None)
        else:
            k[klucz] = {"beaty": [[round(float(b[0]), 6), int(b[1]), round(float(b[2]), 2)] for b in edycja["beaty"]],
                        "hot": [[int(n), int(ms)] for n, ms in edycja.get("hot", [])],
                        "pamiec": [int(ms) for ms in edycja.get("pamiec", [])]}
        p = plik_korekt()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(k, fh)
        os.replace(tmp, p)


# ------------------------------------------------------------ pendrive --
def _baza(root):
    p = os.path.join(root, "PIONEER", "rekordbox", "export.pdb")
    with open(p, "rb") as fh:
        return p, fh.read()


def lista(root):
    _, pdb = _baza(root)
    lib = pw.czytaj_biblioteke(pdb)
    art = dict(lib["artysci"])
    return [{"id": f["id"], "tytul": s["title"], "artysta": art.get(f["artist"], ""), "bpm": f["tempo"] / 100.0,
             "czas": f["duration"], "sciezka": s["file_path"]}
            for f, s in sorted(lib["utwory"], key=lambda x: (art.get(x[0]["artist"], "").lower(), x[1]["title"].lower()))]


def _utwor(root, track_id):
    _, pdb = _baza(root)
    for f, s in pw.czytaj_biblioteke(pdb)["utwory"]:
        if f["id"] == int(track_id):
            return f, s
    raise ValueError("Nie ma takiego utworu na pendrive.")


def _pliki_analizy(root, s):
    dat_p = os.path.join(root, s["analyze_path"].lstrip("/"))
    ext_p = os.path.splitext(dat_p)[0] + ".EXT"
    with open(dat_p, "rb") as fh:
        dat = fh.read()
    with open(ext_p, "rb") as fh:
        ext = fh.read()
    return dat_p, ext_p, dat, ext


def utwor(root, track_id):
    f, s = _utwor(root, track_id)
    _, _, dat, ext = _pliki_analizy(root, s)
    hot, pam, _ = czytaj_cue(ext)
    beaty = czytaj_siatke(dat, ext)
    return {"id": f["id"], "tytul": s["title"], "sciezka": s["file_path"], "czas": f["duration"],
            "bpm": f["tempo"] / 100.0, "beaty": beaty, "hot": hot, "pamiec": pam,
            "dynamiczna": len({round(b[2], 2) for b in beaty}) > 1, "waveform": waveform(ext),
            "korekta": klucz_korekty(s["file_path"], f["file_size"]) in wczytaj_korekty()}


def zapisz(root, track_id, edycja):
    """Zapis poprawionej siatki i cue na pendrive (DAT, EXT, tempo w bazie) i w korektach na komputerze."""
    f, s = _utwor(root, track_id)
    dat_p, ext_p, dat, ext = _pliki_analizy(root, s)
    nowy_dat, nowy_ext = zbuduj_pliki(dat, ext, edycja)
    bpm = bpm_utworu(edycja)
    pdb_p, pdb = _baza(root)
    nowa_baza = ustaw_tempo_w_bazie(pdb, f["id"], bpm)
    for sciezka, bajty in ((dat_p, nowy_dat), (ext_p, nowy_ext), (pdb_p, nowa_baza)):
        tmp = sciezka + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(bajty)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, sciezka)
    zapisz_korekte(klucz_korekty(s["file_path"], f["file_size"]), edycja)
    return {"ok": True, "bpm": bpm}


def plik_audio(root, sciezka_na_pendrive):
    """Pelna sciezka pliku audio na pendrive (tylko pliki audio wewnatrz wybranego pendrive'a)."""
    root = os.path.realpath(root)
    p = os.path.realpath(os.path.join(root, sciezka_na_pendrive.lstrip("/")))
    if os.path.commonpath([root, p]) != root or not p.lower().endswith(ROZSZERZENIA) or not os.path.isfile(p):
        raise ValueError("Nie ma takiego pliku na pendrive.")
    return p
