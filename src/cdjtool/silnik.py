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
    ProcessTimeoutError, BPMConfidenceError
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


# Formaty odtwarzane przez CDJ-2000: MP3, AAC (m4a), WAV, AIFF (FLAC/ALAC - nie).
ROZSZERZENIA_AUDIO = (".mp3", ".m4a", ".aac", ".wav", ".aif", ".aiff")
RODZINA_ROZSZERZEN = {".mp3": "mp3", ".m4a": "aac", ".aac": "aac", ".wav": "pcm", ".aif": "pcm", ".aiff": "pcm"}
KODEKI_PCM = {"pcm_s16le": 16, "pcm_s16be": 16, "pcm_s24le": 24, "pcm_s24be": 24}
# wartosc pola "file type" w wierszu utworu rekordboxa (pdb): 1 MP3, 4 M4A, 11 WAV, 12 AIFF
TYP_PLIKU = {".mp3": 1, ".m4a": 4, ".aac": 4, ".wav": 11, ".aif": 12, ".aiff": 12}


def pliki_mp3(folder: str) -> List[str]:
    """Zwróć pliki audio (MP3/AAC/WAV/AIFF) z podfolderów (sorted, pominąć metadata)."""
    pliki = []
    for dp, dn, fn in os.walk(folder):
        dn[:] = sorted(d for d in dn if not d.startswith(".") and d != "PIONEER")
        for nazwa in fn:
            pelna = os.path.join(dp, nazwa)
            if (nazwa.lower().endswith(ROZSZERZENIA_AUDIO) and not nazwa.startswith(".")
                and not os.path.islink(pelna)):
                pliki.append(os.path.relpath(pelna, folder))
    return sorted(pliki)


# ============================================================ Pamiec RAM ---
import threading

SPOOL = {"katalog": None}
BRAMKA = {"obj": None}
MAKS_MIN_UTWORU = 45          # dluzsze utwory pomijamy (zbyt duze zuzycie pamieci)
BAJTOW_NA_PROBKE = 4 * 16     # float32 x ~16 (zmierzony szczyt analizy ~14.7x dlugosci audio)


def ram_mb():
    """(calkowity, dostepny) RAM w MB; dostepny = wolny + do odzyskania. Bez zaleznosci zewnetrznych."""
    try:
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
                    capture_output=True, text=True, timeout=ffmpeg_timeout
                )
            except subprocess.TimeoutExpired:
                raise ProcessTimeoutError("FFmpeg", ffmpeg_timeout)
            except FileNotFoundError:
                raise FFmpegError(f"FFmpeg nie znaleziony: {FFMPEG}")
            
            # Czytaj tagi
            try:
                with open(tagi_path, "r", encoding="utf-8", errors="replace") as f:
                    wyjscie_tagi = f.read()
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
        if kodek in ("flac", "alac"):
            raise InvalidMP3Error(nazwa_pliku, f"{kodek.upper()} nie jest obsługiwany przez CDJ-2000 (użyj MP3, AAC, WAV lub AIFF)")
        if kodek == "mp3":
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

    shutil.copytree(
        pioneer,
        os.path.join(cel, "PIONEER"),
        ignore=shutil.ignore_patterns("._*", ".DS_Store"),
        copy_function=kopiuj_plik_z_pominieciem_sidecar,
    )
    return cel


# ================================================================ Analiza ---
def _analizuj_jeden(folder, root, nr, nazwa_wzgledna, kopiuj_mp3, dzis):
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
            return _analizuj_zajety(sciezka, nazwa, nazwa_wzgledna, meta, root, nr, kopiuj_mp3, dzis)
        finally:
            if bramka is not None:
                bramka.zwolnij(szac)
    except CDJError:
        raise
    except Exception as e:
        raise InvalidMP3Error(nazwa, str(e))


def _analizuj_zajety(sciezka, nazwa, nazwa_wzgledna, meta, root, nr, kopiuj_mp3, dzis):
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
        
        czasy = g.siatka_ms(bpm, pierwszy, dl)
        
        if kopiuj_mp3:
            pend = sciezka_na_pendrive(meta, nazwa)
        else:
            rel = os.path.relpath(sciezka, root).replace(os.sep, "/")
            pend = "/" + rel
        
        analiza = sciezka_analizy(pend)
        crc = zlib.crc32(pend.encode("utf-8")) & 0xFFFFFFFF
        pwav_wynik = g.tag_pwav(y, dl)
        dat = g.zbuduj_dat(pend, bpm, czasy, y, dl, meta["total_probek"], pwav_wynik=pwav_wynik)
        ext = g.zbuduj_ext(pend, y, dl)
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
def przetworz(a, emit):
    """Punkt wejscia: sprzata katalog tymczasowy z wynikami analizy po zakonczeniu (takze po bledzie)."""
    try:
        _przetworz(a, emit)
    finally:
        kat = SPOOL.get("katalog")
        SPOOL["katalog"] = None
        BRAMKA["obj"] = None
        if kat:
            shutil.rmtree(kat, ignore_errors=True)


def _przetworz(a, emit):
    """a: slownik z folder_mp3, pendrive, playlista, kopiuj_mp3, zastap_pioneer."""
    folder = os.path.realpath(a["folder_mp3"])
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
        raise ValueError("W wybranym folderze nie ma plikow MP3.")
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
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pula:
        futures = [pula.submit(_analizuj_jeden, folder, root, nr, rel, kopiuj_mp3, dzis)
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
    
    utwory = [utwory_wg_indeksu[i] for i in sorted(utwory_wg_indeksu)]
    if not utwory:
        msg = "Żaden utwór nie został przeanalizowany."
        if errors:
            msg += f" Błędy: {errors[0].user_message if hasattr(errors[0], 'user_message') else str(errors[0])}"
        raise ValueError(msg)

    emit({"typ": "etap", "tekst": "Przygotowuję bazę danych..."})
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
        if root.startswith("/Volumes/"):
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
            analyze_path=u["analiza"], analyze_date=dzis
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

    na_dysku = root.startswith("/Volumes/")
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

    emit({"typ": "etap", "tekst": "Zapisuję pliki na pendrive..."})
    if root.startswith("/Volumes/"):
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

    emit({"typ": "etap", "tekst": "Kończę zapis (opróżniam bufory pendrive'a)..."})
    try:
        os.sync()
    except AttributeError:
        pass

    stare = [n for n in ("exportExt.pdb", "exportLibrary.db")
             if os.path.exists(os.path.join(pioneer, "rekordbox", n))]
    
    emit({"typ": "koniec", "ok": True, "liczba": len(utwory), "pendrive": root, "stare_pliki": stare})
