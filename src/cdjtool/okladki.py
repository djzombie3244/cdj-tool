"""Okladki utworow dla CDJ: obraz z tagow pliku (ID3 APIC w MP3/AIFF/WAV, 'covr' w M4A, PICTURE we FLAC) i miniatury
jak w eksporcie rekordboxa: PIONEER/Artwork/00001/a<id>.jpg (80x80) i a<id>_m.jpg (240x240),
w bazie tabela 13 (id + sciezka). Bez dodatkowych bibliotek: na macOS skaluje systemowy `sips`,
na Windows dolaczony ffmpeg (pelna wersja), gdzie indziej Pillow, jesli jest."""
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile

MALA, DUZA = 80, 240
KATALOG = "/PIONEER/Artwork/00001"
MAKS_OBRAZ = 20 * 1024 * 1024


def sciezki(aid):
    """(sciezka w bazie, plik 80 px, plik 240 px) wzgledem korzenia pendrive'a."""
    p = "%s/a%d.jpg" % (KATALOG, aid)
    return p, p, "%s/a%d_m.jpg" % (KATALOG, aid)


# ------------------------------------------------------------------ ID3 --
def _syncsafe(b):
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _bez_unsync(b):
    return b.replace(b"\xff\x00", b"\xff")


def _apic(dane, wersja):
    """Obraz z ramki APIC (v2.3/2.4) albo PIC (v2.2): (typ obrazu, bajty)."""
    if not dane:
        return None
    enc = dane[0]
    if wersja == 2:
        o = 4  # 3 znaki formatu (JPG/PNG)
    else:
        k = dane.find(b"\x00", 1)
        if k < 0:
            return None
        o = k + 1
    if o >= len(dane):
        return None
    typ = dane[o]
    o += 1
    if enc in (1, 2):  # UTF-16: opis konczy sie dwoma zerami na parzystej pozycji
        k = o
        while k + 1 < len(dane) and dane[k:k + 2] != b"\x00\x00":
            k += 2
        o = k + 2
    else:
        k = dane.find(b"\x00", o)
        if k < 0:
            return None
        o = k + 1
    return typ, dane[o:]


def _z_id3(blok):
    """Obraz z bloku ID3v2 (zaczyna sie od 'ID3')."""
    if len(blok) < 10 or blok[:3] != b"ID3":
        return None
    wersja, flagi = blok[3], blok[5]
    rozmiar = _syncsafe(blok[6:10])
    tresc = blok[10:10 + rozmiar]
    if flagi & 0x80 and wersja < 4:
        tresc = _bez_unsync(tresc)
    o = 0
    if flagi & 0x40 and wersja >= 3:  # naglowek rozszerzony
        ext = _syncsafe(tresc[0:4]) if wersja == 4 else struct.unpack(">I", tresc[0:4])[0] + 4
        o = ext
    najlepszy = None
    while o + (6 if wersja == 2 else 10) <= len(tresc):
        if wersja == 2:
            fid, fsz = tresc[o:o + 3], int.from_bytes(tresc[o + 3:o + 6], "big")
            o += 6
            ffl = 0
        else:
            fid = tresc[o:o + 4]
            fsz = _syncsafe(tresc[o + 4:o + 8]) if wersja == 4 else struct.unpack(">I", tresc[o + 4:o + 8])[0]
            ffl = struct.unpack(">H", tresc[o + 8:o + 10])[0]
            o += 10
        if not fid.strip(b"\x00") or fsz <= 0 or o + fsz > len(tresc):
            break
        dane = tresc[o:o + fsz]
        o += fsz
        if fid in (b"APIC", b"PIC"):
            if wersja == 4:
                if ffl & 0x0001:  # dlugosc danych na poczatku ramki
                    dane = dane[4:]
                if ffl & 0x0002:
                    dane = _bez_unsync(dane)
            w = _apic(dane, wersja)
            if w and w[1]:
                if w[0] == 3:  # okladka przednia
                    return w[1]
                najlepszy = najlepszy or w[1]
    return najlepszy


