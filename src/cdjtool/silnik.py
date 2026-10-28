#!/usr/bin/env python3
"""Silnik: Analiza MP3 i zapis dla CDJ-2000 — WERSJA NAPRAWIONA."""

import datetime
import concurrent.futures
import json
import os
import time
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unicodedata
import zlib
from typing import Dict, Tuple, Optional, List

import numpy as np
from scipy.signal import butter, filtfilt

KAT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, KAT)

import gen_dat as g
import pdb_writer as pw
from exceptions import (
    CDJError, FFmpegError, InvalidMP3Error, DiskSpaceError, PathTooLongError,
    ProcessTimeoutError, BPMConfidenceError, NoAudioFilesError
)

FFMPEG = os.environ.get("CDJ_FFMPEG", "ffmpeg")
WZORZEC = os.path.join(KAT, "wzorzec")

# Załaduj konfigurację
CONFIG_PATH = os.path.join(KAT, "config.json")
try:
    with open(CONFIG_PATH) as f:
        CONFIG = json.load(f)
except FileNotFoundError:
    CONFIG = {
        "limits": {"max_path_length": 255, "artist_max_chars": 60},
        "performance": {"max_workers": 8, "ffmpeg_timeout_sec": 300},
        "analysis": {"confidence_threshold": 0.1}
    }


# ================================================================ Utility ---
def bezpieczna_nazwa(s: str) -> str:
    """Bezpieczne nazwy folderow (jak w oryginale: bez ucinania)."""
    s = re.sub(r'[\\/:*?"<>|]', "_", s).strip()
    return s or "Unknown"


def sciezka_na_pendrive(meta: Dict, nazwa_pliku: str) -> str:
    """Sciezka MP3 na pendrive - dokladnie jak w oryginale (nazwa pliku ucieta do 44 ZNAKOW)."""
    # macOS zwraca nazwy plikow rozlozone (NFD: "n" + akcent), rekordbox zapisuje
    # zlozone (NFC). Bez normalizacji ucinanie do 44 znakow i hash sciezki sie rozjezdzaja.
    nazwa_pliku = unicodedata.normalize("NFC", nazwa_pliku)
    stem, ext = os.path.splitext(nazwa_pliku)
    artysta = unicodedata.normalize("NFC", meta["artysta"])
    album = unicodedata.normalize("NFC", meta["album"])
    return "/Contents/%s/%s/%s%s" % (bezpieczna_nazwa(artysta), bezpieczna_nazwa(album), stem[:44], ext)


ZAREZERWOWANE_FOLDERY = {"contents", "pioneer", "system volume information", ".trashes", ".spotlight-v100", ".fseventsd"}


def sciezka_wg_folderow(nazwa_folderu: str, nazwa_wzgledna: str) -> str:
    """Sciezka na pendrive odwzorowujaca uklad folderow zrodlowych: /<folder>/<podfoldery>/<plik>.

    Folder jest na glownym poziomie pendrive'a, wiec CDJ pokazuje go w widoku FOLDER
    (CDJ ukrywa tam foldery Contents i PIONEER)."""
    nazwa_wzgledna = unicodedata.normalize("NFC", nazwa_wzgledna).replace(os.sep, "/")
    czesci = nazwa_wzgledna.split("/")
    stem, ext = os.path.splitext(czesci[-1])
    glowny = bezpieczna_nazwa(unicodedata.normalize("NFC", nazwa_folderu)).lstrip(".") or "Tracks"
    if glowny.lower() in ZAREZERWOWANE_FOLDERY:
        glowny += " (muzyka)"
    katalogi = [glowny] + [bezpieczna_nazwa(c) for c in czesci[:-1]]
    return "/%s/%s%s" % ("/".join(katalogi), stem[:44], ext)


def sciezka_analizy(sciezka_utworu: str, przesuniecie: int = 0) -> str:
    """Hash ścieżki jak robi to Pioneer CDJ. przesuniecie > 0 daje kolejny wolny katalog (gdy dwa utwory trafią do tego samego)."""
    h = 0
    raw = sciezka_utworu.encode("utf-16-be")
    for i in range(0, len(raw), 2):
        c = int.from_bytes(raw[i:i + 2], "big")
        tmp = (h * 0x5BC9 + c) & 0xFFFFFFFF
        h = (tmp * 0x93B5 + c) & 0xFFFFFFFF
    h = (h % 200003 + przesuniecie) % 200003
    p = 0
    for out_bit, source_bit in enumerate((0, 2, 6, 7, 9, 13, 16)):
        p |= ((h >> source_bit) & 1) << out_bit
    return f"/PIONEER/USBANLZ/P{p:03X}/{h:08X}/ANLZ0000.DAT"


# Formaty odtwarzane przez zwykle CDJ-2000: MP3, AAC (m4a), WAV, AIFF (ALAC - nie).
# FLAC gra tylko CDJ-2000 z modem rafttone.com/cdj2000 (stereo, 16/24 bit, 44,1/48 kHz), wiec jest opcjonalny.
ROZSZERZENIA_AUDIO = (".mp3", ".m4a", ".aac", ".wav", ".aif", ".aiff")
ROZSZERZENIE_FLAC = ".flac"
FLAC = {"on": os.environ.get("CDJ_FLAC") == "1"}  # ustawiane z opcji w interfejsie (przetworz)


def rozszerzenia():
    return ROZSZERZENIA_AUDIO + ((ROZSZERZENIE_FLAC,) if FLAC["on"] else ())


RODZINA_ROZSZERZEN = {".mp3": "mp3", ".m4a": "aac", ".aac": "aac", ".wav": "pcm", ".aif": "pcm", ".aiff": "pcm",
                      ".flac": "flac"}
KODEKI_PCM = {"pcm_s16le": 16, "pcm_s16be": 16, "pcm_s24le": 24, "pcm_s24be": 24}
# wartosc pola "file type" w wierszu utworu rekordboxa (pdb): 1 MP3, 4 M4A, 5 FLAC, 11 WAV, 12 AIFF
TYP_PLIKU = {".mp3": 1, ".m4a": 4, ".aac": 4, ".flac": 5, ".wav": 11, ".aif": 12, ".aiff": 12}


def pliki_mp3(folder: str) -> List[str]:
    """Zwróć pliki audio (MP3/AAC/WAV/AIFF) z podfolderów (sorted, pominąć metadata)."""
    pliki = []
    for dp, dn, fn in os.walk(folder):
        dn[:] = sorted(d for d in dn if not d.startswith(".") and d != "PIONEER")
        for nazwa in fn:
            pelna = os.path.join(dp, nazwa)
            if (nazwa.lower().endswith(rozszerzenia()) and not nazwa.startswith(".")
                and not os.path.islink(pelna)):
                pliki.append(os.path.relpath(pelna, folder))
    return sorted(pliki)


# ============================================================ Pamiec RAM ---
import threading

SPOOL = {"katalog": None}
BRAMKA = {"obj": None}
MAKS_MIN_UTWORU = 45          # dluzsze utwory pomijamy (zbyt duze zuzycie pamieci)
BAJTOW_NA_PROBKE = 4 * 16     # float32 x ~16 (zmierzony szczyt analizy ~14.7x dlugosci audio)


def _na_wolumenie(root):
    """Czy katalog jest na pendrive (macOS: /Volumes/..., Windows: litera dysku wymiennego)."""
    if sys.platform == "win32":
        import winplat
        return winplat.jest_pendrive(os.path.splitdrive(os.path.abspath(root))[0] + "\\")
    return root.startswith("/Volumes/")


