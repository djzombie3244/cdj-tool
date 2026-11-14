"""Import siatek beatow i cue z innych programow DJ-skich: rekordbox (eksport kolekcji do XML), Traktor
(collection.nml) i Serato (dane zapisane w tagach plikow). Czysty Python, bez numpy: uzywa go tez okno (app.py).

Wpis utworu po imporcie:
  {"zrodlo": "rekordbox" | "traktor" | "serato",
   "siatka": [[czas_s, bpm, numer_w_takcie]] albo None,   # punkty, od ktorych siatka idzie ze stalym tempem
   "hot": [[numer 1..8, ms]], "pamiec": [ms]}
Czasy sa we wspolrzednych programu zrodlowego; przesuniecie do osi czasu CDJ liczy `przesuniecie_s`."""
import base64
import json
import os
import re
import struct
import sys
import threading
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET

_LOCK = threading.Lock()
NAZWY = {"rekordbox": "rekordbox", "traktor": "Traktor", "serato": "Serato"}


# ------------------------------------------------------------- klucze --
def klucz_sciezki(sciezka):
    """Sciezka pliku do porownan: NFC, ukosniki w przod, bez wielkosci liter."""
    p = unicodedata.normalize("NFC", str(sciezka)).replace("\\", "/")
    return re.sub(r"/+", "/", p).casefold()


def _nazwa(klucz):
    return klucz.rsplit("/", 1)[-1]


# ----------------------------------------------------------- rekordbox --
def _liczba(x, domyslna=None):
    try:
        return float(str(x).replace(",", "."))
    except (TypeError, ValueError):
        return domyslna


def _sciezka_z_url(url):
    """Location z rekordbox.xml: file://localhost/Users/a/b.mp3 albo file://localhost/C:/Music/b.mp3."""
    p = urllib.parse.unquote(url)
    p = re.sub(r"^file://(localhost)?", "", p)
    if re.match(r"^/[A-Za-z]:/", p):
        p = p[1:]
    return p


def czytaj_rekordbox_xml(zrodlo):
    """{klucz sciezki: wpis} z pliku XML kolekcji rekordboxa (File > Export Collection in xml format)."""
    out = {}
    for _, el in ET.iterparse(zrodlo, events=("end",)):
        if el.tag != "TRACK" or not el.get("Location"):
            continue
        siatka = []
        for t in el.findall("TEMPO"):
            start, bpm = _liczba(t.get("Inizio")), _liczba(t.get("Bpm"))
            if start is None or not bpm or not (20 <= bpm <= 400):
                continue
            nr = int(_liczba(t.get("Battito"), 1) or 1)
            siatka.append([max(0.0, start), bpm, nr if 1 <= nr <= 4 else 1])
        hot, pam = {}, []
        for m in el.findall("POSITION_MARK"):
            start, num = _liczba(m.get("Start")), int(_liczba(m.get("Num"), -1))
            if start is None or start < 0:
                continue
            ms = int(round(start * 1000))
            if 0 <= num <= 7:
                hot.setdefault(num + 1, ms)
            else:
                pam.append(ms)
        if siatka or hot or pam:
            out[klucz_sciezki(_sciezka_z_url(el.get("Location")))] = {
                "zrodlo": "rekordbox", "siatka": sorted(siatka) or None,
                "hot": sorted(hot.items()), "pamiec": sorted(set(pam))[:10]}
        el.clear()
    return out


# ------------------------------------------------------------- Traktor --
def _sciezka_traktora(loc):
    """LOCATION z collection.nml: VOLUME="Macintosh HD" DIR="/:Users/:a/:" FILE="b.mp3" (Windows: VOLUME="C:")."""
    katalog = (loc.get("DIR") or "").replace("/:", "/")
    wol, plik = loc.get("VOLUME") or "", loc.get("FILE") or ""
    if re.match(r"^[A-Za-z]:$", wol):
        return wol + katalog + plik
    if katalog.startswith(("/Users/", "/Volumes/", "/home/")) or not wol:
        return katalog + plik
    return "/Volumes/" + wol + katalog + plik


