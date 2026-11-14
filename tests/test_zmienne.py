"""Zwykly utwor ze zmiana tempa (edit przejsciowy): siatka dynamiczna z beatow sieci; utwor o jednym tempie bez zmian."""
import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

import gen_dat as g  # noqa: E402


def _beaty(czesci):
    """czesci: [(ile_beatow, bpm_od, bpm_do)]: czasy beatow z plynna zmiana tempa wewnatrz czesci."""
    t, out = 0.4, []
    for n, a, b in czesci:
        for i in range(n):
            out.append(t)
            t += 60.0 / (a + (b - a) * i / max(1, n - 1))
    return np.array(out)


def _dzwiek(beaty, dl):
    y = np.zeros(int(dl * g.SR), dtype=np.float32)
    n = int(0.1 * g.SR)
    tt = np.arange(n) / g.SR
    stopa = (np.sin(2 * np.pi * (110 - 400 * tt) * tt) * np.exp(-tt * 25)).astype(np.float32)
    for b in beaty:
        i = int(b * g.SR)
        if i + n < len(y):
            y[i:i + n] += stopa
    return y


def _wynik(beaty, zgodnosc):
    siec = np.round(beaty / 0.02) * 0.02  # siec podaje czasy co 20 ms
    return {"bpm": 104.0, "pierwszy": 0.4, "confidence": 1.0, "beaty_sieci": siec, "takty_sieci": siec[::4],
            "siec_zgodnosc": zgodnosc}


def test_tempo_change_gets_dynamic_grid():
    beaty = _beaty([(104, 104.0, 104.0), (40, 104.0, 128.0), (256, 128.0, 128.0)])
    dl = float(beaty[-1]) + 1.0
    w = g.zmienne_tempo(_dzwiek(beaty, dl), _wynik(beaty, 0.25))
    assert w and w["zmienne_tempo"] and len(w["tempa_serii"]) == 2
    assert abs(w["tempa_serii"][0] - 104.0) < 0.1 and abs(w["tempa_serii"][1] - 128.0) < 0.1
    assert abs(w["bpm"] - 128.0) < 0.1  # tempo do bazy: fragment, ktory trwa dluzej
    c, tempa = np.asarray(w["czasy"]), w["czasy"].tempa
    assert len(tempa) == len(c) == len(w["czasy"].numery) and set(w["czasy"].numery) <= {1, 2, 3, 4}
    prawda = beaty + g.OFFSET_REKORDBOX_S - g.SIEC_OPOZNIENIE_S
    i = np.clip(np.searchsorted(c, prawda), 1, len(c) - 1)
    blad = np.minimum(np.abs(c[i] - prawda), np.abs(c[i - 1] - prawda))
    stale = (np.arange(len(beaty)) < 100) | (np.arange(len(beaty)) > 150)
    assert np.percentile(blad[stale], 95) < 0.012     # rowne fragmenty: linie na beatach
    assert np.percentile(blad[~stale], 95) < 0.04     # przejscie: beaty sieci (dokladnosc 20 ms)
    assert np.all(np.diff(c) > 0.3)
    # tagi siatki z tempem kazdego beatu
    assert len(g.tag_pqtz(w["czasy"], w["bpm"])) > 24 and len(g.tag_pqt2(w["czasy"], w["bpm"])) > 56


def test_single_tempo_and_double_time_stay_constant():
    beaty = _beaty([(300, 128.0, 128.0)])
    y = _dzwiek(beaty, float(beaty[-1]) + 1.0)
    assert g.zmienne_tempo(y, _wynik(beaty, 0.25)) is None      # jedno tempo, slaba zgodnosc
    dwa = _beaty([(150, 128.0, 128.0)])
    pol = np.concatenate([dwa, dwa[-1] + 60.0 / 64.0 * np.arange(1, 80)])  # siec przechodzi na polowe tempa
    assert g.zmienne_tempo(_dzwiek(pol, float(pol[-1]) + 1.0), _wynik(pol, 0.25)) is None
    zm = _beaty([(104, 104.0, 104.0), (40, 104.0, 128.0), (256, 128.0, 128.0)])
    assert g.zmienne_tempo(_dzwiek(zm, float(zm[-1]) + 1.0), _wynik(zm, 0.9)) is None  # siec zgodna ze stala siatka
