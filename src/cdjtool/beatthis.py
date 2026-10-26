"""Beat This! (CPJKU, ISMIR 2024, MIT): beaty i pierwsze uderzenia taktow z sieci neuronowej (ONNX Runtime).

Model (wariant small, ~10 MB) i filtr mel leza w katalogu models/ obok programu. Gdy ONNX Runtime albo
pliki modelu sa niedostepne, `dostepny()` zwraca False, a program uzywa algorytmu zapasowego.
Przetwarzanie wstepne zgodne z oryginalem: mono (srednia), 22050 Hz, STFT 1024/441, mel 128, log1p(1000 x).
"""
import os
import threading
import numpy as np

FS = 22050
N_FFT = 1024
HOP = 441
OKNO = 1500        # ramek na okno modelu (~30 s)
BRZEG = 6          # ramek brzegowych odrzucanych przy sklejaniu okien
FPS = FS / HOP     # 50 ramek / s

_STAN = {"sess": None, "mel": None, "blad": None}
_BLOKADA = threading.Semaphore(int(os.environ.get("CDJ_BT_PAR", "2")))  # ile inferencji naraz (RAM i rdzenie)
NORMA = os.environ.get("CDJ_BT_NORM", "sqrtnfft")  # sqrtnfft (musetric) albo okno (torchaudio frame_length)


def _katalog():
    return os.environ.get("CDJ_MODELS_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")


def _sciezki():
    k = _katalog()
    return os.path.join(k, "beat_this.onnx"), os.path.join(k, "mel-filterbank.bin")


def dostepny():
    if _STAN["sess"] is not None:
        return True
    if _STAN["blad"] is not None:
        return False
    try:
        import onnxruntime as ort
        model, mel = _sciezki()
        if not (os.path.isfile(model) and os.path.isfile(mel)):
            raise FileNotFoundError("brak plikow modelu w " + _katalog())
        opcje = ort.SessionOptions()
        opcje.intra_op_num_threads = max(1, int(os.environ.get("CDJ_BT_WATKI", min(4, os.cpu_count() or 2))))
        opcje.log_severity_level = 3
        _STAN["sess"] = ort.InferenceSession(model, opcje, providers=["CPUExecutionProvider"])
        _STAN["mel"] = np.fromfile(mel, dtype="<f4").reshape(N_FFT // 2 + 1, 128)
        return True
    except Exception as e:  # noqa: BLE001
        _STAN["blad"] = "%s: %s" % (type(e).__name__, e)
        return False


def blad():
    return _STAN["blad"]


def spektrogram(y):
    """Spektrogram log-mel (ramki x 128) jak w Beat This!; y: mono float, 22050 Hz."""
    from scipy.signal import get_window
    y = np.asarray(y, dtype=np.float32)
    okno = get_window("hann", N_FFT, fftbins=True).astype(np.float32)
    dziel = np.sqrt(N_FFT) if NORMA == "sqrtnfft" else np.sqrt(float((okno ** 2).sum()))
    p = np.pad(y, (N_FFT // 2, N_FFT // 2), mode="reflect")
    n = 1 + len(y) // HOP
    mel = _STAN["mel"]
    wyj = np.empty((n, 128), dtype=np.float32)
    krok = 2048
    for a in range(0, n, krok):
        b = min(n, a + krok)
        idx = (np.arange(a, b)[:, None] * HOP) + np.arange(N_FFT)[None, :]
        mag = np.abs(np.fft.rfft(p[idx] * okno, axis=1)).astype(np.float32) / dziel
        wyj[a:b] = np.log1p(1000.0 * (mag @ mel))
    return wyj


def _wybierz_okna(spekt, ile):
    """Indeksy kawalkow (po krok ramek) o najmocniejszym basie: tam jest groove z kickiem,
    wystarczajacy do glosowania fazy taktu. ile=None -> wszystkie."""
    krok = OKNO - 2 * BRZEG
    n = len(spekt)
    liczba = (n + krok - 1) // krok
    if not ile or liczba <= ile:
        return list(range(liczba))
    baza = [float(spekt[i * krok:(i + 1) * krok, :24].mean()) for i in range(liczba)]
    ok = sorted(range(liczba), key=lambda i: -baza[i])[:ile]
    return sorted(ok)


def _logity(spekt, okna=None):
    """Uruchamia model na oknach 1500 ramek z brzegiem 6 i skleja logity beat/downbeat.
    okna: lista indeksow kawalkow do policzenia (reszta zostaje zerem)."""
    sess = _STAN["sess"]
    nazwa = sess.get_inputs()[0].name
    n = len(spekt)
    pad = np.concatenate([np.zeros((BRZEG, 128), np.float32), spekt,
                          np.zeros((OKNO + BRZEG, 128), np.float32)])
    krok = OKNO - 2 * BRZEG
    beat = np.zeros(n, np.float32)
    down = np.zeros(n, np.float32)
    wybrane = None if okna is None else set(okna)
    start = 0  # indeks w spekt (okno obejmuje start-BRZEG ... start-BRZEG+OKNO)
    while start < n:
        if wybrane is not None and (start // krok) not in wybrane:
            start += krok
            continue
        kawalek = pad[start:start + OKNO][None]  # pad ma przesuniecie BRZEG
        wb, wd = sess.run(None, {nazwa: kawalek.astype(np.float32)})
        k = min(krok, n - start)
        beat[start:start + k] = wb[0, BRZEG:BRZEG + k]
        down[start:start + k] = wd[0, BRZEG:BRZEG + k]
        start += krok
    return beat, down


def _szczyty(logit, prog=0.0, jadro=7):
    """Maksima lokalne (okno 7 ramek) o logicie > 0; sasiednie (<=1 ramka) sa usredniane."""
    n = len(logit)
    pol = jadro // 2
    p = np.pad(logit, (pol, pol), constant_values=-1e9)
    maks = np.max(np.lib.stride_tricks.sliding_window_view(p, jadro), axis=1)
    idx = np.where((logit == maks) & (logit > prog))[0]
    if len(idx) == 0:
        return np.array([], dtype=np.float64)
    grupy, cur = [], [idx[0]]
    for i in idx[1:]:
        if i - cur[-1] <= 1:
            cur.append(i)
        else:
            grupy.append(cur)
            cur = [i]
    grupy.append(cur)
    return np.array([np.mean(g) for g in grupy])


def analizuj(y, ile_okien=None):
    """Zwraca {'beaty': czasy [s], 'takty': czasy pierwszych uderzen [s]} (okno 20 ms). Wymaga dostepny()."""
    y = np.asarray(y, dtype=np.float32) * 0.70710678  # ffmpeg miesza do mono (L+R)/sqrt2; model wymaga sredniej
    spekt = spektrogram(y)
    okna = _wybierz_okna(spekt, ile_okien) if ile_okien else None
    with _BLOKADA:
        lb, ld = _logity(spekt, okna)
    beaty = _szczyty(lb) / FPS
    takty = _szczyty(ld) / FPS
    if len(beaty):  # takty przyciagnij do najblizszego beatu
        takty = np.array([beaty[int(np.argmin(np.abs(beaty - t)))] for t in takty])
        takty = np.unique(takty)
    return {"beaty": beaty, "takty": takty}
