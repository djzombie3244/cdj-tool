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