def czytaj_traktor_nml(zrodlo):
    """{klucz sciezki: wpis} z collection.nml Traktora. Traktor ma jedno tempo na utwor i znaczniki siatki
    (CUE_V2 TYPE=4): kazdy znacznik to beat "raz", od ktorego siatka idzie ze stalym tempem."""
    out = {}
    for _, el in ET.iterparse(zrodlo, events=("end",)):
        if el.tag != "ENTRY":
            continue
        loc = el.find("LOCATION")
        if loc is None or not loc.get("FILE"):
            el.clear()
            continue
        tempo = el.find("TEMPO")
        bpm = _liczba(tempo.get("BPM")) if tempo is not None else None
        siatka, hot, pam = [], {}, []
        for c in el.findall("CUE_V2"):
            start, typ = _liczba(c.get("START")), int(_liczba(c.get("TYPE"), 0))
            numer = int(_liczba(c.get("HOTCUE"), -1))
            if start is None or start < 0:
                continue
            if typ == 4 and bpm and 20 <= bpm <= 400:
                siatka.append([start / 1000.0, bpm, 1])
            ms = int(round(start))
            if 0 <= numer <= 7:
                hot.setdefault(numer + 1, ms)
            elif typ != 4:
                pam.append(ms)
        if siatka or hot or pam:
            wpis = {"zrodlo": "traktor", "siatka": sorted(siatka) or None,
                    "hot": sorted(hot.items()), "pamiec": sorted(set(pam))[:10]}
            sciezka = _sciezka_traktora(loc)
            out[klucz_sciezki(sciezka)] = wpis
            if sciezka.startswith("/Volumes/"):  # dysk systemowy bywa zapisany z nazwa woluminu
                out.setdefault(klucz_sciezki("/" + sciezka.split("/", 3)[3]), wpis)
        el.clear()
    return out


def kolekcje_traktora():
    """Pliki collection.nml Traktora na tym komputerze, najnowszy pierwszy."""
    dom = os.path.expanduser("~")
    bazy = [os.path.join(dom, "Documents", "Native Instruments"),
            os.path.join(dom, "Dokumenty", "Native Instruments")]
    out = []
    for b in bazy:
        try:
            for nm in os.listdir(b):
                p = os.path.join(b, nm, "collection.nml")
                if nm.lower().startswith("traktor") and os.path.isfile(p):
                    out.append((os.path.getmtime(p), p))
        except OSError:
            continue
    return [p for _, p in sorted(out, reverse=True)]


# -------------------------------------------------------------- Serato --
def _syncsafe(b):
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _blok_id3(sciezka):
    """Surowy blok ID3v2 pliku MP3 albo kawalek 'ID3 ' w WAV/AIFF; None gdy go nie ma."""
    rozmiar = os.path.getsize(sciezka)
    with open(sciezka, "rb") as f:
        p = f.read(12)
        if p[:3] == b"ID3":
            f.seek(0)
            return f.read(10 + _syncsafe(p[6:10]))
        riff = p[:4] == b"RIFF" and p[8:12] == b"WAVE"
        if riff or (p[:4] == b"FORM" and p[8:12] in (b"AIFF", b"AIFC")):
            f.seek(12)
            while f.tell() + 8 <= rozmiar:
                h = f.read(8)
                if len(h) < 8:
                    break
                n = struct.unpack("<I" if riff else ">I", h[4:])[0]
                if h[:4] in (b"ID3 ", b"id3 "):
                    return f.read(n)
                f.seek(n + (n & 1), 1)
    return None


