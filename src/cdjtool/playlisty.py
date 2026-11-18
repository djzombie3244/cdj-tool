"""Inteligentne playlisty i podobne utwory (wersja 1.1).

Reguly dzialaja na danych z analizy, bez nowej analizy: BPM (zakres), tonacja w kole Camelot
(dokladna albo sasiednie: +-1 i relatywna), gatunek, artysta, komentarz (z tagow pliku).
Wynik to zwykla playlista w export.pdb, wiec CDJ-2000 nie potrzebuje zadnych zmian.
"""
import tonacja


def pozycja(u):
    """Pozycja Camelot utworu (numer 1..12, 0 = A / 1 = B) albo None, gdy brak tonacji."""
    t = u.get("tonacja")
    return tonacja.pozycja_tonacji(t[0], t[1]) if t else None


def sasiedzi(poz):
    """Tonacje zgodne miksowo w kole Camelot: ta sama, +-1 (ta sama litera) i relatywna (ta sama liczba, druga litera)."""
    n, ab = poz
    return {(n, ab), ((n - 2) % 12 + 1, ab), (n % 12 + 1, ab), (n, 1 - ab)}


def _tekst_pasuje(wzorzec, tekst):
    return not wzorzec or wzorzec.lower() in (tekst or "").lower()


def pasuje(u, regula):
    """Czy utwor spelnia regule. Pusta wartosc pola = bez ograniczenia."""
    bpm = u.get("bpm") or 0.0
    if regula.get("bpm_min") is not None and bpm < regula["bpm_min"]:
        return False
    if regula.get("bpm_max") is not None and bpm > regula["bpm_max"]:
        return False
    cel = regula.get("tonacja")  # (numer, 0|1) w kole Camelot
    if cel:
        poz = pozycja(u)
        if poz is None:
            return False
        if regula.get("sasiednie"):
            if poz not in sasiedzi(cel):
                return False
        elif poz != tuple(cel):
            return False
    meta = u.get("meta") or {}
    return (_tekst_pasuje(regula.get("gatunek"), meta.get("gatunek"))
            and _tekst_pasuje(regula.get("artysta"), meta.get("artysta"))
            and _tekst_pasuje(regula.get("komentarz"), meta.get("komentarz")))


def podobne(seed, u, procent=3.0):
    """Czy utwor u pasuje do utworu wzorcowego seed: BPM w +-procent i tonacja sasiednia (Camelot)."""
    if u is seed:
        return False
    bpm, bpm0 = u.get("bpm") or 0.0, seed.get("bpm") or 0.0
    if not bpm or not bpm0 or abs(bpm - bpm0) > bpm0 * procent / 100.0:
        return False
    poz0 = pozycja(seed)
    if poz0 is None:
        return False
    poz = pozycja(u)
    return poz is not None and poz in sasiedzi(poz0)


def filtr_kolekcji(utwory, filtr):
    """Zostawia utwory spelniajace filtr kolekcji przed zapisem (pusty filtr = wszystko)."""
    if not filtr:
        return utwory
    wynik = []
    for u in utwory:
        meta = u.get("meta") or {}
        if filtr.get("data_od") and (meta.get("data_pliku") or 0) < filtr["data_od"]:
            continue
        if filtr.get("data_do") and (meta.get("data_pliku") or 0) > filtr["data_do"]:
            continue
        if filtr.get("bpm_min") is not None and (u.get("bpm") or 0.0) < filtr["bpm_min"]:
            continue
        if filtr.get("bpm_max") is not None and (u.get("bpm") or 0.0) > filtr["bpm_max"]:
            continue
        if not _tekst_pasuje(filtr.get("gatunek"), meta.get("gatunek")):
            continue
        wynik.append(u)
    return wynik
