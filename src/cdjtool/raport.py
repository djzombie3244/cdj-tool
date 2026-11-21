"""Raport "przed graniem": kontrola biblioteki zapisanej na pendrive (export.pdb, pliki analizy, pliki audio).

Nie zmienia niczego na pendrive. Zwraca {'ok', 'utworow', 'problemy': [{'poziom': 'blad'|'uwaga', 'utwor', 'kod', 'szczegol'}]}.
Bledy = rzeczy, przez ktore utwor moze sie nie zaladowac albo zawiesic; uwagi = rzeczy do sprawdzenia."""
import os

import edytor
import pdb_writer as pw

PROBA = 256 * 1024
MAX_SCIEZKA = 250      # FAT32 / CDJ: dluzsze sciezki bywaja nieczytelne
BPM_MIN, BPM_MAX = 60.0, 200.0
TOLERANCJA_HOT_A_S = 0.03


def _probka(sciezka, rozmiar):
    """Czyta po kawalku z poczatku, srodka i konca pliku; wyjatek = nosnik nie oddaje danych."""
    with open(sciezka, "rb") as fh:
        for off in (0, max(0, rozmiar // 2 - PROBA // 2), max(0, rozmiar - PROBA)):
            fh.seek(off)
            fh.read(PROBA)


def _p(lista, poziom, utwor, kod, szczegol=""):
    lista.append({"poziom": poziom, "utwor": utwor, "kod": kod, "szczegol": szczegol})


def sprawdz(root, postep=None, czytaj_probki=True):
    problemy = []
    try:
        lib = pw.czytaj_biblioteke(edytor._baza(root)[1])
    except (OSError, ValueError, KeyError, IndexError) as e:
        _p(problemy, "blad", "", "baza", str(e)[:200])
        return {"ok": False, "utworow": 0, "problemy": problemy}
    utwory = lib["utwory"]
    sciezki = {}
    for n, (f, s) in enumerate(utwory):
        nazwa = s["title"] or s["file_path"]
        if postep:
            postep(n + 1, len(utwory), nazwa)
        sc = s["file_path"]
        if sc in sciezki:
            _p(problemy, "blad", nazwa, "duplikat", "ta sama sciezka co " + sciezki[sc])
        sciezki[sc] = nazwa
        if len(sc) > MAX_SCIEZKA:
            _p(problemy, "uwaga", nazwa, "sciezka", "%d znakow" % len(sc))
        audio = os.path.join(root, sc.lstrip("/"))
        if not os.path.isfile(audio):
            _p(problemy, "blad", nazwa, "brak_audio", sc)
        else:
            rozm = os.path.getsize(audio)
            if f.get("file_size") and rozm != f["file_size"]:
                _p(problemy, "blad", nazwa, "rozmiar", "w bazie %d B, na pendrive %d B" % (f["file_size"], rozm))
            if czytaj_probki:
                try:
                    _probka(audio, rozm)
                except OSError as e:
                    _p(problemy, "blad", nazwa, "odczyt", str(e)[:120])
        bpm = f["tempo"] / 100.0
        if not (BPM_MIN <= bpm <= BPM_MAX):
            _p(problemy, "uwaga", nazwa, "bpm", "%.2f" % bpm)
        try:
            dat_p, ext_p, dat, ext = edytor._pliki_analizy(root, s)
        except (OSError, KeyError) as e:
            _p(problemy, "blad", nazwa, "analiza", str(e)[:120])
            continue
        try:
            beaty = edytor.czytaj_siatke(dat, ext)
            hot, pam, _ = edytor.czytaj_cue(ext)
        except Exception as e:  # noqa: BLE001  # uszkodzony plik analizy
            _p(problemy, "blad", nazwa, "analiza_format", str(e)[:120])
            continue
        if not beaty:
            _p(problemy, "blad", nazwa, "brak_siatki")
            continue
        hot_a = [ms for nr, ms in hot if nr == 1]
        if not hot_a:
            _p(problemy, "uwaga", nazwa, "brak_hot_a")
        elif hot_a[0] / 1000.0 > beaty[0][0] + TOLERANCJA_HOT_A_S:
            # hot A moze lezec przed pierwszym beatem (start dzwieku, jak Auto Cue CDJ), ale nie po nim
            _p(problemy, "uwaga", nazwa, "hot_a_nie_na_beacie",
               "hot A %.3f s, pierwszy beat %.3f s" % (hot_a[0] / 1000.0, beaty[0][0]))
    smieci = 0
    for dir_, _, pliki in os.walk(root):
        smieci += sum(1 for p in pliki if p.startswith("._"))
    if smieci:
        _p(problemy, "uwaga", "", "apple_double", "%d plikow ._*" % smieci)
    return {"ok": not any(p["poziom"] == "blad" for p in problemy), "utworow": len(utwory), "problemy": problemy}
