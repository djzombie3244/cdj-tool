"""Automatyczne HOT CUE i Memory Cue na podstawie struktury utworu (fraze po 8 taktow).

Wejscie: cechy z gen_dat._cechy_koloru (okna 1/150 s w osi Rekordboxa: rms nisko/srednio/wysoko, szczyt)
oraz siatka beatow (czasy w s w osi Rekordboxa + numery beatow w takcie 1..4).
Zasady (wg Zehren i in., 2022): kandydaci tylko na pierwszym uderzeniu taktu, w granicach fraz 8-taktowych liczonych
od poczatku glownego beatu; zmiana struktury = wejscie lub zniknieciu kicka oraz skokowa zmiana energii.
"""
import numpy as np

OKNA_NA_S = 150
FRAZA = 8          # taktow
MIN_ODSTEP = 4     # taktow miedzy dwoma cue


def _energie_taktow(cechy, poczatki_s, koniec_s):
    nisko = np.zeros(len(poczatki_s))
    calosc = np.zeros(len(poczatki_s))
    rms_all = np.sqrt(cechy[:, 0] ** 2 + cechy[:, 1] ** 2 + cechy[:, 2] ** 2)
    for k, t0 in enumerate(poczatki_s):
        t1 = poczatki_s[k + 1] if k + 1 < len(poczatki_s) else koniec_s
        a, b = int(round(t0 * OKNA_NA_S)), int(round(t1 * OKNA_NA_S))
        a, b = max(0, min(a, len(cechy) - 1)), max(1, min(b, len(cechy)))
        if b <= a:
            b = a + 1
        nisko[k] = float(np.mean(cechy[a:b, 0]))
        calosc[k] = float(np.mean(rms_all[a:b]))
    return nisko, calosc


def wyznacz(cechy, czasy_s, numery, dlugosc_s):
    """Zwraca {'pamiec': [(ms, nazwa)], 'hot': [(ms, nazwa)]} (czasy w osi Rekordboxa, ms)."""
    czasy_s = np.asarray(czasy_s, dtype=float)
    numery = np.asarray(numery)
    if len(czasy_s) == 0:
        return {"pamiec": [], "hot": []}
    pierwszy_ms = int(round(czasy_s[0] * 1000))
    poczatki = czasy_s[numery == 1]
    wyn = {"pamiec": [(pierwszy_ms, "Start")], "hot": [(pierwszy_ms, "Start")]}
    nb = len(poczatki)
    if nb < 16 or cechy is None or len(cechy) < 10:
        return wyn
    nisko, calosc = _energie_taktow(cechy, poczatki, len(cechy) / OKNA_NA_S)
    ref_n, ref_c = float(np.percentile(nisko, 75)), float(np.percentile(calosc, 75))
    if ref_n <= 1e-6 or ref_c <= 1e-6:
        return wyn
    # poczatek glownego beatu: kick (pasmo <200 Hz) trzyma sie >= 0.4 P75 przez >= 90% z nastepnych 8 taktow
    G = 0
    for k in range(0, nb - FRAZA + 1):
        if np.mean(nisko[k:k + FRAZA] >= 0.4 * ref_n) >= 0.9:
            G = k
            break
    frazy = list(range(G, nb - FRAZA + 1, FRAZA))
    if len(frazy) < 3:
        if G > 0:
            wyn["hot"].append((int(round(poczatki[G] * 1000)), "Groove"))
        return wyn
    pn = np.array([nisko[j:j + FRAZA].mean() for j in frazy])
    pc = np.array([calosc[j:j + FRAZA].mean() for j in frazy])
    kick = pn >= 0.45 * ref_n
    zdarzenia = []  # (indeks frazy, typ)
    for i in range(1, len(frazy)):
        if kick[i] and not kick[i - 1]:
            zdarzenia.append((i, "Drop"))
        elif (not kick[i]) and kick[i - 1]:
            zdarzenia.append((i, "Break"))
        elif kick[i] and kick[i - 1] and pc[i] >= 1.3 * pc[i - 1] and pc[i] >= 0.9 * ref_c:
            zdarzenia.append((i, "Lift"))
    # outro: ostatnia fraza po ktorej energia zostaje niska do konca
    outro = None
    for i in range(len(frazy) - 1, 0, -1):
        if pc[i] < 0.6 * ref_c or pn[i] < 0.5 * ref_n:
            outro = i
        else:
            break
    if outro is not None and outro >= len(frazy) - 1 and len(frazy) > 4:
        outro = None  # pojedyncza koncowa fraza to jeszcze nie outro
    if outro is not None:
        zdarzenia.append((outro, "Outro"))
    # wybierz: zachowaj minimalny odstep, sortuj po czasie
    zdarzenia.sort()
    wybrane, ostatni = [], -99
    for i, typ in zdarzenia:
        if frazy[i] - ostatni >= MIN_ODSTEP:
            wybrane.append((i, typ))
            ostatni = frazy[i]
    def ms(i):
        return int(round(poczatki[frazy[i]] * 1000))
    start_g = int(round(poczatki[G] * 1000))
    nazwa_a = "Groove" if G > 0 else "Start"
    pam = [(pierwszy_ms, "Start")]
    if abs(start_g - pierwszy_ms) > 0:
        pam.append((start_g, "Groove"))
    for i, typ in wybrane[:5]:
        pam.append((ms(i), typ))
    # wejscie miksu: 16 taktow przed pierwszym dropem (zasada DJ: wchodzisz tak, by drop trafil w drop)
    for i, typ in wybrane:
        if typ in ("Drop", "Lift"):
            m = frazy[i] - 16
            if m > G and all(abs(m - frazy[j]) >= MIN_ODSTEP for j, _ in wybrane):
                pam.append((int(round(poczatki[m] * 1000)), "Mix in"))
            break
    # hot: A = poczatek beatu, B = pierwszy drop (lub lift), C = pierwszy break, D = outro albo ostatni drop
    # A = zawsze pierwszy beat siatki (glowny punkt CUE: CDJ ustawia sie tam po zaladowaniu, jak w Rekordboksie);
    # B = poczatek groove'u (wejscie miksu), dalej drop, break, outro
    hot = [(pierwszy_ms, "Start")]
    if start_g != pierwszy_ms:
        hot.append((start_g, "Groove"))
    drops = [(i, t) for i, t in wybrane if t in ("Drop", "Lift")]
    brks = [(i, t) for i, t in wybrane if t == "Break"]
    outros = [(i, t) for i, t in wybrane if t == "Outro"]
    if drops:
        hot.append((ms(drops[0][0]), "Drop"))
    if brks:
        hot.append((ms(brks[0][0]), "Break"))
    if outros:
        hot.append((ms(outros[0][0]), "Outro"))
    elif len(drops) > 1:
        hot.append((ms(drops[-1][0]), "Drop 2"))
    # bez duplikatow czasu
    def uniq(lst):
        wyj, widziane = [], set()
        for t, n in lst:
            if t not in widziane:
                widziane.add(t)
                wyj.append((t, n))
        return wyj
    hot = [hot[0]] + sorted(hot[1:])  # A = pierwszy beat, dalsze po kolei w czasie
    wyn["pamiec"] = uniq(sorted(pam))[:8]  # starsze CDJ ladujq ograniczona liczbe; zostaje <= 8
    wyn["hot"] = uniq(hot)[:8]
    return wyn
