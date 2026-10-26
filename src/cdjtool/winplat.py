"""Windows-specific helpers for CDJ Tool (drive detection, folder dialog, FAT32 formatting, eject, RAM, app window).

Imported only on Windows (sys.platform == "win32"); the macOS build never loads this module.
Everything here uses only the standard library, ctypes and the PowerShell that ships with Windows 10/11.
"""
import base64
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import time

CREATE_NO_WINDOW = 0x08000000
DRIVE_REMOVABLE, DRIVE_FIXED = 2, 3
MAKS_FAT32_B = 32 * 1024 ** 3  # Windows formats FAT32 only up to 32 GB (diskpart / Format-Volume)


# ------------------------------------------------------------------ no console windows ---
def install_no_window_patch():
    """Child console programs (ffmpeg, powershell) must not flash a console window."""
    orig = subprocess.Popen.__init__

    def init(self, *a, **kw):
        kw["creationflags"] = kw.get("creationflags", 0) | CREATE_NO_WINDOW
        orig(self, *a, **kw)

    subprocess.Popen.__init__ = init


def support_dir():
    return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "CDJ-Tool")


# ------------------------------------------------------------------ drives ---
def _drive_type(root):
    return ctypes.windll.kernel32.GetDriveTypeW(root)


def jest_pendrive(path):
    """True for the root of a removable or external drive (never the system drive)."""
    try:
        p = os.path.abspath(path)
    except Exception:  # noqa: BLE001
        return False
    drive, rest = os.path.splitdrive(p)
    if len(drive) != 2 or drive[1] != ":" or rest not in ("\\", ""):
        return False
    if drive.upper() == (os.environ.get("SystemDrive") or "C:").upper():
        return False
    return _drive_type(drive + "\\") in (DRIVE_REMOVABLE, DRIVE_FIXED)


# ------------------------------------------------------------------ powershell ---
def _ps(script, timeout=120, sta=False):
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
    if sta:
        cmd.append("-STA")
    cmd += ["-EncodedCommand", enc]
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    out = r.stdout.decode("utf-8", "replace").strip()
    err = r.stderr.decode("utf-8", "replace").strip()
    return r.returncode, out, err


def _q(s):
    return str(s).replace("'", "''")


def wybierz_folder(komunikat, start=None, korzen_komputer=False):
    """Folder picker. korzen_komputer=True starts at 'This PC' so a whole drive can be chosen."""
    linie = [
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
        "Add-Type -AssemblyName System.Windows.Forms",
        "[System.Windows.Forms.Application]::EnableVisualStyles()",
        "$f = New-Object System.Windows.Forms.FolderBrowserDialog",
        "$f.Description = '%s'" % _q(komunikat),
        "$f.ShowNewFolderButton = $false",
    ]
    if korzen_komputer:
        linie.append("$f.RootFolder = [System.Environment+SpecialFolder]::MyComputer")
    elif start and os.path.isdir(start):
        linie.append("$f.SelectedPath = '%s'" % _q(start))
    linie += [
        "$o = New-Object System.Windows.Forms.Form",
        "$o.TopMost = $true; $o.ShowInTaskbar = $false; $o.StartPosition = 'CenterScreen'; $o.Opacity = 0",
        "$o.Show()",
        "if ($f.ShowDialog($o) -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $f.SelectedPath }",
        "$o.Close()",
    ]
    rc, out, _ = _ps("\n".join(linie), timeout=3600, sta=True)
    if rc != 0 or not out:
        return None
    return out.splitlines()[-1].strip()


# ------------------------------------------------------------------ RAM ---
class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def ram_mb():
    st = _MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
    return st.ullTotalPhys / 1e6, st.ullAvailPhys / 1e6


# ------------------------------------------------------------------ format ---
def _litera(sciezka):
    d = os.path.splitdrive(os.path.abspath(sciezka))[0]
    if len(d) != 2 or d[1] != ":":
        raise ValueError("To nie jest dysk z literą (np. E:).")
    return d[0].upper()


def info_formatowania(sciezka):
    """Describes the disk behind a drive letter; refuses anything but an external USB disk up to 32 GB."""
    lit = _litera(sciezka)
    if not jest_pendrive(lit + ":\\"):
        raise ValueError("To nie jest pendrive (dysk systemowy lub nieobsługiwany). Formatowanie zablokowane.")
    skrypt = ("$p = Get-Partition -DriveLetter %s -ErrorAction Stop; $d = $p | Get-Disk; "
              "$v = Get-Volume -DriveLetter %s; "
              "[pscustomobject]@{Number=$d.Number; Name=$d.FriendlyName; Size=$d.Size; Bus=[string]$d.BusType; "
              "Boot=$d.IsBoot; System=$d.IsSystem; Label=$v.FileSystemLabel; FS=$v.FileSystem} | "
              "ConvertTo-Json -Compress" % (lit, lit))
    rc, out, err = _ps(skrypt, timeout=60)
    if rc != 0 or not out:
        raise ValueError("Nie udało się odczytać informacji o dysku. " + err[:200])
    d = json.loads(out.splitlines()[-1])
    return ocen_pendrive_do_formatu(d, sciezka)


