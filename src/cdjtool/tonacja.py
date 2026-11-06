"""Wykrywanie tonacji utworu (wersja testowa).

Metoda: profil klas wysokosci (chroma) z widma, potem korelacja z profilami tonacji
durowych i molowych (Krumhansl, w wariancie Sha'atha z programu KeyFinder), 24 kandydatow.
Bez nowych zaleznosci: tylko numpy i scipy, ktore aplikacja juz ma.

Zwraca (klasa_wysokosci 0..11 od C, czy_molowa, pewnosc 0..1) albo None, gdy utwor
jest za krotki lub zbyt cichy, zeby cos powiedziec.
"""
import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import resample_poly

# Profile tonacji (Sha'ath 2011, KeyFinder): indeks 0 = tonika
PROFIL_DUR = np.array([6.6, 2.0, 3.5, 2.3, 4.6, 4.0, 2.5, 5.2, 2.4, 3.7, 2.3, 3.4])
PROFIL_MOL = np.array([6.5, 2.7, 3.5, 5.4, 2.6, 3.5, 2.5, 5.2, 4.0, 2.7, 4.3, 3.2])

# Nazwy jak w eksporcie rekordboxa (wzorzec ma "Abm" i "Fm")
DUR_KLASYCZNE = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
MOL_KLASYCZNE = ["Cm", "C#m", "Dm", "Ebm", "Em", "Fm", "F#m", "Gm", "Abm", "Am", "Bbm", "Bm"]

SR_ANALIZY = 11025
N_FFT = 4096          # 2,7 Hz na prazek przy 11025 Hz
HOP = 2048            # ok. 0,19 s
F_MIN, F_MAX = 100.0, 3500.0   # bez najnizszego basu (stopa) i bez gornych harmonicznych/szumu
OKNO_POLTONU = 2.0 / 3.0       # szerokosc wagi wokol srodka poltonu (w poltonach)
TLO_PRAZKI = 41                # szerokosc okna tla widma (ok. 110 Hz)
PROG_TLA = 3.0                 # szczyt musi byc tyle razy wyzszy niz tlo

_MACIERZ = {}


def _macierz_chroma(sr):
    """Macierz [prazek x 12]: ile kazdy prazek widma wnosi do kazdej klasy wysokosci."""
    if sr in _MACIERZ:
        return _MACIERZ[sr]
    f = np.fft.rfftfreq(N_FFT, 1.0 / sr)
    zakres = np.where((f >= F_MIN) & (f <= F_MAX))[0]
    midi = 69 + 12 * np.log2(f[zakres] / 440.0)
    m = np.zeros((len(zakres), 12))
    najblizszy = np.round(midi)
    d = midi - najblizszy
    waga = np.where(np.abs(d) < OKNO_POLTONU, np.cos(np.pi * d / (2 * OKNO_POLTONU)) ** 2, 0.0)
    m[np.arange(len(zakres)), (najblizszy.astype(int) % 12)] = waga
    _MACIERZ[sr] = (zakres, m)
    return _MACIERZ[sr]


def chroma(y, sr):
    """Usredniony profil 12 klas wysokosci (C, C#, ... B), znormalizowany do sumy 1."""
    if sr != SR_ANALIZY:
        g = np.gcd(int(sr), SR_ANALIZY)
        y = resample_poly(np.asarray(y, dtype=np.float32), SR_ANALIZY // g, int(sr) // g)
        sr = SR_ANALIZY
    y = np.asarray(y, dtype=np.float32)
    if len(y) < N_FFT * 8:
        return None
    zakres, m = _macierz_chroma(sr)
    okno = np.hanning(N_FFT).astype(np.float32)
    ramki = np.lib.stride_tricks.sliding_window_view(y, N_FFT)[::HOP]
    suma = np.zeros(12)
    energie = []
    profile = []
    for i in range(0, len(ramki), 256):
        blok = ramki[i:i + 256] * okno
        widmo = np.abs(np.fft.rfft(blok, axis=1))[:, zakres]
        energie.append(widmo.sum(axis=1))
        # tylko tony: lokalne maksima wyraznie ponad tlem widma (szum perkusji daje mnostwo
        # drobnych szczytow, ktore inaczej zaglusza harmonie)
        tlo = uniform_filter1d(widmo, size=TLO_PRAZKI, axis=1, mode="nearest")
        szczyt = np.zeros_like(widmo)
        srodek = (widmo[:, 1:-1] > widmo[:, :-2]) & (widmo[:, 1:-1] >= widmo[:, 2:]) \
            & (widmo[:, 1:-1] > PROG_TLA * tlo[:, 1:-1])
        szczyt[:, 1:-1] = np.where(srodek, np.sqrt(np.maximum(widmo[:, 1:-1] - tlo[:, 1:-1], 0.0)), 0.0)
        profile.append(szczyt @ m)
    energie = np.concatenate(energie)
    profile = np.vstack(profile)
    # ciche ramki (przerwy, wejscia, wyciszenia) nic nie mowia o tonacji
    prog = np.percentile(energie, 25)
    glosne = energie > prog
    if glosne.sum() < 8:
        return None
    p = profile[glosne]
    p = p / (p.max(axis=1, keepdims=True) + 1e-9)   # kazda ramka ma ten sam glos
    suma = p.sum(axis=0)
    if suma.sum() <= 0:
        return None
    return suma / suma.sum()


def _korelacja(a, b):
    a = a - a.mean()
    b = b - b.mean()
    return float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-12))


