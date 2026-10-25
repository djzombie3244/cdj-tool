"""Wykrywanie numeracji beatow w takcie (ktory beat siatki to '1')."""
import numpy as np
from scipy.signal import stft


def _pasma(y, SR):
    f, t, Z = stft(y, SR, nperseg=2048, noverlap=2048 - 512, boundary=None, padded=False)
    P = np.abs(Z) ** 2
    kraw = np.geomspace(40, 9000, 21)
    F = np.stack([P[(f >= kraw[i]) & (f < kraw[i + 1])].sum(0) for i in range(20)], 0)
    return t, np.log(F + 1e-6).T  # [ramki x pasma]


def cechy(y, SR, bpm, pierwszy):
    okres = 60.0 / bpm
    a0 = -int(pierwszy // okres)
    n = int((len(y) / SR - pierwszy) // okres) - a0
    t, F = _pasma(y, SR)
    tb = pierwszy + (np.arange(n) + a0) * okres
    fi = np.clip(np.searchsorted(t, tb), 0, len(t) - 1)
    c = np.vstack([np.zeros(F.shape[1]), np.cumsum(F, 0)])
    def sr(i0, i1):  # srednia profilu na ramkach [i0,i1)
        i0 = np.clip(i0, 0, len(t)); i1 = np.clip(i1, 0, len(t))
        return (c[i1] - c[i0]) / np.maximum(i1 - i0, 1)[:, None]
    ramek_beat = max(1, int(round(okres / (t[1] - t[0]))))
    nov = {}
    for k in (1, 2, 4):  # kontrast: nastepne k beatow vs poprzednie k beatow
        po = sr(fi, fi + k * ramek_beat)
        prz = sr(fi - k * ramek_beat, fi)
        nov[k] = np.abs(po - prz).mean(1)
        nov[k][:k] = 0; nov[k][-k:] = 0
    nisko = F[:, :6].mean(1)
    nov["bas"] = sr(fi, fi + ramek_beat // 2)[:, :6].mean(1) - sr(fi - ramek_beat // 2, fi)[:, :6].mean(1)
    nov["wys"] = sr(fi, fi + ramek_beat // 2)[:, 14:].mean(1) - sr(fi - ramek_beat // 2, fi)[:, 14:].mean(1)
    return a0, nov


def glosy(a0, x, kwant=0.85):
    """Wynik dla r=0..3: suma nadwyzki ponad prog w beatach o indeksie kotwicy = r (mod 4)."""
    n = len(x)
    idx = (np.arange(n) + a0) % 4
    prog = np.quantile(x, kwant)
    w = np.maximum(x - prog, 0)
    s = np.array([w[idx == r].sum() for r in range(4)])
    return s / (s.sum() + 1e-12)


def wykryj_takt(y, SR, bpm, pierwszy):
    """(r, pewnosc): r = indeks kotwicy (mod 4) beatu 'raz'."""
    a0, nov = cechy(y, SR, bpm, pierwszy)
    return a0, nov


def start_bitu(y, SR, bpm, pierwszy, a0):
    """Indeks beatu (kotwica pierwszy + a*okres, liczony od a0), w ktorym zaczyna sie glowny beat:
    pierwszy beat, po ktorym kick (bas <150 Hz) jest obecny w >=90% z nastepnych 16 beatow."""
    from scipy.signal import butter, sosfiltfilt
    okres = 60.0 / bpm
    sos = butter(4, 150 / (SR / 2), output="sos")
    e = sosfiltfilt(sos, y) ** 2
    c = np.concatenate(([0.0], np.cumsum(e)))
    n = int((len(y) / SR - pierwszy) // okres) - a0
    if n < 24:
        return None
    tb = pierwszy + (np.arange(n) + a0) * okres
    i0 = np.clip(((tb - 0.01) * SR).astype(int), 0, len(y))
    i1 = np.clip(((tb + 0.12) * SR).astype(int), 0, len(y))
    s = (c[i1] - c[i0]) / np.maximum(i1 - i0, 1)
    ok = s > 0.4 * np.percentile(s, 75)
    for i in range(n - 16):
        if ok[i] and ok[i:i + 16].mean() >= 0.9:
            return i
    return None
