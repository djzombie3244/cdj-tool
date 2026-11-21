"""Kopia pendrive'a na drugi pendrive z weryfikacja (sumy SHA-256 po zapisie, odczyt z dysku bez cache macOS).

Dwa identyczne pendrive'y to podstawowa ochrona przed awaria nosnika przed wystepem. Kolejnosc: najpierw muzyka i
pliki analizy, a PIONEER/rekordbox/export.pdb na samym koncu, zeby przerwana kopia nie wygladala na kompletna."""
import hashlib
import os
import shutil
import sys
import time

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

POMIJANE = {".Spotlight-V100", ".fseventsd", ".Trashes", ".DS_Store", ".metadata_never_index", ".TemporaryItems",
            "System Volume Information", "$RECYCLE.BIN", ".VolumeIcon.icns", ".DocumentRevisions-V100"}
FRAGMENT = 4 * 1024 * 1024
PDB = os.path.join("PIONEER", "rekordbox", "export.pdb")


def _pomin(nazwa):
    return nazwa in POMIJANE or nazwa.startswith("._")


def lista_plikow(korzen):
    """[(sciezka wzgledna, rozmiar)] bez plikow systemowych; export.pdb zawsze ostatni."""
    wyn = []
    for dir_, dirs, pliki in os.walk(korzen):
        dirs[:] = sorted(d for d in dirs if not _pomin(d))
        for p in sorted(pliki):
            if _pomin(p):
                continue
            pelna = os.path.join(dir_, p)
            if os.path.islink(pelna):
                continue
            wyn.append((os.path.relpath(pelna, korzen), os.path.getsize(pelna)))
    wyn.sort(key=lambda x: (x[0] == PDB, x[0]))
    return wyn


def cel_pusty(cel):
    return not any(not _pomin(n) for n in os.listdir(cel))


def _odczyt(sciezka, bez_cache):
    """Generator fragmentow pliku; bez_cache = odczyt z nosnika (macOS: F_NOCACHE)."""
    fd = os.open(sciezka, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        if bez_cache and fcntl and sys.platform == "darwin":
            try:
                fcntl.fcntl(fd, getattr(fcntl, "F_NOCACHE", 48), 1)
            except OSError:
                pass
        while True:
            dane = os.read(fd, FRAGMENT)
            if not dane:
                return
            yield dane
    finally:
        os.close(fd)


def sprawdz(zrodlo, cel):
    """Rzuca ValueError z komunikatem po polsku, gdy kopia nie ma sensu."""
    if not os.path.isdir(zrodlo) or not os.path.isdir(cel):
        raise ValueError("Wybierz oba pendrive'y.")
    if os.path.realpath(zrodlo) == os.path.realpath(cel) or os.path.samefile(zrodlo, cel):
        raise ValueError("Źródło i cel to ten sam pendrive.")
    if not os.path.isfile(os.path.join(zrodlo, PDB)):
        raise ValueError("Na pendrive'ie źródłowym nie ma biblioteki (PIONEER/rekordbox/export.pdb).")
    razem = sum(r for _, r in lista_plikow(zrodlo))
    if shutil.disk_usage(cel).total < razem * 1.02 + 8 * 1024 * 1024:
        raise ValueError("Pendrive docelowy jest za mały: potrzeba %d MB, ma %d MB."
                         % (razem // 1e6 + 1, shutil.disk_usage(cel).total // 1e6))


def wyczysc(cel):
    """Kasuje cala zawartosc celu (poza plikami systemowymi)."""
    for n in os.listdir(cel):
        if _pomin(n):
            continue
        p = os.path.join(cel, n)
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p)
        else:
            os.remove(p)


def kopiuj(zrodlo, cel, postep=None):
    """Kopiuje i weryfikuje. postep(faza, zrobione_B, razem_B, plik) - faza 'kopiowanie' albo 'weryfikacja'.
    Zwraca {'ok', 'plikow', 'bajtow', 'czas_s', 'bledy': [..]}; cel musi byc pusty (wyczysc() wywoluje wolajacy)."""
    t0 = time.time()
    pliki = lista_plikow(zrodlo)
    razem = sum(r for _, r in pliki)
    sumy, bledy, zrobione = {}, [], 0

    def zglos(faza, z, plik=""):
        if postep:
            postep(faza, z, razem, plik)

    for rel, rozmiar in pliki:
        src, dst = os.path.join(zrodlo, rel), os.path.join(cel, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        h = hashlib.sha256()
        with open(dst, "wb") as out:
            for dane in _odczyt(src, False):
                h.update(dane)
                out.write(dane)
                zrobione += len(dane)
                zglos("kopiowanie", zrobione, rel)
            out.flush()
            os.fsync(out.fileno())
        sumy[rel] = h.hexdigest()
        if os.path.getsize(dst) != rozmiar:
            bledy.append(rel)
    zrobione = 0
    for rel, rozmiar in pliki:
        if rel in bledy:
            continue
        h = hashlib.sha256()
        for dane in _odczyt(os.path.join(cel, rel), True):
            h.update(dane)
            zrobione += len(dane)
            zglos("weryfikacja", zrobione, rel)
        if h.hexdigest() != sumy[rel]:
            bledy.append(rel)
    return {"ok": not bledy, "plikow": len(pliki), "bajtow": razem, "czas_s": round(time.time() - t0, 1), "bledy": bledy}
