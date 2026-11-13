#!/usr/bin/env python3
"""Silnik: Analiza MP3 i zapis dla CDJ-2000 — WERSJA NAPRAWIONA."""

import datetime
import errno
import concurrent.futures
import json
import os
import queue
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
import edytor
import okladki
import tonacja
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
MAKS_MIN_UTWORU = 45          # bez opcji dlugich utworow dluzsze pomijamy
MAKS_MIN_DLUGIEGO = 180       # z opcja: sety i miksy do 3 h, analizowane odcinkami (gen_dat.analizuj_dlugi)
DLUGIE = {"on": True}         # ustawiane z opcji w interfejsie (przetworz)
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
    if czas_s > g.DLUGI_PROG_S:  # odcinkami: caly dzwiek (float32) z zapasem + staly szczyt odcinka i kawalka
        return int(czas_s * g.SR * 8) + 1100 * 1024 * 1024  # zmierzone: set 37 min = +1,2 GB
    return int(czas_s * g.SR * BAJTOW_NA_PROBKE) + 20 * 1024 * 1024


def _limit_dlugosci(nazwa, czas_s):
    maks = MAKS_MIN_DLUGIEGO if DLUGIE["on"] else MAKS_MIN_UTWORU
    if czas_s > maks * 60:
        powod = ("utwór dłuższy niż %d min jest pomijany" % maks) + (
            "" if DLUGIE["on"] else " (włącz opcję długich utworów, by analizować sety do %d min)" % MAKS_MIN_DLUGIEGO)
        raise InvalidMP3Error(nazwa, powod)