def ram_mb():
    """(calkowity, dostepny) RAM w MB; dostepny = wolny + do odzyskania. Bez zaleznosci zewnetrznych."""
    try:
        if sys.platform == "win32":
            import winplat
            return winplat.ram_mb()
        if sys.platform == "darwin":
            calk = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e6
            out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
            m = re.search(r"page size of (\d+) bytes", out)
            ps = int(m.group(1)) if m else 16384
            def stron(nazwa):
                mm = re.search(nazwa + r":\s+(\d+)", out)
                return int(mm.group(1)) if mm else 0
            dost = (stron("Pages free") + stron("Pages inactive") + stron("Pages speculative")
                    + stron("Pages purgeable")) * ps / 1e6
            return calk, dost
        with open("/proc/meminfo") as f:
            t = {l.split(":")[0]: int(l.split()[1]) for l in f if ":" in l}
        return t["MemTotal"] / 1e3, t.get("MemAvailable", t["MemFree"]) / 1e3
    except Exception:  # noqa: BLE001
        return 4096.0, 2048.0


def budzet_ram_mb(procent=None):
    """Ile RAM wolno zajac analizie: ulamek dostepnej pamieci, nie wiecej niz polowa calkowitej."""
    procent = procent if procent is not None else CONFIG.get("performance", {}).get("ram_procent", 35)
    calk, dost = ram_mb()
    return max(300.0, min(dost * procent / 100.0, calk * 0.5))


class BramkaPamieci:
    """Wpuszcza zadania, dopoki suma ich szacunkowej pamieci miesci sie w budzecie."""

    def __init__(self, budzet_bajtow):
        self.budzet = int(budzet_bajtow)
        self.uzyte = 0
        self.szczyt = 0
        self.rownolegle = 0
        self.szczyt_rownolegle = 0
        self.cv = threading.Condition()

    def zajmij(self, bajty):
        with self.cv:
            while self.uzyte > 0 and self.uzyte + bajty > self.budzet:
                self.cv.wait()
            self.uzyte += bajty
            self.rownolegle += 1
            self.szczyt = max(self.szczyt, self.uzyte)
            self.szczyt_rownolegle = max(self.szczyt_rownolegle, self.rownolegle)

    def zwolnij(self, bajty):
        with self.cv:
            self.uzyte -= bajty
            self.rownolegle -= 1
            self.cv.notify_all()


def szacunek_bajtow(czas_s):
    return int(czas_s * g.SR * BAJTOW_NA_PROBKE) + 20 * 1024 * 1024


# ================================================================ Metadane ---
def metadane(sciezka: str) -> Dict[str, any]:
    """Ekstrahuj metadata MP3 (bezpiecznie z timeoutem)."""
    try:
        ffmpeg_timeout = CONFIG.get("performance", {}).get("ffmpeg_timeout_sec", 300)
        
        with tempfile.TemporaryDirectory(prefix="cdj-tags-") as temp_dir:
            tagi_path = os.path.join(temp_dir, "tags.ffmeta")
            
            # Uruchom FFmpeg z timeout
            try:
                r = subprocess.run(
                    [FFMPEG, "-hide_banner", "-v", "info", "-i", sciezka,
                     "-map_metadata", "0", "-f", "ffmetadata", tagi_path],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=ffmpeg_timeout
                )
            except subprocess.TimeoutExpired:
                raise ProcessTimeoutError("FFmpeg", ffmpeg_timeout)
            except FileNotFoundError:
                raise FFmpegError(f"FFmpeg nie znaleziony: {FFMPEG}")
            
            # Czytaj tagi
            try:
                with open(tagi_path, "rb") as f:
                    surowe = f.read()
                try:
                    wyjscie_tagi = surowe.decode("utf-8")
                except UnicodeDecodeError:
                    # tagi WAV/AIFF bywaja zapisane w stronie kodowej systemu (np. polskie znaki w cp1250)
                    wyjscie_tagi = surowe.decode("cp1250", "replace")
            except OSError:
                wyjscie_tagi = ""
            
            tagi = {}
            for linia in wyjscie_tagi.splitlines()[1:]:
                if linia.startswith("["):
                    break
                if linia.startswith(";") or "=" not in linia:
                    continue
                k, v = linia.split("=", 1)
                tagi.setdefault(k.lower(), v.strip())
        
        # Parsuj stderr
        i = r.stderr
        m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", i)
        if not m:
            if sciezka.lower().endswith(ROZSZERZENIE_FLAC):
                raise InvalidMP3Error(os.path.basename(sciezka), "ten ffmpeg nie obsługuje FLAC (zbuduj go ponownie: tools/build_ffmpeg.sh)")
            raise InvalidMP3Error(os.path.basename(sciezka), "Nie można odczytać czasu trwania")
        
        dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        
        # Parsuj audio info
        linia_audio = next((l for l in i.splitlines() if "Audio:" in l), "")
        kodek = re.search(r"Audio:\s*(\w+)", linia_audio)
        sr = re.search(r"(\d+)\s*Hz", linia_audio)
        br = re.search(r"(\d+)\s*kb/s", linia_audio)
        if not br:
            br = re.search(r"bitrate:\s*(\d+)\s*kb/s", i)
        
        kodek = kodek.group(1) if kodek else ""
        sr = int(sr.group(1)) if sr else 44100
        br = int(br.group(1)) if br else 0
        
        # Walidacja formatu (CDJ-2000: MP3, AAC, WAV, AIFF)
        nazwa_pliku = os.path.basename(sciezka)
        ext = os.path.splitext(nazwa_pliku)[1].lower()
        rodzina = RODZINA_ROZSZERZEN.get(ext)
        if kodek == "alac":
            raise InvalidMP3Error(nazwa_pliku, "ALAC nie jest obsługiwany przez CDJ-2000 (użyj MP3, AAC, WAV lub AIFF)")
        if kodek == "flac" and not FLAC["on"]:
            raise InvalidMP3Error(nazwa_pliku, "FLAC gra tylko CDJ-2000 z modem (rafttone.com/cdj2000); włącz opcję FLAC albo użyj MP3, AAC, WAV lub AIFF")
        if kodek == "flac":
            kodek_rodzina = "flac"
            if "stereo" not in linia_audio:
                raise InvalidMP3Error(nazwa_pliku, "FLAC w modzie CDJ-2000: tylko stereo")
            if re.search(r"\bs32 \(24 bit\)", linia_audio):
                bity = 24
            elif re.search(r"\bs16\b", linia_audio):
                bity = 16
            else:
                raise InvalidMP3Error(nazwa_pliku, "FLAC w modzie CDJ-2000: tylko 16 lub 24 bity")
        elif kodek == "mp3":
            kodek_rodzina, bity = "mp3", 16
        elif kodek == "aac":
            kodek_rodzina, bity = "aac", 16
        elif kodek in KODEKI_PCM:
            kodek_rodzina, bity = "pcm", KODEKI_PCM[kodek]
        elif kodek.startswith("pcm_"):
            raise InvalidMP3Error(nazwa_pliku, f"Format {kodek} nie jest obsługiwany (CDJ-2000: WAV/AIFF 16 lub 24 bity)")
        else:
            raise InvalidMP3Error(nazwa_pliku, f"Format: {kodek} nie jest obsługiwany przez CDJ-2000")
        if rodzina != kodek_rodzina:
            raise InvalidMP3Error(nazwa_pliku, f"Rozszerzenie {ext} nie pasuje do zawartości ({kodek})")
        dozwolone_sr = (44100, 48000, 32000, 22050) if kodek_rodzina == "mp3" else (44100, 48000)
        if sr not in dozwolone_sr:
            raise InvalidMP3Error(
                nazwa_pliku,
                f"Sample rate {sr} Hz nie obsługiwany"
            )
        
        # Rozmiar i ID3
        rozmiar = os.path.getsize(sciezka)
        with open(sciezka, "rb") as f:
            glowa = f.read(10)
            id3 = 0
            if glowa[:3] == b"ID3":
                s = glowa[6:10]
                id3 = ((s[0] & 0x7F) << 21) | ((s[1] & 0x7F) << 14) | \
                      ((s[2] & 0x7F) << 7) | (s[3] & 0x7F)
                id3 += 10
            f.seek(max(0, rozmiar - 128))
            id3v1 = 128 if f.read(3) == b"TAG" else 0
        
        # Frame count
        if kodek == "mp3" and br and sr:
            ramki = int((rozmiar - id3 - id3v1) / (144.0 * br * 1000 / sr))
            total_probek = ramki * 1152
        else:
            total_probek = int(round(dur * sr))
        if not br and dur > 0:
            br = int(rozmiar * 8 / dur / 1000)
        
        def liczba(klucz):
            mm = re.match(r"(\d+)", tagi.get(klucz, "") or "")
            return int(mm.group(1)) if mm else 0
        
        return {
            "tytul": tagi.get("title") or os.path.splitext(os.path.basename(sciezka))[0],
            "artysta": tagi.get("artist") or "Unknown Artist",
            "album": tagi.get("album") or "Unknown Album",
            "rok": liczba("date") or liczba("year"),
            "nr": liczba("track"),
            "dysk": liczba("disc"),
            "sr": sr,
            "bitrate": br,
            "czas": dur,
            "rozmiar": rozmiar,
            "total_probek": total_probek,
            "bity": bity,
            "typ_pliku": TYP_PLIKU[ext],
            "kodek": kodek
        }
    
    except (InvalidMP3Error, FFmpegError, PathTooLongError, ProcessTimeoutError):
        raise
    except Exception as e:
        raise InvalidMP3Error(os.path.basename(sciezka), str(e))


