"""End-to-end test: synthetic click track -> fake USB stick -> parse the result.
Needs numpy, scipy and ffmpeg; skipped otherwise."""
import os
import shutil
import subprocess
import wave

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")
if shutil.which(os.environ.get("CDJ_FFMPEG", "ffmpeg")) is None:
    pytest.skip("ffmpeg not available", allow_module_level=True)

import pdb_writer as pw  # noqa: E402
import silnik  # noqa: E402


def _click_track(path, bpm=128.0, seconds=40, sr=44100):
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


def _run(folder, usb, name, replace=False):
    events = []
    silnik.przetworz({"folder_mp3": folder, "pendrive": usb, "playlista": name, "kopiuj_mp3": True,
                      "zastap_pioneer": replace}, events.append)
    return events


def test_end_to_end_and_merge(tmp_path):
    a, b, usb = tmp_path / "a", tmp_path / "b", tmp_path / "usb"
    for d in (a, b, usb):
        d.mkdir()
    _click_track(str(a / "one.wav"), 128.0)
    _click_track(str(b / "two.wav"), 140.0)

    ev = _run(str(a), str(usb), "First")
    assert any(e.get("typ") == "koniec" and e.get("ok") for e in ev)
    track = [e for e in ev if e.get("typ") == "utwor"][0]
    assert abs(track["bpm"] - 128.0) < 0.5

    pdb = usb / "PIONEER" / "rekordbox" / "export.pdb"
    lib = pw.czytaj_biblioteke(pdb.read_bytes())
    assert len(lib["utwory"]) == 1

    # a second run with another folder must ADD to the library, not replace it
    _run(str(b), str(usb), "Second")
    lib = pw.czytaj_biblioteke(pdb.read_bytes())
    assert len(lib["utwory"]) == 2
    assert {n for _, _, _, _, n in lib["drzewo"]} == {"First", "Second"}
    for f, s in lib["utwory"]:
        anlz = usb / s["analyze_path"].lstrip("/")
        assert anlz.exists() and anlz.with_suffix(".EXT").exists()

    # explicit replace starts over
    _run(str(b), str(usb), "Only", replace=True)
    lib = pw.czytaj_biblioteke(pdb.read_bytes())
    assert len(lib["utwory"]) == 1


def test_analysis_path_collisions_are_resolved(tmp_path, monkeypatch):
    a, usb = tmp_path / "a", tmp_path / "usb"
    a.mkdir(); usb.mkdir()
    _click_track(str(a / "one.wav"), 128.0, seconds=20)
    _click_track(str(a / "two.wav"), 130.0, seconds=20)
    real = silnik.sciezka_analizy
    monkeypatch.setattr(silnik, "sciezka_analizy", lambda p, k=0: real("/same/path", k))
    _run(str(a), str(usb), "Collide")
    lib = pw.czytaj_biblioteke((usb / "PIONEER" / "rekordbox" / "export.pdb").read_bytes())
    paths = [s["analyze_path"] for _, s in lib["utwory"]]
    assert len(paths) == 2 and len(set(paths)) == 2
    assert real("/x", 0) != real("/x", 1)


def test_folder_without_audio_raises_its_own_error(tmp_path):
    """A folder with no supported audio must raise NoAudioFilesError (code NO_AUDIO_FILES),
    so the folder queue in the UI can skip it instead of stopping."""
    from exceptions import NoAudioFilesError
    empty, usb = tmp_path / "Płyty skrecz sample", tmp_path / "usb"
    empty.mkdir()
    usb.mkdir()
    (empty / "notes.txt").write_text("not audio")
    (empty / "loop.flac").write_bytes(b"fLaC")
    try:
        _run(str(empty), str(usb), "Empty")
    except NoAudioFilesError as e:
        err = e
    else:
        pytest.fail("NoAudioFilesError was not raised")
    assert err.code == "NO_AUDIO_FILES"
    assert "Płyty skrecz sample" in err.user_message
    assert "FLAC" in err.user_message  # FLAC is off, so the hint is shown
    assert not (usb / "PIONEER").exists()  # nothing was written


def _pliki_na_pendrive(usb):
    out = {}
    for dp, _, fn in os.walk(usb):
        for n in fn:
            p = os.path.join(dp, n)
            out[os.path.relpath(p, usb)] = p
    return out


def test_background_copy_gives_the_same_stick(tmp_path):
    """Copying tracks in the background while the analysis runs must give the same files on the stick
    as copying after the analysis, and must not leave temporary files behind."""
    src = tmp_path / "src"
    src.mkdir()
    _click_track(str(src / "one.wav"), 128.0, seconds=20)
    _click_track(str(src / "two.wav"), 140.0, seconds=20)
    stick = {}
    for tlo in (True, False):
        usb = tmp_path / ("usb_tlo" if tlo else "usb_po")
        usb.mkdir()
        events = []
        silnik.przetworz({"folder_mp3": str(src), "pendrive": str(usb), "playlista": "T", "kopiuj_mp3": True,
                          "zastap_pioneer": False, "kopiuj_w_tle": tlo, "nie_usypiaj": False}, events.append)
        assert any(e.get("typ") == "koniec" and e.get("ok") for e in events)
        logi = [e["tekst"] for e in events if e.get("typ") == "log"]
        assert any(t.startswith("W tle skopiowano 2 plików") for t in logi) == tlo
        pliki = _pliki_na_pendrive(usb)
        assert not [p for p in pliki if p.endswith(".cdjpart")]
        audio = {k: open(v, "rb").read() for k, v in pliki.items() if k.endswith(".wav")}
        assert len(audio) == 2
        assert sorted(audio.values()) == sorted([(src / "one.wav").read_bytes(), (src / "two.wav").read_bytes()])
        lib = pw.czytaj_biblioteke((usb / "PIONEER" / "rekordbox" / "export.pdb").read_bytes())
        assert len(lib["utwory"]) == 2
        stick[tlo] = sorted(audio)
    assert stick[True] == stick[False]