def _ramki_geob(blok):
    """{opis: dane} ramek GEOB z bloku ID3v2.3/2.4 (Serato trzyma tam swoje dane)."""
    out = {}
    if not blok or len(blok) < 10 or blok[:3] != b"ID3" or blok[3] not in (3, 4):
        return out
    wersja, flagi = blok[3], blok[5]
    tresc = blok[10:10 + _syncsafe(blok[6:10])]
    if flagi & 0x80 and wersja == 3:
        tresc = tresc.replace(b"\xff\x00", b"\xff")
    o = 0
    if flagi & 0x40:
        o = _syncsafe(tresc[0:4]) if wersja == 4 else struct.unpack(">I", tresc[0:4])[0] + 4
    while o + 10 <= len(tresc):
        fid = tresc[o:o + 4]
        fsz = _syncsafe(tresc[o + 4:o + 8]) if wersja == 4 else struct.unpack(">I", tresc[o + 4:o + 8])[0]
        ffl = struct.unpack(">H", tresc[o + 8:o + 10])[0]
        o += 10
        if not fid.strip(b"\x00") or fsz <= 0 or o + fsz > len(tresc):
            break
        dane = tresc[o:o + fsz]
        o += fsz
        if fid != b"GEOB":
            continue
        if wersja == 4:
            if ffl & 0x0001:
                dane = dane[4:]
            if ffl & 0x0002:
                dane = dane.replace(b"\xff\x00", b"\xff")
        enc = dane[0]
        k = dane.find(b"\x00", 1)  # typ MIME (zawsze Latin-1)
        if k < 0:
            continue
        p = k + 1
        pola = []
        for _ in range(2):  # nazwa pliku, opis: w kodowaniu ramki
            if enc in (1, 2):
                j = p
                while j + 1 < len(dane) and dane[j:j + 2] != b"\x00\x00":
                    j += 2
                pola.append(dane[p:j].decode("utf-16", "replace"))
                p = j + 2
            else:
                j = dane.find(b"\x00", p)
                if j < 0:
                    j = len(dane)
                pola.append(dane[p:j].decode("latin-1"))
                p = j + 1
        out[pola[1]] = dane[p:]
    return out


def _serato_markers2(dane):
    """Hot cue z ramki "Serato Markers2": [(numer 1..8, ms)]."""
    if dane[:2] != b"\x01\x01":
        return []
    tekst = dane[2:].replace(b"\n", b"").rstrip(b"\x00")
    reszta = len(tekst) % 4
    if reszta == 1:
        tekst += b"A=="
    elif reszta:
        tekst += b"=" * (4 - reszta)
    try:
        b = base64.b64decode(tekst)
    except Exception:  # noqa: BLE001
        return []
    hot, o = {}, 2
    while o < len(b):
        k = b.find(b"\x00", o)
        if k <= o:
            break
        nazwa = b[o:k]
        if k + 5 > len(b):
            break
        dl = struct.unpack(">I", b[k + 1:k + 5])[0]
        tresc = b[k + 5:k + 5 + dl]
        o = k + 5 + dl
        if nazwa == b"CUE" and len(tresc) >= 6:
            numer, ms = tresc[1], struct.unpack(">I", tresc[2:6])[0]
            if 0 <= numer <= 7:
                hot.setdefault(numer + 1, ms)
    return sorted(hot.items())


def _serato_beatgrid(dane):
    """Siatka z ramki "Serato BeatGrid": punkty [czas_s, bpm, 1]."""
    if len(dane) < 6 or dane[:2] != b"\x01\x00":
        return None
    n = struct.unpack(">I", dane[2:6])[0]
    if not (1 <= n <= 4096) or len(dane) < 6 + 8 * n:
        return None
    znaczniki = [dane[6 + 8 * i:14 + 8 * i] for i in range(n)]
    out = []
    for i, z in enumerate(znaczniki):
        poz = struct.unpack(">f", z[:4])[0]
        if i + 1 < n:
            nast = struct.unpack(">f", znaczniki[i + 1][:4])[0]
            beatow = struct.unpack(">I", z[4:])[0]
            if nast <= poz or not beatow:
                continue
            bpm = 60.0 * beatow / (nast - poz)
        else:
            bpm = struct.unpack(">f", z[4:])[0]
        if poz == poz and bpm == bpm and 20 <= bpm <= 400 and poz > -10:
            out.append([float(poz), float(bpm), 1])
    return out or None