def _id3_w_kawalkach(f, rozmiar, riff):
    """WAV (RIFF, little endian) i AIFF (FORM, big endian): kawalek 'ID3 '/'id3 ' z blokiem ID3v2."""
    f.seek(12)
    while f.tell() + 8 <= rozmiar:
        h = f.read(8)
        if len(h) < 8:
            break
        cid = h[:4]
        n = struct.unpack("<I" if riff else ">I", h[4:])[0]
        if cid in (b"ID3 ", b"id3 "):
            return _z_id3(f.read(min(n, MAKS_OBRAZ)))
        f.seek(n + (n & 1), 1)
    return None


def _z_m4a(f, rozmiar):
    """M4A/AAC: atom covr > data (8 bajtow typu i jezyka, potem obraz). Szuka w moov (zwykle na poczatku albo koncu)."""
    def atomy(start, koniec):
        o = start
        while o + 8 <= koniec:
            f.seek(o)
            h = f.read(8)
            n = struct.unpack(">I", h[:4])[0]
            typ = h[4:8]
            hd = 8
            if n == 1:
                n = struct.unpack(">Q", f.read(8))[0]
                hd = 16
            elif n == 0:
                n = koniec - o
            if n < hd:
                return
            yield typ, o + hd, o + n
            o += n

    def szukaj(start, koniec, sciezka):
        for typ, a, b in atomy(start, koniec):
            if typ != sciezka[0]:
                continue
            if len(sciezka) == 1:
                return a, b
            if typ == b"meta":
                a += 4  # wersja i flagi
            r = szukaj(a, b, sciezka[1:])
            if r:
                return r
        return None

    r = szukaj(0, rozmiar, [b"moov", b"udta", b"meta", b"ilst", b"covr", b"data"])
    if not r:
        return None
    a, b = r
    if b - a <= 8 or b - a > MAKS_OBRAZ:
        return None
    f.seek(a + 8)
    return f.read(b - a - 8)


def _z_flac(f):
    """FLAC: blok metadanych PICTURE (typ 6); najpierw okladka przednia. Na koncu ewentualny tag ID3 nie jest szukany."""
    f.seek(4)
    najlepszy = None
    while True:
        h = f.read(4)
        if len(h) < 4:
            break
        ostatni, typ, n = h[0] & 0x80, h[0] & 0x7F, int.from_bytes(h[1:4], "big")
        if typ == 6 and n <= MAKS_OBRAZ:
            b = f.read(n)
            o = 0
            rodzaj = struct.unpack(">I", b[o:o + 4])[0]; o += 4
            o += 4 + struct.unpack(">I", b[o:o + 4])[0]   # typ MIME
            o += 4 + struct.unpack(">I", b[o:o + 4])[0]   # opis
            o += 16                                      # szerokosc, wysokosc, glebia, kolory
            dl = struct.unpack(">I", b[o:o + 4])[0]; o += 4
            obraz = b[o:o + dl]
            if rodzaj == 3:
                return obraz
            najlepszy = najlepszy or obraz
        else:
            f.seek(n, 1)
        if ostatni:
            break
    return najlepszy


def wyciagnij(sciezka):
    """Bajty obrazu okladki zapisanej w pliku albo None."""
    try:
        rozmiar = os.path.getsize(sciezka)
        with open(sciezka, "rb") as f:
            p = f.read(12)
            if p[:3] == b"ID3":
                f.seek(0)
                n = 10 + _syncsafe(p[6:10])
                return _z_id3(f.read(min(n, MAKS_OBRAZ + 10)))
            if p[:4] == b"RIFF" and p[8:12] == b"WAVE":
                return _id3_w_kawalkach(f, rozmiar, True)
            if p[:4] == b"FORM" and p[8:12] in (b"AIFF", b"AIFC"):
                return _id3_w_kawalkach(f, rozmiar, False)
            if p[:4] == b"fLaC":
                return _z_flac(f)
            if p[4:8] == b"ftyp":
                return _z_m4a(f, rozmiar)
    except (OSError, struct.error, IndexError, ValueError):
        return None
    return None