def test_background_copier_skips_files_already_there_and_stops_cleanly(tmp_path):
    src = tmp_path / "a.wav"
    src.write_bytes(b"x" * 1000)
    root = tmp_path / "usb"
    (root / "Tracks").mkdir(parents=True)
    gotowy = root / "Tracks" / "a.wav"
    gotowy.write_bytes(b"y" * 1000)  # same size: treated as already copied
    u = {"pend": "/Tracks/a.wav", "src": str(src), "meta": {"rozmiar": 1000}}
    k = silnik.KopiarkaTla(str(root))
    k.dodaj(u)
    k.zakoncz()
    assert k.plikow == 0 and k.blad is None
    assert gotowy.read_bytes() == b"y" * 1000

    u2 = {"pend": "/Tracks/b.wav", "src": str(src), "meta": {"rozmiar": 1000}}
    k = silnik.KopiarkaTla(str(root))
    k.zakoncz(przerwij=True)  # stopped before any work
    k.dodaj(u2)
    assert not (root / "Tracks" / "b.wav").exists()
    assert not list(root.rglob("*.cdjpart"))

    k = silnik.KopiarkaTla(str(root))
    k.dodaj(u2)
    k.zakoncz()
    assert k.plikow == 1 and (root / "Tracks" / "b.wav").read_bytes() == b"x" * 1000


def test_subfolders_become_playlist_folders(tmp_path):
    """Uklad folderow zrodla -> drzewo playlist: folder z podfolderami = folder playlist (jego wlasne pliki
    w playliscie o tej samej nazwie), folder bez podfolderow = playlista. Ponowny zapis niczego nie dubluje."""
    root, usb = tmp_path / "Muzyka", tmp_path / "usb"
    for d in (root / "House" / "Vocal", root / "Techno", usb):
        d.mkdir(parents=True)
    _click_track(str(root / "luzny.wav"), 120.0, seconds=12)
    _click_track(str(root / "House" / "h1.wav"), 124.0, seconds=12)
    _click_track(str(root / "House" / "Vocal" / "v1.wav"), 126.0, seconds=12)
    _click_track(str(root / "Techno" / "t1.wav"), 132.0, seconds=12)

    def drzewo():
        lib = pw.czytaj_biblioteke((usb / "PIONEER" / "rekordbox" / "export.pdb").read_bytes())
        tytul = {f["id"]: s["title"] or s["filename"] for f, s in lib["utwory"]}
        wezly = {pid: (par, fold, n) for par, srt, pid, fold, n in lib["drzewo"]}

        def sciezka(pid):
            par, fold, n = wezly[pid]
            return (sciezka(par) if par else ()) + ((n + "/") if fold else n,)

        out = {}
        for e, tid, pid in lib["wpisy"]:
            out.setdefault("/".join(sciezka(pid)), []).append(tytul[tid])
        return {k: sorted(v) for k, v in out.items()}, len(lib["drzewo"])

    for _ in range(2):  # drugi raz: dopisanie bez dublowania wezlow i wpisow
        silnik.przetworz({"folder_mp3": str(root), "pendrive": str(usb), "playlista": "Muzyka", "kopiuj_mp3": True,
                          "zastap_pioneer": False, "nie_usypiaj": False, "nazwa_folderu": "Muzyka"}, lambda e: None)
        tree, wezlow = drzewo()
        assert tree == {
            "Muzyka//Muzyka": ["luzny"],
            "Muzyka//House//House": ["h1"],
            "Muzyka//House//Vocal": ["v1"],
            "Muzyka//Techno": ["t1"],
        }, tree
        assert wezlow == 6  # foldery: Muzyka, House; playlisty: Muzyka, House, Vocal, Techno

    # opcja wylaczona: jedna playlista z calym folderem (dawne zachowanie)
    usb2 = tmp_path / "usb2"
    usb2.mkdir()
    silnik.przetworz({"folder_mp3": str(root), "pendrive": str(usb2), "playlista": "Wszystko", "kopiuj_mp3": True,
                      "zastap_pioneer": False, "nie_usypiaj": False, "playlisty_z_folderow": False}, lambda e: None)
    lib = pw.czytaj_biblioteke((usb2 / "PIONEER" / "rekordbox" / "export.pdb").read_bytes())
    assert [(fold, n) for _, _, _, fold, n in lib["drzewo"]] == [(False, "Wszystko")]
    assert len(lib["wpisy"]) == 4