def serato_z_pliku(sciezka):
    """Wpis z danych Serato zapisanych w pliku (MP3, WAV, AIFF) albo None."""
    try:
        ramki = _ramki_geob(_blok_id3(sciezka))
    except (OSError, struct.error, IndexError, ValueError):
        return None
    hot = _serato_markers2(ramki["Serato Markers2"]) if "Serato Markers2" in ramki else []
    siatka = _serato_beatgrid(ramki["Serato BeatGrid"]) if "Serato BeatGrid" in ramki else None
    if not hot and not siatka:
        return None
    return {"zrodlo": "serato", "siatka": siatka, "hot": hot, "pamiec": []}


# ------------------------------------------------------ os czasu MP3 --
def mp3_info_bez_lame(sciezka):
    """MP3 z naglowkiem Xing/Info, ale bez znacznika LAME: rekordbox i CDJ licza czas takich plikow o jedna
    ramke (26 ms) wczesniej niz Traktor."""
    try:
        with open(sciezka, "rb") as f:
            p = f.read(10)
            o = 10 + _syncsafe(p[6:10]) if p[:3] == b"ID3" else 0
            f.seek(o)
            d = f.read(4096)
    except OSError:
        return False
    i = d.find(b"\xff")
    while 0 <= i < len(d) - 4 and (d[i + 1] & 0xE0) != 0xE0:
        i = d.find(b"\xff", i + 1)
    if i < 0:
        return False
    ramka = d[i:i + 1500]
    k = max(ramka.find(b"Xing"), ramka.find(b"Info"))
    if k < 0:
        return False
    return ramka[k + 0x78:k + 0x7C] != b"LAME"


def przesuniecie_s(wpis, sciezka):
    """Ile dodac do czasow wpisu, zeby trafily na os czasu rekordboxa i CDJ."""
    if wpis.get("zrodlo") == "traktor" and str(sciezka).lower().endswith(".mp3") and mp3_info_bez_lame(sciezka):
        return -0.026
    return 0.0


# --------------------------------------------------------------- edycja --
def edycja(wpis, beaty, hot, pamiec, dlugosc_s, przes_s=0.0):
    """Edycja dla edytor.zbuduj_pliki(): siatka i cue z importu na tle wyniku wlasnej analizy.
    beaty/hot/pamiec: obecne dane utworu. Siatka z importu zastepuje nasza; importowane hot cue zajmuja swoje
    miejsca, pozostale miejsca zostaja z naszej analizy (mod RaftTone wymaga pelnego banku D-F); memory cue z
    importu zastepuja nasze, gdy sa."""
    nowe = [list(b) for b in beaty]
    punkty = sorted((p for p in (wpis.get("siatka") or []) if 20 <= p[1] <= 400), key=lambda p: p[0])
    if punkty:
        nowe = []
        for i, (start, bpm, nr) in enumerate(punkty):
            start += przes_s
            okres = 60.0 / bpm
            koniec = (punkty[i + 1][0] + przes_s - 0.5 * okres) if i + 1 < len(punkty) else dlugosc_s - 0.05
            k = -int(start // okres) if i == 0 else 0  # pierwszy punkt: siatka takze przed nim, od poczatku utworu
            while start + k * okres < koniec:
                t = start + k * okres
                if t >= 0 and (not nowe or t - nowe[-1][0] > 0.5 * okres):
                    nowe.append([t, (int(nr) - 1 + k) % 4 + 1, bpm])
                k += 1
        if len(nowe) < 2:
            nowe = [list(b) for b in beaty]
    maks_ms = int(dlugosc_s * 1000)

    def ms_ok(ms):
        return min(maks_ms, max(0, int(round(ms + przes_s * 1000))))

    h = {int(n): int(ms) for n, ms in hot}
    for n, ms in wpis.get("hot") or []:
        if 1 <= int(n) <= 8:
            h[int(n)] = ms_ok(ms)
    pam = [ms_ok(ms) for ms in wpis.get("pamiec") or []] or [int(ms) for ms in pamiec]
    return {"beaty": nowe, "hot": sorted(h.items()), "pamiec": sorted(set(pam))[:10]}


# ------------------------------------------------- import na komputerze --
def plik_importu():
    if sys.platform == "win32":
        baza = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "CDJ-Tool")
    else:
        baza = os.path.expanduser("~/Library/Application Support/CDJ-Tool")
    return os.environ.get("CDJ_IMPORT") or os.path.join(baza, "import.json")


