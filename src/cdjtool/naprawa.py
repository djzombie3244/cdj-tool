"""Naprawa tytulow i artystow zapisywanych do bazy na pendrive (opcja, domyslnie wylaczona).

Zmienia tylko to, co trafia do export.pdb; pliki audio i ich tagi na dysku zostaja bez zmian.
Reguly: adresy www i dopiski z domena w nawiasach, numer utworu na poczatku tytulu, artysta z tytulu
"Artysta - Tytul" (gdy artysta pusty), wielkie litery zamieniane na tytul (np. "HEART OF GLASS" -> "Heart Of Glass").
Artysta NIE jest zmieniany pod wzgledem wielkosci liter (skroty typu VERO, ABBA sa celowe).
Zwraca (tytul, artysta, lista opisow zmian); pusta lista = nic nie zmieniono."""
import re
import unicodedata

ADRES = re.compile(r"(https?://\S+|www\.\S+)", re.I)
DOPISEK_DOMENY = re.compile(r"\s*[\[(][^\])]*\.(com|net|org|pl|ru|de|fm)\b[^\])]*[\])]", re.I)
NUMER = re.compile(r"^\s*\d{1,3}\s*[-.)]\s+")
PODZIAL = " - "
MAKS_DLUGOSC_ARTYSTY = 60


def _spacje(s):
    return re.sub(r"\s+", " ", s).strip()


def _duze_litery(s):
    litery = [c for c in s if c.isalpha()]
    return len(litery) >= 4 and all(c.isupper() for c in litery)


def _popraw_wielkosc(s):
    """WIELKIE LITERY -> Pierwsza Wielka, reszta mala; slowa z kropka/apostrofem zostawiamy jak sa."""
    return " ".join(w[:1].upper() + w[1:].lower() if w.isalpha() else w for w in s.split(" "))


def napraw(tytul, artysta):
    zmiany = []
    t0, a0 = tytul or "", artysta or ""
    t = unicodedata.normalize("NFC", t0)
    a = unicodedata.normalize("NFC", a0)

    def czysc(s):
        s2 = DOPISEK_DOMENY.sub("", ADRES.sub("", s))
        return _spacje(s2)

    t2, a2 = czysc(t), czysc(a)
    if t2 != t:
        zmiany.append("usunięto adres lub dopisek z tytułu")
    if a2 != a:
        zmiany.append("usunięto adres lub dopisek z artysty")
    t = t2
    a = a2

    m = NUMER.match(t)
    if m and len(t[m.end():].strip()) > 1:
        t = t[m.end():].strip()
        zmiany.append("usunięto numer z początku tytułu")

    if not a and PODZIAL in t:
        kand_a, reszta = t.split(PODZIAL, 1)
        kand_a, reszta = _spacje(kand_a), _spacje(reszta)
        if kand_a and reszta and len(kand_a) <= MAKS_DLUGOSC_ARTYSTY:
            a, t = kand_a, reszta
            zmiany.append("artysta wzięty z tytułu")

    if _duze_litery(t):
        t = _popraw_wielkosc(t)
        zmiany.append("tytuł: wielkie litery zamienione")

    if not t:  # nigdy nie zostawiamy pustego tytulu po naprawie
        return t0, a0, []
    return t, a, zmiany
