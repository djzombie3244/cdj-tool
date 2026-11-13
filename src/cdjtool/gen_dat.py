#!/usr/bin/env python3
"""Analiza MP3: BPM, beatgrid, waveform — WERSJA NAPRAWIONA."""

import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
from typing import Tuple, Dict, Any

import numpy as np
from scipy.signal import butter, filtfilt, sosfiltfilt

# Załaduj konfigurację
CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')
try:
    with open(CONFIG_PATH) as f:
        CONFIG = json.load(f)
except FileNotFoundError:
    CONFIG = {
        "audio": {"sample_rate": 22050, "hop_length": 64,
                  "rekordbox_offset_ms": 51.2, "beat_attack_offset_ms": 8.4},
        "analysis": {"bpm_min": 80, "bpm_max": 200, "confidence_threshold": 0.1},
        "performance": {"ffmpeg_timeout_sec": 300}
    }

SR = CONFIG["audio"]["sample_rate"]
HOP = CONFIG["audio"]["hop_length"]
FPS = SR / HOP
OFFSET_REKORDBOX_S = CONFIG["audio"]["rekordbox_offset_ms"] / 1000.0
ATAK_PRZED_WZROSTEM_S = CONFIG["audio"]["beat_attack_offset_ms"] / 1000.0
# Kalibracja na eksporcie rekordboxa (15 utworow, 2026-11-04): beaty sieci Beat This! leza srednio 12,2 ms
# za linia siatki rekordboxa; MP3 bez danych gapless sa w osi CDJ o 46,7 ms wczesniej niz dzwiek z ffmpeg.
SIEC_OPOZNIENIE_S = 0.0122
MP3_BEZ_GAPLESS_S = 0.0467
BPM_MIN = CONFIG["analysis"]["bpm_min"]
BPM_MAX = CONFIG["analysis"]["bpm_max"]
CONFIDENCE_THRESHOLD = CONFIG["analysis"]["confidence_threshold"]
FFMPEG_TIMEOUT = CONFIG["performance"]["ffmpeg_timeout_sec"]

FFMPEG = os.environ.get("CDJ_FFMPEG", "ffmpeg")


# ================================================================ Dekodowanie ---
def wczytaj(sciezka: str) -> np.ndarray:
    """Załaduj MP3 - dekoduj do RAM."""
    with tempfile.TemporaryDirectory(prefix="cdj-audio-") as temp_dir:
        decoded = os.path.join(temp_dir, "samples.f32le")
        try:
            subprocess.run(
                [FFMPEG, "-v", "error", "-threads", "1", "-i", sciezka, "-ac", "1", "-ar", str(SR),
                 "-f", "f32le", decoded],
                capture_output=True, check=True, timeout=FFMPEG_TIMEOUT
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"FFmpeg timeout (>{FFMPEG_TIMEOUT}s)")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"FFmpeg error decoding {os.path.basename(sciezka)}")
        except FileNotFoundError:
            raise RuntimeError(f"FFmpeg nie znaleziony: {FFMPEG}")
        
        return np.fromfile(decoded, dtype="<f4")


# ================================================================ Analiza ---
def _srednia_ruchoma(x: np.ndarray, szer: int) -> np.ndarray:
    """Szybka średnia z oknem prostokątnym."""
    lewo = szer // 2
    prawo = (szer - 1) // 2
    x_pad = np.pad(x, (lewo, prawo), mode="constant")
    suma = np.cumsum(x_pad, dtype=np.float64)
    suma = np.concatenate(([0.0], suma))
    return (suma[szer:] - suma[:-szer]) / szer