# ================================================================ Szablony ---
def szablon_utworu(pdb):
    """Odczytaj template trackowy z PDB."""
    w = pw.Wzorzec(pdb)
    p = w.page(w.chain(0)[-1])
    n, offs = pw.czytaj_wiersze(p)
    gr = sorted(set(offs)) + [struct.unpack("<H", p[30:32])[0]]
    o = offs[0]
    nxt = [b for b in gr if b > o][0]
    return pw.parse_track_row(p[pw.HDR + o:pw.HDR + nxt])


def podglad(y, czasy_audio, wys):
    """Dane do rysowania w oknie: 6 s z siatką beatów i waveformem."""
    sr = g.SR
    seg = y[:int(6 * sr)]
    b, a = butter(4, 150 / (sr / 2), btype="low")
    low = filtfilt(b, a, seg) if len(seg) > 30 else seg
    bins = 600
    w = max(1, len(seg) // bins)
    n = len(seg) // w
    env = np.abs(seg[:n * w]).reshape(n, w).max(axis=1)
    envl = np.abs(low[:n * w]).reshape(n, w).max(axis=1)
    return {
        "env": [round(float(x), 3) for x in env],
        "low": [round(float(x), 3) for x in envl],
        "krok": w / sr,
        "beats": [round(float(t), 4) for t in czasy_audio if 0 <= t < 6.0],
        "pwav": [int(h) for h in wys]
    }


def kopia_zapasowa_pdb(sciezka_pdb, korzen):
    """Kopia samego export.pdb (maly plik) przed dopisaniem nowych utworow."""
    dzis = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    nazwa = re.sub(r'[/\\]', "_", os.path.basename(korzen.rstrip("/")) or "pendrive")
    cel = os.path.join(os.path.expanduser("~"), "Documents", "CDJ-Tool-kopie", f"{nazwa}_{dzis}_baza")
    os.makedirs(cel, exist_ok=True)
    shutil.copy2(sciezka_pdb, os.path.join(cel, "export.pdb"))
    return cel


def kopia_zapasowa(pioneer, korzen):
    """Backup starego PIONEER folder."""
    dzis = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    nazwa = re.sub(r'[/\\]', "_", os.path.basename(korzen.rstrip("/")) or "pendrive")
    cel = os.path.join(os.path.expanduser("~"), "Documents", "CDJ-Tool-kopie", f"{nazwa}_{dzis}")
    os.makedirs(cel, exist_ok=True)
    
    def kopiuj_plik_z_pominieciem_sidecar(src, dst):
        try:
            return shutil.copy2(src, dst)
        except FileNotFoundError:
            if os.path.basename(src).startswith("._"):
                return dst
            raise

    try:
        shutil.copytree(
            pioneer,
            os.path.join(cel, "PIONEER"),
            ignore=shutil.ignore_patterns("._*", ".DS_Store"),
            copy_function=kopiuj_plik_z_pominieciem_sidecar,
        )
    except (shutil.Error, OSError) as e:
        raise CDJError(
            "backup failed: %s" % str(e)[:300],
            user_message=("Nie można odczytać dotychczasowego folderu PIONEER z pendrive'a (kopia zapasowa się nie powiodła). "
                          "Najczęściej oznacza to uszkodzony system plików, np. po przerwanym zapisie lub wyjęciu pendrive'a bez wysunięcia. "
                          "Sformatuj pendrive (krok 2: Formatowanie pendrive'a) albo napraw go w Narzędziu dyskowym (Pierwsza pomoc), potem spróbuj ponownie. "
                          "Nic nie zostało zmienione na pendrivie."),
            code="BACKUP_FAILED",
        ) from e
    return cel


_T0 = time.time()


def _t():
    """Znacznik czasu od startu zadania (do diagnozy wolnego zapisu)."""
    return "  [+%d s]" % (time.time() - _T0)


# ================================================================ Analiza ---
def _poczatek_dzwieku_ms(y, sr, prog_db=-60.0, okno_s=0.005):
    """Pierwszy moment, w ktorym dzwiek przekracza prog (jak Auto Cue w CDJ, domyslnie -60 dB); ms lub None."""
    import numpy as np
    n = max(1, int(sr * okno_s))
    ile = len(y) // n
    if ile < 2:
        return None
    rms = np.sqrt(np.mean(np.square(y[: ile * n].reshape(ile, n)), axis=1))
    idx = np.nonzero(rms > 10 ** (prog_db / 20.0))[0]
    return int(idx[0] * n * 1000.0 / sr) if len(idx) else None


def _cue_od_dzwieku(cue, y, g):
    """Glowny CUE (hot A i pamiec 'Start') ustawiony na poczatku dzwieku, tam gdzie sam staje CDJ (Auto Cue):
    inaczej odtwarzacz po zaladowaniu skacze do hot cue, a potem wraca na poczatek dzwieku."""
    if not cue:
        return cue
    ms = _poczatek_dzwieku_ms(y, g.SR)
    if ms is None:
        return cue
    ms += int(round(g.OFFSET_REKORDBOX_S * 1000))
    ms = max(0, ms)
    for klucz in ("hot", "pamiec"):
        lista = cue.get(klucz) or []
        if lista and lista[0][1] == "Start" and ms < lista[0][0]:
            lista[0] = (ms, "Start")
    return cue


def _analizuj_jeden(folder, root, nr, nazwa_wzgledna, kopiuj_mp3, dzis, nazwa_folderu=None):
    """Analizuje pojedynczy utwór; uruchamiany równolegle w wątku roboczym."""
    sciezka = os.path.join(folder, nazwa_wzgledna)
    nazwa = os.path.basename(nazwa_wzgledna)
    try:
        meta = metadane(sciezka)
        if meta["czas"] > MAKS_MIN_UTWORU * 60:
            raise InvalidMP3Error(nazwa, "utwór dłuższy niż %d min jest pomijany (limit pamięci)" % MAKS_MIN_UTWORU)
        bramka = BRAMKA["obj"]
        szac = szacunek_bajtow(meta["czas"])
        if bramka is not None and szac > bramka.budzet * 0.9:
            raise InvalidMP3Error(nazwa, "za długi dla dostępnej pamięci RAM (zwolnij pamięć lub skróć utwór)")
        if bramka is not None:
            bramka.zajmij(szac)
        try:
            return _analizuj_zajety(sciezka, nazwa, nazwa_wzgledna, meta, root, nr, kopiuj_mp3, dzis, nazwa_folderu)
        finally:
            if bramka is not None:
                bramka.zwolnij(szac)
    except CDJError:
        raise
    except Exception as e:
        raise InvalidMP3Error(nazwa, str(e))


def _init_proces(spool):
    """Inicjalizacja procesu roboczego analizy (osobny proces zamiast watku: omija blokade GIL)."""
    # Kazdy proces liczy jeden utwor naraz — ONNX nie potrzebuje wielowatkowosci wewnetrznej
    os.environ["CDJ_BT_WATKI"] = "1"
    os.environ["CDJ_BT_PAR"] = "1"
    # Ogranicz watki wewnetrzne numpy/scipy/ONNX do 1: wiele procesow rownoleglych jest lepsze niz
    # jeden proces wielowatkowy z GIL-em
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
    SPOOL["katalog"] = spool
    BRAMKA["obj"] = None


def _zadanie_proc(sciezka, nazwa, nazwa_wzgledna, meta, root, nr, kopiuj_mp3, dzis, nazwa_folderu):
    """Uruchamiane w procesie roboczym; zwraca krotke (zawsze serializowalna), bez wyjatkow wlasnych."""
    try:
        u, ev = _analizuj_zajety(sciezka, nazwa, nazwa_wzgledna, meta, root, nr, kopiuj_mp3, dzis, nazwa_folderu)
        return ("ok", u, ev)
    except CDJError as e:
        return ("blad", e.user_message, e.code)
    except Exception as e:  # noqa: BLE001
        return ("blad", str(e) or e.__class__.__name__, None)


def _analizuj_jeden_proc(pula_proc, folder, root, nr, nazwa_wzgledna, kopiuj_mp3, dzis, nazwa_folderu=None):
    """Jak _analizuj_jeden, ale obliczenia ida do puli procesow; pamiec nadal pilnuje bramka w procesie glownym."""
    sciezka = os.path.join(folder, nazwa_wzgledna)
    nazwa = os.path.basename(nazwa_wzgledna)
    try:
        meta = metadane(sciezka)
        if meta["czas"] > MAKS_MIN_UTWORU * 60:
            raise InvalidMP3Error(nazwa, "utwór dłuższy niż %d min jest pomijany (limit pamięci)" % MAKS_MIN_UTWORU)
        bramka = BRAMKA["obj"]
        szac = szacunek_bajtow(meta["czas"])
        if bramka is not None and szac > bramka.budzet * 0.9:
            raise InvalidMP3Error(nazwa, "za długi dla dostępnej pamięci RAM (zwolnij pamięć lub skróć utwór)")
        if bramka is not None:
            bramka.zajmij(szac)
        try:
            try:
                wynik = pula_proc.submit(_zadanie_proc, sciezka, nazwa, nazwa_wzgledna, meta, root, nr,
                                         kopiuj_mp3, dzis, nazwa_folderu).result()
            except concurrent.futures.process.BrokenProcessPool:
                # pula procesow padla (np. brak pamieci): ten utwor liczymy zwyklym watkiem
                return _analizuj_zajety(sciezka, nazwa, nazwa_wzgledna, meta, root, nr, kopiuj_mp3, dzis, nazwa_folderu)
            if wynik[0] == "ok":
                return wynik[1], wynik[2]
            raise CDJError(wynik[1], user_message=wynik[1], code=wynik[2])
        finally:
            if bramka is not None:
                bramka.zwolnij(szac)
    except CDJError:
        raise
    except Exception as e:
        raise InvalidMP3Error(nazwa, str(e))


# ------------------------------------------------- pamiec podreczna analiz ---
# Utwor juz przeanalizowany (ten sam plik: sciezka, rozmiar, data zmiany; ta sama wersja analizy) nie jest liczony
# drugi raz: gotowe DAT/EXT ida z dysku, podmieniana jest tylko sciezka na pendrive (tag PPTH).
_CACHE = {"katalog": None, "wersja": None}
CACHE_MAKS_BAJTOW = int(os.environ.get("CDJ_CACHE_MB", "2048")) * 1024 * 1024


def _cache_katalog():
    if os.environ.get("CDJ_CACHE", "1") == "0":
        return None
    if _CACHE["katalog"] is None:
        if sys.platform == "win32":
            baza = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "CDJ-Tool", "cache")
        else:
            baza = os.path.expanduser("~/Library/Caches/CDJ-Tool")
        kat = os.path.join(baza, "analizy")
        try:
            os.makedirs(kat, exist_ok=True)
        except OSError:
            return None
        _CACHE["katalog"] = kat
    return _CACHE["katalog"]


