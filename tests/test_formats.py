"""FLAC option (for CDJ-2000 with the rafttone mod) and regression tests for the analysis speed-ups.
Needs numpy, scipy and ffmpeg (with FLAC support); skipped otherwise."""
import os
import shutil
import subprocess

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")
FFMPEG = os.environ.get("CDJ_FFMPEG", "ffmpeg")
if shutil.which(FFMPEG) is None:
    pytest.skip("ffmpeg not available", allow_module_level=True)

import gen_dat as g  # noqa: E402
import pdb_writer as pw  # noqa: E402
import silnik  # noqa: E402
from exceptions import InvalidMP3Error  # noqa: E402
from test_engine import _click_track  # noqa: E402


def _to_flac(wav, flac, channels=2, rate=44100, bits=16):
    fmt = ["-sample_fmt", "s32", "-bits_per_raw_sample", "24"] if bits == 24 else ["-sample_fmt", "s16"]
    r = subprocess.run([FFMPEG, "-v", "error", "-y", "-i", wav, "-ac", str(channels), "-ar", str(rate),
                        "-c:a", "flac"] + fmt + [flac], capture_output=True)
    if r.returncode != 0:
        pytest.skip("this ffmpeg cannot encode FLAC")


@pytest.fixture(autouse=True)
def _flac_off():
    silnik.FLAC["on"] = False
    yield
    silnik.FLAC["on"] = False
    os.environ.pop("CDJ_FLAC", None)


def test_flac_is_rejected_unless_enabled(tmp_path):
    _click_track(str(tmp_path / "a.wav"), 128.0, seconds=10)
    _to_flac(str(tmp_path / "a.wav"), str(tmp_path / "a.flac"))
    assert silnik.rozszerzenia() == silnik.ROZSZERZENIA_AUDIO
    with pytest.raises(InvalidMP3Error):
        silnik.metadane(str(tmp_path / "a.flac"))
    silnik.FLAC["on"] = True
    assert ".flac" in silnik.rozszerzenia()
    meta = silnik.metadane(str(tmp_path / "a.flac"))
    assert meta["typ_pliku"] == 5 and meta["bity"] == 16 and meta["sr"] == 44100


@pytest.mark.parametrize("channels,rate,bits,ok", [
    (2, 48000, 24, True),    # allowed by the mod: stereo, 16/24 bit, 44.1/48 kHz
    (1, 44100, 16, False),   # mono
    (2, 96000, 24, False),   # sample rate too high
])
def test_flac_mod_limits(tmp_path, channels, rate, bits, ok):
    _click_track(str(tmp_path / "a.wav"), 128.0, seconds=10)
    _to_flac(str(tmp_path / "a.wav"), str(tmp_path / "a.flac"), channels, rate, bits)
    silnik.FLAC["on"] = True
    if ok:
        assert silnik.metadane(str(tmp_path / "a.flac"))["bity"] == bits
    else:
        with pytest.raises(InvalidMP3Error):
            silnik.metadane(str(tmp_path / "a.flac"))


def test_flac_end_to_end(tmp_path):
    a, usb = tmp_path / "a", tmp_path / "usb"
    a.mkdir(); usb.mkdir()
    _click_track(str(tmp_path / "t.wav"), 128.0, seconds=30)
    _to_flac(str(tmp_path / "t.wav"), str(a / "one.flac"))
    events = []
    silnik.przetworz({"folder_mp3": str(a), "pendrive": str(usb), "playlista": "F", "kopiuj_mp3": True,
                      "zastap_pioneer": True, "flac": True}, events.append)
    assert any(e.get("typ") == "koniec" and e.get("ok") for e in events)
    track = [e for e in events if e.get("typ") == "utwor"][0]
    assert abs(track["bpm"] - 128.0) < 0.5
    lib = pw.czytaj_biblioteke((usb / "PIONEER" / "rekordbox" / "export.pdb").read_bytes())
    assert len(lib["utwory"]) == 1
    assert (usb / "Contents").exists() or any(p.suffix == ".flac" for p in usb.rglob("*.flac"))


