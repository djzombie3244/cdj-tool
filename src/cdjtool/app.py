#!/usr/bin/env python3
"""Lokalne zaplecze CDJ Tool; w wydaniu offline składniki są dołączone do aplikacji."""
import http.server
import json
import os
import re
import plistlib
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
import concurrent.futures
import fcntl
import tempfile

KAT = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True  # do not modify the (signed) app bundle at run time
NATIVE = os.environ.get("CDJ_NATIVE") == "1"
BUNDLED = os.environ.get("CDJ_BUNDLED") == "1"
PORT = int(os.environ.get("CDJ_PORT", "0" if NATIVE else "8766"))
TOKEN = secrets.token_urlsafe(18)
DEV = os.environ.get("CDJ_DEV") == "1"
OSASCRIPT = os.environ.get("CDJ_OSASCRIPT", "osascript")
SUPPORT = os.path.expanduser("~/Library/Application Support/CDJ-Tool")
VENV = os.path.join(SUPPORT, "venv")
VENV_PY = os.path.join(VENV, "bin", "python3")

LOCK = threading.Lock()
STAN = {
    "setup": {"stan": "sprawdzam", "log": []},
    "job": {"stan": "brak"},
    "benchmark": {"stan": "brak"},
    "ostatnio": time.time(),
    "widziano": False,
}
PODGLADY = {}
PROC = {"p": None}
FFMPEG = {"sciezka": os.environ.get("CDJ_FFMPEG", "")}


def dodaj_log(lista, linia, limit=80):
    lista.append(linia.rstrip())
    del lista[:-limit]


def python_roboczy():
    if BUNDLED:
        return os.path.join(os.path.dirname(KAT), "Frameworks", "Python3Bundled.framework",
                            "Versions", "3.9", "bin", "python3.9")
    # If the app-specific environment exists, always use it for analysis.
    # A stray CDJ_DEV variable in the launch environment must not send the
    # worker to Apple's system Python, where NumPy may be unavailable/broken.
    if os.path.isfile(VENV_PY):
        return VENV_PY
    return sys.executable if DEV else VENV_PY


# ------------------------------------------------------------ instalacja ---
def uruchom_z_logiem(cmd, log):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    for linia in p.stdout:
        with LOCK:
            dodaj_log(log, linia)
    return p.wait()


def przygotuj():
    s = STAN["setup"]
    try:
        if BUNDLED:
            env = dict(os.environ)
            env.pop("PYTHONPATH", None)
            probe_code = ("import os, sys; sys.path.insert(0, os.environ['CDJ_SITE_PACKAGES']); "
                          "import numpy, scipy, imageio_ffmpeg")
            probe = subprocess.run([python_roboczy(), "-c", probe_code],
                                   capture_output=True, text=True, env=env)
            if probe.returncode != 0:
                raise RuntimeError("Nie udało się uruchomić dołączonych bibliotek: " +
                                   (probe.stderr or probe.stdout).strip())
            ffmpeg = FFMPEG["sciezka"]
            if not ffmpeg or not os.path.isfile(ffmpeg) or not os.access(ffmpeg, os.X_OK):
                raise RuntimeError("W pakiecie brakuje programu FFmpeg.")
            sprawdzenie = subprocess.run([ffmpeg, "-version"], capture_output=True)
            if sprawdzenie.returncode != 0:
                raise RuntimeError("Dołączony FFmpeg nie uruchamia się.")
            s["stan"] = "gotowe"
            return
        if DEV:
            FFMPEG["sciezka"] = os.environ.get("CDJ_FFMPEG") or shutil.which("ffmpeg") or ""
            if not FFMPEG["sciezka"]:
                raise RuntimeError("Tryb deweloperski: zainstaluj ffmpeg lub ustaw CDJ_FFMPEG. / "
                                   "Dev mode: install ffmpeg or set CDJ_FFMPEG.")
            s["stan"] = "gotowe"
            return
        if os.path.exists(VENV_PY) and subprocess.run(
                [VENV_PY, "-c", "import numpy, scipy, imageio_ffmpeg"], capture_output=True).returncode == 0:
            pass
        else:
            s["stan"] = "instalacja"
            os.makedirs(SUPPORT, exist_ok=True)
            if uruchom_z_logiem([sys.executable, "-m", "venv", VENV], s["log"]) != 0:
                raise RuntimeError("Nie udalo sie utworzyc srodowiska Pythona.")
            kod = uruchom_z_logiem([VENV_PY, "-m", "pip", "install", "--disable-pip-version-check",
                                    "numpy", "scipy", "imageio-ffmpeg"], s["log"])
            if kod != 0:
                raise RuntimeError("Instalacja skladnikow nie powiodla sie. Sprawdz polaczenie z internetem.")
        r = subprocess.run([VENV_PY, "-c", "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"],
                           capture_output=True, text=True)
        kandydaci = [r.stdout.strip()] if r.returncode == 0 else []
        systemowy = shutil.which("ffmpeg")
        if systemowy:
            kandydaci.append(systemowy)
        try:
            kod = ("import imageio_ffmpeg, glob, os; "
                   "print('\\n'.join(glob.glob(os.path.join(os.path.dirname("
                   "imageio_ffmpeg.__file__), 'binaries', 'ffmpeg-*'))))")
            b = subprocess.run([VENV_PY, "-c", kod], capture_output=True, text=True)
            if b.returncode == 0:
                kandydaci.extend(b.stdout.splitlines())
        except Exception:  # noqa: BLE001
            pass
        FFMPEG["sciezka"] = ""
        for kandydat in kandydaci:
            if not kandydat or not os.path.isfile(kandydat) or not os.access(kandydat, os.X_OK):
                continue
            sprawdzenie = subprocess.run([kandydat, "-version"], capture_output=True, text=True)
            if sprawdzenie.returncode == 0:
                FFMPEG["sciezka"] = kandydat
                break
        if not FFMPEG["sciezka"]:
            szczegoly = (r.stderr or r.stdout or "").strip()
            raise RuntimeError("Nie znaleziono działającego programu ffmpeg. " + szczegoly)
        s["stan"] = "gotowe"
    except Exception as e:  # noqa: BLE001
        s["stan"] = "blad"
        s["blad"] = str(e)


