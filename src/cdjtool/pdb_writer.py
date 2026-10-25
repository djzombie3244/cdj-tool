"""Zapis bazy export.pdb (format DeviceSQL używany przez odtwarzacze Pioneer).

Format odtworzony na podstawie wzorca z rekordbox oraz publicznej dokumentacji
społeczności (Deep Symmetry). Tabele statyczne (kolory, kolumny, historia itd.)
są kopiowane z wzorca, a tabele z danymi (utwory, artyści, albumy, playlisty)
budowane od zera.
"""
import struct

PL = 4096
HDR = 40

TYPE_TRACKS, TYPE_GENRES, TYPE_ARTISTS, TYPE_ALBUMS, TYPE_LABELS, TYPE_KEYS = 0, 1, 2, 3, 4, 5
TYPE_PLAYLIST_TREE, TYPE_PLAYLIST_ENTRIES = 7, 8


# ------------------------------------------------------------ napisy -------
def dstr(text):
    """Napis w formacie DeviceSQL (krótki ASCII, długi ASCII lub UTF-16)."""
    try:
        raw = text.encode("ascii")
    except UnicodeEncodeError:
        u = text.encode("utf-16le")
        return b"\x90" + struct.pack("<H", len(u) + 4) + b"\x00" + u
    if len(raw) <= 126:
        return bytes([((len(raw) + 1) << 1) | 1]) + raw
    return b"\x40" + struct.pack("<H", len(raw) + 4) + b"\x00" + raw


def dstr_len(buf, o):
    h = buf[o]
    if h & 1:
        return h >> 1
    return struct.unpack("<H", buf[o + 1:o + 3])[0]


def dstr_decode(buf, o):
    h = buf[o]
    if h & 1:
        return buf[o + 1:o + (h >> 1)].decode("latin1")
    n = struct.unpack("<H", buf[o + 1:o + 3])[0]
    if h == 0x40:
        return buf[o + 4:o + n].decode("latin1")
    if h == 0x90:
        return buf[o + 4:o + n].decode("utf-16le")
    return "?"


def _pad4(b, extra=0):
    b = bytes(b) + b"\x00" * extra
    return b + b"\x00" * ((-len(b)) % 4)


# ------------------------------------------------------------ wiersze ------
TRACK_FIXED = "<HHIIIIIHHIIIIIIIIIIIIHHHHHHBBHH"  # 94 bajtow
TRACK_FIELDS = ["sub", "shift", "bitmask", "sample_rate", "composer", "file_size", "unk20",
                "unk24", "unk26", "artwork", "key", "orig_artist", "label", "remixer",
                "bitrate", "track_no", "tempo", "genre", "album", "artist", "id", "disc",
                "plays", "year", "depth", "duration", "unk86", "color", "rating", "unk90", "unk92"]
TRACK_STR_NAMES = ["isrc", "lyricist", "s2", "s3", "s4", "message", "kuvo", "autoload",
                   "s8", "s9", "date_added", "release_date", "mix_name", "s13", "analyze_path",
                   "analyze_date", "comment", "title", "s18", "filename", "file_path"]


def track_row(shift, f, strs):
    """f: słownik pól liczbowych, strs: słownik napisów (TRACK_STR_NAMES)."""
    vals = dict(f)
    vals["sub"] = 0x24
    vals["shift"] = shift
    fixed = struct.pack(TRACK_FIXED, *[vals[k] for k in TRACK_FIELDS])
    blob = b""
    offs = []
    pos = 94 + 42
    for name in TRACK_STR_NAMES:
        s = dstr(strs.get(name, ""))
        if s[0] in (0x90, 0x40):  # dlugie napisy musza zaczynac sie na adresie podzielnym przez 4 (jak w Rekordboxie)
            pad = (-pos) % 4
            blob += b"\x00" * pad
            pos += pad
        offs.append(pos)
        blob += s
        pos += len(s)
    return _pad4(fixed + struct.pack("<21H", *offs) + blob, 4)