# ================================================================ Metadane ---
def _uzupelnij_z_nazw(sciezka, tytul, artysta, album):
    """Brakujace tagi z nazwy pliku ("Wykonawca - Tytul.mp3") i folderu ("Wykonawca - Album 2011").
    Istniejace tagi zostaja bez zmian."""
    tytul, artysta, album = (tytul or "").strip(), (artysta or "").strip(), (album or "").strip()
    rok = 0
    if not artysta:
        z = okladki.z_nazwy_pliku((tytul + ".x") if " - " in tytul else sciezka)
        if z:
            artysta = z[0]
            if not tytul or " - " in tytul:
                tytul = z[1]
    if not album and artysta:
        folder = unicodedata.normalize("NFC", os.path.basename(os.path.dirname(os.path.abspath(sciezka))))
        if " - " in folder:
            fa, fal = (c.strip() for c in folder.split(" - ", 1))
            if okladki._norm(fa) == okladki._norm(artysta) and fal:
                m = re.match(r"^(.*?)[\s\(\[]*((?:19|20)\d\d)[\)\]]?$", fal)
                if m and m.group(1).strip():
                    fal, rok = m.group(1).strip(" -"), int(m.group(2))
                album = fal
    nfc = lambda x: unicodedata.normalize("NFC", x)  # noqa: E731  # macOS zapisuje nazwy plikow w NFD
    return {"tytul": nfc(tytul), "artysta": nfc(artysta), "album": nfc(album), "rok": rok}



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
                k, v = re.split(r"(?<!\\)=", linia, maxsplit=1) if re.search(r"(?<!\\)=", linia) else (linia, "")
                # format ffmetadata poprzedza znaki = ; # \ ukosnikiem (np. "F\#m", "Track \#1")
                odslon = lambda x: re.sub(r"\\(.)", r"\1", x)
                tagi.setdefault(odslon(k).lower(), odslon(v).strip())
        
        # Parsuj stderr
        i = r.stderr
        m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", i)
        if not m:
            if sciezka.lower().endswith(ROZSZERZENIE_FLAC):
                raise InvalidMP3Error(os.path.basename(sciezka), "ten ffmpeg nie obsługuje FLAC (zbuduj go ponownie: tools/build_ffmpeg.sh)")
            raise InvalidMP3Error(os.path.basename(sciezka), "Nie można odczytać czasu trwania")
        
        dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        ms = re.search(r"Duration:[^\n]*?start:\s*(-?[\d.]+)", i)
        start_s = float(ms.group(1)) if ms else 0.0
        
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
        
        ton_tag = tonacja.z_tagu(tagi.get("tkey") or tagi.get("initialkey") or tagi.get("key"))
        uzup = _uzupelnij_z_nazw(sciezka, tagi.get("title"), tagi.get("artist"), tagi.get("album"))
        return {
            "tonacja_tag": [ton_tag[0], ton_tag[1]] if ton_tag else None,
            "tytul": uzup["tytul"] or os.path.splitext(os.path.basename(sciezka))[0],
            "artysta": uzup["artysta"] or "Unknown Artist",
            "album": uzup["album"] or "Unknown Album",
            "rok": liczba("date") or liczba("year") or uzup["rok"],
            "z_nazwy": [k for k, tag in (("wykonawca", "artist"), ("tytuł", "title"), ("album", "album"))
                        if not tagi.get(tag) and uzup[{"wykonawca": "artysta", "tytuł": "tytul", "album": "album"}[k]]],
            "nr": liczba("track"),
            "dysk": liczba("disc"),
            "sr": sr,
            "bitrate": br,
            "czas": dur,
            "rozmiar": rozmiar,
            "total_probek": total_probek,
            "bity": bity,
            "typ_pliku": TYP_PLIKU[ext],
            "kodek": kodek,
            # MP3 bez danych gapless (naglowek LAME): ffmpeg niczego nie obcina, a CDJ/rekordbox tak (patrz _analizuj_licz)
            "mp3_bez_gapless": TYP_PLIKU[ext] == 1 and start_s <= 0.0
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


def kopia_zapasowa(pioneer, korzen, pominiete=None):
    """Kopia zapasowa starego folderu PIONEER (w Dokumenty/CDJ-Tool-kopie). Pojedyncze pliki, ktorych nie da sie
    odczytac (np. uszkodzony plik analizy), sa pomijane i dopisywane do listy `pominiete`; przerywa tylko wtedy,
    gdy nie da sie skopiowac samej bazy (export.pdb) albo zapisac kopii na komputerze."""
    dzis = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    nazwa = re.sub(r'[/\\]', "_", os.path.basename(korzen.rstrip("/")) or "pendrive")
    cel = os.path.join(os.path.expanduser("~"), "Documents", "CDJ-Tool-kopie", f"{nazwa}_{dzis}")
    pominiete = pominiete if pominiete is not None else []
    try:
        os.makedirs(cel, exist_ok=True)
    except OSError as e:
        raise CDJError("backup dir failed: %s" % e, code="BACKUP_FAILED", user_message=(
            "Nie mogę utworzyć kopii zapasowej w folderze Dokumenty (%s). Sprawdź wolne miejsce na dysku komputera "
            "i czy aplikacja ma dostęp do folderu Dokumenty (Ustawienia systemowe → Prywatność i ochrona → Pliki "
            "i foldery). Nic nie zostało zmienione na pendrivie." % (e.strerror or e))) from e

    def kopiuj(src, dst):
        try:
            return shutil.copy2(src, dst)
        except OSError as e:
            if os.path.basename(src).startswith("._"):
                return dst
            pominiete.append((os.path.relpath(src, pioneer), e.strerror or str(e)))
            return dst

    try:
        shutil.copytree(pioneer, os.path.join(cel, "PIONEER"), ignore=shutil.ignore_patterns("._*", ".DS_Store"),
                        copy_function=kopiuj)
    except (shutil.Error, OSError) as e:
        # copytree zbiera bledy katalogow (np. nieczytelny podfolder); pliki zgubione w ten sposob tez pomijamy
        if isinstance(e, shutil.Error) and e.args and isinstance(e.args[0], list):
            for src, _dst, why in e.args[0][:50]:
                pominiete.append((os.path.relpath(str(src), pioneer), str(why)[:120]))
        else:
            pominiete.append((".", str(e)[:200]))
    baza = os.path.join(cel, "PIONEER", "rekordbox", "export.pdb")
    if os.path.isfile(os.path.join(pioneer, "rekordbox", "export.pdb")) and not os.path.isfile(baza):
        szczeg = "; ".join("%s: %s" % p for p in pominiete[:3])
        raise CDJError(
            "backup failed: %s" % szczeg[:300], code="BACKUP_FAILED",
            user_message=("Nie mogę skopiować bazy (export.pdb) z pendrive'a do kopii zapasowej (%s). Pendrive może "
                          "mieć uszkodzony system plików, np. po wyjęciu bez wysunięcia: napraw go w Narzędziu dyskowym "
                          "(Pierwsza pomoc) i spróbuj ponownie. Uwaga: formatowanie usunęłoby też muzykę zapisaną na tym "
                          "dysku. Nic nie zostało zmienione na pendrivie." % (szczeg[:200] or "nieznany błąd")))
    return cel
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
    y = y[: int(sr * 300)]  # poczatek dzwieku szukamy w pierwszych 5 min (bez kopii calego seta)
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
        _limit_dlugosci(nazwa, meta["czas"])
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
    finally:
        _oddaj_pamiec()


def _oddaj_pamiec():
    """Po kazdym utworze proces roboczy oddaje systemowi zwolniona pamiec. Bez tego kazdy z kilku procesow
    trzymal ok. 0,8-1 GB po najwiekszym utworze i na Macu z 16 GB system zaczynal uzywac pliku wymiany
    (zmierzone 2026-11-08: 7 procesow, 6 GB wymiany, 25% czasu procesora na obsluge pamieci)."""
    import gc
    gc.collect()
    if sys.platform == "darwin":
        try:
            import ctypes
            libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
            libc.malloc_zone_pressure_relief.restype = ctypes.c_size_t
            libc.malloc_zone_pressure_relief(None, ctypes.c_size_t(0))
        except Exception:  # noqa: BLE001
            pass
    elif sys.platform.startswith("linux"):
        try:
            import ctypes
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except Exception:  # noqa: BLE001
            pass


def _analizuj_jeden_proc(pula_proc, folder, root, nr, nazwa_wzgledna, kopiuj_mp3, dzis, nazwa_folderu=None):
    """Jak _analizuj_jeden, ale obliczenia ida do puli procesow; pamiec nadal pilnuje bramka w procesie glownym."""
    sciezka = os.path.join(folder, nazwa_wzgledna)
    nazwa = os.path.basename(nazwa_wzgledna)
    try:
        meta = metadane(sciezka)
        _limit_dlugosci(nazwa, meta["czas"])
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
# Podnies, gdy zmienia sie cos w silniku, co wplywa na wynik analizy poza funkcjami hashowanymi w _cache_wersja
# (np. metadane: wykrywanie MP3 bez naglowka LAME, ktore przycina poczatek dzwieku).
WERSJA_ANALIZY_SILNIKA = 1
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
        for plik in ("gen_dat.py", "cue.py", "takt.py", "beatthis.py", "tonacja.py", "config.json"):
            try:
                with open(os.path.join(tu, plik), "rb") as fh:
                    h.update(fh.read())
            except OSError:
                pass
        # z silnik.py tylko funkcje, ktore licza zapamietywane wyniki: zmiany w dzienniku, okladkach czy zapisie
        # pendrive'a nie kasuja juz analiz calej biblioteki (2000 utworow = ok. 45 min liczenia od nowa)
        h.update(("wersja-analizy-%d" % WERSJA_ANALIZY_SILNIKA).encode())
        import inspect
        for fn in (_analizuj_licz, _cue_od_dzwieku, _poczatek_dzwieku_ms, podglad):
            try:
                h.update(inspect.getsource(fn).encode("utf-8"))
            except (OSError, TypeError):
                with open(os.path.abspath(__file__), "rb") as fh:
                    h.update(fh.read())
                break
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
        dl_dat = info["dat_len"]
        dl_ext = info.get("ext_len", len(reszta) - dl_dat)
        dat, ext, ex2 = reszta[:dl_dat], reszta[dl_dat:dl_dat + dl_ext], reszta[dl_dat + dl_ext:]
        os.utime(plik, None)  # LRU: swiezo uzyty wpis
        return info, dat, ext, ex2
    except (OSError, ValueError, KeyError, zlib.error, struct.error):
        return None


def _cache_zapisz(klucz, info, dat, ext, ex2=b""):
    kat = _cache_katalog()
    if not kat:
        return
    try:
        info = dict(info, dat_len=len(dat), ext_len=len(ext))
        js = json.dumps(info).encode("utf-8")
        tmp = os.path.join(kat, klucz + ".tmp%d" % threading.get_ident())
        with open(tmp, "wb") as fh:
            fh.write(struct.pack(">I", len(js)) + js + zlib.compress(dat + ext + ex2, 6))
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
            dat, ext = _czytaj_analize(u)
            ex2 = _czytaj_2ex(u)
            info = {"pend": u["pend"], "bpm": u["bpm"], "confidence": u["confidence"], "reliable": u["reliable"],
                    "pierwszy_ms": ev["pierwszy_ms"], "beatow": ev["beatow"], "czas": ev["czas"],
                    "podglad": ev.get("podglad"), "tonacja": u.get("tonacja_analiza"), "dlugi": ev.get("dlugi")}
            _cache_zapisz(klucz, info, dat, ext, ex2)
        except Exception:  # noqa: BLE001  # pamiec podreczna jest dodatkiem
            pass
    return u, ev


def _z_cache(traf, sciezka, nazwa, nazwa_wzgledna, meta, root, nr, kopiuj_mp3, nazwa_folderu):
    info, dat, ext, ex2 = traf
    if kopiuj_mp3:
        pend = sciezka_wg_folderow(nazwa_folderu, nazwa_wzgledna) if nazwa_folderu else sciezka_na_pendrive(meta, nazwa)
    else:
        rel = os.path.relpath(sciezka, root).replace(os.sep, "/")
        pend = "/" + rel
    if pend != info["pend"]:
        dat, ext = _podmien_ppth(dat, pend), _podmien_ppth(ext, pend)
        if ex2:
            ex2 = _podmien_ppth(ex2, pend)
    analiza = sciezka_analizy(pend)
    crc = zlib.crc32(pend.encode("utf-8")) & 0xFFFFFFFF
    kat = SPOOL["katalog"]
    plik_dat = plik_ext = plik_ex2 = None
    dat_len, ext_len, ex2_len = len(dat), len(ext), len(ex2)
    if kat:
        plik_dat = os.path.join(kat, "%d.dat" % nr)
        plik_ext = os.path.join(kat, "%d.ext" % nr)
        plik_ex2 = os.path.join(kat, "%d.2ex" % nr)
        for p, b in ((plik_dat, dat), (plik_ext, ext), (plik_ex2, ex2)):
            with open(p, "wb") as fh:
                fh.write(b)
        dat = ext = ex2 = None
    u = {"nazwa": nazwa, "meta": meta, "bpm": info["bpm"], "pend": pend, "analiza": analiza,
         "dat": dat, "ext": ext, "dat_plik": plik_dat, "ext_plik": plik_ext, "dat_len": dat_len, "ext_len": ext_len,
         "ex2": ex2, "ex2_plik": plik_ex2, "ex2_len": ex2_len,
         "crc": crc, "src": sciezka, "confidence": info["confidence"], "reliable": info["reliable"],
         "tonacja": _wybierz_tonacje(meta, info.get("tonacja")), "tonacja_analiza": info.get("tonacja")}
    ev = {"typ": "utwor", "i": nr, "n": 0, "nazwa": nazwa_wzgledna.replace(os.sep, "/"),
          "tytul": meta["tytul"], "artysta": meta["artysta"], "bpm": round(info["bpm"], 2),
          "confidence": round(info["confidence"], 3), "pierwszy_ms": info["pierwszy_ms"],
          "beatow": info["beatow"], "czas": info["czas"], "z_pamieci": True}
    if u["tonacja"]:
        ev["tonacja"] = _opis_tonacji(u["tonacja"])
    if info.get("podglad") is not None and nr <= 50:
        ev["podglad"] = info["podglad"]
    if info.get("dlugi"):
        ev["dlugi"] = info["dlugi"]
    return u, ev


TONACJA_Z_TAGU = {"on": True}  # ustawiane z opcji w oknie (przetworz)


def _wybierz_tonacje(meta, wykryta):
    """[tonika, molowa, pewnosc, zrodlo]: tag pliku (gdy jest i opcja wlaczona), inaczej wlasna analiza."""
    tag = meta.get("tonacja_tag")
    if TONACJA_Z_TAGU["on"] and tag:
        return [int(tag[0]), bool(tag[1]), 1.0, "tag"]
    if wykryta:
        return [int(wykryta[0]), bool(wykryta[1]), float(wykryta[2]), "analiza"]
    return None


SYSTEMY_PLIKOW_CDJ = ("msdos", "fat", "fat32", "fat16", "hfs")  # CDJ-2000 czyta FAT, FAT32 i HFS+


def system_plikow(root):
    """Typ systemu plikow dysku (np. "msdos", "exfat", "hfs", "apfs", "ntfs") albo "" gdy nie da sie ustalic."""
    try:
        if sys.platform == "darwin":
            import plistlib
            r = subprocess.run(["/usr/sbin/diskutil", "info", "-plist", root], capture_output=True, timeout=10)
            if r.returncode == 0:
                return str(plistlib.loads(r.stdout).get("FilesystemType") or "").lower()
        elif sys.platform == "win32":
            import ctypes
            buf = ctypes.create_unicode_buffer(64)
            dysk = os.path.splitdrive(os.path.abspath(root))[0] + "\\"
            if ctypes.windll.kernel32.GetVolumeInformationW(dysk, None, 0, None, None, None, buf, 64):
                return buf.value.lower()
    except Exception:  # noqa: BLE001
        pass
    return ""


def test_dysku(root, folder, pliki, rozmiar=8 * 1024 * 1024):
    """Krotki test przed dluga analiza: zapis i odczyt probki na pendrive, odczyt kilku plikow zrodlowych, format.
    Zwraca {"zapis_mb_s", "fs", "ostrzezenia": [...]}; rzuca CDJError, gdy dysk nie nadaje sie do pracy."""
    wynik = {"zapis_mb_s": None, "fs": system_plikow(root), "ostrzezenia": []}
    probka = os.urandom(1024 * 1024) * max(1, rozmiar // (1024 * 1024))
    tmp = os.path.join(root, ".cdj-tool-test.tmp")
    try:
        t = time.time()
        with open(tmp, "wb") as fh:
            fh.write(probka)
            fh.flush()
            os.fsync(fh.fileno())
        dt = max(time.time() - t, 1e-3)
        with open(tmp, "rb") as fh:
            zgodne = fh.read() == probka
        wynik["zapis_mb_s"] = round(len(probka) / 1e6 / dt, 1)
    except OSError as e:
        raise CDJError("disk write test failed: %s" % e, code="DYSK_TEST", user_message=(
            "Test zapisu na pendrive nie powiódł się (%s). Sprawdź, czy dysk nie jest zablokowany do zapisu, czy ma "
            "wolne miejsce i czy jest dobrze podłączony. Nic nie zostało zmienione." % (e.strerror or e))) from e
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    if not zgodne:
        raise CDJError("disk verify failed", code="DYSK_TEST", user_message=(
            "Pendrive zapisuje dane z błędami: próbka odczytana po zapisie różni się od zapisanej. Taki nośnik zgubi "
            "utwory albo bazę. Użyj innego pendrive'a."))
    # odczyt zrodla: poczatek pierwszego, srodkowego i ostatniego pliku
    for rel in {pliki[0], pliki[len(pliki) // 2], pliki[-1]}:
        try:
            with open(os.path.join(folder, rel), "rb") as fh:
                fh.read(1024 * 1024)
        except OSError as e:
            raise CDJError("source read test failed: %s" % e, code="DYSK_TEST", user_message=(
                "Nie mogę odczytać pliku %s (%s). Sprawdź dysk z muzyką (kabel, Pierwsza pomoc w Narzędziu dyskowym)."
                % (os.path.basename(rel), e.strerror or e))) from e
    fs = wynik["fs"]
    if fs and fs not in SYSTEMY_PLIKOW_CDJ:
        wynik["ostrzezenia"].append(
            "Dysk ma format %s. CDJ-2000 czyta tylko FAT32 (i HFS+): odtwarzacz może nie zobaczyć tego nośnika. "
            "Sformatuj go w kroku 2 (to usuwa wszystkie pliki z dysku)." % fs.upper())
    if wynik["zapis_mb_s"] is not None and wynik["zapis_mb_s"] < 3:
        wynik["ostrzezenia"].append(
            "Bardzo wolny zapis na pendrive (%.1f MB/s): kopiowanie muzyki potrwa długo." % wynik["zapis_mb_s"])
    return wynik


# bledy systemu przy odlaczonym albo wadliwym nosniku (macOS przy zniknietym FAT32 zwraca tez EFAULT "Bad address")
BLEDY_NOSNIKA = {errno.EIO, errno.EFAULT, errno.ENXIO, errno.ENODEV, errno.EROFS, errno.ENOSPC}


def blad_nosnika(e, a):
    """Jasny komunikat (CDJError) dla bledu wejscia/wyjscia pendrive'a albo dysku z muzyka; None dla innych bledow."""
    if not isinstance(e, OSError):
        return None
    root = os.path.abspath(a.get("pendrive") or "")
    folder = os.path.abspath(a.get("folder_mp3") or "")
    zniknal = _odlaczony(root) if a.get("pendrive") else None
    zniknal_zrodlo = _odlaczony(folder) if a.get("folder_mp3") else None
    if e.errno not in BLEDY_NOSNIKA and not zniknal and not zniknal_zrodlo:
        return None
    plik = os.path.abspath(str(e.filename)) if e.filename else ""
    w_zrodle = bool(plik) and plik.startswith(folder + os.sep) and not plik.startswith(root + os.sep)
    nazwa = os.path.basename(root.rstrip(os.sep)) or root
    opis = e.strerror or str(e)
    if zniknal_zrodlo or (w_zrodle and not zniknal):
        return CDJError("source disk error: %s" % e, code="DYSK_ZRODLO", user_message=(
            "Błąd odczytu dysku z muzyką (%s). Dysk odłączył się albo jest uszkodzony. Podłącz go ponownie, sprawdź go "
            "w Narzędziu dyskowym (Pierwsza pomoc) i uruchom zapis jeszcze raz: przeanalizowane utwory wezmę z pamięci "
            "podręcznej." % opis))
    if e.errno == errno.ENOSPC and not zniknal:
        return CDJError("stick full: %s" % e, code="PENDRIVE_PELNY", user_message=(
            "Na pendrive %s skończyło się miejsce. Zwolnij miejsce albo użyj większego pendrive'a i uruchom zapis "
            "jeszcze raz." % nazwa))
    if zniknal:
        return CDJError("stick gone: %s" % e, code="DYSK_ODLACZONY", user_message=(
            "Pendrive %s odłączył się w trakcie zapisu. Zapis jest niekompletny. Podłącz go ponownie i uruchom zapis "
            "jeszcze raz: przeanalizowane utwory wezmę z pamięci podręcznej. Jeśli to się powtarza, podłącz pendrive "
            "bezpośrednio do komputera (bez rozgałęźnika) albo użyj innego." % nazwa))
    return CDJError("stick io error: %s" % e, code="PENDRIVE_IO", user_message=(
        "Błąd zapisu na pendrive %s (%s). Pendrive mógł się na chwilę odłączyć albo jest uszkodzony, a zapis jest "
        "niekompletny. Wysuń go, podłącz ponownie, sprawdź w Narzędziu dyskowym (Pierwsza pomoc) i uruchom zapis "
        "jeszcze raz: przeanalizowane utwory wezmę z pamięci podręcznej. Jeśli błąd wróci, użyj innego pendrive'a."
        % (nazwa, opis)))


def _odlaczony(*sciezki):
    """Nazwa dysku (wolumenu), ktory zniknal, gdy ktoras ze sciezek nie istnieje; inaczej None."""
    for p in sciezki:
        if p and not os.path.isdir(p):
            if p.startswith("/Volumes/"):
                return p.split("/")[2]
            return p
    return None


def _zastosuj_korekte(u, kor):
    """Siatka i cue z edytora w plikach analizy utworu (w pamieci albo w plikach tymczasowych)."""
    def czytaj(klucz):
        if u.get(klucz + "_plik"):
            with open(u[klucz + "_plik"], "rb") as fh:
                return fh.read()
        return u[klucz]
    dat, ext = edytor.zbuduj_pliki(czytaj("dat"), czytaj("ext"), kor)
    for klucz, bajty in (("dat", dat), ("ext", ext)):
        if u.get(klucz + "_plik"):
            with open(u[klucz + "_plik"], "wb") as fh:
                fh.write(bajty)
        else:
            u[klucz] = bajty
        u[klucz + "_len"] = len(bajty)
    u["bpm"] = edytor.bpm_utworu(kor)


def _opis_utworu(ev, u):
    """Jedna linia dziennika o utworze: co znalezlismy i skad."""
    m = u["meta"]
    cz = ["%.2f BPM" % ev["bpm"]]
    ton = ev.get("tonacja")
    if ton:
        cz.append("%s / %s (%s)" % (ton["klasyczna"], ton["camelot"], "z tagu" if ton.get("zrodlo") == "tag" else "analiza"))
    if m.get("z_nazwy"):
        cz.append("tagi z nazwy: " + ", ".join(m["z_nazwy"]))
    if ev.get("z_pamieci"):
        cz.append("z pamięci podręcznej")
    if ev.get("dlugi"):
        cz.append("długi utwór, %d odcinków" % ev["dlugi"]["odcinkow"])
    return "✓ %s — %s: %s" % (m["artysta"], m["tytul"], ", ".join(cz))


def _opis_tonacji(ton):
    """Tonacja do pokazania w oknie: obie notacje, pewnosc i zrodlo (tag pliku albo analiza)."""
    return {"klasyczna": tonacja.nazwa(ton[0], ton[1]), "camelot": tonacja.nazwa(ton[0], ton[1], "camelot"),
            "pewnosc": round(float(ton[2]), 2), "zrodlo": ton[3] if len(ton) > 3 else "analiza"}


def _analizuj_licz(sciezka, nazwa, nazwa_wzgledna, meta, root, nr, kopiuj_mp3, dzis, nazwa_folderu=None):
    """Wlasciwa analiza utworu; wywolywana po zajeciu miejsca w bramce pamieci."""
    try:
        y = g.wczytaj(sciezka)
        if meta.get("mp3_bez_gapless"):
            # Na wzorcu rekordboxa (10 plikow MP3 z naglowkiem Info bez LAME) cala os czasu CDJ jest o ok. 46,7 ms
            # wczesniej niz dzwiek z ffmpeg; pliki z naglowkiem LAME zgadzaja sie co do 1-2 ms. Przycinamy poczatek,
            # wiec siatka, waveformy i cue przesuwaja sie razem.
            y = y[int(round(g.MP3_BEZ_GAPLESS_S * g.SR)):]
        dl = len(y) / g.SR
        dlugi = dl > g.DLUGI_PROG_S
        analysis = g.analizuj_dlugi(y) if dlugi else g.analizuj(y)
        
        bpm = analysis["bpm"]
        pierwszy = analysis["pierwszy"]
        confidence = analysis["confidence"]
        reliable = analysis["reliable"]
        
        # Niska pewnosc BPM nie odrzuca utworu (oryginal tez nic nie odrzucal);
        # informacja trafia do zdarzenia w polu "reliable".
        
        if dlugi:  # set/miks: siatka dynamiczna (tempo moze zmieniac sie miedzy fragmentami)
            czasy = analysis["czasy"]
        else:
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
        try:  # waveform 3-pasmowy (.2EX) liczony zawsze, zapisywany na pendrive tylko z opcja
            ex2 = g.zbuduj_2ex(pend, cechy_koloru) if cechy_koloru is not None else b""
        except Exception:  # noqa: BLE001
            ex2 = b""
        pod = podglad(y, czasy - g.OFFSET_REKORDBOX_S, pwav_wynik[1]) if nr <= 50 else None
        try:  # tonacja jest dodatkiem: blad tutaj nie moze zatrzymac eksportu utworu
            if dlugi:  # set ma wiele tonacji; zostaje tylko tonacja z tagu
                raise ValueError("dlugi")
            ton_analiza = tonacja.wykryj(y, g.SR)
            ton_analiza = [int(ton_analiza[0]), bool(ton_analiza[1]), float(ton_analiza[2])] if ton_analiza else None
        except Exception:  # noqa: BLE001
            ton_analiza = None
        ton = _wybierz_tonacje(meta, ton_analiza)
        kat = SPOOL["katalog"]
        if kat:  # wyniki ida na dysk lokalny, zeby pamiec nie rosla z liczba utworow
            plik_dat = os.path.join(kat, "%d.dat" % nr)
            plik_ext = os.path.join(kat, "%d.ext" % nr)
            with open(plik_dat, "wb") as fh:
                fh.write(dat)
            with open(plik_ext, "wb") as fh:
                fh.write(ext)
            plik_ex2 = os.path.join(kat, "%d.2ex" % nr)
            with open(plik_ex2, "wb") as fh:
                fh.write(ex2)
            dat_len, ext_len, ex2_len = len(dat), len(ext), len(ex2)
            dat = ext = ex2 = None
        else:
            plik_dat = plik_ext = plik_ex2 = None
            dat_len, ext_len, ex2_len = len(dat), len(ext), len(ex2)
        
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
            "ex2": ex2,
            "ex2_plik": plik_ex2,
            "ex2_len": ex2_len,
            "crc": crc,
            "src": sciezka,
            "confidence": confidence,
            "reliable": reliable,
            "tonacja": ton,
            "tonacja_analiza": ton_analiza
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
        if dlugi:
            ev["dlugi"] = {"odcinkow": analysis["odcinkow"], "tempa": analysis["tempa_serii"][:40]}
        
        if pod is not None:
            ev["podglad"] = pod
        if ton:
            ev["tonacja"] = _opis_tonacji(ton)
        
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


def _nie_usypiaj():
    """Nie pozwala komputerowi zasnąć, dopóki działa ten proces; jego koniec (także przerwanie) zdejmuje blokadę."""
    try:
        if sys.platform == "darwin":
            # -i: bez uśpienia przy bezczynności, -m: dysk nie zasypia, -s: system nie zasypia na zasilaczu;
            # -w: caffeinate kończy się razem z tym procesem
            subprocess.Popen(["/usr/bin/caffeinate", "-ims", "-w", str(os.getpid())],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        if sys.platform == "win32":
            import ctypes
            ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
            return bool(ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED))
    except Exception:  # noqa: BLE001  # to dodatek: bez niego analiza działa jak dawniej
        pass
    return False


class OkladkiTla:
    """Okladki szukane w tle, gdy kolejne utwory koncza analize: z tagow pliku, a gdy ich brak i opcja jest
    wlaczona, z sieci. Przy duzej bibliotece wyszukiwanie w sieci szlo wczesniej dopiero po analizie
    (276 utworow = kilka minut czekania); teraz konczy sie razem z nia."""

    def __init__(self, z_sieci, emit=None):
        self.emit = emit
        self.pula = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        self.fut = {}
        self.siec = None
        if z_sieci:
            kc = _cache_katalog()
            self.siec = okladki.Siec(os.path.join(os.path.dirname(kc), "okladki-siec") if kc else None)
        self.szukano = 0
        self.z_sieci = 0
        self._lock = threading.Lock()

    def _jedna(self, u):
        ob = okladki.wyciagnij(u["src"])
        if not ob and self.siec is not None:
            m = u["meta"]
            with self._lock:
                self.szukano += 1
            ob = self.siec.szukaj(m["artysta"], m["tytul"], m["album"], os.path.basename(u["src"]))
            if ob:
                with self._lock:
                    self.z_sieci += 1
            if self.emit:
                self.emit({"typ": "log", "tekst": "%s okładka %s: %s — %s" % (
                    "✓" if ob else "·", "z sieci" if ob else "nie znaleziona w sieci", m["artysta"], m["tytul"])})
        elif not ob and self.emit:
            m = u["meta"]
            self.emit({"typ": "log", "tekst": "· brak okładki w pliku: %s — %s" % (m["artysta"], m["tytul"])})
        return (okladki.skrot(ob), ob) if ob else None

    def dodaj(self, i, u):
        self.fut[i] = self.pula.submit(self._jedna, u)

    def wynik(self, i, u):
        f = self.fut.get(i)
        try:
            return f.result() if f is not None else self._jedna(u)
        except Exception:  # noqa: BLE001  # okladka jest dodatkiem
            return None

    def zakoncz(self):
        self.pula.shutdown(wait=True)

    def przerwij(self):
        for f in self.fut.values():
            f.cancel()
        self.pula.shutdown(wait=False)


class KopiarkaTla:
    """Kopiuje pliki audio przeanalizowanych już utworów na pendrive, gdy analiza kolejnych trwa dalej.

    Jeden wątek, pliki po kolei (pendrive najlepiej znosi zapis sekwencyjny). Plik powstaje pod nazwą
    tymczasową i dopiero kompletny zastępuje docelowy, więc przerwanie nie zostawia uciętego pliku pod
    ścieżką, którą zna dotychczasowa biblioteka. Etap zapisu po analizie pomija pliki, które już są na
    miejscu (ten sam rozmiar), i dokopiowuje resztę. Baza export.pdb nadal powstaje na samym końcu."""

    def __init__(self, root):
        self.root = root
        self.kolejka = queue.Queue()
        self.stop = threading.Event()
        self.plikow = 0
        self.bajtow = 0
        self.w_kolejce = 0   # bajty wszystkich plikow przekazanych do skopiowania
        self.zapisane = 0    # bajty juz na pendrive (z pominietymi, bo byly na miejscu)
        self.blad = None
        self.watek = threading.Thread(target=self._praca, name="kopiarka-tla", daemon=True)
        self.watek.start()

    def dodaj(self, u):
        self.w_kolejce += u["meta"]["rozmiar"]
        self.kolejka.put(u)

    def _praca(self):
        while True:
            u = self.kolejka.get()
            if u is None or self.stop.is_set():
                return
            try:
                self._kopiuj(u)
            except Exception as e:  # noqa: BLE001  # etap zapisu spróbuje jeszcze raz i wtedy zgłosi błąd
                self.blad = e
                return

    def _kopiuj(self, u):
        cel = os.path.join(self.root, u["pend"].lstrip("/"))
        rozmiar = u["meta"]["rozmiar"]
        if os.path.exists(cel) and os.path.getsize(cel) == rozmiar:
            self.zapisane += rozmiar
            return
        os.makedirs(os.path.dirname(cel), exist_ok=True)
        tmp = cel + ".cdjpart"
        try:
            with open(u["src"], "rb") as fin, open(tmp, "wb") as fout:
                while True:
                    if self.stop.is_set():
                        raise InterruptedError("kopiowanie w tle przerwane")
                    blok = fin.read(4 * 1024 * 1024)
                    if not blok:
                        break
                    fout.write(blok)
                    self.zapisane += len(blok)
            os.replace(tmp, cel)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        self.plikow += 1
        self.bajtow += rozmiar

    def zakoncz(self, przerwij=False, postep=None):
        if przerwij:
            self.stop.set()
        self.kolejka.put(None)
        while self.watek.is_alive():
            self.watek.join(0.4)
            if postep is not None:
                postep(self.zapisane, self.w_kolejce)


# Pliki, ktore na pendrive zapisuje mod RaftTone v1.1 (siatka poprawiona na decku, bank hot cue D-F).
# Wpisy sa przypisane do numeru katalogu analizy utworu (USBANLZ/Pxxx/<numer>), ktory zalezy tylko od sciezki
# pliku na pendrive, wiec zostaja wazne po ponownym zapisie. CDJ Tool ich nie zmienia, takze przy zastapieniu PIONEER.
PLIKI_MODA = ("MODGRID.BIN", "MODHCUE.BIN")


def _czytaj_analize(u):
    if u["dat_plik"]:
        with open(u["dat_plik"], "rb") as fh:
            dat = fh.read()
        with open(u["ext_plik"], "rb") as fh:
            ext = fh.read()
        return dat, ext
    return u["dat"], u["ext"]


def _czytaj_2ex(u):
    if u.get("ex2_plik"):
        with open(u["ex2_plik"], "rb") as fh:
            return fh.read()
    return u.get("ex2") or b""


def przetworz(a, emit):
    """Punkt wejscia: sprzata katalog tymczasowy z wynikami analizy po zakonczeniu (takze po bledzie)."""
    _niski_priorytet()
    TONACJA_Z_TAGU["on"] = bool(a.get("tonacja_z_tagu", True))
    if a.get("nie_usypiaj", True):
        _nie_usypiaj()
    blokada = threading.Lock()
    emit_surowy = emit

    def emit(ev):  # zdarzenia wysylaja tez watki w tle (okladki, kopiowanie): po jednej linii naraz
        with blokada:
            emit_surowy(ev)
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
    global _T0
    _T0 = time.time()  # znaczniki [+N s] i czasy w dzienniku od startu tego zadania
    FLAC["on"] = bool(a.get("flac"))
    DLUGIE["on"] = bool(a.get("dlugie", True))
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

    # krotki test dysku: klopot z nosnikiem ma wyjsc w kilka sekund, a nie po godzinie analizy
    podsum = {"ostrzezenia": []}
    if _na_wolumenie(root) and a.get("test_dysku", True):
        emit({"typ": "etap", "tekst": "Sprawdzam dysk..."})
        td = test_dysku(root, folder, pliki)
        podsum.update(zapis_mb_s=td["zapis_mb_s"], fs=td["fs"], ostrzezenia=list(td["ostrzezenia"]))
        emit({"typ": "log", "tekst": "Test dysku: zapis %s MB/s, odczyt i weryfikacja OK, format %s." % (
            td["zapis_mb_s"], (td["fs"] or "nieznany").upper())})
        for o in td["ostrzezenia"]:
            emit({"typ": "log", "tekst": "⚠ " + o})
            emit({"typ": "ostrzezenie", "tekst": o})

    # Limit pamieci: analiza zajmie najwyzej ulamek dostepnego RAM (domyslnie 35%).
    pelna_moc = bool(a.get("pelna_moc", True))
    # pelna moc: wiecej procesow analizy i wiekszy udzial wolnej pamieci (procesy zyja krotko)
    budzet_mb = float(a.get("ram_limit_mb") or budzet_ram_mb(55 if pelna_moc else None))
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

    kopiarka = None
    if kopiuj_mp3 and a.get("kopiuj_w_tle", True):
        try:
            potrzeba = sum(os.path.getsize(os.path.join(folder, p)) for p in pliki)
            # tylko gdy na pendrive na pewno zmieszczą się wszystkie pliki z zapasem; inaczej zostaje
            # dotychczasowa kolejność, w której brak miejsca jest wykrywany przed zapisem czegokolwiek
            if shutil.disk_usage(root).free > potrzeba * 1.05 + 256 * 1024 * 1024:
                kopiarka = KopiarkaTla(root)
        except OSError:
            kopiarka = None

    # poprawki siatki i cue zrobione w edytorze: zastepuja automatyczne przy kazdym kolejnym zapisie
    korekty = edytor.wczytaj_korekty()

    # okladki szukane w tle w trakcie analizy (z tagow i opcjonalnie z sieci), a nie dopiero po niej
    zapisz_okladki = bool(a.get("okladki", True))
    okl_tlo = OkladkiTla(bool(a.get("okladki_siec")), emit) if zapisz_okladki else None
    
    pula_proc = None
    if len(pliki) >= 4 and os.environ.get("CDJ_PROCESY", "1") == "1":
        try:
            import multiprocessing
            # Ogranicz do max polowy rdzeni zeby nie przegrzewac
            rdz = os.cpu_count() or 4
            n_proc = max(2, min(rdz - 2 if pelna_moc else rdz // 2, 12 if pelna_moc else 6, workers, len(pliki)))
            # numpy/Accelerate/ONNX w procesach roboczych po 1 watku (procesy dziedzicza srodowisko przy starcie):
            # wiele procesow po 1 watku jest szybsze niz kilka wielowatkowych walczacych o rdzenie
            for zm in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
                       "NUMEXPR_NUM_THREADS"):
                os.environ.setdefault(zm, "1")
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

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pula:
            fn_an = (lambda *x: _analizuj_jeden_proc(pula_proc, *x)) if pula_proc is not None else _analizuj_jeden
            nazwa_fut = {}
            for nr, rel in enumerate(pliki, 1):
                nazwa_fut[pula.submit(fn_an, folder, root, nr, rel, kopiuj_mp3, dzis, nazwa_folderu)] = rel
            futures = list(nazwa_fut)
            for future in concurrent.futures.as_completed(futures):
                try:
                    utwor, ev = future.result()
                    ev["n"] = len(pliki)
                    if utwor:
                        # zrodlo tonacji wybierane tutaj (procesy analizy nie znaja opcji z okna)
                        utwor["tonacja"] = _wybierz_tonacje(utwor["meta"], utwor.get("tonacja_analiza"))
                        if utwor["tonacja"]:
                            ev["tonacja"] = _opis_tonacji(utwor["tonacja"])
                        else:
                            ev.pop("tonacja", None)
                        utwory_wg_indeksu[ev["i"]] = utwor
                        kor = korekty.get(edytor.klucz_korekty(utwor["pend"], utwor["meta"]["rozmiar"])) if korekty else None
                        if kor:
                            try:
                                _zastosuj_korekte(utwor, kor)
                                ev["bpm"] = utwor["bpm"]
                                podsum["korekty"] = podsum.get("korekty", 0) + 1
                                emit({"typ": "log", "tekst": "✎ %s: Twoja poprawka siatki i cue z edytora." % ev["nazwa"]})
                            except Exception as e:  # noqa: BLE001
                                emit({"typ": "log", "tekst": "⚠ %s: nie udało się zastosować poprawki z edytora (%s)." % (ev["nazwa"], e)})
                        podsum["z_pamieci"] = podsum.get("z_pamieci", 0) + (1 if ev.get("z_pamieci") else 0)
                        podsum["tagi_z_nazw"] = podsum.get("tagi_z_nazw", 0) + (1 if utwor["meta"].get("z_nazwy") else 0)
                        podsum["dlugie"] = podsum.get("dlugie", 0) + (1 if ev.get("dlugi") else 0)
                        if kopiarka is not None:
                            kopiarka.dodaj(utwor)
                        if okl_tlo is not None:
                            okl_tlo.dodaj(ev["i"], utwor)
                    emit(ev)
                    if utwor:
                        emit({"typ": "log", "tekst": _opis_utworu(ev, utwor)})
                    if ev.get("dlugi"):
                        tem = ev["dlugi"]["tempa"]
                        emit({"typ": "log", "tekst": "Długi utwór %s: %d min, analiza w %d odcinkach, %s." % (
                            ev["nazwa"], round(ev["czas"] / 60), ev["dlugi"]["odcinkow"],
                            "stałe tempo %.2f BPM" % tem[0] if len(tem) == 1 else
                            "siatka dynamiczna, tempa: " + ", ".join("%.2f" % t for t in tem[:12])
                            + (" …" if len(tem) > 12 else ""))})
                except Exception as e:  # noqa: BLE001
                    brak = _odlaczony(folder, root)
                    if brak:  # dysk zniknal w trakcie: nie oznaczamy reszty utworow jako "zle MP3"
                        for f in futures:
                            f.cancel()
                        raise CDJError("volume gone: %s" % brak, code="DYSK_ODLACZONY", user_message=(
                            "Dysk %s został odłączony w trakcie pracy (przeanalizowano %d z %d utworów). Na pendrive nic "
                            "nie zostało zapisane. Podłącz go ponownie i uruchom zapis jeszcze raz: przeanalizowane już "
                            "utwory wezmę z pamięci podręcznej. Jeśli dysk odłącza się często, sprawdź kabel, użyj "
                            "portu bez rozgałęźnika (dysk na USB może potrzebować więcej prądu) i wyłącz w Ustawieniach "
                            "systemowych → Bateria/Energia usypianie dysków." % (brak, zakonczone, len(pliki))))
                    emit({"typ": "utwor_blad", "nazwa": nazwa_fut.get(future, "?").replace(os.sep, "/"),
                          "tekst": e.user_message if isinstance(e, CDJError) else str(e),
                          **({"kod": e.code} if isinstance(e, CDJError) else {})})
                    errors.append(e)
            
                zakonczone += 1
                emit({"typ": "postep_analizy", "i": zakonczone, "n": len(pliki),
                      "w_tle": kopiarka.plikow if kopiarka is not None else None})
    except BaseException:
        if kopiarka is not None:
            kopiarka.zakoncz(przerwij=True)
        if okl_tlo is not None:
            okl_tlo.przerwij()
        if pula_proc is not None:
            pula_proc.shutdown(wait=False)
        raise
    if pula_proc is not None:
        pula_proc.shutdown(wait=True)
    t_analiza = time.time() - _T0
    podsum.update(pominiete=len(errors), analiza_s=round(t_analiza), s_na_utwor=round(t_analiza / max(1, zakonczone), 2))
    emit({"typ": "log", "tekst": "Analiza: %d utworów w %.1f min (%.1f s na utwór)." % (
        zakonczone, t_analiza / 60, t_analiza / max(1, zakonczone))})
    if kopiarka is not None:
        emit({"typ": "etap", "tekst": "Kończę kopiowanie w tle..." + _t()})
        t_k = time.time()
        # pasek i czas do konca: bajty, ktore zostaly do skopiowania po analizie
        od, pozostalo = kopiarka.zapisane, max(0, kopiarka.w_kolejce - kopiarka.zapisane)
        emit({"typ": "kopiuj_start", "razem": pozostalo, "n": kopiarka.plikow})
        kopiarka.zakoncz(postep=lambda z, r: emit({"typ": "kopiuj_bajty", "zrobione": min(pozostalo, max(0, z - od)),
                                                    "razem": pozostalo}))
        if kopiarka.bajtow:
            emit({"typ": "log", "tekst": "Po analizie czekanie na kopiowanie: %.1f min." % ((time.time() - t_k) / 60)})
        emit({"typ": "log", "tekst": "W tle skopiowano %d plików (%.0f MB)%s." % (
            kopiarka.plikow, kopiarka.bajtow / 1e6,
            "; reszta zostanie skopiowana teraz (%s)" % kopiarka.blad if kopiarka.blad else "")})
    
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

    # Hot cue: w DAT tylko A-C (tyle zna firmware CDJ-2000; deck nadpisujac DAT psul dalsze wpisy), w EXT do 6.
    # Mod RaftTone v1.1 bierze bank D-F z EXT (sprawdzone na decku 2026-11-04).

    # tonacje: tabela kluczy (typ 5) wspolna dla calej biblioteki; istniejace wpisy zachowujemy
    zapisz_tonacje = bool(a.get("tonacja", False))
    notacja = "camelot" if a.get("notacja") == "camelot" else "klasyczna"
    klucze = {n: kid for kid, n in lib.get("klucze", [])}
    klucze_nast = max(list(klucze.values()) + [0]) + 1
    zmiana_kluczy = False

    def id_klucza(u):
        nonlocal klucze_nast, zmiana_kluczy
        if not zapisz_tonacje or not u.get("tonacja"):
            return None
        nm = tonacja.nazwa(u["tonacja"][0], u["tonacja"][1], notacja)
        if nm not in klucze:
            klucze[nm] = klucze_nast
            klucze_nast += 1
            zmiana_kluczy = True
        return klucze[nm]

    # okladki z tagow plikow: miniatury 80/240 px w PIONEER/Artwork/00001, tabela 13 jak w rekordboxie
    art_dla, art_pliki, art_wiersze = {}, [], []
    if zapisz_okladki:
        emit({"typ": "etap", "tekst": "Okładki albumów..." + _t()})
        obrazy = [okl_tlo.wynik(i, utwory_wg_indeksu[i]) for i in sorted(utwory_wg_indeksu)]
        okl_tlo.zakoncz()
        podsum.update(okladki=sum(1 for r in obrazy if r), okladki_z_sieci=okl_tlo.z_sieci,
                      bez_okladki=sum(1 for r in obrazy if not r))
        if okl_tlo.siec is not None and okl_tlo.szukano:
            emit({"typ": "log", "tekst": "Okładki z sieci (Deezer, iTunes): znaleziono %d z %d%s." % (
                okl_tlo.z_sieci, okl_tlo.szukano,
                "; brak połączenia z siecią, pominięto resztę" if okl_tlo.siec.wylaczone else "")})
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pula:
            unikalne = {}
            for r in obrazy:
                if r:
                    unikalne.setdefault(r[0], r[1])
            mini = dict(zip(unikalne, pula.map(lambda ob: okladki.miniatury(ob, FFMPEG), unikalne.values())))
        stare_art = {s["file_path"]: f["artwork"] for f, s in lib["utwory"]}
        uzywane = {f["artwork"] for f, s in lib["utwory"] if s["file_path"] not in nowe_sciezki and f["artwork"]}
        nast_art = max(list(uzywane) + [0]) + 1
        id_obrazu = {}
        for u, r in zip(utwory, obrazy):
            if not r or not mini.get(r[0]):
                continue
            if r[0] not in id_obrazu:
                stary = stare_art.get(u["pend"], 0)
                if stary and stary not in uzywane and stary not in id_obrazu.values():
                    id_obrazu[r[0]] = stary  # ten sam utwor ponownie: ta sama nazwa pliku okladki
                else:
                    while nast_art in id_obrazu.values():
                        nast_art += 1
                    id_obrazu[r[0]] = nast_art
                    nast_art += 1
            art_dla[u["pend"]] = id_obrazu[r[0]]
        for h, aid in id_obrazu.items():
            sciezka_bazy, mala, duza = okladki.sciezki(aid)
            art_pliki.append((mala, mini[h][0]))
            art_pliki.append((duza, mini[h][1]))
        art_wiersze = sorted([(aid, pth) for aid, pth in lib.get("okladki", []) if aid in uzywane]
                             + [(aid, okladki.sciezki(aid)[0]) for aid in id_obrazu.values()])
        bez = sum(1 for r in obrazy if not r)
        emit({"typ": "log", "tekst": "Okładki: %d utworów z okładką (%d różnych obrazów), %d bez okładki."
              % (len(art_dla), len(id_obrazu), bez)})

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
        kid = id_klucza(u)
        if kid is not None:
            pola["key"] = kid
        if zapisz_okladki:
            pola["artwork"] = art_dla.get(u["pend"], 0)
        napisy = dict(
            title=m["tytul"], filename=os.path.basename(u["pend"]), file_path=u["pend"],
            analyze_path=u["analiza"], analyze_date=dzis,
            s4="",  # jak w eksporcie Rekordboxa (szablon mial "1")
            # Hot Cue Auto Load: domyslnie wylaczone (hot cue tylko zaznaczone, ladowane recznie przez CALL);
            # "ON" jak w Rekordboksie, gdy odtwarzacz ma w UTILITY ustawienie rekordbox SETTING
            autoload="ON" if a.get("autoload") else "",
        )
        if u["pend"] in wg_sciezki:  # ten sam plik analizowany ponownie: zachowaj id, gatunek, ocene, odtworzenia
            i = wg_sciezki[u["pend"]]
            f, s = dict(kodowane[i][0]), dict(kodowane[i][1])
            f.update(pola); s.update(napisy)
            kodowane[i] = (f, s)
            id_nowych.append(f["id"])
        else:
            f = dict(f_szablon)
            f.update(key=0)
            f.update(artwork=0)
            f.update(pola, id=nast_id, label=0, genre=0, composer=0, orig_artist=0,
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

    def wezel(rodzic, nazwa, folder):
        """Istniejacy wezel drzewa playlist (rodzic, nazwa, typ) albo nowy na koncu listy rodzica."""
        for par, srt, pid, fold, n in drzewo:
            if par == rodzic and bool(fold) == folder and n == nazwa:
                return pid
        pid = max([p for _, _, p, _, _ in drzewo] + [0]) + 1
        srt = max([s for par, s, _, _, _ in drzewo if par == rodzic] + [-1]) + 1
        drzewo.append((rodzic, srt, pid, folder, nazwa))
        return pid

    def dodaj_do(pl_id, tidy):
        juz = {tid for _, tid, pid in wpisy if pid == pl_id}
        idx = max([e for e, _, pid in wpisy if pid == pl_id] + [0])
        for tid in tidy:
            if tid not in juz:
                idx += 1
                wpisy.append((idx, tid, pl_id))
                juz.add(tid)

    # podfoldery zrodla -> foldery playlist (widok Playlist odwzorowuje uklad folderow; w widoku Folder
    # CDJ-2000 nie pokazuje BPM z bazy, a w widoku Playlist tak)
    def katalog(u):
        rel = os.path.relpath(u["src"], folder).replace(os.sep, "/")
        return tuple(unicodedata.normalize("NFC", c)[:60] for c in rel.split("/")[:-1])

    kat_utworow = [katalog(u) for u in utwory]
    if a.get("playlisty_z_folderow", True) and any(kat_utworow):
        z_podfolderami = {p[:k] for p in kat_utworow for k in range(len(p))}

        def folder_id(prefix):
            pid = wezel(0, nazwa_pl, True)
            for nazwa in prefix:
                pid = wezel(pid, nazwa, True)
            return pid

        grupy = {}
        for p, tid in zip(kat_utworow, id_nowych):
            if p in z_podfolderami or not p:  # katalog z podkatalogami: jego pliki w playliscie wewnatrz
                pl = wezel(folder_id(p), p[-1] if p else nazwa_pl, False)
            else:
                pl = wezel(folder_id(p[:-1]), p[-1], False)
            grupy.setdefault(pl, []).append(tid)
        for pl, tidy in grupy.items():
            dodaj_do(pl, tidy)
    else:
        dodaj_do(wezel(0, nazwa_pl, False), id_nowych)

    tabele = {
        0: zgrupowane([(lambda sh, f=f, s=s: pw.track_row(sh, f, s)) for f, s in kodowane]),
        2: zgrupowane([(lambda sh, aid=aid, n=n: pw.artist_row(sh, aid, n)) for n, aid in artysci.items()]),
        3: zgrupowane([(lambda sh, alid=alid, n=n, ar=ar: pw.album_row(sh, alid, ar, n))
                       for n, alid, ar in ((n, alid, dict((x[0], x[1]) for x in lib["albumy"]).get(alid, 0))
                                           for n, alid in albumy.items())]),
        7: [pw.playlist_tree_row(par, srt, pid, fold, n) for par, srt, pid, fold, n in drzewo],
        8: [pw.playlist_entry_row(e, tid, pid) for e, tid, pid in wpisy],
    }
    if zmiana_kluczy:  # nowe nazwy tonacji: tabela kluczy od nowa (stare wpisy zachowuja swoje id)
        tabele[pw.TYPE_KEYS] = [pw.key_row(kid, n) for n, kid in sorted(klucze.items(), key=lambda x: x[1])]
    if art_wiersze:  # wiersze uzywanych okladek (bez pustych wpisow wzorca)
        tabele[pw.TYPE_ARTWORK] = [pw.art_row(aid, pth) for aid, pth in art_wiersze]
    pdb = pw.zbuduj_pdb(pdb_wzor, tabele)

    pioneer = os.path.join(root, "PIONEER")
    zapisz_2ex = bool(a.get("trzy_pasma", False))
    dat_bajty = sum(u["dat_len"] + u["ext_len"] + (u.get("ex2_len", 0) if zapisz_2ex else 0) for u in utwory)
    dane_bajty = dat_bajty + len(pdb) + sum(len(b) for _, b in art_pliki)
    stat = os.path.join(WZORZEC, "PIONEER")
    
    szablony = []
    for dp, _, fn in os.walk(stat):
        for nm in fn:
            src = os.path.join(dp, nm)
            rel = os.path.relpath(src, stat)
            szablony.append((src, os.path.join(pioneer, rel), os.path.getsize(src)))
    
    def _juz_na_miejscu(u):
        try:
            return os.path.getsize(os.path.join(root, u["pend"].lstrip("/"))) == u["meta"]["rozmiar"]
        except OSError:
            return False

    wolne = shutil.disk_usage(root).free
    do_zapisu = dane_bajty + sum(rozmiar for _, dst, rozmiar in szablony
                                 if a.get("zastap_pioneer", False) or not os.path.exists(dst))
    do_zapisu += sum(u["meta"]["rozmiar"] for u in utwory if not _juz_na_miejscu(u)) if kopiuj_mp3 else 0
    
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
            docelowe.extend(os.path.join(root, u["pend"].lstrip("/")) for u in utwory if not _juz_na_miejscu(u))
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
            pominiete = []
            cel = kopia_zapasowa(pioneer, root, pominiete)
            emit({"typ": "kopia", "sciezka": cel})
            if pominiete:
                emit({"typ": "log", "tekst": "Kopia zapasowa: pominięto %d nieczytelnych plików (zostaną zapisane od "
                      "nowa), np. %s" % (len(pominiete), "; ".join("%s (%s)" % p for p in pominiete[:3]))})
            pliki_moda = {}
            for nm in PLIKI_MODA:
                try:
                    with open(os.path.join(pioneer, nm), "rb") as fh:
                        pliki_moda[nm] = fh.read()
                except OSError:
                    pass
            
            def pomin_znikniety_sidecar(func, sciezka, exc_info):
                blad = exc_info[1]
                if isinstance(blad, FileNotFoundError) and os.path.basename(sciezka).startswith("._"):
                    return
                raise blad

            shutil.rmtree(pioneer, onerror=pomin_znikniety_sidecar)
            if pliki_moda:
                os.makedirs(pioneer, exist_ok=True)
                for nm, bajty in pliki_moda.items():
                    with open(os.path.join(pioneer, nm), "wb") as fh:
                        fh.write(bajty)
                emit({"typ": "log", "tekst": "Zachowano pliki moda RaftTone (%s): siatka i hot cue poprawione na decku."
                      % ", ".join(sorted(pliki_moda))})
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
        cel_2ex = os.path.splitext(dst)[0] + ".2EX"
        if zapisz_2ex and u.get("ex2_len"):
            if u.get("ex2_plik"):
                shutil.copyfile(u["ex2_plik"], cel_2ex)
            else:
                with open(cel_2ex, "wb") as fh:
                    fh.write(u["ex2"])
        elif os.path.exists(cel_2ex):
            try:  # bez opcji nie zostawiamy starego .2EX obok nowych DAT/EXT
                os.remove(cel_2ex)
            except OSError:
                pass
    
    for rel, bajty in art_pliki:
        cel_art = os.path.join(root, rel.lstrip("/"))
        os.makedirs(os.path.dirname(cel_art), exist_ok=True)
        with open(cel_art, "wb") as fh:
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
    # os.sync() oproznia bufory WSZYSTKICH dyskow: zawieszony inny nosnik (np. wadliwy pendrive) blokowalby go bez
    # konca. Baza jest juz zapisana z fsync; na reszte czekamy najwyzej minute i konczymy.
    if hasattr(os, "sync"):
        w = threading.Thread(target=os.sync, name="sync", daemon=True)
        w.start()
        w.join(60)

    stare = [n for n in ("exportExt.pdb", "exportLibrary.db")
             if os.path.exists(os.path.join(pioneer, "rekordbox", n))]
    
    emit({"typ": "etap", "tekst": "Koniec zapisu" + _t()})
    podsum.update(utwory=len(utwory), czas_s=round(time.time() - _T0))
    emit({"typ": "koniec", "ok": True, "liczba": len(utwory), "pendrive": root, "stare_pliki": stare,
          "podsumowanie": podsum})