def _cache_wersja():
    """Skrot kodu analizy: zmiana algorytmu automatycznie uniewaznia stare wpisy."""
    if _CACHE["wersja"] is None:
        import hashlib
        h = hashlib.sha1()
        tu = os.path.dirname(os.path.abspath(__file__))
        for plik in ("gen_dat.py", "cue.py", "takt.py", "beatthis.py", "silnik.py", "config.json"):
            try:
                with open(os.path.join(tu, plik), "rb") as fh:
                    h.update(fh.read())
            except OSError:
                pass
        _CACHE["wersja"] = h.hexdigest()[:16]
    return _CACHE["wersja"]


def _cache_klucz(sciezka):
    import hashlib
    st = os.stat(sciezka)
    txt = "%s|%d|%d|%s" % (os.path.realpath(sciezka), st.st_size, st.st_mtime_ns, _cache_wersja())
    return hashlib.sha1(txt.encode("utf-8", "surrogateescape")).hexdigest()


def _podmien_ppth(dane, pend):
    """Podmienia sciezke w tagu PPTH (pierwszy tag po naglowku PMAI) i poprawia dlugosc pliku."""
    dl_nagl = struct.unpack(">I", dane[4:8])[0]
    assert dane[dl_nagl:dl_nagl + 4] == b"PPTH"
    dl_tagu = struct.unpack(">I", dane[dl_nagl + 8:dl_nagl + 12])[0]
    raw = (pend + "\x00").encode("utf-16-be")
    ppth = b"PPTH" + struct.pack(">III", 16, 16 + len(raw), len(raw)) + raw
    calosc = dane[:dl_nagl] + ppth + dane[dl_nagl + dl_tagu:]
    return calosc[:8] + struct.pack(">I", len(calosc)) + calosc[12:]