# ---------------------------------------------------------- miniatury --
def _pillow(obraz, rozm):
    from io import BytesIO
    from PIL import Image
    im = Image.open(BytesIO(obraz)).convert("RGB")
    w, h = im.size
    k = min(w, h)
    im = im.crop(((w - k) // 2, (h - k) // 2, (w - k) // 2 + k, (h - k) // 2 + k))
    out = []
    for r in rozm:
        b = BytesIO()
        im.resize((r, r), Image.LANCZOS).save(b, "JPEG", quality=85)
        out.append(b.getvalue())
    return out


SIPS = shutil.which("sips") or "/usr/bin/sips"


def _sips(obraz, rozm):
    with tempfile.TemporaryDirectory(prefix="cdj-art-") as kat:
        src = os.path.join(kat, "in.png" if obraz[:4] == b"\x89PNG" else "in.jpg")
        with open(src, "wb") as fh:
            fh.write(obraz)
        out = []
        for r in rozm:
            dst = os.path.join(kat, "o%d.jpg" % r)
            subprocess.run([SIPS, "-s", "format", "jpeg", "-s", "formatOptions", "85", "-z", str(r), str(r),
                            src, "--out", dst], capture_output=True, check=True, timeout=30)
            with open(dst, "rb") as fh:
                out.append(fh.read())
        return out


def _ffmpeg(obraz, rozm, ffmpeg):
    with tempfile.TemporaryDirectory(prefix="cdj-art-") as kat:
        src = os.path.join(kat, "in")
        with open(src, "wb") as fh:
            fh.write(obraz)
        out = []
        for r in rozm:
            dst = os.path.join(kat, "o%d.jpg" % r)
            subprocess.run([ffmpeg, "-v", "error", "-y", "-i", src, "-vf",
                            "crop='min(iw,ih)':'min(iw,ih)',scale=%d:%d" % (r, r), "-frames:v", "1",
                            "-q:v", "3", dst], capture_output=True, check=True, timeout=30)
            with open(dst, "rb") as fh:
                out.append(fh.read())
        return out


def miniatury(obraz, ffmpeg="ffmpeg"):
    """(jpg 80x80, jpg 240x240) albo None, gdy obrazu nie da sie przeskalowac."""
    proby = []
    try:
        import PIL  # noqa: F401
        proby.append(lambda: _pillow(obraz, (MALA, DUZA)))
    except ImportError:
        pass
    if sys.platform == "darwin" and os.path.exists(SIPS):
        proby.append(lambda: _sips(obraz, (MALA, DUZA)))
    proby.append(lambda: _ffmpeg(obraz, (MALA, DUZA), ffmpeg))
    for p in proby:
        try:
            m, d = p()
            if m[:2] == b"\xff\xd8" and d[:2] == b"\xff\xd8":
                return m, d
        except Exception:  # noqa: BLE001  # nastepna metoda
            continue
    return None


def skrot(obraz):
    return hashlib.sha1(obraz).hexdigest()


# ------------------------------------------------------------ z sieci --
# Opcja dla chetnych (domyslnie wylaczona): utwory bez okladki w tagach szukane po wykonawcy i tytule
# w Deezer, potem w Apple iTunes. Wysylane sa tylko te dwa napisy (i album). Wynik (takze "nie znaleziono")
# zostaje w pamieci podrecznej na komputerze, wiec ponowny zapis nie odpytuje sieci.
import json
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

AGENT = "CDJ-Tool (+https://github.com/djzombie3244/cdj-tool)"
BRAK_WAZNY_S = 30 * 24 * 3600
_SLOWA_WERSJI = re.compile(r"\b(mix|remix|edit|version|extended|original|radio|club|dub|vip|rework|bootleg|"
                           r"instrumental|remaster(ed)?|feat|ft|featuring|prod)\b")


def _bez_akcentow(s):
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).replace("ł", "l").replace("Ł", "L")


def _norm(s):
    s = _bez_akcentow(s).lower().replace("&", " and ")
    return " ".join(re.sub(r"[^0-9a-z]+", " ", s).split())


def _rdzen_tytulu(t):
    """Tytul bez dopiskow wersji: "Forever (Original Mix)" -> "forever", "X feat. Y" -> "x"."""
    t = _bez_akcentow(t).lower()
    t = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", t)
    t = re.split(r"\s(feat|ft|featuring)\.?\s", t)[0]
    t = re.split(r"\s-\s", t)[0]
    return _norm(t)


def _wykonawcy(a):
    a = _bez_akcentow(a).lower()
    czesci = re.split(r",|&|\band\b|\bfeat\.?|\bft\.?|\bfeaturing\b|\bvs\.?|\bx\b|/|;", a)
    return {n for n in (_norm(c) for c in czesci) if len(n) >= 2}


def _ten_sam_wykonawca(zapytanie, kandydat):
    z, k = _wykonawcy(zapytanie), _wykonawcy(kandydat)
    if not z or not k:
        return False
    if z & k:
        return True
    zz, kk = _norm(zapytanie), _norm(kandydat)
    return len(kk) >= 3 and len(zz) >= 3 and (kk in zz or zz in kk)


def _ten_sam_tytul(zapytanie, kandydat):
    z, k = _rdzen_tytulu(zapytanie), _rdzen_tytulu(kandydat)
    return bool(z) and z == k


def z_nazwy_pliku(nazwa):
    """(wykonawca, tytul) z nazwy pliku w stylu "12. Artist - Title (Mix).mp3"; None gdy nie pasuje."""
    # "_" w nazwach plikow zastepuje znaki niedozwolone (":" , "/"): "ft. Kali_ Marta" -> "ft. Kali, Marta"
    n = os.path.splitext(os.path.basename(nazwa))[0].replace("_ ", ", ").replace("_", " ")
    n = re.sub(r"^\s*\d{1,3}\s*[\.\-\)]?\s+", "", n)
    if " - " not in n:
        return None
    # "12A - Artist - Title", "1A - 128 - Artist - Title", "[8A] Artist - Title": klucz (Camelot / Open Key) i tempo
    # z programow DJ-skich to nie wykonawca
    n = re.sub(r"^\s*[\[\(]\s*\d{1,2}\s*[ABabdm]\s*[\]\)]\s*", "", n)
    czesci = [c.strip() for c in n.split(" - ")]
    while len(czesci) > 2 and re.fullmatch(r"(?i)\d{1,2}\s*[abdm]|\d{1,3}(?:[.,]\d+)?\s*(?:bpm)?|[A-G][#b♯♭]?\s*(?:m|min|maj|major|minor)?", czesci[0]):
        czesci.pop(0)
    n = " - ".join(czesci)
    a, t = n.split(" - ", 1)
    a, t = " ".join(a.split()), " ".join(t.split())
    return (a, t) if a and t else None


class Siec:
    """Wyszukiwanie okladek w sieci dla jednego zapisu pendrive'a (wspolny bezpiecznik i limity)."""

    def __init__(self, katalog_cache=None, pobierz=None):
        self.cache = katalog_cache
        if self.cache:
            try:
                os.makedirs(self.cache, exist_ok=True)
            except OSError:
                self.cache = None
        self._pobierz = pobierz or _pobierz
        self._lock = threading.Lock()
        self.bledy_z_rzedu = 0
        self.wylaczone = False      # brak sieci: nie probujemy dalej w tym zapisie
        self.itunes_wylaczony = False  # iTunes ogranicza liczbe zapytan (ok. 20/min)
        self.odpytano = 0

    # -- http
    def _json(self, url, itunes=False):
        if self.wylaczone or (itunes and self.itunes_wylaczony):
            return None
        try:
            dane = self._pobierz(url)
        except BladHttp as e:
            if itunes and e.kod in (403, 429):
                self.itunes_wylaczony = True
            return None
        except Exception:  # noqa: BLE001  # brak sieci, DNS, czas
            with self._lock:
                self.bledy_z_rzedu += 1
                if self.bledy_z_rzedu >= 3:
                    self.wylaczone = True
            return None
        with self._lock:
            self.bledy_z_rzedu = 0
            self.odpytano += 1
        try:
            return json.loads(dane.decode("utf-8"))
        except ValueError:
            return None

    # -- zrodla
    def _deezer_album(self, artysta, album):
        q = 'artist:"%s" album:"%s"' % (artysta, album)
        j = self._json("https://api.deezer.com/search/album?limit=10&q=" + urllib.parse.quote(q))
        for d in (j or {}).get("data", []):
            if _ten_sam_wykonawca(artysta, (d.get("artist") or {}).get("name", "")) and \
                    _rdzen_tytulu(d.get("title", "")) == _rdzen_tytulu(album):
                return d.get("cover_xl") or d.get("cover_big")
        return None

    def _deezer_utwor(self, artysta, tytul):
        rdzen = _rdzen_tytulu(tytul)
        for q in ('artist:"%s" track:"%s"' % (artysta, rdzen), "%s %s" % (artysta, rdzen)):
            j = self._json("https://api.deezer.com/search?limit=15&q=" + urllib.parse.quote(q))
            trafienia = [d for d in (j or {}).get("data", [])
                         if _ten_sam_wykonawca(artysta, (d.get("artist") or {}).get("name", ""))
                         and _ten_sam_tytul(tytul, d.get("title", ""))]
            if trafienia:
                # najpierw ta sama wersja (np. ten sam remix), potem dowolna
                pelny = _norm(tytul)
                trafienia.sort(key=lambda d: _norm(d.get("title", "")) != pelny)
                al = trafienia[0].get("album") or {}
                return al.get("cover_xl") or al.get("cover_big")
        return None

    def _itunes(self, artysta, tytul):
        j = self._json("https://itunes.apple.com/search?media=music&entity=song&limit=15&term="
                       + urllib.parse.quote("%s %s" % (artysta, _rdzen_tytulu(tytul))), itunes=True)
        for d in (j or {}).get("results", []):
            if _ten_sam_wykonawca(artysta, d.get("artistName", "")) and _ten_sam_tytul(tytul, d.get("trackName", "")):
                url = d.get("artworkUrl100") or ""
                return url.replace("100x100bb", "600x600bb") if url else None
        return None

    # -- cache
    def _klucz(self, artysta, tytul, album):
        return hashlib.sha1(("%s|%s|%s" % (_norm(artysta), _rdzen_tytulu(tytul), _norm(album))).encode()).hexdigest()

    def _z_cache(self, klucz):
        if not self.cache:
            return None
        p = os.path.join(self.cache, klucz)
        try:
            if os.path.exists(p + ".jpg"):
                with open(p + ".jpg", "rb") as fh:
                    return fh.read()
            if time.time() - os.path.getmtime(p + ".brak") < BRAK_WAZNY_S:
                return b""
        except OSError:
            pass
        return None

    def _do_cache(self, klucz, obraz):
        if not self.cache:
            return
        p = os.path.join(self.cache, klucz)
        try:
            with open(p + (".jpg" if obraz else ".brak"), "wb") as fh:
                fh.write(obraz or b"")
        except OSError:
            pass

    def szukaj(self, artysta, tytul, album="", nazwa_pliku=""):
        """Bajty okladki z sieci albo None. Bez wykonawcy w tagach probuje nazwy pliku ("Artist - Title")."""
        if not artysta or artysta == "Unknown Artist":
            z_pliku = z_nazwy_pliku(nazwa_pliku)
            if not z_pliku:
                return None
            artysta, tytul = z_pliku
        album = "" if album in ("", "Unknown Album") else album
        klucz = self._klucz(artysta, tytul, album)
        c = self._z_cache(klucz)
        if c is not None:
            return c or None
        if self.wylaczone:
            return None
        url = (self._deezer_album(artysta, album) if album else None) or self._deezer_utwor(artysta, tytul) \
            or self._itunes(artysta, tytul)
        obraz = None
        if url:
            try:
                obraz = self._pobierz(url)
            except Exception:  # noqa: BLE001
                obraz = None
            if obraz and obraz[:2] != b"\xff\xd8" and obraz[:4] != b"\x89PNG":
                obraz = None
        if obraz or not self.wylaczone:  # brak sieci nie zapisuje "nie znaleziono"
            self._do_cache(klucz, obraz)
        return obraz


class BladHttp(Exception):
    def __init__(self, kod):
        super().__init__("HTTP %s" % kod)
        self.kod = kod


CURL = "/usr/bin/curl"


def _pobierz(url, czas=10, maks=8 * 1024 * 1024):
    """GET; na macOS przez systemowy curl (certyfikaty systemu), gdzie indziej urllib."""
    if sys.platform == "darwin" and os.path.exists(CURL):
        r = subprocess.run([CURL, "-sS", "-L", "-m", str(czas), "--max-filesize", str(maks), "-A", AGENT,
                            "-w", "\n%{http_code}", url], capture_output=True, timeout=czas + 5)
        if r.returncode != 0:
            raise OSError(r.stderr.decode("utf-8", "replace").strip() or "curl %d" % r.returncode)
        tresc, _, kod = r.stdout.rpartition(b"\n")
        kod = int(kod or 0)
        if kod >= 400:
            raise BladHttp(kod)
        return tresc
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    try:
        with urllib.request.urlopen(req, timeout=czas) as resp:
            return resp.read(maks + 1)[:maks]
    except urllib.error.HTTPError as e:
        raise BladHttp(e.code)
