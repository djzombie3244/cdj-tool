"""Dlugie utwory (sety, miksy): siatka dynamiczna z odcinkow, tempo kazdego beatu w PQTZ/PQT2, waveformy
liczone kawalkami, limit dlugosci."""
import struct

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

import gen_dat as g  # noqa: E402
import silnik  # noqa: E402
from exceptions import InvalidMP3Error  # noqa: E402


def _set(czesci):
    """Prawdziwe beaty (wsp. rekordboxa) seta: [(od_s, do_s, bpm, faza_s)]."""
    t = []
    for od, do, bpm, faza in czesci:
        okres = 60.0 / bpm
        k = np.arange(int((do - od) / okres) + 2)
        b = od + faza + k * okres
        t.append(b[(b >= od) & (b < do)])
    return np.concatenate(t)


def _odcinki(czesci, dl, odcinek):
    """Wyniki analizuj() dla odcinkow: tempo i faza utworu, ktory w odcinku trwa najdluzej."""
    O, A = g.OFFSET_REKORDBOX_S, g.ATAK_PRZED_WZROSTEM_S
    out = []
    for s0 in np.arange(0, dl, odcinek):
        s1 = min(dl, s0 + odcinek)
        od, do, bpm, faza = max(czesci, key=lambda c: min(c[1], s1) - max(c[0], s0))
        okres = 60.0 / bpm
        pierwszy = (od + faza - s0 - O + A) % okres  # siatka_ms dodaje O - A
        out.append((float(s0), float(s1), {"bpm": bpm, "pierwszy": pierwszy, "takt": 0, "confidence": 1.0}))
    return out


def test_dynamic_grid_follows_tempo_changes_inside_sections():
    czesci = [(0, 410, 124.0, 0.30), (410, 800, 126.0, 0.11), (800, 1300, 128.0, 0.27)]
    prawda = _set(czesci)
    w = g.siatka_dynamiczna(_odcinki(czesci, 1300, g.ODCINEK_S), 1300, beaty=prawda)
    c = np.asarray(w["czasy"])
    assert w["tempa_serii"] == [124.0, 126.0, 128.0]
    blad = np.abs(c[np.clip(np.searchsorted(c, prawda), 1, len(c) - 1)[:, None] + [-1, 0]] - prawda[:, None]).min(1)
    assert np.mean(blad < 0.003) > 0.995                     # zmiany w 410 i 800 s, czyli wewnatrz odcinkow
    assert set(np.round(w["czasy"].tempa, 2)) == {124.0, 126.0, 128.0}
    assert len(w["czasy"].numery) == len(c) and set(w["czasy"].numery) <= {1, 2, 3, 4}


def test_section_with_wrong_phase_is_fixed_from_network_beats():
    czesci = [(0, 900, 130.0, 0.2)]
    prawda = _set(czesci)
    od = _odcinki(czesci, 900, g.ODCINEK_S)
    s0, s1, w = od[2]
    od[2] = (s0, s1, dict(w, pierwszy=(w["pierwszy"] + 0.09) % (60 / 130.0)))  # heurystyka myli sie o 90 ms
    wyn = g.siatka_dynamiczna(od, 900, beaty=prawda)
    assert wyn["serii"] == 1
    c = np.asarray(wyn["czasy"])
    assert np.max(np.abs(c[:len(prawda)] - prawda[:len(c)])) < 0.003


def test_per_beat_tempo_in_pqtz_and_pqt2_checksum():
    w = g.siatka_dynamiczna(_odcinki([(0, 30, 120.0, 0.1), (30, 60, 125.0, 0.05)], 60, 30), 60,
                            beaty=_set([(0, 30, 120.0, 0.1), (30, 60, 125.0, 0.05)]))
    c = w["czasy"]
    pqtz = g.tag_pqtz(c, w["bpm"])
    n = struct.unpack(">I", pqtz[20:24])[0]
    wpisy = [struct.unpack(">HHI", pqtz[24 + 8 * i:32 + 8 * i]) for i in range(n)]
    assert {b for _, b, _ in wpisy} == {12000, 12500}
    pqt2 = g.tag_pqt2(c, w["bpm"])
    hdr = struct.unpack(">11I", pqt2[12:56])
    # jak rekordbox dla siatki dynamicznej: suma z tempem kazdego beatu, w naglowku tempo pierwszego i ostatniego
    assert hdr[8] == sum(nr + b + ms for nr, b, ms in wpisy) & 0xFFFFFFFF
    assert hdr[3] & 0xFFFF == 12000 and hdr[5] & 0xFFFF == 12500


def test_waveform_features_in_chunks_match_whole(monkeypatch):
    rng = np.random.default_rng(3)
    y = (rng.standard_normal(g.SR * 40) * np.repeat(rng.random(400), g.SR // 10)).astype(np.float32)
    dl = len(y) / g.SR
    caly = g._cechy_koloru(y, dl)
    pwv3 = g.tag_pwv3(y, dl)
    monkeypatch.setattr(g, "KAWALEK_S", 7)
    kaw = g._cechy_koloru(y, dl)
    assert np.allclose(kaw, caly, rtol=1e-3, atol=1e-5)
    assert np.mean(np.frombuffer(g.tag_pwv3(y, dl), np.uint8) != np.frombuffer(pwv3, np.uint8)) < 0.001


def test_length_limit_depends_on_option(monkeypatch):
    monkeypatch.setitem(silnik.DLUGIE, "on", False)
    with pytest.raises(InvalidMP3Error):
        silnik._limit_dlugosci("set.mp3", 60 * 60)
    monkeypatch.setitem(silnik.DLUGIE, "on", True)
    silnik._limit_dlugosci("set.mp3", 60 * 60)
    with pytest.raises(InvalidMP3Error):
        silnik._limit_dlugosci("set.mp3", 4 * 60 * 60)
    assert silnik.szacunek_bajtow(2 * 3600) < silnik.szacunek_bajtow(20 * 60) * 3  # set nie liczy 15x calosci
