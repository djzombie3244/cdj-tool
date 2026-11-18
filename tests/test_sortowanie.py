"""Sortowanie playlist po tonacji (Camelot) i BPM."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "cdjtool"))

import tonacja  # noqa: E402


def test_pozycja_tonacji_camelot():
    # C-dur = 8B, A-moll = 8A, G-dur = 9B, E-moll = 9A (rownolegle do G)
    assert tonacja.pozycja_tonacji(0, False) == (8, 1)
    assert tonacja.pozycja_tonacji(9, True) == (8, 0)
    assert tonacja.pozycja_tonacji(7, False) == (9, 1)
    assert tonacja.pozycja_tonacji(4, True) == (9, 0)


def test_pozycja_nazwy_camelot_i_klasyczna():
    assert tonacja.pozycja_nazwy("8A") == (8, 0)
    assert tonacja.pozycja_nazwy("12B") == (12, 1)
    assert tonacja.pozycja_nazwy("Am") == (8, 0)
    assert tonacja.pozycja_nazwy("C") == (8, 1)
    assert tonacja.pozycja_nazwy("F#m") == tonacja.pozycja_tonacji(6, True)
    assert tonacja.pozycja_nazwy("") is None
    assert tonacja.pozycja_nazwy(None) is None
    assert tonacja.pozycja_nazwy("xyz") is None


def test_klucz_kolejnosci_klucz_potem_bpm():
    utwory = [((9, 0), 128.0), ((8, 0), 130.0), (None, 90.0), ((8, 0), 124.0), ((1, 1), 100.0)]
    posortowane = sorted(utwory, key=lambda u: tonacja.klucz_kolejnosci("klucz", u[0], u[1]))
    assert posortowane == [((1, 1), 100.0), ((8, 0), 124.0), ((8, 0), 130.0), ((9, 0), 128.0), (None, 90.0)]


def test_klucz_kolejnosci_samo_bpm():
    utwory = [((9, 0), 128.0), ((8, 0), 130.0), (None, 90.0), (None, 0.0)]
    posortowane = sorted(utwory, key=lambda u: tonacja.klucz_kolejnosci("bpm", u[0], u[1]))
    # brak BPM (0) na koncu, reszta po BPM, tonacja nieistotna
    assert [u[1] for u in posortowane] == [90.0, 128.0, 130.0, 0.0]


def test_brak_bpm_na_koncu_w_trybie_klucz():
    a = tonacja.klucz_kolejnosci("klucz", (8, 0), 0.0)
    b = tonacja.klucz_kolejnosci("klucz", (8, 0), 120.0)
    assert b < a


# ---------------------------------------------------------------- zapis na pendrive (end-to-end)
def _click_track(path, bpm, seconds=12, sr=44100):
    import wave
    import numpy as np
    t = np.arange(int(seconds * sr)) / sr
    y = np.zeros_like(t)
    period = 60.0 / bpm
    for k in range(int(seconds / period)):
        i0 = int((0.3 + k * period) * sr)
        n = int(0.12 * sr)
        tt = np.arange(n) / sr
        y[i0:i0 + n] += np.sin(2 * np.pi * (110 - 400 * tt) * tt) * np.exp(-tt * 25)
    pcm = (np.clip(y, -1, 1) * 30000).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def _kolejnosc_na_sticku(usb):
    import pdb_writer as pw
    lib = pw.czytaj_biblioteke((usb / "PIONEER" / "rekordbox" / "export.pdb").read_bytes())
    tytul = {f["id"]: (s["title"] or s["filename"]) for f, s in lib["utwory"]}
    bpm = {f["id"]: f["tempo"] / 100.0 for f, s in lib["utwory"]}
    wpisy = sorted((e, tid) for e, tid, pid in lib["wpisy"])
    return [(tytul[tid], round(bpm[tid])) for _, tid in wpisy]


def test_sortowanie_bpm_przestawia_stare_wpisy(tmp_path):
    """Playlista zapisana bez sortowania, potem dopisanie z sortowaniem po BPM: caly stan playlisty idzie
    w kolejnosci BPM (stare i nowe utwory razem), a numery wpisow sa ciagle od 1."""
    import silnik
    import pathlib
    a, b, usb = tmp_path / "a", tmp_path / "b", tmp_path / "usb"
    for d in (a, b, usb):
        d.mkdir()
    _click_track(str(a / "x128.wav"), 128.0)
    _click_track(str(a / "x140.wav"), 140.0)
    _click_track(str(a / "x100.wav"), 100.0)
    silnik.przetworz({"folder_mp3": str(a), "pendrive": str(usb), "playlista": "Set", "kopiuj_mp3": True,
                      "zastap_pioneer": False, "nie_usypiaj": False, "sortuj_playlisty": None}, lambda e: None)
    _click_track(str(b / "y110.wav"), 110.0)
    silnik.przetworz({"folder_mp3": str(b), "pendrive": str(usb), "playlista": "Set", "kopiuj_mp3": True,
                      "zastap_pioneer": False, "nie_usypiaj": False, "sortuj_playlisty": "bpm"}, lambda e: None)
    wynik = _kolejnosc_na_sticku(usb)
    assert [bpm for _, bpm in wynik] == sorted(bpm for _, bpm in wynik), wynik
    assert len(wynik) == 4, wynik
    lib = __import__("pdb_writer").czytaj_biblioteke(
        (usb / "PIONEER" / "rekordbox" / "export.pdb").read_bytes())
    numery = sorted(e for e, _, _ in lib["wpisy"])
    assert numery == list(range(1, len(numery) + 1)), numery


def _akord_track(path, bpm, nuty, seconds=20, sr=44100):
    import wave
    import numpy as np
    t = np.arange(int(seconds * sr)) / sr
    y = sum(np.sin(2 * np.pi * f * t) for f in nuty) * 0.2
    period = 60.0 / bpm
    for k in range(int(seconds / period)):
        i0 = int((0.3 + k * period) * sr)
        n = int(0.12 * sr)
        tt = np.arange(n) / sr
        y[i0:i0 + n] += np.sin(2 * np.pi * (110 - 400 * tt) * tt) * np.exp(-tt * 25)
    pcm = (np.clip(y, -1, 1) * 30000).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def test_sortowanie_po_tonacji_camelot(tmp_path):
    """Trzy utwory w tonacjach C-dur (8B), A-moll (8A), G-dur (9B): kolejnosc 8A, 8B, 9B
    (numer kola, potem A przed B). Zawsze z analizy, nie z BPM."""
    import silnik
    import pdb_writer as pw
    a, usb = tmp_path / "a", tmp_path / "usb"
    a.mkdir()
    usb.mkdir()
    _akord_track(str(a / "c_maj.wav"), 120.0, [261.63, 329.63, 392.0])
    _akord_track(str(a / "a_min.wav"), 100.0, [220.0, 261.63, 329.63])
    _akord_track(str(a / "g_maj.wav"), 128.0, [196.0, 246.94, 293.66])
    silnik.przetworz({"folder_mp3": str(a), "pendrive": str(usb), "playlista": "K", "kopiuj_mp3": True,
                      "zastap_pioneer": False, "nie_usypiaj": False, "sortuj_playlisty": "klucz",
                      "tonacja": True, "notacja": "camelot"}, lambda e: None)
    lib = pw.czytaj_biblioteke((usb / "PIONEER" / "rekordbox" / "export.pdb").read_bytes())
    tytul = {f["id"]: (s["title"] or s["filename"]) for f, s in lib["utwory"]}
    kolejka = [tytul[tid] for _, tid, _ in sorted(lib["wpisy"])]
    assert kolejka == ["a_min", "c_maj", "g_maj"], kolejka