def parse_track_row(row):
    f = dict(zip(TRACK_FIELDS, struct.unpack(TRACK_FIXED, row[:94])))
    so = struct.unpack("<21H", row[94:136])
    strs = {n: dstr_decode(row, o) for n, o in zip(TRACK_STR_NAMES, so)}
    return f, strs


def artist_row(shift, aid, name):
    s = dstr(name)
    if s[0] & 1:
        return _pad4(struct.pack("<HHIBB", 0x60, shift, aid, 3, 10) + s, 4)
    # Dlugi napis (UTF-16, np. "n" z akcentem): rekordbox uzywa tego samego typu 0x60
    # z przesunieciem 12 (nie 0x64 z far-offset) - zweryfikowane na eksporcie rekordbox.
    return _pad4(struct.pack("<HHIBBH", 0x60, shift, aid, 3, 12, 0) + s, 4)


def album_row(shift, alid, artist_id, name):
    s = dstr(name)
    if s[0] & 1:
        return _pad4(struct.pack("<HHIIIIBB", 0x80, shift, 0, artist_id, alid, 0, 3, 22) + s, 4)
    # Analogicznie do wiersza artysty (bez wzorca z rekordboxa dla dlugiego albumu - hipoteza).
    return _pad4(struct.pack("<HHIIIIBBH", 0x80, shift, 0, artist_id, alid, 0, 3, 24, 0) + s, 4)


def playlist_tree_row(parent_id, sort_order, pid, is_folder, name):
    return _pad4(struct.pack("<IIIII", parent_id, 0, sort_order, pid, 1 if is_folder else 0) + dstr(name), 4)


def playlist_entry_row(entry_index, track_id, playlist_id):
    return struct.pack("<III", entry_index, track_id, playlist_id)