def wczytaj():
    try:
        with open(plik_importu(), encoding="utf-8") as fh:
            d = json.load(fh)
        if isinstance(d, dict) and isinstance(d.get("utwory"), dict):
            return d
    except (OSError, ValueError):
        pass
    return {"zrodla": [], "utwory": {}}


def stan():
    """Krotki opis dla okna: [{typ, plik, utworow}]."""
    return wczytaj().get("zrodla", [])


def _zapisz(d):
    p = plik_importu()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, p)


def rozpoznaj_i_czytaj(zrodlo, nazwa=""):
    """(typ, {klucz: wpis}) z pliku rekordbox.xml albo collection.nml (rozpoznanie po zawartosci)."""
    if hasattr(zrodlo, "read"):
        poczatek = zrodlo.read(4096)
        zrodlo.seek(0)
    else:
        with open(zrodlo, "rb") as fh:
            poczatek = fh.read(4096)
    if isinstance(poczatek, str):
        poczatek = poczatek.encode("utf-8", "replace")
    if b"<NML" in poczatek or nazwa.lower().endswith(".nml"):
        return "traktor", czytaj_traktor_nml(zrodlo)
    if b"<DJ_PLAYLISTS" in poczatek or nazwa.lower().endswith(".xml"):
        return "rekordbox", czytaj_rekordbox_xml(zrodlo)
    raise ValueError("To nie jest plik kolekcji rekordboxa (XML) ani Traktora (collection.nml).")


def dodaj(zrodlo, nazwa=""):
    """Wczytuje plik kolekcji i dopisuje go do importu (ten sam program zastepuje poprzedni import)."""
    try:
        typ, utwory = rozpoznaj_i_czytaj(zrodlo, nazwa)
    except ET.ParseError as e:
        raise ValueError("Nie mogę odczytać pliku kolekcji (%s)." % e) from e
    if not utwory:
        raise ValueError("W tym pliku nie ma utworów z siatką ani cue.")
    with _LOCK:
        d = wczytaj()
        d["utwory"] = {k: w for k, w in d["utwory"].items() if w.get("zrodlo") != typ}
        d["utwory"].update(utwory)
        d["zrodla"] = [z for z in d.get("zrodla", []) if z.get("typ") != typ]
        d["zrodla"].append({"typ": typ, "plik": os.path.basename(nazwa or str(zrodlo)), "utworow": len(utwory)})
        _zapisz(d)
    return {"typ": typ, "utworow": len(utwory)}


def usun():
    with _LOCK:
        try:
            os.remove(plik_importu())
        except OSError:
            pass


class Import:
    """Wyszukiwanie wpisu dla pliku zrodlowego: po pelnej sciezce, potem po nazwie pliku (gdy jest jedna taka)."""

    def __init__(self, dane=None, serato=False):
        dane = dane if dane is not None else wczytaj()
        self.utwory = dane.get("utwory", {})
        self.serato = bool(serato)
        self._nazwy = None

    def __bool__(self):
        return bool(self.utwory) or self.serato

    def dla(self, sciezka):
        k = klucz_sciezki(os.path.abspath(sciezka))
        w = self.utwory.get(k)
        if w is None and self.utwory:
            if self._nazwy is None:
                self._nazwy = {}
                for kk in self.utwory:
                    self._nazwy.setdefault(_nazwa(kk), []).append(kk)
            kand = self._nazwy.get(_nazwa(k), [])
            if len(kand) == 1:
                w = self.utwory[kand[0]]
        if w is None and self.serato:
            w = serato_z_pliku(sciezka)
        return w