def _cache_czytaj(klucz):
    kat = _cache_katalog()
    if not kat:
        return None
    plik = os.path.join(kat, klucz + ".bin")
    try:
        with open(plik, "rb") as fh:
            surowe = fh.read()
        n = struct.unpack(">I", surowe[:4])[0]
        info = json.loads(surowe[4:4 + n].decode("utf-8"))
        reszta = zlib.decompress(surowe[4 + n:])
        dat, ext = reszta[:info["dat_len"]], reszta[info["dat_len"]:]
        os.utime(plik, None)  # LRU: swiezo uzyty wpis
        return info, dat, ext
    except (OSError, ValueError, KeyError, zlib.error, struct.error):
        return None


def _cache_zapisz(klucz, info, dat, ext):
    kat = _cache_katalog()
    if not kat:
        return
    try:
        info = dict(info, dat_len=len(dat))
        js = json.dumps(info).encode("utf-8")
        tmp = os.path.join(kat, klucz + ".tmp%d" % threading.get_ident())
        with open(tmp, "wb") as fh:
            fh.write(struct.pack(">I", len(js)) + js + zlib.compress(dat + ext, 6))
        os.replace(tmp, os.path.join(kat, klucz + ".bin"))
    except OSError:
        pass


def _cache_przytnij():
    """Trzyma pamiec podreczna w limicie (domyslnie 2 GB), usuwajac najdawniej uzywane wpisy."""
    kat = _cache_katalog()
    if not kat:
        return
    try:
        wpisy = []
        for e in os.scandir(kat):
            if e.is_file():
                st = e.stat()
                wpisy.append((st.st_mtime, st.st_size, e.path))
        suma = sum(w[1] for w in wpisy)
        for _, rozm, sc in sorted(wpisy):
            if suma <= CACHE_MAKS_BAJTOW:
                break
            try:
                os.remove(sc)
                suma -= rozm
            except OSError:
                pass
    except OSError:
        pass


def _analizuj_zajety(sciezka, nazwa, nazwa_wzgledna, meta, root, nr, kopiuj_mp3, dzis, nazwa_folderu=None):
    """Analiza z pamiecia podreczna: trafienie = bez dekodowania i bez sieci."""
    try:
        klucz = _cache_klucz(sciezka)
    except OSError:
        klucz = None
    if klucz:
        traf = _cache_czytaj(klucz)
        if traf is not None:
            try:
                return _z_cache(traf, sciezka, nazwa, nazwa_wzgledna, meta, root, nr, kopiuj_mp3, nazwa_folderu)
            except Exception:  # noqa: BLE001  # uszkodzony wpis: liczymy od nowa
                pass
    u, ev = _analizuj_licz(sciezka, nazwa, nazwa_wzgledna, meta, root, nr, kopiuj_mp3, dzis, nazwa_folderu)
    if klucz:
        try:
            if u.get("dat_plik"):
                with open(u["dat_plik"], "rb") as fh:
                    dat = fh.read()
                with open(u["ext_plik"], "rb") as fh:
                    ext = fh.read()
            else:
                dat, ext = u["dat"], u["ext"]
            info = {"pend": u["pend"], "bpm": u["bpm"], "confidence": u["confidence"], "reliable": u["reliable"],
                    "pierwszy_ms": ev["pierwszy_ms"], "beatow": ev["beatow"], "czas": ev["czas"],
                    "podglad": ev.get("podglad")}
            _cache_zapisz(klucz, info, dat, ext)
        except Exception:  # noqa: BLE001  # pamiec podreczna jest dodatkiem
            pass
    return u, ev


def _z_cache(traf, sciezka, nazwa, nazwa_wzgledna, meta, root, nr, kopiuj_mp3, nazwa_folderu):
    info, dat, ext = traf
    if kopiuj_mp3:
        pend = sciezka_wg_folderow(nazwa_folderu, nazwa_wzgledna) if nazwa_folderu else sciezka_na_pendrive(meta, nazwa)
    else:
        rel = os.path.relpath(sciezka, root).replace(os.sep, "/")
        pend = "/" + rel
    if pend != info["pend"]:
        dat, ext = _podmien_ppth(dat, pend), _podmien_ppth(ext, pend)
    analiza = sciezka_analizy(pend)
    crc = zlib.crc32(pend.encode("utf-8")) & 0xFFFFFFFF
    kat = SPOOL["katalog"]
    plik_dat = plik_ext = None
    dat_len, ext_len = len(dat), len(ext)
    if kat:
        plik_dat = os.path.join(kat, "%d.dat" % nr)
        plik_ext = os.path.join(kat, "%d.ext" % nr)
        with open(plik_dat, "wb") as fh:
            fh.write(dat)
        with open(plik_ext, "wb") as fh:
            fh.write(ext)
        dat = ext = None
    u = {"nazwa": nazwa, "meta": meta, "bpm": info["bpm"], "pend": pend, "analiza": analiza,
         "dat": dat, "ext": ext, "dat_plik": plik_dat, "ext_plik": plik_ext, "dat_len": dat_len, "ext_len": ext_len,
         "crc": crc, "src": sciezka, "confidence": info["confidence"], "reliable": info["reliable"]}
    ev = {"typ": "utwor", "i": nr, "n": 0, "nazwa": nazwa_wzgledna.replace(os.sep, "/"),
          "tytul": meta["tytul"], "artysta": meta["artysta"], "bpm": round(info["bpm"], 2),
          "confidence": round(info["confidence"], 3), "pierwszy_ms": info["pierwszy_ms"],
          "beatow": info["beatow"], "czas": info["czas"], "z_pamieci": True}
    if info.get("podglad") is not None and nr <= 50:
        ev["podglad"] = info["podglad"]
    return u, ev