def ocen_pendrive_do_formatu(d, sciezka):
    if d.get("Bus") != "USB":
        raise ValueError("To nie jest dysk USB. Formatowanie zablokowane.")
    if d.get("Boot") or d.get("System"):
        raise ValueError("To dysk systemowy. Formatowanie zablokowane.")
    rozmiar = int(d.get("Size") or 0)
    if rozmiar <= 0:
        raise ValueError("Nie udało się odczytać rozmiaru dysku.")
    if rozmiar > MAKS_FAT32_B:
        raise ValueError("Windows formatuje FAT32 tylko do 32 GB. Ten dysk ma %.0f GB: użyj mniejszego pendrive'a "
                         "albo sformatuj go na komputerze Mac." % (rozmiar / 1e9))
    numer = d.get("Number")
    if not isinstance(numer, int) or numer < 0:
        raise ValueError("Nieoczekiwany numer dysku: %r" % (numer,))
    return {"sciezka": sciezka, "nazwa": d.get("Label") or _litera(sciezka) + ":", "dysk": numer,
            "rozmiar_gb": round(rozmiar / 1e9, 1), "model": d.get("Name") or "",
            "system_plikow": d.get("FS") or ""}


def formatuj_pendrive(sciezka, nowa_nazwa):
    """FAT32 + MBR through an elevated diskpart (Windows asks for permission). Erases the WHOLE disk."""
    opis = info_formatowania(sciezka)  # re-validated right before the destructive step
    lit = _litera(sciezka)
    nazwa = re.sub(r"[^A-Z0-9_ -]", "", (nowa_nazwa or "CDJ").upper())[:11].strip() or "CDJ"
    tekst = "select disk %d\nclean\nconvert mbr\ncreate partition primary\nformat fs=fat32 quick label=\"%s\"\nassign letter=%s\n" % (
        opis["dysk"], nazwa, lit)
    skrypt_plik = os.path.join(support_dir(), "format.txt")
    os.makedirs(support_dir(), exist_ok=True)
    with open(skrypt_plik, "w", encoding="ascii") as f:
        f.write(tekst)
    ps = ("try { Start-Process -FilePath diskpart.exe -ArgumentList '/s','%s' -Verb RunAs -Wait -WindowStyle Hidden "
          "-ErrorAction Stop } catch { Write-Output 'CANCELLED'; exit 3 }" % _q(skrypt_plik))
    rc, out, err = _ps(ps, timeout=900)
    if rc != 0 or "CANCELLED" in out:
        raise ValueError("Formatowanie anulowano lub Windows nie dał uprawnień administratora.")
    nowa = lit + ":\\"
    for _ in range(40):  # the new volume needs a moment to appear
        time.sleep(0.5)
        if os.path.isdir(nowa):
            break
    else:
        raise ValueError("Formatowanie się nie powiodło (nie widać nowego dysku %s)." % nowa)
    return {"ok": True, "sciezka": nowa, "nazwa": nazwa, "dysk": opis["dysk"]}


# ------------------------------------------------------------------ eject ---
def wysun_pendrive(sciezka, czekaj=time.sleep):
    """Flush, lock, dismount and eject the volume. Returns (ok, text)."""
    lit = _litera(sciezka)
    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = ctypes.c_void_p
    k32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p,
                                ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
    k32.DeviceIoControl.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
                                    ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p]
    k32.FlushFileBuffers.argtypes = [ctypes.c_void_p]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    invalid = ctypes.c_void_p(-1).value
    ostatni = "Nie udało się zablokować woluminu (zajęty przez inny program)."
    for _ in range(5):
        h = k32.CreateFileW("\\\\.\\%s:" % lit, 0xC0000000, 3, None, 3, 0, None)  # GENERIC_READ|WRITE, share R|W
        if h in (None, invalid):
            ostatni = "Nie można otworzyć woluminu %s: (kod %d)." % (lit, ctypes.GetLastError())
            czekaj(2)
            continue
        try:
            k32.FlushFileBuffers(h)
            ret = ctypes.c_ulong(0)
            if k32.DeviceIoControl(h, 0x00090018, None, 0, None, 0, ctypes.byref(ret), None):  # FSCTL_LOCK_VOLUME
                k32.DeviceIoControl(h, 0x00090020, None, 0, None, 0, ctypes.byref(ret), None)  # FSCTL_DISMOUNT_VOLUME
                prevent = ctypes.c_ubyte(0)
                k32.DeviceIoControl(h, 0x002D4804, ctypes.byref(prevent), 1, None, 0, ctypes.byref(ret), None)  # allow removal
                k32.DeviceIoControl(h, 0x002D4808, None, 0, None, 0, ctypes.byref(ret), None)  # IOCTL_STORAGE_EJECT_MEDIA
                return True, "Pendrive wysunięty. Możesz go teraz odłączyć."
            ostatni = "Wolumin %s: jest używany przez inny program (np. Eksplorator plików lub antywirus)." % lit
        finally:
            k32.CloseHandle(h)
        czekaj(3)
    # last resort: the shell's own Eject verb
    rc, out, err = _ps("(New-Object -comObject Shell.Application).Namespace(17).ParseName('%s:').InvokeVerb('Eject')" % lit,
                       timeout=60)
    time.sleep(2)
    if not os.path.isdir(lit + ":\\"):
        return True, "Pendrive wysunięty. Możesz go teraz odłączyć."
    return False, ostatni


# ------------------------------------------------------------------ application window ---
def _przegladarki():
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", "")
    return [
        os.path.join(pf86, "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(pf, "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(pf, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(pf86, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(local, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(pf, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
    ]


def otworz_okno(url):
    """Opens the UI in its own application window (Edge/Chrome --app mode). Returns the process or None."""
    profil = os.path.join(support_dir(), "okno")
    os.makedirs(profil, exist_ok=True)
    for exe in _przegladarki():
        if exe and os.path.isfile(exe):
            return subprocess.Popen([exe, "--app=" + url, "--user-data-dir=" + profil, "--window-size=1100,900",
                                     "--no-first-run", "--no-default-browser-check", "--disable-extensions"])
    return None