def tonacja_z_chroma(c):
    """(tonika 0..11, czy_molowa, pewnosc) dla profilu chroma."""
    wyniki = []
    for t in range(12):
        wyniki.append((_korelacja(c, np.roll(PROFIL_DUR, t)), t, False))
        wyniki.append((_korelacja(c, np.roll(PROFIL_MOL, t)), t, True))
    wyniki.sort(reverse=True)
    best, drugi = wyniki[0], wyniki[1]
    pewnosc = max(0.0, min(1.0, (best[0] - drugi[0]) * 5 + 0.5 * max(0.0, best[0])))
    return best[1], best[2], round(pewnosc, 3)


def wykryj(y, sr):
    c = chroma(y, sr)
    if c is None:
        return None
    return tonacja_z_chroma(c)


def nazwa(tonika, molowa, notacja="klasyczna"):
    """Nazwa tonacji: klasyczna (np. Am, F#, Bbm) albo Camelot (np. 8A, 2B)."""
    if notacja == "camelot":
        dur = (tonika + 3) % 12 if molowa else tonika      # molowa -> rownolegla durowa
        numer = (7 * dur % 12 + 7) % 12 + 1                # C = 8, G = 9, F = 7 ...
        return "%d%s" % (numer, "A" if molowa else "B")
    return (MOL_KLASYCZNE if molowa else DUR_KLASYCZNE)[tonika % 12]


# ---------------------------------------------------------------- tonacja z tagu pliku
_LITERY = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11, "H": 11}


def z_tagu(tekst):
    """Tonacja zapisana w tagu (TKEY, initialkey): klasyczna (Am, F#m, Ebm, A minor, Dbmaj),
    Camelot (8A, 12B) albo Open Key (1m, 6d). Zwraca (tonika, czy_molowa) albo None."""
    if not tekst:
        return None
    s = str(tekst).strip().replace("♯", "#").replace("♭", "b")
    import re
    m = re.fullmatch(r"(\d{1,2})\s*([ABab])", s)          # Camelot
    if m and 1 <= int(m.group(1)) <= 12:
        n, molowa = int(m.group(1)), m.group(2).upper() == "A"
        dur = ((n - 8) * 7) % 12                             # 8B = C, 9B = G, 7B = F
        return ((dur - 3) % 12 if molowa else dur), molowa
    m = re.fullmatch(r"(\d{1,2})\s*([dmDM])", s)          # Open Key: 1d = C, 1m = Am
    if m and 1 <= int(m.group(1)) <= 12:
        n, molowa = int(m.group(1)), m.group(2).lower() == "m"
        dur = ((n - 1) * 7) % 12
        return ((dur - 3) % 12 if molowa else dur), molowa
    m = re.fullmatch(r"([A-Ha-h])\s*(#|b|is|es|s)?\s*(m|min|minor|moll|-)?\s*(maj|major|dur)?", s)
    if not m or (m.group(3) and m.group(4)):
        return None
    t = _LITERY[m.group(1).upper()]
    if m.group(2) in ("#", "is"):
        t += 1
    elif m.group(2) in ("b", "es", "s"):
        t -= 1
    return t % 12, bool(m.group(3))