def test_folder_scan_ignores_flac_when_disabled(tmp_path):
    (tmp_path / "x.flac").write_bytes(b"fLaC")
    (tmp_path / "y.mp3").write_bytes(b"x")
    assert silnik.pliki_mp3(str(tmp_path)) == ["y.mp3"]
    silnik.FLAC["on"] = True
    assert silnik.pliki_mp3(str(tmp_path)) == ["x.flac", "y.mp3"]


def _reference_profile(on1, bpm):
    """The original (slow) implementation of gen_dat._zlozony_profil, kept as a reference."""
    krok_ms = g._HOP_MS * 1000.0 / g.SR
    okres = 60000.0 / bpm
    nb = int(round(okres * 2))
    ph = (((np.arange(len(on1)) + 0.5) * krok_ms) % okres) / okres
    h = np.bincount((ph * nb).astype(int) % nb, weights=on1, minlength=nb)
    k = 4
    hs = np.convolve(np.concatenate([h[-k:], h, h[:k]]), np.ones(2 * k + 1) / (2 * k + 1), "same")[k:-k]
    return hs.max() / (hs.mean() + 1e-12), (np.argmax(hs) + 0.5) / nb * okres / 1000.0


def test_speedups_do_not_change_results(tmp_path):
    _click_track(str(tmp_path / "a.wav"), 128.0, seconds=30)
    _click_track(str(tmp_path / "b.wav"), 140.0, seconds=30)
    ya, yb = g.wczytaj(str(tmp_path / "a.wav")), g.wczytaj(str(tmp_path / "b.wav"))
    on_a, on_b = g._obwiednia_kicka(ya), g._obwiednia_kicka(yb)
    # the cached sparse profile must equal the reference exactly, also when the input array changes
    for on in (on_a, on_b, on_a):
        for bpm in (127.5, 128.0, 140.0, 140.005):
            assert g._zlozony_profil(on, bpm) == _reference_profile(on, bpm)
    # two different tracks after each other must not see each other's cached filter output
    ra, rb = g.analizuj(ya), g.analizuj(yb)
    assert abs(ra["bpm"] - 128.0) < 0.5 and abs(rb["bpm"] - 140.0) < 0.5
    assert g.analizuj(ya)["bpm"] == ra["bpm"]


def test_analysis_is_thread_safe(tmp_path):
    """Regression: with fewer than 4 files the tracks are analysed in threads of one process, and the
    per-track caches (sparse profile, 120 Hz filter) must not leak between those threads."""
    import threading

    _click_track(str(tmp_path / "a.wav"), 128.0, seconds=30)
    _click_track(str(tmp_path / "b.wav"), 140.0, seconds=20)
    ya, yb = g.wczytaj(str(tmp_path / "a.wav")), g.wczytaj(str(tmp_path / "b.wav"))
    on_a, on_b = g._obwiednia_kicka(ya), g._obwiednia_kicka(yb)
    assert len(on_a) != len(on_b)
    bpms = (126.0, 128.0, 128.5, 139.0, 140.0, 141.5)
    expected = {(k, b): _reference_profile(on, b) for k, on in (("a", on_a), ("b", on_b)) for b in bpms}
    errors = []

    def hammer(key, on):
        try:
            for _ in range(40):
                for b in bpms:
                    if g._zlozony_profil(on, b) != expected[(key, b)]:
                        errors.append((key, b))
        except Exception as e:  # e.g. "The weights and list don't have the same length"
            errors.append(repr(e))

    threads = [threading.Thread(target=hammer, args=("a", on_a)), threading.Thread(target=hammer, args=("b", on_b))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors[:3]
    # whole analysis in two threads gives the same result as one after the other
    seq = {"a": g.analizuj(ya), "b": g.analizuj(yb)}
    par = {}
    threads = [threading.Thread(target=lambda k=k, y=y: par.__setitem__(k, g.analizuj(y))) for k, y in (("a", ya), ("b", yb))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    for k in ("a", "b"):
        assert par[k]["bpm"] == seq[k]["bpm"]
        assert par[k]["pierwszy"] == seq[k]["pierwszy"]
