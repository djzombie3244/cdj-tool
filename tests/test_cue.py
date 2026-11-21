"""Cue z cech pasm: syntetyczny utwor o znanej strukturze (intro, glowna, break, drop, outro) - punkty musza trafic w granice sekcji."""
import pytest

np = pytest.importorskip("numpy")

import cue  # noqa: E402

BPM = 128.0
BEAT = 60.0 / BPM
TAKT = 4 * BEAT
PIERWSZY = 1.0
# (takty, nisko, srednio, wysoko)
SEKCJE = [("intro", 8, 1.0, 0.0, 0.0), ("main", 16, 1.0, 1.0, 1.0), ("break", 8, 0.1, 0.0, 1.0),
          ("drop", 16, 1.0, 1.0, 1.0), ("outro", 8, 1.0, 0.0, 0.0)]


def _utwor():
    n_takty = sum(s[1] for s in SEKCJE)
    dl = PIERWSZY + n_takty * TAKT + 3.0
    cechy = np.zeros((int(dl * cue.OKNA_NA_S), 3))
    t0, granice = PIERWSZY, {}
    for nazwa, nt, lo, mid, hi in SEKCJE:
        a, b = int(t0 * cue.OKNA_NA_S), int((t0 + nt * TAKT) * cue.OKNA_NA_S)
        cechy[a:b] = [0.13 * lo, 0.01 * mid, 0.01 * hi]
        granice[nazwa] = t0
        t0 += nt * TAKT
    czasy = PIERWSZY + np.arange(n_takty * 4) * BEAT
    numery = np.tile([1, 2, 3, 4], n_takty)
    return cechy, czasy, numery, dl, granice


def _ms(lst, nazwa):
    return [t for t, n in lst if n == nazwa]


def test_granice_sekcji():
    cechy, czasy, numery, dl, g = _utwor()
    w = cue.wyznacz(cechy, czasy, numery, dl)
    pam, hot = w["pamiec"], w["hot"]
    for nazwa, klucz in (("Break", "break"), ("Drop", "drop"), ("Outro", "outro"), ("Lift", "main")):
        t = _ms(pam, nazwa)
        assert t, "brak memory cue " + nazwa
        assert abs(t[0] / 1000 - g[klucz]) < 0.05, (nazwa, t[0], g[klucz])
    assert len(hot) == 6 and abs(hot[0][0] / 1000 - PIERWSZY) < 0.05
    assert len(pam) <= 8


def test_mix_in_i_ostrzezenie_nie_dubluja_sie():
    cechy, czasy, numery, dl, g = _utwor()
    pam = cue.wyznacz(cechy, czasy, numery, dl)["pamiec"]
    czasy_pam = sorted(t for t, _ in pam)
    assert all(b - a > 1000 for a, b in zip(czasy_pam, czasy_pam[1:]))
    mix = _ms(pam, "Mix in")
    assert mix and abs(mix[0] / 1000 - (g["drop"] - 16 * TAKT)) < 0.05