# ------------------------------------------------------------ strony -------
def build_data_page(page_idx, ptype, next_page, rows, flags, seq):
    n = len(rows)
    heap = b""
    offs = []
    for r in rows:
        offs.append(len(heap))
        heap += r
    used = len(heap)
    ng = (n + 15) // 16
    idx_size = sum(2 * min(16, n - 16 * g) + 4 for g in range(ng))
    free = PL - HDR - used - idx_size
    if free < 0:
        raise ValueError("wiersze nie mieszcza sie na jednej stronie")
    page = bytearray(PL)
    struct.pack_into("<IIIIII", page, 0, 0, page_idx, ptype, next_page, seq, 0)
    b24 = (n & 0x1FFF) | ((n & 0x7FF) << 13)
    page[24:27] = b24.to_bytes(3, "little")
    page[27] = flags
    struct.pack_into("<HH", page, 28, free, used)
    u3 = min(1, n)
    struct.pack_into("<HHHH", page, 32, u3, (n + 1) // 2, 0, 0)
    page[HDR:HDR + used] = heap
    for g in range(ng):
        base = PL - 36 * (g + 1)
        cnt = min(16, n - 16 * g)
        for i in range(cnt):
            struct.pack_into("<H", page, base + 2 * (15 - i), offs[16 * g + i])
        fl = (1 << cnt) - 1
        unk = 0xFFFF if cnt == 16 else (1 << (cnt - 1))
        struct.pack_into("<HH", page, base + 32, fl, unk)
    return bytes(page)


def rows_per_pages(rows):
    """Dzieli listę wierszy na grupy mieszczące się na pojedynczych stronach."""
    pages, cur = [], []
    for r in rows:
        trial = cur + [r]
        used = sum(len(x) for x in trial)
        ng = (len(trial) + 15) // 16
        idx = sum(2 * min(16, len(trial) - 16 * g) + 4 for g in range(ng))
        if HDR + used + idx > PL and cur:
            pages.append(cur)
            cur = [r]
        else:
            cur = trial
    if cur:
        pages.append(cur)
    return pages


def patch_index_page(page_bytes, entry_pages, tail=False):
    """Strona indeksowa tabeli: kopia wzorca z listą stron danych (dla stron 0x34)."""
    p = bytearray(page_bytes)
    words = list(struct.unpack("<%dI" % ((PL - HDR) // 4), p[HDR:PL]))
    if len(entry_pages) > len(words) - 5:
        raise ValueError("Przekroczono pojemność indeksu bazy CDJ (około %d stron danych). "
                         "Podziel zbiór utworów na kilka pendrive'ów." % (len(words) - 5))
    if tail:  # ostatnia strona danych jest ogonem (0x24) i nie wchodzi do indeksu
        entry_pages = entry_pages[:-1]
        words[4] = (len(entry_pages) << 16) | len(entry_pages)
    else:
        words[4] = 0x1FFF0000 | len(entry_pages)
    for i in range(len(words) - 5):
        words[5 + i] = (entry_pages[i] << 3) if i < len(entry_pages) else 0x1FFFFFF8
    if tail and len(entry_pages) > 1:
        words[5 + len(entry_pages) - 1] |= 3  # w RB ostatni wpis przed ogonem ma flage 3
    p[HDR:PL] = struct.pack("<%dI" % len(words), *words)
    return bytes(p)


# ------------------------------------------------------------ plik ---------
class Wzorzec:
    def __init__(self, data):
        self.data = data
        z, pl, nt, nu, u5, seq, gap = struct.unpack("<IIIIIII", data[:28])
        assert pl == PL
        self.ntab, self.next_unused, self.seq = nt, nu, seq
        self.tabs = {}
        for i in range(nt):
            t, empty, first, last = struct.unpack("<IIII", data[28 + 16 * i:44 + 16 * i])
            self.tabs[t] = [empty, first, last]
        self.npages = len(data) // PL

    def page(self, i):
        return self.data[i * PL:(i + 1) * PL]

    def chain(self, t):
        """Strony tabeli od pierwszej (indeksowej) do ostatniej."""
        empty, first, last = self.tabs[t]
        out, p = [], first
        while True:
            out.append(p)
            if p == last:
                break
            p = struct.unpack("<I", self.page(p)[12:16])[0]
        return out


def zbuduj_pdb(wzorzec_bytes, tabele):
    """tabele: {typ: [wiersze]} dla tabel dynamicznych. Reszta kopiowana ze wzorca."""
    w = Wzorzec(wzorzec_bytes)
    pages = {i: w.page(i) for i in range(1, w.npages)}
    nextfree = w.next_unused
    nowe_tabs = {t: list(v) for t, v in w.tabs.items()}

    for t, rows in tabele.items():
        empty, first, last = w.tabs[t]
        chain = w.chain(t)
        idx_page = chain[0]
        d0 = [pg for pg in chain[1:] if w.page(pg)[27] in (0x24, 0x34)][0]
        flags = w.page(d0)[27]
        seq = struct.unpack("<I", w.page(d0)[16:20])[0]
        groups = rows_per_pages(rows)

        # strony danych tabeli: najpierw ponownie uzywamy istniejacych (jak Rekordbox, bez osieroconych stron)
        cand = [pg for pg in chain[1:] if w.page(pg)[27] in (0x24, 0x34)]
        alloc = cand[:len(groups)]
        end_candidate = empty
        while len(alloc) < len(groups):
            if end_candidate == empty:
                alloc.append(empty)
                end_candidate = nextfree
                nextfree += 1
            else:
                alloc.append(end_candidate)
                end_candidate = nextfree
                nextfree += 1
        for pg in cand[len(groups):]:
            pages[pg] = bytes(PL)  # niepotrzebna stara strona danych -> pusta

        for gi, rws in enumerate(groups):
            nxt = alloc[gi + 1] if gi + 1 < len(groups) else end_candidate
            fl = flags
            if flags == 0x34 and len(groups) > 1 and gi == len(groups) - 1:
                fl = 0x24  # jak w Rekordboxie: ostatnia strona (ogon) ma flage 0x24
            pages[alloc[gi]] = build_data_page(alloc[gi], t, nxt, rws, fl, seq)

        ip = bytearray(w.page(idx_page))
        struct.pack_into("<I", ip, 12, alloc[0])
        if flags == 0x34:
            ip = bytearray(patch_index_page(bytes(ip), alloc, tail=len(groups) > 1))
        pages[idx_page] = bytes(ip)
        nowe_tabs[t] = [end_candidate, first, alloc[-1]]

    head = bytearray(w.data[:PL])
    struct.pack_into("<I", head, 12, nextfree)
    for i, (t, v) in enumerate(sorted(nowe_tabs.items())):
        struct.pack_into("<IIII", head, 28 + 16 * i, t, v[0], v[1], v[2])
    out = bytearray(bytes(head))
    for i in range(1, max(pages) + 1):
        out += pages.get(i, bytes(PL))
    return bytes(out)


# ---------------------------------------------------- odczyt (do testów) ----
def czytaj_wiersze(page):
    n = page[24] | ((page[25] & 0x1F) << 8)
    n = int.from_bytes(page[24:27], "little") & 0x1FFF
    ng = (n + 15) // 16
    rows = []
    for g in range(ng):
        base = PL - 36 * (g + 1)
        offs = struct.unpack("<16H", page[base:base + 32])
        fl = struct.unpack("<H", page[base + 32:base + 34])[0]
        for i in range(16):
            if g * 16 + i >= n:
                break
            if (fl >> i) & 1:
                rows.append(offs[15 - i])
    return n, rows


# ---------------------------------------------- odczyt istniejacej biblioteki ----
def _wiersze_tabeli(w, t):
    """Surowe wiersze (od poczatku wiersza do konca strony) wszystkich stron danych tabeli."""
    for pg in w.chain(t)[1:]:
        p = w.page(pg)
        if p[27] not in (0x24, 0x34):
            continue
        n, offs = czytaj_wiersze(p)
        for o in offs:
            yield p[HDR + o:PL]


def czytaj_biblioteke(data):
    """Odczytuje utwory, artystow, albumy i playlisty z istniejacego export.pdb (nasz lub z rekordboxa).
    Zwraca slownik; rzuca ValueError, gdy czegos nie da sie bezstratnie odczytac."""
    w = Wzorzec(data)
    for t in (0, 2, 3, 7, 8):
        if t not in w.tabs:
            raise ValueError("w bazie brakuje tabeli %d" % t)
    utwory = []
    for r in _wiersze_tabeli(w, 0):
        f, s = parse_track_row(r)
        if any(v == "?" for v in s.values()):
            raise ValueError("nieznany format napisu w utworze id=%s" % f["id"])
        utwory.append((f, s))
    artysci = []
    for r in _wiersze_tabeli(w, 2):
        sub = struct.unpack("<H", r[0:2])[0]
        aid = struct.unpack("<I", r[4:8])[0]
        o = struct.unpack("<H", r[10:12])[0] if sub & 4 else r[9]
        artysci.append((aid, dstr_decode(r, o)))
    albumy = []
    for r in _wiersze_tabeli(w, 3):
        sub = struct.unpack("<H", r[0:2])[0]
        art_id, alid = struct.unpack("<II", r[8:16])
        o = struct.unpack("<H", r[22:24])[0] if sub & 4 else r[21]
        albumy.append((alid, art_id, dstr_decode(r, o)))
    drzewo = []
    for r in _wiersze_tabeli(w, 7):
        parent, _, sort, pid, fold = struct.unpack("<IIIII", r[:20])
        drzewo.append((parent, sort, pid, bool(fold), dstr_decode(r, 20)))
    wpisy = []
    for r in _wiersze_tabeli(w, 8):
        wpisy.append(struct.unpack("<III", r[:12]))
    return {"utwory": utwory, "artysci": artysci, "albumy": albumy, "drzewo": drzewo, "wpisy": wpisy}
