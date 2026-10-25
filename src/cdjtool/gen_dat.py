#!/usr/bin/env python3
"""Analiza MP3: BPM, beatgrid, waveform — WERSJA NAPRAWIONA."""

import json
import os
import struct
import subprocess
import sys
import tempfile
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
                [FFMPEG, "-v", "error", "-i", sciezka, "-ac", "1", "-ar", str(SR),
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


def _zlozony_profil(on1: np.ndarray, bpm: float):
    """Histogram fazy obwiedni zlozony modulo okres; zwraca (ostrosc, faza_s)."""
    krok_ms = _HOP_MS * 1000.0 / SR
    okres = 60000.0 / bpm
    nb = int(round(okres * 2))
    ph = (((np.arange(len(on1)) + 0.5) * krok_ms) % okres) / okres
    h = np.bincount((ph * nb).astype(int) % nb, weights=on1, minlength=nb)
    k = 4
    hs = np.convolve(np.concatenate([h[-k:], h, h[:k]]), np.ones(2 * k + 1) / (2 * k + 1), "same")[k:-k]
    return hs.max() / (hs.mean() + 1e-12), (np.argmax(hs) + 0.5) / nb * okres / 1000.0


def _poczatek_kicka(y: np.ndarray, bpm: float, t0: float) -> float:
    """Przesuniecie (s) siatki t0+k*okres do poczatku narastania kicka (basu <120 Hz)."""
    okres = 60.0 / bpm
    sos = butter(4, 120 / (SR / 2), output="sos")
    e = np.abs(sosfiltfilt(sos, y))
    c = np.concatenate(([0.0], np.cumsum(e)))
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
    return {"bpm": bpm, "pierwszy": float(faza), "confidence": float(ostrosc),
            "reliable": True}


# ================================================================ DAT ---
def siatka_ms(bpm: float, pierwszy_s: float, dlugosc_s: float) -> np.ndarray:
    """Czasy beatów w ms (współrzędne Rekordbox)."""
    okres = 60.0 / bpm
    t0 = pierwszy_s + OFFSET_REKORDBOX_S - ATAK_PRZED_WZROSTEM_S
    while t0 - okres >= 0:
        t0 -= okres
    koniec = dlugosc_s + OFFSET_REKORDBOX_S
    return np.arange(t0, koniec - 0.05, okres)


def tag_pqtz(czasy_s: np.ndarray, bpm: float) -> bytes:
    """PQTZ tag (beat grid)."""
    n = len(czasy_s)
    body = b""
    for i, t in enumerate(czasy_s):
        body += struct.pack(">HHI", (i % 4) + 1, int(round(bpm * 100)), int(round(t * 1000)))
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
    dane = bytearray()
    for i in range(n):
        a = int(round(i * okno - OFFSET_REKORDBOX_S * SR))
        b = int(round((i + 1) * okno - OFFSET_REKORDBOX_S * SR))
        a, b = max(a, 0), min(b, len(y))
        rms = float(np.sqrt(np.mean(y[a:b] ** 2))) if b > a else 0.0
        h = int(min(31, max(0, round(40.5 * rms))))
        dane.append((5 << 5) | h)
    body = bytes(dane)
    return (b"PWV3" + struct.pack(">III", 24, 24 + len(body), 1)
            + struct.pack(">II", n, 0x00960000) + body)


def zbuduj_ext(sciezka_na_pendrive, y, dlugosc_s):
    """Zbuduj plik .EXT (PPTH, PWV3, puste znaczniki cue)."""
    NAGLOWEK_PMAI = bytes.fromhex("504d41490000001c00000000" "00000001000100000001000000000000")
    raw = (sciezka_na_pendrive + "\x00").encode("utf-16-be")
    ppth = b"PPTH" + struct.pack(">III", 16, 16 + len(raw), len(raw)) + raw
    tagi = [ppth, tag_pwv3(y, dlugosc_s), PCOB_EXT_PAMIEC, PCOB_EXT_HOT,
            PCO2_EXT_PAMIEC, PCO2_EXT_HOT]
    calosc = NAGLOWEK_PMAI + b"".join(tagi)
    return calosc[:8] + struct.pack(">I", len(calosc)) + calosc[12:]


def zbuduj_dat(sciezka_na_pendrive: str, bpm: float, czasy_s: np.ndarray,
               y: np.ndarray, dlugosc_s: float, total_probek: int,
               pwav_wynik=None) -> bytes:
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
    
    tagi = [tag_ppth(sciezka_na_pendrive), tag_pvbr(total_probek), tag_pqtz(czasy_s, bpm),
            pwav, tag_pwv2(wys), tag_pcob_pamiec(round(czasy_s[0] * 1000)), PCOB_PUSTY]
    calosc = NAGLOWEK_PMAI + b"".join(tagi)
    return calosc[:8] + struct.pack(">I", len(calosc)) + calosc[12:]
