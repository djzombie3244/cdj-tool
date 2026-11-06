"""Tonacja: wykrywanie na syntetycznych akordach i zapis w export.pdb (tabela kluczy, pole key utworu).
Wymaga numpy, scipy i ffmpeg (test zapisu); bez nich testy sa pomijane."""
import os
import shutil
import struct
import wave

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

import tonacja  # noqa: E402

SR = 22050


def _ton(f, t, harm=6):
    return sum((0.6 ** h) * np.sin(2 * np.pi * f * (h + 1) * t) for h in range(harm))


def _akordy(tonika, molowa, sek=40, seed=0, SR=SR):
    """Kadencja i-iv-v-i (moll) albo I-IV-V-I (dur) z basem, stopa 128 BPM i szumem."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(sek * SR)) / SR
    y = np.zeros_like(t)
    akordy = [(0, 3, 7), (5, 8, 12), (7, 10, 14), (0, 3, 7)] if molowa else \
             [(0, 4, 7), (5, 9, 12), (7, 11, 14), (0, 4, 7)]
    seg = sek / 8
    base = 130.81 * 2 ** (tonika / 12)
    for k in range(8):
        a = akordy[k % 4]
        m = (t >= k * seg) & (t < (k + 1) * seg)
        for st in a:
            y[m] += _ton(base * 2 ** (st / 12), t[m])
        y[m] += 0.8 * _ton(base / 2 * 2 ** (a[0] / 12), t[m], 3)
    for b in np.arange(0, sek, 60 / 128):
        i0 = int(b * SR)
        n = min(int(0.2 * SR), len(y) - i0)
        tt = np.arange(n) / SR
        y[i0:i0 + n] += 3 * np.sin(2 * np.pi * (50 + 80 * np.exp(-tt * 30)) * tt) * np.exp(-tt * 8)
    y += 0.3 * rng.standard_normal(len(y))
    return (y / np.abs(y).max()).astype(np.float32)


def test_key_detection_on_synthetic_cadences():
    trafione = 0
    for tonika in range(12):
        for molowa in (False, True):
            w = tonacja.wykryj(_akordy(tonika, molowa, seed=tonika), SR)
            trafione += (w[0], w[1]) == (tonika, molowa)
    # 24 tonacje; dopuszczamy kilka pomylek o kwinte (typowe dla tej metody)
    assert trafione >= 20, trafione


def test_key_names_classic_and_camelot():
    assert tonacja.nazwa(9, True) == "Am" and tonacja.nazwa(9, True, "camelot") == "8A"
    assert tonacja.nazwa(0, False) == "C" and tonacja.nazwa(0, False, "camelot") == "8B"
    assert tonacja.nazwa(8, True) == "Abm" and tonacja.nazwa(8, True, "camelot") == "1A"
    assert tonacja.nazwa(6, False) == "F#" and tonacja.nazwa(6, False, "camelot") == "2B"
    assert tonacja.nazwa(1, True, "camelot") == "12A"
    # kazda nazwa Camelot wystepuje dokladnie raz
    cam = {tonacja.nazwa(t, m, "camelot") for t in range(12) for m in (False, True)}
    assert len(cam) == 24


def test_too_short_or_silent_gives_none():
    assert tonacja.wykryj(np.zeros(SR * 30, dtype=np.float32), SR) is None
    assert tonacja.wykryj(np.zeros(1000, dtype=np.float32), SR) is None


@pytest.mark.skipif(shutil.which(os.environ.get("CDJ_FFMPEG", "ffmpeg")) is None, reason="ffmpeg not available")
def test_key_is_written_to_the_database_and_kept_when_appending(tmp_path):
    import pdb_writer as pw
    import silnik

    def wav(path, y):
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(44100)  # CDJ-2000 (i aplikacja) przyjmuje WAV 44,1 lub 48 kHz
            w.writeframes((y * 30000).astype("<i2").tobytes())

    a, b, usb = tmp_path / "a", tmp_path / "b", tmp_path / "usb"
    for d in (a, b, usb):
        d.mkdir()
    wav(a / "am.wav", _akordy(9, True, sek=30, SR=44100))    # A-moll
    wav(b / "g.wav", _akordy(7, False, sek=30, SR=44100))    # G-dur

    def run(folder, name, **opcje):
        ev = []
        silnik.przetworz(dict({"folder_mp3": str(folder), "pendrive": str(usb), "playlista": name,
                               "kopiuj_mp3": True, "zastap_pioneer": False, "nie_usypiaj": False}, **opcje), ev.append)
        assert any(e.get("typ") == "koniec" and e.get("ok") for e in ev)
        return ev

    def baza():
        lib = pw.czytaj_biblioteke((usb / "PIONEER" / "rekordbox" / "export.pdb").read_bytes())
        klucze = dict(lib["klucze"])
        return {s["title"] or s["filename"]: klucze.get(f["key"]) for f, s in lib["utwory"]}, lib

    ev = run(a, "Am", tonacja=True, notacja="klasyczna")
    utw = [e for e in ev if e.get("typ") == "utwor"][0]
    assert utw["tonacja"]["klasyczna"] == "Am" and utw["tonacja"]["camelot"] == "8A"
    tony, _ = baza()
    assert list(tony.values()) == ["Am"]

    # dopisanie drugiego folderu w notacji Camelot: stary utwor zachowuje swoja tonacje
    run(b, "G", tonacja=True, notacja="camelot")
    tony, lib = baza()
    assert sorted(tony.values()) == ["9B", "Am"], tony
    assert len({kid for kid, _ in lib["klucze"]}) == len(lib["klucze"])  # bez powtorzonych id

    # bez opcji tonacji: ponowny zapis (z pamieci podrecznej) nie kasuje zapisanych tonacji
    run(a, "Am", tonacja=False)
    tony, _ = baza()
    assert sorted(tony.values()) == ["9B", "Am"], tony

    # z pamieci podrecznej tonacja wraca bez ponownej analizy
    ev = run(a, "Am", tonacja=True, notacja="camelot")
    utw = [e for e in ev if e.get("typ") == "utwor"][0]
    assert utw.get("z_pamieci") and utw["tonacja"]["camelot"] == "8A"
    tony, _ = baza()
    assert sorted(tony.values()) == ["8A", "9B"], tony


def test_key_table_row_layout_matches_rekordbox():
    import pdb_writer as pw
    # wzorzec z eksportu rekordboxa: id, id2 (= id), krotki napis ASCII
    assert pw.key_row(1, "Abm") == bytes.fromhex("0100000001000000") + b"\x09Abm"
    assert len(pw.key_row(2, "Fm")) % 4 == 0
    assert struct.unpack("<II", pw.key_row(7, "8A")[:8]) == (7, 7)


def test_key_from_tags_in_all_notations():
    for tekst, oczek in [("Am", (9, True)), ("F#m", (6, True)), ("Gbm", (6, True)), ("Ebm", (3, True)),
                         ("B", (11, False)), ("A minor", (9, True)), ("Dbmaj", (1, False)),
                         ("8A", (9, True)), ("12A", (1, True)), ("2B", (6, False)), ("1d", (0, False)),
                         ("1m", (9, True))]:
        assert tonacja.z_tagu(tekst) == oczek, tekst
    for zle in ("", None, "brak", "13A", "Xm", "Cm maj"):
        assert tonacja.z_tagu(zle) is None, zle


def _ffmpeg_mp3():
    ff = shutil.which(os.environ.get("CDJ_FFMPEG", "ffmpeg"))
    if not ff:
        return None
    import subprocess
    r = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True, text=True)
    return ff if "libmp3lame" in r.stdout else None


@pytest.mark.skipif(_ffmpeg_mp3() is None, reason="ffmpeg with an MP3 encoder not available")
def test_key_from_file_tag_wins_over_analysis_unless_disabled(tmp_path):
    """Plik z tagiem TKEY: na pendrive trafia tonacja z tagu (jak w sklepie i rekordboxie);
    po wylaczeniu opcji wlasna analiza."""
    import subprocess
    import pdb_writer as pw
    import silnik
    ff = _ffmpeg_mp3()
    src, usb = tmp_path / "src", tmp_path / "usb"
    src.mkdir()
    usb.mkdir()
    raw = tmp_path / "am.wav"
    with wave.open(str(raw), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes((_akordy(9, True, sek=30, SR=44100) * 30000).astype("<i2").tobytes())
    # brzmi jak A-moll, ale tag mowi Dm (jak gdyby sklep podal inna tonacje)
    subprocess.run([ff, "-v", "error", "-y", "-i", str(raw), "-c:a", "libmp3lame", "-b:a", "192k",
                    "-id3v2_version", "3", "-metadata", "TKEY=Dm", "-metadata", "title=Tagged",
                    str(src / "tagged.mp3")], check=True)

    def run(**opcje):
        for p in usb.iterdir():
            shutil.rmtree(p) if p.is_dir() else p.unlink()
        ev = []
        silnik.przetworz(dict({"folder_mp3": str(src), "pendrive": str(usb), "playlista": "T", "kopiuj_mp3": True,
                               "zastap_pioneer": False, "nie_usypiaj": False, "tonacja": True}, **opcje), ev.append)
        utw = [e for e in ev if e.get("typ") == "utwor"][0]
        lib = pw.czytaj_biblioteke((usb / "PIONEER" / "rekordbox" / "export.pdb").read_bytes())
        klucze = dict(lib["klucze"])
        return utw["tonacja"], klucze[lib["utwory"][0][0]["key"]]

    opis, w_bazie = run()
    assert opis["zrodlo"] == "tag" and opis["klasyczna"] == "Dm" and w_bazie == "Dm"
    opis, w_bazie = run(tonacja_z_tagu=False)
    assert opis["zrodlo"] == "analiza" and opis["klasyczna"] == "Am" and w_bazie == "Am"


@pytest.mark.skipif(_ffmpeg_mp3() is None, reason="ffmpeg with an MP3 encoder not available")
def test_tags_with_special_characters_are_read_exactly(tmp_path):
    """ffmetadata poprzedza # = ; \\ ukosnikiem; tytul i tonacja maja trafic na pendrive bez niego."""
    import subprocess
    import silnik
    raw, mp3 = tmp_path / "t.wav", tmp_path / "t.mp3"
    with wave.open(str(raw), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(np.zeros(44100 * 2, dtype="<i2").tobytes())
    subprocess.run([_ffmpeg_mp3(), "-v", "error", "-y", "-i", str(raw), "-c:a", "libmp3lame", "-id3v2_version", "3",
                    "-metadata", "title=Track #1; A=B \\ C", "-metadata", "TKEY=F#m", str(mp3)], check=True)
    m = silnik.metadane(str(mp3))
    assert m["tytul"] == "Track #1; A=B \\ C"
    assert m["tonacja_tag"] == [6, True]