def _analizuj_licz(sciezka, nazwa, nazwa_wzgledna, meta, root, nr, kopiuj_mp3, dzis, nazwa_folderu=None):
    """Wlasciwa analiza utworu; wywolywana po zajeciu miejsca w bramce pamieci."""
    try:
        y = g.wczytaj(sciezka)
        dl = len(y) / g.SR
        analysis = g.analizuj(y)
        
        bpm = analysis["bpm"]
        pierwszy = analysis["pierwszy"]
        confidence = analysis["confidence"]
        reliable = analysis["reliable"]
        
        # Niska pewnosc BPM nie odrzuca utworu (oryginal tez nic nie odrzucal);
        # informacja trafia do zdarzenia w polu "reliable".
        
        czasy = g.siatka_ms(bpm, pierwszy, dl, analysis.get("takt", 0))
        
        if kopiuj_mp3:
            pend = sciezka_wg_folderow(nazwa_folderu, nazwa_wzgledna) if nazwa_folderu else sciezka_na_pendrive(meta, nazwa)
        else:
            rel = os.path.relpath(sciezka, root).replace(os.sep, "/")
            pend = "/" + rel
        
        analiza = sciezka_analizy(pend)
        crc = zlib.crc32(pend.encode("utf-8")) & 0xFFFFFFFF
        pwav_wynik = g.tag_pwav(y, dl)
        cechy_koloru, cue = None, None
        try:
            cechy_koloru = g._cechy_koloru(y, dl)
            import cue as cue_mod
            cue = cue_mod.wyznacz(cechy_koloru, czasy, czasy.numery, dl)
            cue = _cue_od_dzwieku(cue, y, g)
        except Exception:  # noqa: BLE001  # cue i kolor sa dodatkami: bez nich zostaje podstawowy eksport
            pass
        dat = g.zbuduj_dat(pend, bpm, czasy, y, dl, meta["total_probek"], pwav_wynik=pwav_wynik, cue=cue)
        ext = g.zbuduj_ext(pend, y, dl, czasy, bpm, cechy=cechy_koloru, cue=cue)
        pod = podglad(y, czasy - g.OFFSET_REKORDBOX_S, pwav_wynik[1]) if nr <= 50 else None
        kat = SPOOL["katalog"]
        if kat:  # wyniki ida na dysk lokalny, zeby pamiec nie rosla z liczba utworow
            plik_dat = os.path.join(kat, "%d.dat" % nr)
            plik_ext = os.path.join(kat, "%d.ext" % nr)
            with open(plik_dat, "wb") as fh:
                fh.write(dat)
            with open(plik_ext, "wb") as fh:
                fh.write(ext)
            dat_len, ext_len = len(dat), len(ext)
            dat = ext = None
        else:
            plik_dat = plik_ext = None
            dat_len, ext_len = len(dat), len(ext)
        
        u = {
            "nazwa": nazwa,
            "meta": meta,
            "bpm": bpm,
            "pend": pend,
            "analiza": analiza,
            "dat": dat,
            "ext": ext,
            "dat_plik": plik_dat,
            "ext_plik": plik_ext,
            "dat_len": dat_len,
            "ext_len": ext_len,
            "crc": crc,
            "src": sciezka,
            "confidence": confidence,
            "reliable": reliable
        }
        
        ev = {
            "typ": "utwor",
            "i": nr,
            "n": 0,
            "nazwa": nazwa_wzgledna.replace(os.sep, "/"),
            "tytul": meta["tytul"],
            "artysta": meta["artysta"],
            "bpm": round(bpm, 2),
            "confidence": round(confidence, 3),
            "pierwszy_ms": int(round(czasy[0] * 1000)),
            "beatow": len(czasy),
            "czas": round(dl, 1)
        }
        
        if pod is not None:
            ev["podglad"] = pod
        
        return u, ev
    
    except CDJError:
        raise
    except Exception as e:
        raise InvalidMP3Error(nazwa, str(e))


# ================================================================ Main ---
def _niski_priorytet():
    """Analiza ustepuje innym programom (nice 10): komputer zostaje plynny, a wolna moc i tak idzie na analize."""
    try:
        if hasattr(os, "setpriority") and not os.environ.get("CDJ_NICE") == "0":
            obecny = os.getpriority(os.PRIO_PROCESS, 0)
            cel = int(os.environ.get("CDJ_NICE", "10"))
            if obecny < cel:
                os.setpriority(os.PRIO_PROCESS, 0, cel)
    except (OSError, ValueError):
        pass


def przetworz(a, emit):
    """Punkt wejscia: sprzata katalog tymczasowy z wynikami analizy po zakonczeniu (takze po bledzie)."""
    _niski_priorytet()
    try:
        _przetworz(a, emit)
    finally:
        _cache_przytnij()
        kat = SPOOL.get("katalog")
        SPOOL["katalog"] = None
        BRAMKA["obj"] = None
        if kat:
            shutil.rmtree(kat, ignore_errors=True)