# ------------------------------------------------------------ wybor folderu -
def wybierz_folder(komunikat, start=None):
    domyslny = ""
    if start and os.path.isdir(start):
        domyslny = ' default location (POSIX file "%s" as alias)' % start.replace('"', "")
    skrypt = ('tell application "System Events"\n activate\n'
              ' set f to POSIX path of (choose folder with prompt "%s"%s)\nend tell\nreturn f' % (komunikat, domyslny))
    r = subprocess.run([OSASCRIPT, "-e", skrypt], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    sciezka = r.stdout.strip()
    return sciezka.rstrip("/") if len(sciezka) > 1 else sciezka


ROZSZERZENIA_AUDIO = (".mp3", ".m4a", ".aac", ".wav", ".aif", ".aiff")


def info_mp3(sciezka):
    pliki = []
    for dp, dn, fn in os.walk(sciezka):
        dn[:] = sorted(d for d in dn if not d.startswith(".") and d != "PIONEER")
        for nazwa in fn:
            pelna = os.path.join(dp, nazwa)
            if nazwa.lower().endswith(ROZSZERZENIA_AUDIO) and not nazwa.startswith(".") and not os.path.islink(pelna):
                pliki.append(os.path.relpath(pelna, sciezka))
    pliki.sort()
    rozmiar = sum(os.path.getsize(os.path.join(sciezka, f)) for f in pliki)
    return {"sciezka": sciezka, "liczba": len(pliki), "rozmiar_mb": round(rozmiar / 1e6, 1),
            "przyklady": pliki[:4]}


def info_pendrive(sciezka):
    du = shutil.disk_usage(sciezka)
    return {"sciezka": sciezka, "nazwa": os.path.basename(sciezka) or sciezka,
            "wolne_mb": round(du.free / 1e6), "ma_pioneer": os.path.isdir(os.path.join(sciezka, "PIONEER")),
            "na_dysku": sciezka.startswith("/Volumes/")}


def _odczytaj_fragment(pliku, offset, limit):
    """Czyta wyznaczony fragment bez cache macOS, żeby pomiar dotyczył nośnika."""
    fd = os.open(pliku, os.O_RDONLY)
    try:
        if sys.platform == "darwin":
            try:
                fcntl.fcntl(fd, getattr(fcntl, "F_NOCACHE", 48), 1)
            except OSError:
                pass
        odczytano = 0
        while odczytano < limit:
            ile = min(1024 * 1024, limit - odczytano)
            if hasattr(os, "pread"):
                dane = os.pread(fd, ile, offset + odczytano)
            else:
                os.lseek(fd, offset + odczytano, os.SEEK_SET)
                dane = os.read(fd, ile)
            if not dane:
                break
            odczytano += len(dane)
        return odczytano
    finally:
        os.close(fd)


def _zapisz_fragment(pliku, offset, limit):
    """Zapisuje niezerowane bloki próbne na docelowym urządzeniu."""
    fd = os.open(pliku, os.O_WRONLY)
    dane = b"\0" * (1024 * 1024)
    try:
        if sys.platform == "darwin":
            try:
                fcntl.fcntl(fd, getattr(fcntl, "F_NOCACHE", 48), 1)
            except OSError:
                pass
        zapisano = 0
        while zapisano < limit:
            blok = dane[:min(len(dane), limit - zapisano)]
            if hasattr(os, "pwrite"):
                n = os.pwrite(fd, blok, offset + zapisano)
            else:
                os.lseek(fd, offset + zapisano, os.SEEK_SET)
                n = os.write(fd, blok)
            if not n:
                break
            zapisano += n
        return zapisano
    finally:
        os.close(fd)


def _kopiuj_fragment(wejscie, wyjscie, offset, limit):
    """Czyta próbkę z dysku źródłowego i równocześnie zapisuje ją na celu."""
    src = os.open(wejscie, os.O_RDONLY)
    dst = os.open(wyjscie, os.O_WRONLY)
    try:
        if sys.platform == "darwin":
            for fd in (src, dst):
                try:
                    fcntl.fcntl(fd, getattr(fcntl, "F_NOCACHE", 48), 1)
                except OSError:
                    pass
        pozycja = 0
        while pozycja < limit:
            ile = min(1024 * 1024, limit - pozycja)
            if hasattr(os, "pread"):
                dane = os.pread(src, ile, offset + pozycja)
            else:
                os.lseek(src, offset + pozycja, os.SEEK_SET)
                dane = os.read(src, ile)
            if not dane:
                break
            zapisano = 0
            while zapisano < len(dane):
                if hasattr(os, "pwrite"):
                    n = os.pwrite(dst, dane[zapisano:], offset + pozycja + zapisano)
                else:
                    os.lseek(dst, offset + pozycja + zapisano, os.SEEK_SET)
                    n = os.write(dst, dane[zapisano:])
                if not n:
                    break
                zapisano += n
            pozycja += zapisano
            if zapisano < len(dane):
                break
        return pozycja
    finally:
        os.close(src)
        os.close(dst)



MAKS_ZADAN = 256  # twardy sufit liczby rownoleglych zadan (RAM i tak ogranicza bramka pamieci)


def ram_mb_app():
    """(calkowity, dostepny) RAM w MB; tylko do informacji w interfejsie."""
    try:
        calk = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e6
        if sys.platform == "darwin":
            out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
            m = re.search(r"page size of (\d+) bytes", out)
            ps = int(m.group(1)) if m else 16384
            def stron(n):
                mm = re.search(n + r":\s+(\d+)", out)
                return int(mm.group(1)) if mm else 0
            return calk, (stron("Pages free") + stron("Pages inactive") + stron("Pages speculative")
                          + stron("Pages purgeable")) * ps / 1e6
        return calk, calk * 0.5
    except Exception:  # noqa: BLE001
        return 4096.0, 2048.0


def zmierz_predkosc(folder_mp3, pendrive, kopiuj_mp3):
    """Dobiera równoległość według przepływu właściwego dla wybranego trybu."""
    folder_real = os.path.realpath(folder_mp3)
    pendrive_real = os.path.realpath(pendrive)
    if not kopiuj_mp3:
        try:
            wspolny = os.path.commonpath([pendrive_real, folder_real])
        except ValueError:
            wspolny = ""
        if wspolny != pendrive_real:
            raise ValueError("Bez kopiowania folder MP3 musi znajdować się na wybranym pendrivie.")
    kandydaci = []
    for dp, dn, fn in os.walk(folder_mp3):
        dn[:] = [d for d in dn if not d.startswith(".") and d != "PIONEER"]
        for nazwa in fn:
            if nazwa.lower().endswith(ROZSZERZENIA_AUDIO) and not nazwa.startswith("."):
                sciezka = os.path.join(dp, nazwa)
                if os.path.isfile(sciezka) and not os.path.islink(sciezka):
                    kandydaci.append((os.path.getsize(sciezka), sciezka))
    if not kandydaci:
        raise ValueError("W wybranym folderze nie ma plików MP3 do pomiaru.")
    rozmiar, przyklad = max(kandydaci)
    # Pomiar dotyczy ODCZYTU folderu z MP3 (zrodlo), bez zapisu probnego na pendrive:
    # zapis na pendrive jest wolny i niestabilny, a MP3 kopiuje sie sekwencyjnie.
    # Liczbe rownoleglych utworow dobieramy tak, by laczny odczyt nie przekroczyl 80% zmierzonej
    # przepustowosci zrodla; RAM pilnuje osobno bramka pamieci w silniku.
    probka = min(rozmiar, 16 * 1024 * 1024)
    pomiary = []
    for _ in range(3):  # trzy krotkie proby, bierzemy najlepsza: wynik jest powtarzalny
        start = time.perf_counter()
        odczytane = _odczytaj_fragment(przyklad, 0, probka)
        dt = max(time.perf_counter() - start, 1e-6)
        pomiary.append(odczytane / dt / 1e6)
        if dt > 4:  # wolne zrodlo: nie meczymy go dluzej
            break
    maks = max(pomiary)
    cel = maks * 0.8
    rdzenie = os.cpu_count() or 4
    mb_s_na_watek = 5.0  # srednio tyle MB MP3 na sekunde zuzywa jedno zadanie (dekodowanie + analiza)
    workers = int(max(2, min(MAKS_ZADAN, cel / mb_s_na_watek)))
    ram_calk, ram_dost = ram_mb_app()
    budzet = max(300.0, min(ram_dost * 0.35, ram_calk * 0.5))
    return {"stan": "gotowe", "folder_mp3": folder_mp3, "pendrive": pendrive,
            "kopiuj_mp3": bool(kopiuj_mp3),
            "urzadzenie": os.path.splitdrive(folder_mp3)[0] or folder_mp3,
            "maks_mb_s": round(maks, 1), "cel_mb_s": round(cel, 1),
            "ram_budzet_mb": round(budzet), "ram_calk_mb": round(ram_calk), "rdzenie": rdzenie,
            "workers": workers, "pomiary": [
                {"workers": workers, "mb_s": round(maks, 1), "odczyt_mb_s": round(maks, 1), "zapis_mb_s": None}],
            "przykladowy_plik": os.path.basename(przyklad)}



# ------------------------------------------------------------ formatowanie --
def _plist(*args):
    r = subprocess.run(["diskutil", "info", "-plist", *args], capture_output=True)
    if r.returncode != 0:
        raise ValueError((r.stderr or r.stdout).decode("utf-8", "replace").strip() or "diskutil info nie powiodło się")
    return plistlib.loads(r.stdout)


def ocen_pendrive_do_formatu(info_wolumen, info_dysk, sciezka):
    """Czysta funkcja decyzyjna: zwraca opis dysku albo rzuca ValueError.
    Dopuszcza wyłącznie zewnętrzne, wymienne dyski do 2 TB; nigdy dysku systemowego."""
    if not sciezka.startswith("/Volumes/") or sciezka.rstrip("/") == "/Volumes":
        raise ValueError("To nie jest pendrive (brak w /Volumes).")
    if info_wolumen.get("MountPoint") == "/" or info_dysk.get("Internal") is True:
        raise ValueError("To dysk wewnętrzny lub systemowy. Formatowanie zablokowane.")
    zewnetrzny = info_dysk.get("RemovableMediaOrExternalDevice") or info_dysk.get("External") \
        or info_dysk.get("Removable") or info_dysk.get("Ejectable")
    if not zewnetrzny:
        raise ValueError("Nie rozpoznano zewnętrznego nośnika wymiennego. Formatowanie zablokowane.")
    rozmiar = int(info_dysk.get("TotalSize") or info_dysk.get("Size") or 0)
    if rozmiar <= 0:
        raise ValueError("Nie udało się odczytać rozmiaru dysku.")
    if rozmiar > 2 * 1024 ** 4:
        raise ValueError("Dysk większy niż 2 TB: FAT32 tego nie obsługuje.")
    dysk = info_dysk.get("DeviceIdentifier") or ""
    if not re.fullmatch(r"disk\d+", dysk):
        raise ValueError("Nieoczekiwany identyfikator dysku: %r" % dysk)
    return {"sciezka": sciezka, "nazwa": info_wolumen.get("VolumeName") or os.path.basename(sciezka),
            "dysk": dysk, "rozmiar_gb": round(rozmiar / 1e9, 1),
            "model": info_dysk.get("MediaName") or info_dysk.get("IORegistryEntryName") or "",
            "system_plikow": info_wolumen.get("FilesystemName") or ""}


def info_formatowania(sciezka):
    wol = _plist(sciezka)
    cały = wol.get("ParentWholeDisk") or wol.get("DeviceIdentifier")
    dysk = _plist(cały)
    return ocen_pendrive_do_formatu(wol, dysk, sciezka)


def formatuj_pendrive(sciezka, nowa_nazwa):
    """FAT32 + tablica MBR: układ, który czyta CDJ-2000. Kasuje CAŁY dysk."""
    opis = info_formatowania(sciezka)
    nazwa = re.sub(r"[^A-Z0-9_ -]", "", (nowa_nazwa or "CDJ").upper())[:11].strip() or "CDJ"
    r = subprocess.run(["diskutil", "eraseDisk", "FAT32", nazwa, "MBR", "/dev/" + opis["dysk"]],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise ValueError((r.stderr or r.stdout).strip() or "diskutil eraseDisk nie powiodło się")
    return {"ok": True, "sciezka": "/Volumes/" + nazwa, "nazwa": nazwa, "dysk": opis["dysk"]}



def wysun_pendrive(sciezka, uruchom=None, czekaj=time.sleep):
    """Wysuwa pendrive. Spotlight (mds) potrafi chwilę trzymać świeżo zapisany wolumin,
    więc: sync, kilka prób zwykłego wysunięcia, a na końcu odmontowanie wymuszone
    (po sync zapis jest już na nośniku)."""
    uruchom = uruchom or (lambda *c: subprocess.run(list(c), capture_output=True, text=True))
    try:
        os.sync()
    except AttributeError:
        pass
    ostatni = ""
    for proba in range(4):
        r = uruchom("diskutil", "eject", sciezka)
        ostatni = (r.stdout + r.stderr).strip()
        if r.returncode == 0:
            return True, ostatni
        if "dissent" not in ostatni and "busy" not in ostatni.lower():
            break
        czekaj(3)
    r = uruchom("diskutil", "unmount", "force", sciezka)
    if r.returncode == 0:
        # po odmontowaniu wolumin nie jest już widoczny; wysuń cały dysk, jeśli się da
        info = ostatni
        try:
            dysk = None
            m = re.search(r"disk\d+", ostatni)
            if m:
                dysk = m.group(0)
            if dysk:
                uruchom("diskutil", "eject", dysk)
        except Exception:  # noqa: BLE001
            pass
        return True, "Pendrive odmontowany (wymuszone odmontowanie po zapisie wszystkich danych)."
    return False, ostatni + " " + (r.stdout + r.stderr).strip()


def start_pomiaru(folder_mp3, pendrive, kopiuj_mp3):
    with LOCK:
        if STAN["job"].get("stan") == "trwa" or STAN["benchmark"].get("stan") == "trwa":
            return False
        STAN["benchmark"] = {"stan": "trwa", "folder_mp3": folder_mp3,
                              "pendrive": pendrive, "kopiuj_mp3": bool(kopiuj_mp3)}
    def rob():
        try:
            wynik = zmierz_predkosc(folder_mp3, pendrive, kopiuj_mp3)
            with LOCK:
                STAN["benchmark"] = wynik
        except Exception as e:  # noqa: BLE001
            with LOCK:
                STAN["benchmark"] = {"stan": "blad", "folder_mp3": folder_mp3,
                                      "pendrive": pendrive, "kopiuj_mp3": bool(kopiuj_mp3),
                                      "blad": str(e)}
    threading.Thread(target=rob, daemon=True).start()
    return True


# ------------------------------------------------------------ zadanie ------
def start_zadania(a):
    with LOCK:
        if STAN["job"].get("stan") == "trwa":
            return False
        PODGLADY.clear()
        STAN["job"] = {"stan": "trwa", "etap": "Start...", "postep": 0.0, "n": 0, "utwory": [], "sukcesy": 0,
                       "bledy": [], "bledow_razem": 0,
                       "log": [], "kopia": None, "stare_pliki": [], "blad": None,
                       "kopiuj": bool(a.get("kopiuj_mp3", True)), "t0": time.time()}
    threading.Thread(target=biegnie_zadanie, args=(a,), daemon=True).start()
    return True


def biegnie_zadanie(a):
    job = STAN["job"]
    env = dict(os.environ, CDJ_FFMPEG=FFMPEG["sciezka"], PYTHONDONTWRITEBYTECODE="1")
    # Nie dziedzicz ścieżek deweloperskich, które mogą wskazywać na źródła NumPy.
    env.pop("PYTHONPATH", None)
    if not BUNDLED:
        env.pop("PYTHONHOME", None)
    try:
        cmd = [python_roboczy()]
        if not BUNDLED:
            cmd.append("-I")
        cmd.extend([os.path.join(KAT, "worker.py"), json.dumps(a)])
        p = subprocess.Popen(cmd,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env,
                             cwd=KAT)
        PROC["p"] = p
        for linia in p.stdout:
            linia = linia.rstrip("\n")
            if not linia.startswith("@@"):
                with LOCK:
                    dodaj_log(job["log"], linia)
                continue
            try:
                ev = json.loads(linia[2:])
            except ValueError:
                continue
            with LOCK:
                obsluz(job, ev)
        kod = p.wait()
        with LOCK:
            if job["stan"] == "trwa":
                job["stan"] = "blad"
                job["blad"] = job["blad"] or ("Proces zakonczyl sie nieoczekiwanie (kod %s)." % kod)
    except Exception as e:  # noqa: BLE001
        with LOCK:
            job["stan"] = "blad"
            job["blad"] = str(e)


def _waga(job):
    """Udzial analizy w pasku postepu: z kopiowaniem MP3 to glownie kopiowanie."""
    return 0.2 if job.get("kopiuj", True) else 0.8


def szacuj_koniec(job):
    """(sekundy do konca, faza) albo (None, faza), gdy za wczesnie na oszacowanie."""
    if job.get("stan") != "trwa":
        return None, ""
    teraz = time.time()
    if job.get("kop_razem") is not None and job.get("t_kop"):
        dt = teraz - job["t_kop"]
        zr, razem = job.get("kop_zrobione", 0), job["kop_razem"]
        if razem <= 0 or zr >= razem:
            return 0, "zapis"
        if zr > 2e6 and dt > 2:
            return (razem - zr) / (zr / dt), "kopiowanie"
        return None, "kopiowanie"
    n, i = job.get("n") or 0, job.get("an_zrobione", 0)
    if job.get("t_an") and i >= 1 and n:
        return (teraz - job["t_an"]) * (n - i) / i, "analiza"
    return None, "analiza"


def obsluz(job, ev):
    t = ev.get("typ")
    n = ev.get("n") or job.get("n") or 0
    if t == "start":
        job["n"] = ev["n"]
    elif t == "analiza_start":
        job["etap"] = ev["tekst"]
        job["postep"] = 0.0
        job["t_an"] = time.time()
    elif t == "utwor_start":
        job["etap"] = "Analizuję %d z %d: %s" % (ev["i"], ev["n"], ev["nazwa"])
        job["postep"] = (ev["i"] - 1) / max(1, ev["n"]) * _waga(job)
    elif t == "utwor":
        job["sukcesy"] = job.get("sukcesy", 0) + 1
        if ev["i"] <= 50:
            podglad = ev.pop("podglad", None)
            if podglad is not None:
                PODGLADY[ev["i"]] = podglad
            job["utwory"].append(ev)
    elif t == "postep_analizy":
        job["etap"] = "Przeanalizowano %d z %d utworów..." % (ev["i"], ev["n"])
        job["postep"] = ev["i"] / max(1, ev["n"]) * _waga(job)
        job["an_zrobione"] = ev["i"]
    elif t == "utwor_blad":
        job["bledow_razem"] = job.get("bledow_razem", 0) + 1
        if len(job["bledy"]) < 100:
            job["bledy"].append({"nazwa": ev["nazwa"], "tekst": ev["tekst"]})
    elif t == "etap":
        job["etap"] = ev["tekst"]
        job["postep"] = max(job["postep"], _waga(job))
    elif t == "kopia":
        job["kopia"] = ev["sciezka"]
    elif t == "kopiuje":
        job["etap"] = "Kopiuję plik %d z %d..." % (ev["i"], ev["n"])
    elif t == "kopiuj_start":
        job["kop_razem"] = ev["razem"]
        job["kop_zrobione"] = 0
        job["t_kop"] = time.time()
        job["postep"] = max(job["postep"], _waga(job))
    elif t == "kopiuj_bajty":
        job["kop_razem"] = ev["razem"]
        job["kop_zrobione"] = ev["zrobione"]
        w = _waga(job)
        job["postep"] = max(job["postep"], w + (1 - w) * ev["zrobione"] / max(1, ev["razem"]) * 0.97)
    elif t == "koniec":
        job["stan"] = "gotowe"
        job["postep"] = 1.0
        job["etap"] = "Gotowe"
        job["stare_pliki"] = ev.get("stare_pliki", [])
        job["pendrive"] = ev.get("pendrive")
    elif t == "blad":
        job["stan"] = "blad"
        job["blad"] = ev["tekst"]


# ------------------------------------------------------------ serwer HTTP --
class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "CDJTool"

    def log_message(self, fmt, *args):  # cisza w logu
        pass

    def _wyslij(self, kod, dane, typ="application/json; charset=utf-8"):
        cialo = dane if isinstance(dane, bytes) else json.dumps(dane, ensure_ascii=False).encode("utf-8")
        self.send_response(kod)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(cialo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cialo)

    def _host_ok(self):
        return self.headers.get("Host", "") in ("127.0.0.1:%d" % PORT, "localhost:%d" % PORT)

    def _auth(self):
        return self._host_ok() and self.headers.get("X-Token") == TOKEN

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if not self._host_ok():
            return self._wyslij(403, {"blad": "zabronione"})
        if url.path == "/":
            html = open(os.path.join(KAT, "ui.html"), encoding="utf-8").read().replace("__TOKEN__", TOKEN)
            html = html.replace("__NATIVE__", "true" if NATIVE else "false")
            return self._wyslij(200, html.encode("utf-8"), "text/html; charset=utf-8")
        if not self._auth():
            return self._wyslij(403, {"blad": "brak autoryzacji"})
        STAN["ostatnio"] = time.time()
        STAN["widziano"] = True
        if url.path == "/api/status":
            with LOCK:
                job = dict(STAN["job"])
                benchmark = dict(STAN["benchmark"])
                eta, faza = szacuj_koniec(job)
                job["eta_s"], job["eta_faza"] = eta, faza
                job["uplynelo_s"] = (time.time() - job["t0"]) if job.get("t0") and job.get("stan") == "trwa" else None
                if "utwory" in job:
                    job["utwory"] = list(job["utwory"])
                    job["log"] = list(job.get("log", []))[-30:]
                return self._wyslij(200, {"setup": STAN["setup"], "job": job, "benchmark": benchmark})
        if url.path == "/api/podglad":
            i = int(urllib.parse.parse_qs(url.query).get("i", ["0"])[0])
            return self._wyslij(200, PODGLADY.get(i) or {})
        return self._wyslij(404, {"blad": "nie znaleziono"})

    def do_POST(self):
        if not self._auth():
            return self._wyslij(403, {"blad": "brak autoryzacji"})
        STAN["ostatnio"] = time.time()
        dl = int(self.headers.get("Content-Length") or 0)
        try:
            dane = json.loads(self.rfile.read(dl) or b"{}")
        except ValueError:
            return self._wyslij(400, {"blad": "zly format"})
        sciezka = urllib.parse.urlparse(self.path).path
        try:
            if sciezka == "/api/wybierz":
                if dane.get("co") == "mp3":
                    p = dane.get("sciezka") if NATIVE else wybierz_folder(
                        "Wybierz folder z utworami MP3", os.path.expanduser("~/Music"))
                    return self._wyslij(200, {"sciezka": None} if not p else info_mp3(p))
                if dane.get("bez_dialogu") and str(dane.get("sciezka", "")).startswith("/Volumes/") \
                        and os.path.isdir(dane["sciezka"]):
                    p = dane["sciezka"]
                else:
                    p = dane.get("sciezka") if NATIVE else wybierz_folder(
                        "Wybierz pendrive (cały dysk, np. w Finderze w sekcji Lokalizacje)", "/Volumes")
                return self._wyslij(200, {"sciezka": None} if not p else info_pendrive(p))
            if sciezka == "/api/start":
                a = {"folder_mp3": dane["folder_mp3"], "pendrive": dane["pendrive"],
                     "playlista": str(dane.get("playlista") or "Tracks")[:60],
                     "kopiuj_mp3": bool(dane.get("kopiuj_mp3", True)),
                     "zastap_pioneer": bool(dane.get("zastap_pioneer", False))}
                benchmark = STAN["benchmark"]
                if (benchmark.get("stan") != "gotowe" or
                        benchmark.get("folder_mp3") != str(dane.get("folder_pomiaru") or a["folder_mp3"]) or
                        benchmark.get("pendrive") != a["pendrive"] or
                        bool(benchmark.get("kopiuj_mp3")) != a["kopiuj_mp3"]):
                    return self._wyslij(409, {"blad": "Najpierw zmierz szybkość ponownie dla wybranego trybu kopiowania."})
                a["workers"] = benchmark["workers"]
                a["maks_mb_s"] = benchmark["maks_mb_s"]
                a["cel_mb_s"] = benchmark["cel_mb_s"]
                if STAN["setup"]["stan"] != "gotowe":
                    return self._wyslij(409, {"blad": "Skladniki jeszcze nie sa gotowe."})
                if not (os.path.isdir(a["folder_mp3"]) and os.path.isdir(a["pendrive"])):
                    return self._wyslij(400, {"blad": "Nie znaleziono wybranych folderow."})
                return self._wyslij(200, {"ok": start_zadania(a)})
            if sciezka == "/api/predkosc":
                folder = os.path.abspath(dane.get("folder_mp3", ""))
                pendrive = os.path.abspath(dane.get("pendrive", ""))
                kopiuj_mp3 = bool(dane.get("kopiuj_mp3", True))
                if not os.path.isdir(folder) or not os.path.isdir(pendrive):
                    return self._wyslij(400, {"blad": "Najpierw wybierz folder MP3 i pendrive."})
                if STAN["setup"]["stan"] != "gotowe":
                    return self._wyslij(409, {"blad": "Składniki aplikacji nie są jeszcze gotowe."})
                return self._wyslij(200, {"ok": start_pomiaru(folder, pendrive, kopiuj_mp3)})
            if sciezka == "/api/anuluj":
                p = PROC["p"]
                if p and p.poll() is None:
                    p.terminate()
                    with LOCK:
                        STAN["job"]["stan"] = "anulowano"
                        STAN["job"]["etap"] = "Anulowano"
                return self._wyslij(200, {"ok": True})
            if sciezka == "/api/formatuj-info":
                return self._wyslij(200, info_formatowania(os.path.abspath(dane.get("sciezka", ""))))
            if sciezka == "/api/formatuj":
                if STAN["job"].get("stan") == "trwa":
                    return self._wyslij(409, {"blad": "Trwa zapis. Poczekaj na koniec."})
                p = os.path.abspath(dane.get("sciezka", ""))
                opis = info_formatowania(p)
                if str(dane.get("potwierdzenie", "")).strip() != opis["nazwa"]:
                    return self._wyslij(400, {"blad": "Wpisana nazwa nie zgadza się z nazwą pendrive'a."})
                return self._wyslij(200, formatuj_pendrive(p, dane.get("nowa_nazwa", "CDJ")))
            if sciezka == "/api/wysun":
                p = os.path.abspath(dane.get("sciezka", ""))
                if not p.startswith("/Volumes/") or not os.path.isdir(p):
                    return self._wyslij(400, {"blad": "To nie jest pendrive."})
                ok, tekst = wysun_pendrive(p)
                return self._wyslij(200, {"ok": ok, "tekst": tekst})
            if sciezka == "/api/zakoncz":
                self._wyslij(200, {"ok": True})
                threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)), daemon=True).start()
                return
        except Exception as e:  # noqa: BLE001
            return self._wyslij(500, {"blad": str(e)})
        return self._wyslij(404, {"blad": "nie znaleziono"})


def port_zajety():
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def pilnuj():
    """Kończy program, gdy okno przeglądarki jest zamknięte (brak zapytań przez minutę)."""
    while True:
        time.sleep(5)
        with LOCK:
            zajety = STAN["job"].get("stan") == "trwa" or STAN["setup"]["stan"] == "instalacja"
        if STAN["widziano"] and not zajety and time.time() - STAN["ostatnio"] > 60:
            os._exit(0)


def main():
    global PORT
    if not NATIVE and port_zajety():
        if not os.environ.get("CDJ_NO_BROWSER"):
            webbrowser.open("http://127.0.0.1:%d/" % PORT)
        return
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    PORT = srv.server_address[1]
    url = "http://127.0.0.1:%d/" % PORT
    threading.Thread(target=przygotuj, daemon=True).start()
    threading.Thread(target=pilnuj, daemon=True).start()
    if NATIVE:
        print("CDJ_URL " + url, flush=True)
    elif os.environ.get("CDJ_NO_BROWSER"):
        print("TOKEN", TOKEN, flush=True)
    else:
        webbrowser.open(url)
    srv.serve_forever()


if __name__ == "__main__":
    main()
