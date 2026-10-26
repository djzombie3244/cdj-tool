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

    # Coarse pass
    grube = np.arange(BPM_MIN, BPM_MAX, 0.20)
    scores = []
    for x in grube:
        score, _, _ = wynik(x, krok_fazy=1.0)
        scores.append(score)
    bpm0 = grube[int(np.argmax(scores))]

    # Focused pass
    dok = np.arange(max(BPM_MIN, bpm0 - 0.30), min(BPM_MAX, bpm0 + 0.30) + 0.0001, 0.01)
    scores = [wynik(x)[0] for x in dok]
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
    try:
        takt_d = wykryj_takt(y, bpm, float(faza))
    except Exception:
        takt_d = 0
    wynik = {"bpm": bpm, "pierwszy": float(faza), "confidence": float(ostrosc),
             "reliable": True, "takt": int(takt_d), "takt_zrodlo": "heurystyka"}
    try:  # siec neuronowa Beat This!: faza taktu (czerwony pasek) i struktura utworu
        wynik.update(_takt_z_sieci(y, bpm, float(faza), takt_d))
    except Exception as e:  # noqa: BLE001  # bez sieci zostaje algorytm zapasowy
        wynik["siec_blad"] = "%s: %s" % (type(e).__name__, e)
    _profil().update(ref=None, w=None, t=None)  # zwolnij pamiec podreczna filtrow/profilu tego utworu
    _kick120().update(ref=None, e=None, c=None)
    return wynik


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
    for k in ([ile_okien, 2 * ile_okien, 4 * ile_okien, None] if ile_okien else [None]):
        wynik = _glosowanie_sieci(y, bpm, faza, BT, k)
        if "siec_blad" in wynik:
            return wynik
        g_ = wynik["takt_glosy"]
        if k is None or g_ and max(g_) >= 0.6 * sum(g_):
            return wynik
    return wynik


def _glosowanie_sieci(y, bpm, faza, BT, ile_okien):
    r = BT.analizuj(y, ile_okien)
    bt, bd = r["beaty"], r["takty"]
    okres = 60.0 / bpm
    if len(bt) < 16 or len(bd) < 8:
        return {"siec_blad": "za malo beatow z sieci"}
    d = ((bt - faza + okres / 2) % okres) - okres / 2
    med = float(np.median(d))
    idx = np.round((bd - med - faza) / okres).astype(int) % 4
    glosy = np.bincount(idx, minlength=4)
    wynik = {"beaty_sieci": bt, "takty_sieci": bd, "siec_przesuniecie_s": med,
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


class _Siatka(np.ndarray):
    numery = None

    def __array_finalize__(self, obj):
        self.numery = getattr(obj, "numery", None)


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
    bpm100 = int(round(bpm * 100))
    ms = [_ms_calk(t) for t in czasy_s]
    ulamki = [min(999, max(0, int(round((t * 1000.0 - m) * 1000.0)))) for t, m in zip(czasy_s, ms)]
    suma = sum(nr[i] + bpm100 + ms[i] for i in range(n)) & 0xFFFFFFFF
    body = b"".join(struct.pack(">H", u) for u in ulamki)
    hdr = struct.pack(">11I", 0, 0x02000002, 0,
                      (nr[0] << 16) | bpm100, ms[0], (nr[-1] << 16) | bpm100, ms[-1], n, suma, 0, 0)
    return b"PQT2" + struct.pack(">II", 56, 56 + len(body)) + hdr + body


def tag_pqtz(czasy_s: np.ndarray, bpm: float) -> bytes:
    """PQTZ tag (beat grid)."""
    n = len(czasy_s)
    body = b""
    numery = getattr(czasy_s, "numery", None)
    for i, t in enumerate(czasy_s):
        nr = int(numery[i]) if numery is not None else (i % 4) + 1
        body += struct.pack(">HHI", nr, int(round(bpm * 100)), _ms_calk(t))
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


def tag_pwv3(y, dlugosc_s):
    """PWV3: waveform szczegolowy, 150 probek/s, 1 bajt (3 bity jasnosc, 5 bitow wysokosc)."""
    calk = dlugosc_s + OFFSET_REKORDBOX_S
    n = int(round(calk * PWV3_PROBEK_NA_S))
    okno = SR / PWV3_PROBEK_NA_S
    idx = np.arange(n)
    a = np.clip(np.round(idx * okno - OFFSET_REKORDBOX_S * SR).astype(np.int64), 0, len(y))
    b = np.clip(np.round((idx + 1) * okno - OFFSET_REKORDBOX_S * SR).astype(np.int64), 0, len(y))
    cs = np.concatenate(([0.0], np.cumsum(np.asarray(y, dtype=np.float64) ** 2)))
    dl = np.maximum(b - a, 1)
    rms = np.where(b > a, np.sqrt(np.maximum(cs[b] - cs[a], 0.0) / dl), 0.0)
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
    from scipy.signal import butter, sosfiltfilt
    calk = dlugosc_s + OFFSET_REKORDBOX_S
    n = int(round(calk * PWV3_PROBEK_NA_S))
    okno = SR / PWV3_PROBEK_NA_S
    a = np.clip(np.round(np.arange(n) * okno - OFFSET_REKORDBOX_S * SR).astype(np.int64), 0, len(y))
    b = np.clip(np.round((np.arange(n) + 1) * okno - OFFSET_REKORDBOX_S * SR).astype(np.int64), 0, len(y))
    b = np.maximum(b, a)
    dl = np.maximum(b - a, 1)
    ny = SR / 2
    pasma = (butter(4, 200 / ny, "low", output="sos"), butter(4, [200 / ny, 2000 / ny], "band", output="sos"),
             butter(4, 2000 / ny, "high", output="sos"))
    wyj = []
    for sos in pasma:
        z = sosfiltfilt(sos, y.astype(np.float64))
        cs = np.concatenate(([0.0], np.cumsum(z * z)))
        wyj.append(np.sqrt(np.maximum(cs[b] - cs[a], 0.0) / dl))
    ay = np.abs(y)
    szczyt = np.zeros(n)
    ok = b > a
    if ok.any():
        # reduceat po poczatkach okien; okna sa ciagle i nie zachodza na siebie
        idx = a[ok]
        red = np.maximum.reduceat(ay, np.minimum(idx, len(ay) - 1)) if len(ay) else np.zeros(ok.sum())
        szczyt[ok] = red
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


def listy_cue(cue):
    """Z wyniku cue.wyznacz: (hot [(nr, ms, nazwa)], pamiec [(0, ms, nazwa)])."""
    hot = [(i + 1, ms, nazwa) for i, (ms, nazwa) in enumerate(cue.get("hot", [])[:8])]
    pam = [(0, ms, nazwa) for ms, nazwa in cue.get("pamiec", [])[:10]]
    return hot, pam


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