def _przetworz(a, emit):
    """a: slownik z folder_mp3, pendrive, playlista, kopiuj_mp3, zastap_pioneer, flac (opcjonalnie)."""
    FLAC["on"] = bool(a.get("flac"))
    os.environ["CDJ_FLAC"] = "1" if FLAC["on"] else "0"  # dziedziczone przez procesy robocze
    folder = os.path.realpath(a["folder_mp3"])
    nazwa_folderu = str(a.get("nazwa_folderu") or os.path.basename(folder.rstrip("/")) or "Tracks")[:60]
    root = os.path.realpath(os.path.abspath(a["pendrive"]))
    kopiuj_mp3 = bool(a.get("kopiuj_mp3", True))
    
    if not kopiuj_mp3:
        try:
            wspolny = os.path.commonpath([root, folder])
        except ValueError:
            wspolny = ""
        if wspolny != root:
            raise ValueError("Tryb bez kopiowania wymaga, aby folder z MP3 znajdował się na wybranym pendrivie.")
        
        pioneer = os.path.join(root, "PIONEER")
        try:
            w_pioneer = os.path.commonpath([os.path.realpath(pioneer), folder]) == os.path.realpath(pioneer)
        except ValueError:
            w_pioneer = False
        if w_pioneer:
            raise ValueError("W trybie bez kopiowania wybierz folder z MP3 poza folderem PIONEER.")
    
    pliki = pliki_mp3(folder)
    if not pliki:
        tylko_flac = not FLAC["on"] and any(
            n.lower().endswith(ROZSZERZENIE_FLAC) and not n.startswith(".")
            for _, _, fn in os.walk(folder) for n in fn)
        raise NoAudioFilesError(folder, tylko_flac)
    if not os.path.isdir(root):
        raise ValueError("Nie znaleziono wybranego pendrive'a.")
    
    emit({"typ": "start", "n": len(pliki)})

    # Limit pamieci: analiza zajmie najwyzej ulamek dostepnego RAM (domyslnie 35%).
    budzet_mb = float(a.get("ram_limit_mb") or budzet_ram_mb())
    BRAMKA["obj"] = BramkaPamieci(budzet_mb * 1e6)
    SPOOL["katalog"] = tempfile.mkdtemp(prefix="cdj-spool-")
    potrzeba_spool = len(pliki) * 120 * 1024
    wolne_spool = shutil.disk_usage(SPOOL["katalog"]).free
    if potrzeba_spool > wolne_spool * 0.9:
        raise ValueError("Za mało miejsca na dysku komputera na wyniki tymczasowe: potrzeba ok. %.0f MB, wolne %.0f MB."
                         % (potrzeba_spool / 1e6, wolne_spool / 1e6))

    pdb_wzor = open(os.path.join(WZORZEC, "export.pdb"), "rb").read()
    f_szablon, s_szablon = szablon_utworu(pdb_wzor)
    dzis = datetime.date.today().isoformat()

    utwory_wg_indeksu = {}
    workers = max(1, int(a.get("workers", 1)))
    cel_mb_s = float(a.get("cel_mb_s", 0) or 0)
    opis = f"Analizuję {len(pliki)} utworów równolegle (do {workers} naraz, limit pamięci RAM ok. {budzet_mb:.0f} MB)"
    if cel_mb_s:
        opis += f" — cel 80%% przepustowości odczytu: {cel_mb_s:.1f} MB/s"
    emit({"typ": "analiza_start", "tekst": opis + "..."})
    
    zakonczone = 0
    errors = []
    
    pula_proc = None
    if len(pliki) >= 4 and os.environ.get("CDJ_PROCESY", "1") == "1":
        try:
            import multiprocessing
            # Ogranicz do max polowy rdzeni zeby nie przegrzewac
            n_proc = max(2, min((os.cpu_count() or 4) // 2, 6, workers, len(pliki)))
            # forkserver jest szybszy niz spawn na macOS: serwer startuje raz, kolejne procesy sa
            # klonowane z gotowej kopii z zaladowanym Pythonem/numpy — eliminuje wielosekundowe
            # opoznienie przy uruchamianiu kazdego zadania.
            try:
                ctx = multiprocessing.get_context("forkserver")
            except Exception:
                ctx = multiprocessing.get_context("spawn")
            pula_proc = concurrent.futures.ProcessPoolExecutor(
                max_workers=n_proc, mp_context=ctx,
                initializer=_init_proces, initargs=(SPOOL["katalog"],))
            emit({"typ": "etap", "tekst": "Analiza w %d procesach (szybsza dla dużych bibliotek)" % n_proc})
        except Exception:  # noqa: BLE001
            pula_proc = None

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pula:
        if pula_proc is not None:
            futures = [pula.submit(_analizuj_jeden_proc, pula_proc, folder, root, nr, rel, kopiuj_mp3, dzis, nazwa_folderu)
                       for nr, rel in enumerate(pliki, 1)]
        else:
            futures = [pula.submit(_analizuj_jeden, folder, root, nr, rel, kopiuj_mp3, dzis, nazwa_folderu)
                       for nr, rel in enumerate(pliki, 1)]
        for future in concurrent.futures.as_completed(futures):
            try:
                utwor, ev = future.result()
                ev["n"] = len(pliki)
                if utwor:
                    utwory_wg_indeksu[ev["i"]] = utwor
                emit(ev)
            except CDJError as e:
                emit({
                    "typ": "utwor_blad",
                    "nazwa": "unknown",
                    "tekst": e.user_message,
                    "kod": e.code
                })
                errors.append(e)
            except Exception as e:
                emit({"typ": "utwor_blad", "nazwa": "unknown", "tekst": str(e)})
                errors.append(e)
            
            zakonczone += 1
            emit({"typ": "postep_analizy", "i": zakonczone, "n": len(pliki)})
    if pula_proc is not None:
        pula_proc.shutdown(wait=True)
    
    utwory = [utwory_wg_indeksu[i] for i in sorted(utwory_wg_indeksu)]
    if not utwory:
        msg = "Żaden utwór nie został przeanalizowany."
        if errors:
            msg += f" Błędy: {errors[0].user_message if hasattr(errors[0], 'user_message') else str(errors[0])}"
        raise ValueError(msg)

    emit({"typ": "etap", "tekst": "Przygotowuję bazę danych..." + _t()})
    pioneer = os.path.join(root, "PIONEER")
    stary_pdb_sciezka = os.path.join(pioneer, "rekordbox", "export.pdb")
    scalaj = (not a.get("zastap_pioneer", False)) and os.path.isfile(stary_pdb_sciezka)
    lib = {"utwory": [], "artysci": [], "albumy": [], "drzewo": [], "wpisy": []}
    if scalaj:
        try:
            stary_pdb = open(stary_pdb_sciezka, "rb").read()
            lib = pw.czytaj_biblioteke(stary_pdb)
        except Exception as e:
            raise ValueError("Nie mogę bezpiecznie odczytać biblioteki zapisanej już na pendrive (%s). "
                             "Nic nie zostało zmienione. Wybierz opcję zastąpienia zawartości (zrobię kopię zapasową) "
                             "albo użyj innego pendrive'a." % e)
        pdb_wzor = stary_pdb
        if _na_wolumenie(root):
            try:
                kopia_pdb = kopia_zapasowa_pdb(stary_pdb_sciezka, root)
                emit({"typ": "kopia", "sciezka": kopia_pdb})
            except OSError:
                pass

    artysci = {n: aid for aid, n in lib["artysci"]}
    artysci_nast = max([aid for aid, _ in lib["artysci"]] + [0]) + 1
    albumy = {n: alid for alid, _, n in lib["albumy"]}
    albumy_nast = max([alid for alid, _, _ in lib["albumy"]] + [0]) + 1
    for u in utwory:
        if u["meta"]["artysta"] not in artysci:
            artysci[u["meta"]["artysta"]] = artysci_nast
            artysci_nast += 1
        if u["meta"]["album"] not in albumy:
            albumy[u["meta"]["album"]] = albumy_nast
            albumy_nast += 1

    # Dwa rozne utwory moga dac ten sam hash katalogu analizy (200003 mozliwych wartosci); CDJ czyta sciezke
    # z bazy, wiec kolizje rozwiazujemy przez wybranie kolejnego wolnego katalogu.
    nowe_sciezki = {u["pend"] for u in utwory}
    zajete = {s["analyze_path"] for _, s in lib["utwory"] if s["file_path"] not in nowe_sciezki}
    for u in utwory:
        k = 0
        analiza = u["analiza"]
        while analiza in zajete:
            k += 1
            analiza = sciezka_analizy(u["pend"], k)
        u["analiza"] = analiza
        zajete.add(analiza)

    kodowane = [(f, s) for f, s in lib["utwory"]]
    wg_sciezki = {s["file_path"]: i for i, (f, s) in enumerate(kodowane)}
    nast_id = max([f["id"] for f, _ in kodowane] + [0]) + 1
    id_nowych = []
    for u in utwory:
        m = u["meta"]
        pola = dict(
            artist=artysci[m["artysta"]], album=albumy[m["album"]],
            sample_rate=m["sr"], file_size=m["rozmiar"],
            bitrate=m["bitrate"], track_no=m["nr"], tempo=int(round(u["bpm"] * 100)), year=m["rok"],
            duration=int(m["czas"]), depth=m["bity"], unk90=m["typ_pliku"], disc=m["dysk"], unk20=u["crc"]
        )
        napisy = dict(
            title=m["tytul"], filename=os.path.basename(u["pend"]), file_path=u["pend"],
            analyze_path=u["analiza"], analyze_date=dzis,
            s4="",  # jak w eksporcie Rekordboxa (szablon mial "1")
            autoload="",  # Hot Cue Auto Load wylaczone: hot cue tylko zaznaczone, ladowane recznie przez CALL
        )
        if u["pend"] in wg_sciezki:  # ten sam plik analizowany ponownie: zachowaj id, gatunek, ocene, odtworzenia
            i = wg_sciezki[u["pend"]]
            f, s = dict(kodowane[i][0]), dict(kodowane[i][1])
            f.update(pola); s.update(napisy)
            kodowane[i] = (f, s)
            id_nowych.append(f["id"])
        else:
            f = dict(f_szablon)
            f.update(pola, id=nast_id, key=0, label=0, genre=0, artwork=0, composer=0, orig_artist=0,
                     remixer=0, plays=0)
            s = dict(s_szablon)
            s.update(napisy, date_added=dzis, lyricist="", release_date="", mix_name="", comment="")
            wg_sciezki[u["pend"]] = len(kodowane)
            kodowane.append((f, s))
            id_nowych.append(nast_id)
            nast_id += 1

    def zgrupowane(budowniki):
        """Wiersze z poprawnym przesunieciem (0x20 * numer w obrebie strony)."""
        sztuki = pw.rows_per_pages([b(0) for b in budowniki])
        out, k = [], 0
        for grupa in sztuki:
            for j in range(len(grupa)):
                out.append(budowniki[k](0x20 * j))
                k += 1
        return out

    nazwa_pl = a.get("playlista") or "Tracks"
    drzewo = list(lib["drzewo"])
    wpisy = list(lib["wpisy"])
    istn = [pid for par, srt, pid, fold, n in drzewo if par == 0 and not fold and n == nazwa_pl]
    if istn:
        pl_id = istn[0]
    else:
        pl_id = max([pid for _, _, pid, _, _ in drzewo] + [0]) + 1
        drzewo.append((0, max([srt for par, srt, _, _, _ in drzewo if par == 0] + [-1]) + 1, pl_id, False, nazwa_pl))
    juz = {tid for _, tid, pid in wpisy if pid == pl_id}
    idx = max([e for e, _, pid in wpisy if pid == pl_id] + [0])
    for tid in id_nowych:
        if tid not in juz:
            idx += 1
            wpisy.append((idx, tid, pl_id))
            juz.add(tid)

    pdb = pw.zbuduj_pdb(pdb_wzor, {
        0: zgrupowane([(lambda sh, f=f, s=s: pw.track_row(sh, f, s)) for f, s in kodowane]),
        2: zgrupowane([(lambda sh, aid=aid, n=n: pw.artist_row(sh, aid, n)) for n, aid in artysci.items()]),
        3: zgrupowane([(lambda sh, alid=alid, n=n, ar=ar: pw.album_row(sh, alid, ar, n))
                       for n, alid, ar in ((n, alid, dict((x[0], x[1]) for x in lib["albumy"]).get(alid, 0))
                                           for n, alid in albumy.items())]),
        7: [pw.playlist_tree_row(par, srt, pid, fold, n) for par, srt, pid, fold, n in drzewo],
        8: [pw.playlist_entry_row(e, tid, pid) for e, tid, pid in wpisy],
    })

    pioneer = os.path.join(root, "PIONEER")
    dat_bajty = sum(u["dat_len"] + u["ext_len"] for u in utwory)
    dane_bajty = dat_bajty + len(pdb)
    stat = os.path.join(WZORZEC, "PIONEER")
    
    szablony = []
    for dp, _, fn in os.walk(stat):
        for nm in fn:
            src = os.path.join(dp, nm)
            rel = os.path.relpath(src, stat)
            szablony.append((src, os.path.join(pioneer, rel), os.path.getsize(src)))
    
    wolne = shutil.disk_usage(root).free
    do_zapisu = dane_bajty + sum(rozmiar for _, dst, rozmiar in szablony
                                 if a.get("zastap_pioneer", False) or not os.path.exists(dst))
    do_zapisu += sum(u["meta"]["rozmiar"] for u in utwory) if kopiuj_mp3 else 0
    
    odzysk = 0
    if a.get("zastap_pioneer", False) and os.path.isdir(pioneer):
        for dp, _, fn in os.walk(pioneer):
            for nm in fn:
                try:
                    odzysk += os.path.getsize(os.path.join(dp, nm))
                except OSError:
                    pass
    else:
        docelowe = [os.path.join(pioneer, "rekordbox", "export.pdb")]
        docelowe.extend(os.path.join(root, u["analiza"].lstrip("/")) for u in utwory)
        if kopiuj_mp3:
            docelowe.extend(os.path.join(root, u["pend"].lstrip("/")) for u in utwory)
        for p in docelowe:
            try:
                odzysk += os.path.getsize(p)
            except OSError:
                pass
    
    if do_zapisu > wolne + odzysk + 1024 * 1024:
        raise DiskSpaceError(
            dane_bajty / 1e6,
            (wolne + odzysk) / 1e6
        )
    
    emit({
        "typ": "etap",
        "tekst": f"Waveformy i baza CDJ: {dane_bajty/1e6:.2f} MB"
                 + (f"; MP3 do skopiowania: {sum(u['meta']['rozmiar'] for u in utwory)/1e6:.2f} MB" if kopiuj_mp3 else "")
                 + ". Zapisuję pliki..."
    })

    na_dysku = _na_wolumenie(root)
    if a.get("zastap_pioneer", False) and os.path.isdir(pioneer):
        if na_dysku:
            emit({"typ": "etap", "tekst": "Robię kopię zapasową dotychczasowego folderu PIONEER..."})
            cel = kopia_zapasowa(pioneer, root)
            emit({"typ": "kopia", "sciezka": cel})
            
            def pomin_znikniety_sidecar(func, sciezka, exc_info):
                blad = exc_info[1]
                if isinstance(blad, FileNotFoundError) and os.path.basename(sciezka).startswith("._"):
                    return
                raise blad

            shutil.rmtree(pioneer, onerror=pomin_znikniety_sidecar)
        else:
            emit({"typ": "etap", "tekst": "Wybrany katalog nie jest pendrive'em, nie usuwam starego folderu PIONEER."})

    emit({"typ": "etap", "tekst": "Zapisuję pliki na pendrive..." + _t()})
    if _na_wolumenie(root):
        # Spotlight nie ma indeksować pendrive'a: indeksowanie blokuje później jego wysunięcie
        for znacznik in (".metadata_never_index",):
            try:
                open(os.path.join(root, znacznik), "a").close()
            except OSError:
                pass
    for dp, dn, fn in os.walk(stat):
        rel = os.path.relpath(dp, stat)
        for nm in fn:
            dst = os.path.join(pioneer, rel, nm)
            if not os.path.exists(dst):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(os.path.join(dp, nm), dst)
    
    os.makedirs(os.path.join(pioneer, "rekordbox"), exist_ok=True)
    
    for u in utwory:
        dst = os.path.join(root, u["analiza"].lstrip("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        for zrodlo, bajty, cel_pliku in ((u["dat_plik"], u["dat"], dst),
                                        (u["ext_plik"], u["ext"], os.path.splitext(dst)[0] + ".EXT")):
            if zrodlo:
                shutil.copyfile(zrodlo, cel_pliku)
            else:
                with open(cel_pliku, "wb") as fh:
                    fh.write(bajty)
    
    if kopiuj_mp3:
        do_skopiowania = []
        for u in utwory:
            cel = os.path.join(root, u["pend"].lstrip("/"))
            istnieje = os.path.exists(cel) and os.path.getsize(cel) == u["meta"]["rozmiar"]
            do_skopiowania.append((u, cel, istnieje))
        razem = sum(u["meta"]["rozmiar"] for u, _, ist in do_skopiowania if not ist)
        emit({"typ": "kopiuj_start", "razem": razem, "n": len(utwory)})
        zrobione = 0
        ostatni_emit = 0.0
        for i, (u, cel, istnieje) in enumerate(do_skopiowania, 1):
            emit({"typ": "kopiuje", "i": i, "n": len(utwory), "nazwa": u["nazwa"]})
            os.makedirs(os.path.dirname(cel), exist_ok=True)
            if istnieje:
                continue
            with open(u["src"], "rb") as fin, open(cel, "wb") as fout:
                while True:
                    blok = fin.read(4 * 1024 * 1024)
                    if not blok:
                        break
                    fout.write(blok)
                    zrobione += len(blok)
                    teraz = time.monotonic()
                    if teraz - ostatni_emit >= 0.4:
                        ostatni_emit = teraz
                        emit({"typ": "kopiuj_bajty", "zrobione": zrobione, "razem": razem})
        emit({"typ": "kopiuj_bajty", "zrobione": zrobione, "razem": razem})

    # baza zapisywana na samym koncu (atomowo): do tej chwili stara biblioteka na pendrive dziala bez zmian
    tmp_pdb = os.path.join(pioneer, "rekordbox", "export.pdb.tmp")
    with open(tmp_pdb, "wb") as fh:
        fh.write(pdb)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_pdb, os.path.join(pioneer, "rekordbox", "export.pdb"))

    emit({"typ": "etap", "tekst": "Kończę zapis (opróżniam bufory pendrive'a)..." + _t()})
    try:
        os.sync()
    except AttributeError:
        pass

    stare = [n for n in ("exportExt.pdb", "exportLibrary.db")
             if os.path.exists(os.path.join(pioneer, "rekordbox", n))]
    
    emit({"typ": "etap", "tekst": "Koniec zapisu" + _t()})
    emit({"typ": "koniec", "ok": True, "liczba": len(utwory), "pendrive": root, "stare_pliki": stare})