def _analizuj_wstepna(y: np.ndarray) -> Dict[str, Any]:
    """Zwraca słownik z BPM, pierwszym beatem, confidence.
    
    Returns:
        {
            "bpm": float,
            "pierwszy": float,  # czas w sekundach
            "confidence": float,  # 0.0-1.0
            "reliable": bool
        }
    """
    if len(y) == 0:
        raise ValueError("Audio jest puste")
    
    dl = len(y) / SR
    b, a = butter(4, 150 / (SR / 2), btype="low")
    y_low = filtfilt(b, a, y)

    # Obwiednia uderzeń
    e = _srednia_ruchoma(np.abs(y_low), int(0.040 * SR))
    lag = int(0.020 * SR)
    d = np.zeros_like(e)
    d[lag:] = e[lag:] - e[:-lag]
    env = np.maximum(d, 0)[::HOP].astype(np.float64)
    env = env - env.mean()

    def wynik(bpm: float, start=0, koniec=None, krok_fazy=0.5) -> Tuple[float, float, float]:
        """Zwraca (score, offset, confidence)."""
        seg = env[start:koniec]
        if len(seg) == 0:
            return 0.0, 0.0, 0.0
        
        fp = 60.0 / bpm * FPS
        n = int((len(seg) - 1) // fp)
        if n <= 0:
            return 0.0, 0.0, 0.0
        
        ks = np.arange(n) * fp
        offs = np.arange(0, fp, krok_fazy)
        pos = offs[:, None] + ks[None, :]
        i0 = np.floor(pos).astype(int)
        fr = pos - i0
        wart = seg[i0] * (1 - fr) + seg[np.minimum(i0 + 1, len(seg) - 1)] * fr
        oceny = wart.mean(axis=1)
        j = int(np.argmax(oceny))
        
        # Confidence = różnica między best a mean
        best = np.max(oceny) if len(oceny) > 0 else 0.0
        mean = np.mean(oceny) if len(oceny) > 0 else 0.0
        confidence = best - mean if mean > 0 else 0.0
        
        return best, offs[j], confidence

    # Coarse pass: na ok. 2 min fragmencie z najmocniejszym basem (kilkaset temp do sprawdzenia, a dokladne
    # przejscia nizej i tak licza caly utwor); w dlugich utworach ok. 3x szybciej przy tym samym wyniku
    okno = int(120 * FPS)
    g0, g1 = 0, None
    if len(env) > 1.5 * okno:
        cs = np.concatenate(([0.0], np.cumsum(np.maximum(env, 0.0))))
        g0 = int(np.argmax(cs[okno:] - cs[:-okno]))
        g1 = g0 + okno
    grube = np.arange(BPM_MIN, BPM_MAX, 0.20)
    scores = []
    for x in grube:
        score, _, _ = wynik(x, g0, g1, krok_fazy=1.0)
        scores.append(score)
    bpm0 = grube[int(np.argmax(scores))]

    # Focused pass
    dok = np.arange(max(BPM_MIN, bpm0 - 0.30), min(BPM_MAX, bpm0 + 0.30) + 0.0001, 0.01)
    scores = [wynik(x, g0, g1)[0] for x in dok]  # ten sam fragment; przejscie 0,001 BPM nizej liczy caly utwor
    bpm0 = float(dok[int(np.argmax(scores))])

    # Fine pass
    dok = np.arange(max(BPM_MIN, bpm0 - 0.01), min(BPM_MAX, bpm0 + 0.01) + 0.0001, 0.001)
    scores = [wynik(x)[0] for x in dok]
    bpm = float(dok[int(np.argmax(scores))])

    # Drift correction
    for _ in range(3):
        fp = 60.0 / bpm * FPS
        cw = len(env) // 4
        _, o1, _ = wynik(bpm, 0, cw)
        _, o4, _ = wynik(bpm, 3 * cw, None)
        o4_abs = (3 * cw + o4) % fp
        r = ((o4_abs - o1 + fp / 2) % fp) - fp / 2
        fp_nowy = fp + r / (3 * cw / fp) if (3 * cw / fp) != 0 else fp
        bpm = 60.0 * FPS / fp_nowy

    # Final confidence
    _, _, final_confidence = wynik(bpm)
    
    # Beat grid
    okres = 60.0 / bpm
    _, przes, _ = wynik(bpm)
    pierwszy = przes / FPS

    # Refine beat position by finding peak in envelope
    gl = int(0.008 * SR)
    obw = _srednia_ruchoma(np.abs(y_low), gl)
    pol = int(0.08 * SR)
    profil = np.zeros(2 * pol)
    ile = 0
    for t in np.arange(pierwszy, dl, okres):
        s = int(round(t * SR)) - pol
        if 0 <= s < len(obw) - 2 * pol:
            profil += obw[s:s + 2 * pol]
            ile += 1
    
    if ile > 10:
        peak_idx = int(np.argmax(np.gradient(profil / ile))) - pol
        pierwszy += peak_idx / SR
    
    # Normalize do pierwszego beatu
    while pierwszy < 0:
        pierwszy += okres
    while pierwszy - okres >= 0:
        pierwszy -= okres

    return {
        "bpm": float(bpm),
        "pierwszy": float(pierwszy),
        "confidence": float(final_confidence),
        "reliable": final_confidence > CONFIDENCE_THRESHOLD
    }



# ------------------------------------------------- precyzyjna siatka (v4) ---
_HOP_MS = 22  # probek na krok ~1 ms przy 22050 Hz


def _obwiednia_kicka(y: np.ndarray) -> np.ndarray:
    """Sila narastania basu (<150 Hz) co ~1 ms."""
    sos = butter(4, 150 / (SR / 2), output="sos")
    x = sosfiltfilt(sos, y)
    k = int(0.003 * SR)
    e = np.convolve(np.abs(x), np.ones(k) / k, "same")
    lag = int(0.006 * SR)
    d = np.zeros_like(e)
    d[lag:] = e[lag:] - e[:-lag]
    on = np.maximum(d, 0)
    n = len(on) // _HOP_MS
    return on[:n * _HOP_MS].reshape(n, _HOP_MS).sum(1)


# Cache'e sa lokalne dla watku: analiza kilku utworow moze isc w watkach jednego procesu
# (przy mniej niz 4 plikach), a wspolny slownik mieszalby dane roznych utworow.
_TL = threading.local()


def _cache(nazwa: str, pola: dict) -> dict:
    c = getattr(_TL, nazwa, None)
    if c is None:
        c = {k: None for k in pola}
        setattr(_TL, nazwa, c)
    return c


def _profil() -> dict:
    return _cache("profil", {"ref": 0, "w": 0, "t": 0})


def _zlozony_profil(on1: np.ndarray, bpm: float):
    """Histogram fazy obwiedni zlozony modulo okres; zwraca (ostrosc, faza_s)."""
    krok_ms = _HOP_MS * 1000.0 / SR
    okres = 60000.0 / bpm
    nb = int(round(okres * 2))
    # Tylko niezerowe probki obwiedni wnosza cos do histogramu (dodanie 0.0 nic nie zmienia),
    # a petla po ~160 kandydatach BPM wola te funkcje wielokrotnie dla tej samej tablicy.
    pr = _profil()
    if pr["ref"] is not on1:
        nz = np.nonzero(on1)[0]
        pr.update(ref=on1, w=on1[nz], t=(nz + 0.5) * krok_ms)
    ph = (pr["t"] % okres) / okres
    h = np.bincount((ph * nb).astype(int) % nb, weights=pr["w"], minlength=nb)
    k = 4
    hs = np.convolve(np.concatenate([h[-k:], h, h[:k]]), np.ones(2 * k + 1) / (2 * k + 1), "same")[k:-k]
    return hs.max() / (hs.mean() + 1e-12), (np.argmax(hs) + 0.5) / nb * okres / 1000.0


def _kick120() -> dict:
    return _cache("kick120", {"ref": 0, "e": 0, "c": 0})


def _poczatek_kicka(y: np.ndarray, bpm: float, t0: float) -> float:
    """Przesuniecie (s) siatki t0+k*okres do poczatku narastania kicka (basu <120 Hz)."""
    okres = 60.0 / bpm
    kc = _kick120()
    if kc["ref"] is not y:  # filtr zalezy tylko od y: petla w analizuj() wola to 2x dla tego samego utworu
        sos = butter(4, 120 / (SR / 2), output="sos")
        e = np.abs(sosfiltfilt(sos, y))
        kc.update(ref=y, e=e, c=np.concatenate(([0.0], np.cumsum(e))))
    e, c = kc["e"], kc["c"]
    w = int(0.003 * SR)
    ts = t0 + np.arange(0, int((len(y) / SR - t0) / okres) - 1)[::2] * okres
    offs = np.arange(-int(okres * 500), int(okres * 500))
    prof = np.empty(len(offs))
    for i, o in enumerate(offs):
        idx = ((ts + o / 1000.0) * SR).astype(int)
        idx = idx[(idx >= 0) & (idx < len(e) - w)]
        prof[i] = np.mean((c[idx + w] - c[idx]) / w) if len(idx) else 0.0
    pk = int(np.argmax(prof))
    baza = np.percentile(prof, 10)
    prog = baza + 0.25 * (prof[pk] - baza)
    j = pk
    while j > 0 and prof[j] > prog:
        j -= 1
    return offs[j] / 1000.0


def analizuj(y: np.ndarray) -> Dict[str, Any]:
    """BPM + pierwszy beat. Zwraca {bpm, pierwszy [s], confidence, reliable}."""
    if len(y) == 0:
        raise ValueError("Audio jest puste")
    wstep = _analizuj_wstepna(y)
    on1 = _obwiednia_kicka(y)
    hint = wstep["bpm"]
    kand = np.arange(hint - 0.4, hint + 0.4, 0.005)
    sc = np.array([_zlozony_profil(on1, b)[0] for b in kand])
    bpm = float(kand[int(np.argmax(sc))])
    # utwory klubowe maja zwykle tempo calkowite lub polowkowe: przyciagnij, gdy wynik prawie rowny
    for r in (round(bpm), round(bpm * 2) / 2):
        if abs(r - bpm) <= 0.06 and _zlozony_profil(on1, float(r))[0] >= 0.97 * sc.max():
            bpm = float(r)
            break
    ostrosc, faza = _zlozony_profil(on1, bpm)
    okres = 60.0 / bpm
    for _ in range(2):  # dosun siatke do poczatku narastania kicka
        faza = (faza + _poczatek_kicka(y, bpm, faza)) % okres
    _kick120().update(ref=None, e=None, c=None)

    def _takt_heurystyka():
        try:
            return int(wykryj_takt(y, bpm, float(faza)))
        except Exception:
            return 0
    wynik = {"bpm": bpm, "pierwszy": float(faza), "confidence": float(ostrosc),
             "reliable": True, "takt": 0, "takt_zrodlo": "heurystyka"}
    try:  # siec neuronowa Beat This!: faza siatki, faza taktu (czerwony pasek) i struktura utworu
        z_sieci = _takt_z_sieci(y, bpm, float(faza), None)
        if "takt" not in z_sieci:  # heurystyka taktu tylko gdy siec go nie rozstrzygnela (oszczedza ok. 7% czasu)
            z_sieci = dict(z_sieci, takt=_takt_heurystyka())
        wynik.update(z_sieci)
        med = wynik.get("siec_przesuniecie_s")
        zg = wynik.get("siec_zgodnosc", 0.0)
        # przesuniecie o prawie pol beatu bierzemy tylko przy pewnej sieci (np. hip-hop liczony w podwojnym
        # tempie: heurystyka basu stawia siatke miedzy stopami, a siec zgodnie wskazuje ~pol beatu dalej)
        if med is not None and (abs(med) < 0.45 * okres or zg >= 0.8) and zg >= 0.4:
            # Faza z sieci: na wzorcu rekordboxa mediana bledu 8 ms (heurystyka energii basu: ok. 60 ms, bo stawiala
            # linie na szczycie basu stopy zamiast na jej ataku). Heurystyka zostaje, gdy siec nie dziala.
            pierwszy = faza + med - SIEC_OPOZNIENIE_S + ATAK_PRZED_WZROSTEM_S
            takt = wynik["takt"]
            if pierwszy < 0:  # kotwica o beat pozniej: numeracja w takcie przesuwa sie o jeden
                pierwszy += okres
                takt = (takt - 1) % 4
            wynik.update(pierwszy=float(pierwszy), takt=int(takt), faza_zrodlo="siec",
                         faza_heurystyka=float(faza))
    except Exception as e:  # noqa: BLE001  # bez sieci zostaje algorytm zapasowy
        wynik["siec_blad"] = "%s: %s" % (type(e).__name__, e)
        if wynik.get("takt_zrodlo") != "siec":
            wynik["takt"] = _takt_heurystyka()
    try:
        _polowa_tempa(wynik)
    except Exception:  # noqa: BLE001  # zostaje tempo z analizy
        pass
    _profil().update(ref=None, w=None, t=None)  # zwolnij pamiec podreczna filtrow/profilu tego utworu
    _kick120().update(ref=None, e=None, c=None)
    return wynik


def _polowa_tempa(wynik):
    """Podwojne tempo (hip-hop, trap ~70-90 BPM liczone jako 140-180): gdy siec zgodnie widzi beaty co drugi
    beat naszej siatki, tempo i siatka ida na pol. Beat 'raz' i takt biora sie z beatow i downbeatow sieci."""
    bt = np.asarray(wynik.get("beaty_sieci", ()), dtype=np.float64)
    bd = np.asarray(wynik.get("takty_sieci", ()), dtype=np.float64)
    if wynik.get("faza_zrodlo") != "siec" or len(bt) < 16 or len(bd) < 4:
        return
    bpm = wynik["bpm"]
    odst = np.diff(bt)
    odst = odst[odst < 2.0]
    if len(odst) < 12 or bpm < 120:
        return
    bpm_sieci = 60.0 / float(np.median(odst))
    if abs(2 * bpm_sieci / bpm - 1) > 0.03:
        return
    okres = 60.0 / bpm
    kot = wynik["pierwszy"] + SIEC_OPOZNIENIE_S - ATAK_PRZED_WZROSTEM_S  # pierwszy beat w czasie sieci
    # ktore z dwoch ustawien (parzyste/nieparzyste beaty) trafia w beaty sieci
    trafia = [np.mean(np.abs(((bt - kot - p * okres + okres) % (2 * okres)) - okres) < 0.03) for p in (0, 1)]
    p = int(np.argmax(trafia))
    if trafia[p] < 0.7:
        return
    pierwszy = wynik["pierwszy"] + p * okres
    okres2 = 2 * okres
    while pierwszy - okres2 >= 0:
        pierwszy -= okres2
    kot = pierwszy + SIEC_OPOZNIENIE_S - ATAK_PRZED_WZROSTEM_S
    glosy = np.bincount(np.round((bd - kot) / okres2).astype(int) % 4, minlength=4)
    wynik.update(bpm=bpm / 2, pierwszy=float(pierwszy), takt=int(np.argmax(glosy)), takt_glosy=[int(v) for v in glosy],
                 polowa_tempa=True)


NET_OKIEN = int(os.environ.get("CDJ_BT_OKNA", "1"))  # ile ~30 s fragmentow analizuje siec (0 = caly utwor)


def _takt_z_sieci(y, bpm, faza, takt_zapas, ile_okien=None):
    """Glosowanie downbeatow z Beat This! nad indeksem beatu mod 4 wzgledem naszej siatki.
    Siec liczy tylko kilka najbardziej basowych fragmentow (to wystarcza do glosowania i skraca analize
    ~4x); gdy glosy sa niejednoznaczne, powtarza obliczenia na calym utworze."""
    import beatthis as BT
    if not BT.dostepny():
        return {"siec_blad": BT.blad() or "brak modelu"}
    if ile_okien is None:
        ile_okien = NET_OKIEN
    # 1, 2, potem od razu 8 okien (utwor 13 wzorca: 8 okien = zgodnosc 0,53, blad 20 ms, polowa czasu calego
    # utworu); caly utwor tylko gdy i to nie wystarcza
    for k in ([ile_okien, 2 * ile_okien, 8 * ile_okien, None] if ile_okien else [None]):
        wynik = _glosowanie_sieci(y, bpm, faza, BT, k)
        if "siec_blad" in wynik:
            return wynik
        g_ = wynik["takt_glosy"]
        # slaba zgodnosc fazy (siec zaznacza tez beaty posrodku): liczymy wiecej okien
        if k is None or (g_ and max(g_) >= 0.6 * sum(g_) and wynik.get("siec_zgodnosc", 1.0) >= 0.5):
            return wynik
    return wynik


def _skupienie_beatow(d, okres):
    """Przesuniecie beatow sieci wzgledem siatki: najsilniejsze skupienie odchylek d (s), a nie mediana, bo siec
    bywa, ze zaznacza tez beaty posrodku (osemki) i mediana trafia wtedy miedzy nie. Z dwoch podobnie silnych
    skupien wybiera blizsze 0 (fazy z heurystyki). Zwraca (przesuniecie, ulamek beatow w +-25 ms od niego)."""
    d = np.asarray(d, dtype=np.float64)
    if len(d) == 0:
        return 0.0, 0.0
    krok = 0.004
    n = max(8, int(np.ceil(okres / krok)))
    h, kr = np.histogram(d, bins=n, range=(-okres / 2, okres / 2))
    hs = np.convolve(np.r_[h[-2:], h, h[:2]].astype(np.float64), np.ones(5) / 5, "same")[2:-2]
    srodki = (kr[:-1] + kr[1:]) / 2
    szczyty = [i for i in range(n) if hs[i] >= 0.6 * hs.max() and hs[i] >= hs[i - 1] and hs[i] >= hs[(i + 1) % n]]
    i = min(szczyty, key=lambda j: abs(srodki[j])) if szczyty else int(np.argmax(hs))
    blisko = np.abs(d - srodki[i]) < 0.02
    przes = float(np.median(d[blisko])) if blisko.any() else float(srodki[i])
    return przes, float(np.mean(np.abs(d - przes) < 0.025))


def _glosowanie_sieci(y, bpm, faza, BT, ile_okien):
    r = BT.analizuj(y, ile_okien)
    bt, bd = r["beaty"], r["takty"]
    okres = 60.0 / bpm
    if len(bt) < 16 or len(bd) < 8:
        return {"siec_blad": "za malo beatow z sieci"}
    d = ((bt - faza + okres / 2) % okres) - okres / 2
    med, zgodnosc = _skupienie_beatow(d, okres)
    idx = np.round((bd - med - faza) / okres).astype(int) % 4
    glosy = np.bincount(idx, minlength=4)
    wynik = {"beaty_sieci": bt, "takty_sieci": bd, "siec_przesuniecie_s": med, "siec_zgodnosc": zgodnosc,
             "takt_glosy": [int(v) for v in glosy]}
    if glosy.max() >= 0.6 * glosy.sum():
        wynik["takt"] = int(np.argmax(glosy))
        wynik["takt_zrodlo"] = "siec"
    return wynik


def wykryj_takt(y: np.ndarray, bpm: float, pierwszy: float) -> int:
    """Ktory beat siatki (kotwica: pierwszy + a*okres) jest poczatkiem taktu: indeks mod 4.
    Beat 'raz' = miejsce, w ktorym zaczyna sie glowny beat utworu (po wstepie bez kicka);
    gdy nie da sie go znalezc: najwieksza zmiana brzmienia (poczatki fraz)."""
    import takt as T
    a0, nov = T.cechy(y, SR, bpm, pierwszy)
    if len(nov[1]) < 32:
        return 0
    i = T.start_bitu(y, SR, bpm, pierwszy, a0)
    if i is not None:
        return int((a0 + i) % 4)
    s = sum(T.glosy(a0, nov[k]) for k in (1, 2, 4, "bas"))
    return int(np.argmax(s))


# ================================================================ DAT ---
def siatka_ms(bpm: float, pierwszy_s: float, dlugosc_s: float, takt: int = 0):
    """Czasy beatów w s (współrzędne Rekordbox). Zwraca tablicę czasów; numery beatów
    w takcie (1..4) są w atrybucie .numery (beat 'raz' wg wykrytego taktu `takt`)."""
    okres = 60.0 / bpm
    t0 = pierwszy_s + OFFSET_REKORDBOX_S - ATAK_PRZED_WZROSTEM_S
    m = 0
    while t0 - okres >= 0:
        t0 -= okres
        m += 1
    koniec = dlugosc_s + OFFSET_REKORDBOX_S
    czasy = np.arange(t0, koniec - 0.05, okres).view(_Siatka)
    czasy.numery = ((np.arange(len(czasy)) - m - takt) % 4) + 1
    return czasy


# ------------------------------------------------- dlugie utwory (sety, miksy) ---
DLUGI_PROG_S = 20 * 60     # od tej dlugosci analiza idzie odcinkami, a siatka moze zmieniac tempo
ODCINEK_S = 180            # dlugosc odcinka analizy (tempo i faza liczone osobno w kazdym)
ZGODNE_BPM = 0.06          # sasiednie odcinki o tym samym tempie i fazie lacza sie w jedna rowna siatke
ZGODNA_FAZA_S = 0.025


def beaty_sieci_dlugie(y, postep=None):
    """Beaty i pierwsze uderzenia taktow z Beat This! dla calego nagrania, odcinkami (z zakladka)."""
    import beatthis as BT
    kaw, zap = int(ODCINEK_S * SR), int(10 * SR)
    beaty, takty = [], []
    granice = list(range(0, len(y), kaw))
    for j, p in enumerate(granice):
        k = min(len(y), p + kaw)
        lo, hi = max(0, p - zap), min(len(y), k + zap)
        r = BT.analizuj(y[lo:hi], None)
        for zrodlo, cel in ((r["beaty"], beaty), (r["takty"], takty)):
            t = np.asarray(zrodlo) + lo / SR
            cel.append(t[(t >= p / SR) & (t < k / SR)])
        if postep:
            postep(j + 1, len(granice))
    return np.concatenate(beaty), np.concatenate(takty)


def analizuj_dlugi(y, postep=None):
    """Dlugie nagranie (set, miks): odcinki po ODCINEK_S analizowane jak zwykly utwor (BPM, faza, takt),
    zgodne sasiednie odcinki laczone w serie, a moment zmiany utworu wyznaczony z beatow sieci Beat This!
    dla calego nagrania (bez sieci: z uderzen basu). Siatka dynamiczna jak w rekordboxie.
    Pamiec: ok. 15x jednego odcinka zamiast calego nagrania."""
    dl = len(y) / SR
    kaw = int(ODCINEK_S * SR)
    granice = list(range(0, len(y), kaw))
    if len(granice) > 1 and len(y) - granice[-1] < 60 * SR:  # krotka koncowka dolacza do poprzedniego
        granice.pop()
    beaty = None
    try:
        import beatthis as BT
        if BT.dostepny():
            beaty, _ = beaty_sieci_dlugie(y)
            beaty = np.sort(beaty) + OFFSET_REKORDBOX_S - SIEC_OPOZNIENIE_S  # wspolrzedne rekordboxa
    except Exception:  # noqa: BLE001
        beaty = None
    odcinki = []
    for j, p in enumerate(granice):
        k = granice[j + 1] if j + 1 < len(granice) else len(y)
        try:
            w = analizuj(y[p:k])
        except Exception:  # noqa: BLE001  # cisza albo szum: odcinek bez wlasnej siatki
            w = None
        odcinki.append((p / SR, k / SR, w))
        if postep:
            postep(j + 1, len(granice))
    return siatka_dynamiczna(odcinki, dl, y, beaty)


def _trafienia(sr_, t):
    """Ktore z czasow t (wsp. rekordboxa) leza na siatce serii (+-25 ms)."""
    k = np.round((t - sr_["t0"]) / sr_["okres"])
    return np.abs(sr_["t0"] + k * sr_["okres"] - t) < 0.025


def _sily_basu(y, lo, hi):
    """Funkcja: czasy (wsp. rekordboxa, w [lo, hi)) -> sila narastania basu przy beacie."""
    przes = ATAK_PRZED_WZROSTEM_S - OFFSET_REKORDBOX_S  # wsp. rekordboxa -> czas dzwieku
    p0 = int(max(0.0, lo + przes - 0.1) * SR)
    env = _obwiednia_kicka(y[p0:int(max(0.0, hi + przes + 0.1) * SR)])
    krok = _HOP_MS / SR

    def f(t):
        if len(env) == 0:
            return np.zeros(len(t))
        a = t + przes - p0 / SR
        i0 = np.clip(((a - 0.010) / krok).astype(int), 0, len(env) - 1)
        i1 = np.clip(((a + 0.040) / krok).astype(int), 1, len(env))
        return np.array([env[x:max(z, x + 1)].max() for x, z in zip(i0, i1)])
    return f


def _oceniacz(lo, hi, beaty, y, serie):
    """Funkcja seria -> (czasy, wagi) w oknie [lo, hi): trafione beaty sieci, a gdy siec w tym miejscu nie widzi
    beatow zadnej z serii (np. zaznacza tylko hi-haty) - sila basu na beatach siatki."""
    if beaty is not None:
        t = beaty[(beaty >= lo) & (beaty < hi)]
        if len(t) >= 8 and max(_trafienia(s_, t).mean() for s_ in serie) >= 0.25:
            return lambda sr_: (t, _trafienia(sr_, t).astype(float))
    if y is None:
        return None
    f = _sily_basu(y, lo, hi)

    def bas(sr_):
        k0 = int(np.ceil((lo - sr_["t0"]) / sr_["okres"]))
        k1 = int(np.floor((hi - sr_["t0"]) / sr_["okres"]))
        tt = sr_["t0"] + np.arange(k0, k1 + 1) * sr_["okres"]
        tt = tt[(tt >= lo) & (tt < hi)]
        return tt, f(tt)
    return bas


def _najlepszy_podzial(a, b, lo, hi, oc):
    """Moment T w [lo, hi): siatka a przed T, b od T; zwraca (T, ocena)."""
    ta, wa = oc(a)
    tb, wb = oc(b)
    kand = np.unique(np.r_[ta, tb, lo])
    ca = np.concatenate(([0.0], np.cumsum(wa)))
    cb = np.concatenate(([0.0], np.cumsum(wb)))
    ocena = ca[np.searchsorted(ta, kand)] + (cb[-1] - cb[np.searchsorted(tb, kand)])
    i = int(np.argmax(ocena))
    return float(kand[i]), float(ocena[i])


def siatka_dynamiczna(odcinki, dl, y=None, beaty=None):
    """odcinki: [(start_s, koniec_s, wynik analizuj() albo None)], beaty: beaty sieci (wsp. rekordboxa) albo None.
    Zwraca slownik jak analizuj() plus "czasy" (_Siatka z .numery i .tempa)."""
    O = OFFSET_REKORDBOX_S
    # 1. serie: kolejne odcinki o zgodnym tempie i fazie (prosta t = t0 + k*okres przez kotwice odcinkow)
    serie = []
    for s0, s1, w in odcinki:
        kw = None
        if w:
            c = siatka_ms(w["bpm"], w["pierwszy"], s1 - s0, w.get("takt", 0))
            if len(c):
                kw = (s0 + float(c[0]), 60.0 / w["bpm"], int(c.numery[0]))
                if beaty is not None:  # faza odcinka z beatow sieci (odcinek z przejsciem: faza utworu, ktory trwa dluzej)
                    b = beaty[(beaty >= s0 + O) & (beaty < s1 + O)]
                    if len(b) >= 16:
                        okr = kw[1]
                        przes, zg = _skupienie_beatow(((b - kw[0] + okr / 2) % okr) - okr / 2, okr)
                        if zg >= 0.3 and abs(przes) < 0.45 * okr:  # jak w analizuj(): nie o pol beatu
                            kw = (kw[0] + przes, okr, kw[2])
        if serie and kw is None:
            serie[-1]["koniec"] = s1 + O
            continue
        if kw is None:
            continue
        if serie:
            sr_ = serie[-1]
            t2, okres2, _ = kw
            k = int(round((t2 - sr_["t0"]) / sr_["okres"]))
            if abs(60.0 / sr_["okres"] - 60.0 / okres2) <= ZGODNE_BPM and \
                    abs(sr_["t0"] + k * sr_["okres"] - t2) <= ZGODNA_FAZA_S:
                ks, ts = np.array(sr_["k"] + [k], float), np.array(sr_["t"] + [t2])
                okr, t0 = np.polyfit(ks, ts, 1)
                if np.max(np.abs(t0 + okr * ks - ts)) <= ZGODNA_FAZA_S:
                    sr_["k"].append(k); sr_["t"].append(t2)
                    sr_["okres"], sr_["t0"] = float(okr), float(t0)
                    sr_["koniec"] = s1 + O
                    continue
        t, okres, nr = kw
        serie.append({"start": s0 + O, "koniec": s1 + O, "k": [0], "t": [t], "t0": t, "okres": okres, "nr0": nr})
    if not serie:
        raise ValueError("nie znaleziono rytmu w nagraniu")
    if beaty is not None:  # tempo i faza serii dopasowane do wszystkich trafionych beatow sieci (dokladniej)
        for sr_ in serie:
            b = beaty[(beaty >= sr_["start"]) & (beaty < sr_["koniec"])]
            for _ in range(2):
                tr = _trafienia(sr_, b)
                if tr.sum() < 32:
                    break
                ks = np.round((b[tr] - sr_["t0"]) / sr_["okres"])
                okr, t0 = np.polyfit(ks, b[tr], 1)
                if abs(60.0 / okr - 60.0 / sr_["okres"]) > 0.2:
                    break
                sr_["okres"], sr_["t0"] = float(okr), float(t0)
    ocen = beaty is not None or y is not None
    # 2. serie z jednego odcinka, ktore lepiej tlumacza sasiedzi (odcinek z przejsciem miedzy utworami) - usun
    if ocen:
        zmiana = True
        while zmiana and len(serie) > 2:
            zmiana = False
            for i in range(1, len(serie) - 1):
                a, sr_, c = serie[i - 1], serie[i], serie[i + 1]
                if len(sr_["k"]) > 1:
                    continue
                lo, hi = sr_["start"], sr_["koniec"]
                oc_ = _oceniacz(lo, hi, beaty, y, [a, sr_, c])
                if oc_ is None:
                    continue
                _, w_own = oc_(sr_)
                _, oc = _najlepszy_podzial(a, c, lo, hi, oc_)
                if oc >= w_own.sum():
                    a["koniec"] = c["start"] = lo
                    c["szukaj_do"] = max(c.get("szukaj_do", 0.0), hi + ODCINEK_S)
                    del serie[i]
                    zmiana = True
                    break
    # 3. dokladny moment zmiany: w otoczeniu granicy odcinkow (zmiana zwykle wypada wewnatrz odcinka)
    serie[0]["start"] = 0.0
    koniec = dl + O - 0.05
    serie[-1]["koniec"] = koniec
    if ocen:
        for i in range(1, len(serie)):
            a, b = serie[i - 1], serie[i]
            lo = max(a["start"], b["start"] - ODCINEK_S)
            hi = min(b["koniec"], max(b["start"] + ODCINEK_S, b.get("szukaj_do", 0.0)))
            if hi - lo > 2:
                try:
                    oc_ = _oceniacz(lo, hi, beaty, y, [a, b])
                    if oc_ is None:
                        continue
                    gr, _ = _najlepszy_podzial(a, b, lo, hi, oc_)
                    a["koniec"] = b["start"] = gr - 0.05
                except Exception:  # noqa: BLE001
                    pass
    # 4. beaty
    czasy, numery, tempa = [], [], []
    for sr_ in serie:
        t0, okres = sr_["t0"], sr_["okres"]
        k = int(np.ceil((sr_["start"] - t0) / okres - 1e-9))
        while True:
            t = t0 + k * okres
            if t >= sr_["koniec"] or t >= koniec:
                break
            if t >= 0 and (not czasy or t >= czasy[-1] + 0.6 * okres):
                czasy.append(t)
                numery.append((sr_["nr0"] - 1 + k) % 4 + 1)
                tempa.append(60.0 / okres)
            k += 1
    wyn = np.array(czasy).view(_Siatka)
    wyn.numery = np.array(numery, dtype=int)
    wyn.tempa = np.array(tempa)
    pewne = [w for _, _, w in odcinki if w]
    dlug = [(s_["koniec"] - s_["start"], 60.0 / s_["okres"]) for s_ in serie]
    return {"bpm": float(max(dlug)[1]), "czasy": wyn, "pierwszy": float(wyn[0]) if len(wyn) else 0.0,
            "confidence": float(np.median([w["confidence"] for w in pewne])) if pewne else 0.0,
            "reliable": True, "takt": 0, "odcinkow": len(odcinki), "serii": len(serie),
            "tempa_serii": [round(60.0 / s_["okres"], 2) for s_ in serie]}


class _Siatka(np.ndarray):
    numery = None
    tempa = None  # BPM kazdego beatu (siatka dynamiczna); None = staly bpm

    def __array_finalize__(self, obj):
        self.numery = getattr(obj, "numery", None)
        self.tempa = getattr(obj, "tempa", None)


def _bpm100_beatow(czasy_s, bpm):
    tempa = getattr(czasy_s, "tempa", None)
    if tempa is not None and len(tempa) == len(czasy_s):
        return [int(round(t * 100)) for t in tempa]
    return [int(round(bpm * 100))] * len(czasy_s)


def _ms_calk(t_s: float) -> int:
    """Czas beatu w pelnych ms (Rekordbox obcina, nie zaokragla); ulamek ms jest w PQT2."""
    return int(np.floor(t_s * 1000.0 + 1e-6))


def tag_pqt2(czasy_s: np.ndarray, bpm: float) -> bytes:
    """PQT2 (rozszerzona siatka beatow, plik .EXT): naglowek 56 B + po 2 B na beat (ulamek ms w
    mikrosekundach). Odtwarzacz uzywa jej do natychmiastowego odczytu BPM i taktow. Format odtworzony
    z eksportu Rekordbox: suma kontrolna = suma (numer beatu + tempo + czas ms) po wszystkich beatach."""
    n = len(czasy_s)
    numery = getattr(czasy_s, "numery", None)
    nr = [int(numery[i]) if numery is not None else (i % 4) + 1 for i in range(n)]
    b100 = _bpm100_beatow(czasy_s, bpm)  # siatka dynamiczna: suma kontrolna z tempem kazdego beatu (jak rekordbox)
    ms = [_ms_calk(t) for t in czasy_s]
    ulamki = [min(999, max(0, int(round((t * 1000.0 - m) * 1000.0)))) for t, m in zip(czasy_s, ms)]
    suma = sum(nr[i] + b100[i] + ms[i] for i in range(n)) & 0xFFFFFFFF
    body = b"".join(struct.pack(">H", u) for u in ulamki)
    hdr = struct.pack(">11I", 0, 0x02000002, 0,
                      (nr[0] << 16) | b100[0], ms[0], (nr[-1] << 16) | b100[-1], ms[-1], n, suma, 0, 0)
    return b"PQT2" + struct.pack(">II", 56, 56 + len(body)) + hdr + body


def tag_pqtz(czasy_s: np.ndarray, bpm: float) -> bytes:
    """PQTZ tag (beat grid)."""
    n = len(czasy_s)
    body = b""
    numery = getattr(czasy_s, "numery", None)
    b100 = _bpm100_beatow(czasy_s, bpm)
    for i, t in enumerate(czasy_s):
        nr = int(numery[i]) if numery is not None else (i % 4) + 1
        body += struct.pack(">HHI", nr, b100[i], _ms_calk(t))
    return b"PQTZ" + struct.pack(">III", 24, 24 + len(body), 0) + struct.pack(">II", 0x80000, n) + body


def tag_pwav(y: np.ndarray, dlugosc_s: float) -> Tuple[bytes, np.ndarray]:
    """PWAV tag (waveform visualization)."""
    calk = dlugosc_s + OFFSET_REKORDBOX_S
    dane = bytearray()
    wys = []
    for i in range(400):
        a = int(round((i * calk / 400 - OFFSET_REKORDBOX_S) * SR))
        b = int(round(((i + 1) * calk / 400 - OFFSET_REKORDBOX_S) * SR))
        a, b = max(a, 0), min(b, len(y))
        rms = float(np.sqrt(np.mean(y[a:b] ** 2))) if b > a else 0.0
        h = int(min(31, max(0, round(40.5 * rms))))
        wys.append(h)
        dane.append((3 << 5) | h)
    tag = b"PWAV" + struct.pack(">III", 20, 20 + 400, 400) + struct.pack(">I", 0x10000) + bytes(dane)
    return tag, np.array(wys)


def tag_pwv2(wys_pwav: np.ndarray) -> bytes:
    """PWV2 tag (waveform summary)."""
    g = wys_pwav.reshape(100, 4)
    dane = bytearray()
    for grupa in g:
        m = int(grupa.max())
        v = int(min(15, max(1, round(0.5 * m + 2.6))))
        dane.append(v)
    return b"PWV2" + struct.pack(">III", 20, 20 + 100, 100) + struct.pack(">I", 0x10000) + bytes(dane)



# ================================================================ EXT ---
# Plik .EXT niesie waveform szczegolowy (PWV3), ktorego CDJ potrzebuje
# oprocz podgladu z .DAT. Struktura odtworzona z plikow eksportu rekordbox.
PWV3_PROBEK_NA_S = 150

PCOB_EXT_PAMIEC = bytes.fromhex("50434f4200000018000000180000000100000000ffffffff")
PCOB_EXT_HOT = bytes.fromhex("50434f4200000018000000180000000000000000ffffffff")
PCO2_EXT_PAMIEC = bytes.fromhex("50434f32000000140000001400000001" "00000000")
PCO2_EXT_HOT = bytes.fromhex("50434f32000000140000001400000000" "00000000")


KAWALEK_S = 600       # dlugie utwory: filtry i sumy liczone po 10 min (krotsze utwory w jednym kawalku)
ZAKLADKA_S = 2.0      # zapas na brzegach kawalka, zeby filtr nie zostawil sladu w wyniku


def _okna_150(y, dlugosc_s):
    calk = dlugosc_s + OFFSET_REKORDBOX_S
    n = int(round(calk * PWV3_PROBEK_NA_S))
    okno = SR / PWV3_PROBEK_NA_S
    idx = np.arange(n)
    a = np.clip(np.round(idx * okno - OFFSET_REKORDBOX_S * SR).astype(np.int64), 0, len(y))
    b = np.clip(np.round((idx + 1) * okno - OFFSET_REKORDBOX_S * SR).astype(np.int64), 0, len(y))
    return n, a, np.maximum(b, a)


def _rms_okien(y, a, b, sos=None):
    """RMS sygnalu (opcjonalnie po filtrze sos) w oknach [a, b), liczony kawalkami po KAWALEK_S."""
    from scipy.signal import sosfiltfilt
    wyn = np.zeros(len(a))
    kaw, zap = int(KAWALEK_S * SR), int(ZAKLADKA_S * SR)
    for p in range(0, max(len(y), 1), kaw):
        k = min(len(y), p + kaw)
        sel = np.nonzero((a >= p) & (a < k))[0] if len(y) > kaw else np.arange(len(a))
        if len(sel) == 0:
            continue
        lo = max(0, p - zap) if len(y) > kaw else 0
        hi = min(len(y), max(k, int(b[sel].max())) + zap) if len(y) > kaw else len(y)
        z = np.asarray(y[lo:hi], dtype=np.float64)
        if sos is not None and len(z) > 27:
            z = sosfiltfilt(sos, z)
        cs = np.concatenate(([0.0], np.cumsum(z * z)))
        aa, bb = a[sel] - lo, b[sel] - lo
        wyn[sel] = np.sqrt(np.maximum(cs[bb] - cs[aa], 0.0) / np.maximum(bb - aa, 1))
        if len(y) <= kaw:
            break
    return np.where(b > a, wyn, 0.0)


def tag_pwv3(y, dlugosc_s):
    """PWV3: waveform szczegolowy, 150 probek/s, 1 bajt (3 bity jasnosc, 5 bitow wysokosc)."""
    n, a, b = _okna_150(y, dlugosc_s)
    rms = _rms_okien(y, a, b)
    h = np.clip(np.round(40.5 * rms), 0, 31).astype(np.uint8)
    body = ((5 << 5) | h).astype(np.uint8).tobytes()
    return (b"PWV3" + struct.pack(">III", 24, 24 + len(body), 1)
            + struct.pack(">II", n, 0x00960000) + body)


# --- kolorowy waveform (PWV5 szczegolowy, PWV4 podglad) -------------------------------------
# Rekordbox koduje kolor jako trzy pasma (niskie/srednie/wysokie -> R/G/B), a wysokosc jako
# glosnosc. Wspolczynniki dopasowane regresja do eksportu rekordbox na 15 utworach
# (wysokosc: korelacja ~0.93, kolory: R^2 0.55-0.82), wiec to wierne przyblizenie, nie kopia bit w bit.
PWV4_KOLUMN = 1200
NASYCENIE_KOLORU = 1.5  # >1 = bardziej nasycone kolory (1.0 = dopasowanie 1:1 do rekordbox)
PWV4_WSP = np.array([
    [106.921, -19.89, 1.801, 6.467, 33.59, 10.962, 2.163, 15.587, -6.839],
    [32.625, -49.913, 2.197, 5.351, -47.827, -10.855, -7.895, 13.885, 194.061],
    [28.641, -3.532, 8.878, 39.956, -23.097, 75.354, 8.963, -6.434, -1.496],
    [4.353, -0.892, 9.012, 1.663, -21.969, 104.682, -1.315, 12.759, -0.902],
    [-1.107, -1.934, 1.173, 57.25, -17.137, 1.883, 105.765, 12.433, -0.57],
    [-36.866, 32.247, -14.871, 1.212, -37.691, -4.502, -9.356, 177.541, 11.472]])


def _cechy_koloru(y, dlugosc_s):
    """Na kazde okno 1/150 s: (rms nisko <200 Hz, rms srednio 200-2000 Hz, rms wysoko >2 kHz, szczyt)."""
    from scipy.signal import butter
    n, a, b = _okna_150(y, dlugosc_s)
    ny = SR / 2
    pasma = (butter(4, 200 / ny, "low", output="sos"), butter(4, [200 / ny, 2000 / ny], "band", output="sos"),
             butter(4, 2000 / ny, "high", output="sos"))
    wyj = [_rms_okien(y, a, b, sos) for sos in pasma]
    szczyt = np.zeros(n)
    ok = b > a
    if ok.any() and len(y):
        # maksimum |y| w kazdym oknie (okna ciagle, bez zakladek); kawalkami, bez kopii calego |y|
        idx = np.nonzero(ok)[0]
        kaw = int(KAWALEK_S * SR)
        for p in range(0, len(y), kaw):
            sel = idx[(a[idx] >= p) & (a[idx] < p + kaw)]
            if len(sel) == 0:
                continue
            hi = len(y) if p + kaw >= len(y) else min(len(y), int(b[sel].max()))
            ay = np.abs(y[p:hi])
            szczyt[sel] = np.maximum.reduceat(ay, np.minimum(a[sel] - p, len(ay) - 1))
    return np.c_[wyj[0], wyj[1], wyj[2], szczyt]


def tag_pwv5(cechy):
    """PWV5: kolorowy waveform szczegolowy, 150 probek/s, 16 bitow: R(3) G(3) B(3) wysokosc(5) 00."""
    lo, mi, hi, pk = cechy.T
    m = np.maximum.reduce([lo, mi, hi]) + 1e-9
    r, g_, b = lo / m, mi / m, hi / m
    R = np.clip(np.round(4.85 * r - 1.66 * g_ - 1.8 * b + 2.66), 0, 7).astype(np.int64)
    G = np.clip(np.round(0.75 * r + 4.44 * g_ - 0.52 * b + 0.45), 0, 7).astype(np.int64)
    B = np.clip(np.round(1.21 * r + 2.23 * g_ + 4.39 * b + 1.35), 0, 7).astype(np.int64)
    H = np.clip(np.round(18.0 * pk ** 2), 0, 31).astype(np.int64)
    rgb = np.c_[R, G, B].astype(np.float64)
    sr = rgb.mean(axis=1, keepdims=True)
    rgb = np.clip(np.round(sr + NASYCENIE_KOLORU * (rgb - sr)), 0, 7).astype(np.int64)
    R, G, B = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    w = ((R << 13) | (G << 10) | (B << 7) | (H << 2)).astype(">u2")
    body = w.tobytes()
    return (b"PWV5" + struct.pack(">III", 24, 24 + len(body), 2)
            + struct.pack(">II", len(w), 0x00960305) + body)


def tag_pwv4(cechy):
    """PWV4: kolorowy podglad calego utworu, 1200 kolumn po 6 bajtow."""
    n = len(cechy)
    k = n / PWV4_KOLUMN
    X = np.zeros((PWV4_KOLUMN, 9))
    for i in range(PWV4_KOLUMN):
        s = cechy[int(i * k):max(int((i + 1) * k), int(i * k) + 1)]
        if len(s) == 0:
            s = cechy[-1:]
        pk = s[:, 3].max()
        X[i] = [pk, pk * pk, s[:, 0].mean(), s[:, 1].mean(), s[:, 2].mean(),
                s[:, 0].max(), s[:, 1].max(), s[:, 2].max(), 1.0]
    P = X @ PWV4_WSP.T
    P[:, 1] = np.clip(P[:, 1], 0, 255)
    sr = P[:, 2:6].mean(axis=1, keepdims=True)
    P[:, 2:6] = sr + NASYCENIE_KOLORU * (P[:, 2:6] - sr)
    for j in (0, 2, 3, 4, 5):
        P[:, j] = np.clip(P[:, j], 0, 127)
    body = np.round(P).astype(np.uint8).tobytes()
    return (b"PWV4" + struct.pack(">III", 24, 24 + len(body), 6)
            + struct.pack(">II", PWV4_KOLUMN, 0) + body)



# --- znaczniki cue: PCOB (stare odtwarzacze, DAT i EXT) i PCO2 (EXT) -----------------------
def _pcpt(k, n, hot, ms):
    return (b"PCPT" + struct.pack(">III", 0x1C, 0x38, hot) + struct.pack(">II", 0, 0x00010000)
            + struct.pack(">HH", 0xFFFF if k == 0 else k - 1, 0xFFFF if k == n - 1 else k + 1)
            + b"\x01\x00\x03\xe8" + struct.pack(">II", int(ms), 0xFFFFFFFF) + b"\x00" * 16)


def tag_pcob(tryb, wpisy):
    """PCOB: tryb 1 = hot cue (wpisy: [(numer 1.., ms)]), tryb 0 = memory cue (numer 0)."""
    n = len(wpisy)
    body = b"".join(_pcpt(k, n, hot, ms) for k, (hot, ms) in enumerate(wpisy))
    pamiec = 0xFFFFFFFF  # jak w kazdym znanym pliku Rekordboxa (hot i memory, takze puste)
    return (b"PCOB" + struct.pack(">III", 0x18, 0x18 + len(body), tryb) + struct.pack(">HH", 0, n)
            + struct.pack(">I", pamiec) + body)


def _pcp2(hot, ms, nazwa):
    """Wpis PCP2 jak w eksporcie Rekordboxa: 0x2c bajty + komentarz UTF-16BE + 44 bajty (kolor + zera)."""
    kom = (nazwa + "\x00").encode("utf-16-be") if nazwa else b""
    wnetrze = (b"\x01\x00\x03\xe8" + struct.pack(">II", int(ms), 0xFFFFFFFF) + b"\x00" + b"\x01" + b"\x00" * 6
               + struct.pack(">HH", 0, 0) + struct.pack(">I", len(kom)) + kom + b"\x00" * 44)
    return b"PCP2" + struct.pack(">II", 0x10, 0x10 + len(wnetrze)) + struct.pack(">I", hot) + wnetrze


def tag_pco2(tryb, wpisy):
    """PCO2: wpisy [(numer, ms, nazwa)]."""
    body = b"".join(_pcp2(hot, ms, nazwa) for hot, ms, nazwa in wpisy)
    return (b"PCO2" + struct.pack(">III", 0x14, 0x14 + len(body), tryb) + struct.pack(">HH", len(wpisy), 0) + body)


def tagi_anlz(dane):
    """(przesuniecie, nazwa, dlugosc) kolejnych tagow pliku ANLZ (po naglowku PMAI)."""
    o = struct.unpack(">I", dane[4:8])[0]
    while o + 12 <= len(dane):
        dl = struct.unpack(">I", dane[o + 8:o + 12])[0]
        if dl < 12 or o + dl > len(dane):
            break
        yield o, dane[o:o + 4], dl
        o += dl


def hot_z_pliku(dane):
    """Hot cue [(numer 1.., ms)] z tagu PCOB (tryb 1) pliku DAT albo EXT."""
    for o, t, dl in tagi_anlz(dane):
        if t == b"PCOB" and struct.unpack(">I", dane[o + 12:o + 16])[0] == 1:
            p, wyn = o + struct.unpack(">I", dane[o + 4:o + 8])[0], []
            while p + 36 <= o + dl and dane[p:p + 4] == b"PCPT":
                wyn.append((struct.unpack(">I", dane[p + 12:p + 16])[0], struct.unpack(">I", dane[p + 32:p + 36])[0]))
                p += struct.unpack(">I", dane[p + 8:p + 12])[0]
            return wyn
    return []


def listy_cue(cue):
    """Z wyniku cue.wyznacz: (hot [(nr, ms, nazwa)], pamiec [(0, ms, nazwa)])."""
    hot = [(i + 1, ms, nazwa) for i, (ms, nazwa) in enumerate(cue.get("hot", [])[:8])]
    pam = [(0, ms, nazwa) for ms, nazwa in cue.get("pamiec", [])[:10]]
    return hot, pam


# --- trzeci plik analizy ANLZ0000.2EX: waveform 3-pasmowy (PWV7 szczegolowy, PWV6 podglad) ------------------
# Uklad i skale dopasowane do eksportu rekordboxa (15 utworow, wzorzec 2026-11-02): bajty kolejno pasmo niskie,
# srednie, wysokie; niskie i srednie wyrownane do glosnosci utworu, wysokie bezwzgledne. PWV6 to srednie z PWV7
# w 1200 odcinkach, kazde pasmo przeskalowane do wspolnej sredniej (0,57 sredniej pasma niskiego).
PWV7_SKALA = ((80.94, 0.503), (69.49, 0.817), (881.98, 1.759))
PWVC_K = (30.77, 21.07, 12.12)
PVDI_STALY = bytes.fromhex("505644490000001800000018" "000004005622000100000000")


def trzy_pasma(cechy):
    """(n, 3) bajty 0..127 na 1/150 s z cech koloru (rms niskie, srednie, wysokie)."""
    wyj = []
    for j, (a, p) in enumerate(PWV7_SKALA):
        f = np.asarray(cechy[:, j], dtype=np.float64)
        if j < 2:
            odn = np.percentile(f, 95) if len(f) else 0.0
            f = f / odn if odn > 1e-9 else np.zeros_like(f)
        wyj.append(np.clip(np.round(a * np.maximum(f, 0.0) ** p), 0, 127))
    return np.stack(wyj, axis=1).astype(np.uint8)


def tag_pwv7(w):
    body = np.ascontiguousarray(w, dtype=np.uint8).tobytes()
    return b"PWV7" + struct.pack(">III", 24, 24 + len(body), 3) + struct.pack(">II", len(w), 0x00960000) + body


def tag_pwv6(w, n_wyj=1200):
    n = len(w)
    w = np.asarray(w, dtype=np.float64)
    if n == 0:
        sr = np.zeros((n_wyj, 3))
    else:
        gr = np.minimum((np.arange(n) * n_wyj) // n, n_wyj - 1)
        ile = np.maximum(np.bincount(gr, minlength=n_wyj), 1)
        sr = np.stack([np.bincount(gr, w[:, j], n_wyj) / ile for j in range(3)], axis=1)
        wspolna = 0.57 * w[:, 0].mean()
        sred = sr.mean(axis=0)
        sr = sr * np.where(sred > 1e-9, wspolna / np.maximum(sred, 1e-9), 0.0)
    body = np.clip(np.round(sr), 0, 127).astype(np.uint8).tobytes()
    return b"PWV6" + struct.pack(">III", 20, 20 + len(body), 3) + struct.pack(">I", n_wyj) + body


def tag_pwvc(cechy):
    """PWVC: trzy wartosci zalezne od glosnosci pasm (w rekordboxie ok. 80-160; odwrotnie do rms pasma)."""
    v = []
    for j, k in enumerate(PWVC_K):
        rms = float(np.sqrt(np.mean(np.square(cechy[:, j])))) if len(cechy) else 0.0
        v.append(int(np.clip(round(k / rms), 80, 200)) if rms > 1e-9 else 100)
    return b"PWVC" + struct.pack(">II", 14, 20) + struct.pack(">HHHH", 0, *v)


def zbuduj_2ex(sciezka_na_pendrive, cechy):
    """Plik .2EX jak w eksporcie rekordboxa: PPTH, PWV7, PWV6, PWVC, PVDI."""
    NAGLOWEK_PMAI = bytes.fromhex("504d41490000001c00000000" "00000001000100000001000000000000")
    raw = (sciezka_na_pendrive + "\x00").encode("utf-16-be")
    ppth = b"PPTH" + struct.pack(">III", 16, 16 + len(raw), len(raw)) + raw
    w = trzy_pasma(cechy)
    calosc = NAGLOWEK_PMAI + ppth + tag_pwv7(w) + tag_pwv6(w) + tag_pwvc(cechy) + PVDI_STALY
    return calosc[:8] + struct.pack(">I", len(calosc)) + calosc[12:]


def zbuduj_ext(sciezka_na_pendrive, y, dlugosc_s, czasy_s=None, bpm=None, cechy=None, cue=None):
    """Zbuduj plik .EXT (PPTH, PWV3, cue, PQT2, kolorowy waveform PWV5/PWV4)."""
    NAGLOWEK_PMAI = bytes.fromhex("504d41490000001c00000000" "00000001000100000001000000000000")
    raw = (sciezka_na_pendrive + "\x00").encode("utf-16-be")
    ppth = b"PPTH" + struct.pack(">III", 16, 16 + len(raw), len(raw)) + raw
    if cue:
        hot, pam = listy_cue(cue)
        cue_tagi = [tag_pcob(1, [(n, ms) for n, ms, _ in hot]), tag_pcob(0, [(0, ms) for _, ms, _ in pam]),
                    tag_pco2(1, hot), tag_pco2(0, pam)]
    else:
        cue_tagi = [PCOB_EXT_PAMIEC, PCOB_EXT_HOT, PCO2_EXT_PAMIEC, PCO2_EXT_HOT]
    tagi = [ppth, tag_pwv3(y, dlugosc_s)] + cue_tagi
    if czasy_s is not None and bpm:
        tagi.append(tag_pqt2(czasy_s, bpm))
    try:
        if cechy is None:
            cechy = _cechy_koloru(y, dlugosc_s)
        tagi.append(tag_pwv5(cechy))
        tagi.append(tag_pwv4(cechy))
    except Exception:  # noqa: BLE001  # kolor jest dodatkiem: bez niego zostaje niebieski waveform
        pass
    calosc = NAGLOWEK_PMAI + b"".join(tagi)
    return calosc[:8] + struct.pack(">I", len(calosc)) + calosc[12:]


def zbuduj_dat(sciezka_na_pendrive: str, bpm: float, czasy_s: np.ndarray,
               y: np.ndarray, dlugosc_s: float, total_probek: int,
               pwav_wynik=None, cue=None) -> bytes:
    """Zbuduj plik .DAT."""
    pwav, wys = pwav_wynik if pwav_wynik is not None else tag_pwav(y, dlugosc_s)
    
    NAGLOWEK_PMAI = bytes.fromhex("504d41490000001c00000000" "00000001000100000001000000000000")
    
    def tag_ppth(sciezka):
        raw = (sciezka + "\x00").encode("utf-16-be")
        return b"PPTH" + struct.pack(">III", 16, 16 + len(raw), len(raw)) + raw
    
    def tag_pvbr(total_probek):
        body = b"\x00" * (4 * 400) + struct.pack(">I", total_probek)
        return b"PVBR" + struct.pack(">III", 16, 16 + len(body), 0) + body
    
    def tag_pcob_pamiec(czas_ms):
        PCOB_PAMIEC = bytes.fromhex(
            "50434f4200000018000000500000000100000001ffffffff"
            "504350540000001c00000038000000010000000000010000ffffffff"
            "010003e800000031ffffffff" + "00" * 16)
        t = bytearray(PCOB_PAMIEC)
        struct.pack_into(">I", t, 56, int(czas_ms))
        return bytes(t)
    
    PCOB_PUSTY = bytes.fromhex("50434f4200000018000000180000000000000000ffffffff")
    
    if cue:
        hot, pam = listy_cue(cue)
        cue_tagi = [tag_pcob(1, [(n, ms) for n, ms, _ in hot[:3]]), tag_pcob(0, [(0, ms) for _, ms, _ in pam])]
    else:
        cue_tagi = [tag_pcob_pamiec(round(czasy_s[0] * 1000)), PCOB_PUSTY]
    tagi = [tag_ppth(sciezka_na_pendrive), tag_pvbr(total_probek), tag_pqtz(czasy_s, bpm),
            pwav, tag_pwv2(wys)] + cue_tagi
    calosc = NAGLOWEK_PMAI + b"".join(tagi)
    return calosc[:8] + struct.pack(">I", len(calosc)) + calosc[12:]
