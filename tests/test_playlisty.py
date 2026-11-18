"""Inteligentne playlisty, podobne utwory i filtr kolekcji (reguly na danych z analizy)."""
import playlisty as pl


def _u(bpm, tonika=None, molowa=False, gatunek="", artysta="", komentarz="", data=0):
    ton = [tonika, molowa] if tonika is not None else None
    return {"bpm": bpm, "tonacja": ton, "pend": "/x/%s-%s.mp3" % (artysta, bpm),
            "meta": {"gatunek": gatunek, "artysta": artysta, "komentarz": komentarz, "data_pliku": data}}


def test_sasiedzi_camelot():
    # 8A (A-minor): sasiedzi 7A, 9A i relatywna 8B
    assert pl.sasiedzi((8, 0)) == {(8, 0), (7, 0), (9, 0), (8, 1)}
    # wrap: 1A -> 12A i 2A
    assert pl.sasiedzi((1, 0)) == {(1, 0), (12, 0), (2, 0), (1, 1)}


def test_regula_bpm_i_tonacja_dokladna():
    a = _u(128, tonika=9, molowa=True)           # Am = 8A
    r = {"bpm_min": 120, "bpm_max": 130, "tonacja": (8, 0)}
    assert pl.pasuje(a, r)
    assert not pl.pasuje(_u(140, tonika=9, molowa=True), r)
    assert not pl.pasuje(_u(128, tonika=0), r)    # C = 8B, nie 8A


def test_regula_tonacja_sasiednia():
    r = {"tonacja": (8, 0), "sasiednie": True}
    assert pl.pasuje(_u(128, tonika=0), r)        # C = 8B (relatywna)
    assert pl.pasuje(_u(128, tonika=2, molowa=True), r)  # Dm = 7A, sasiad 8A
    assert not pl.pasuje(_u(128, tonika=6), r)    # F# = 2B, daleko


def test_regula_bez_tonacji_odrzuca_utwor_bez_klucza():
    assert not pl.pasuje(_u(128), {"tonacja": (8, 0)})


def test_regula_tekst_bez_wielkosci_liter():
    u = _u(128, gatunek="Techno Dark", artysta="Ame", komentarz="klub, peak")
    assert pl.pasuje(u, {"gatunek": "techno"})
    assert pl.pasuje(u, {"artysta": "AME", "komentarz": "PEAK"})
    assert not pl.pasuje(u, {"gatunek": "house"})


def test_pusta_regula_pasuje_do_wszystkiego():
    assert pl.pasuje(_u(90), {})


def test_podobne_bpm_i_tonacja():
    seed = _u(128.0, tonika=9, molowa=True)     # 8A
    assert pl.podobne(seed, _u(130.0, tonika=0), 3.0)      # 130 = 1,6% od 128, C = 8B (relatywna)
    assert not pl.podobne(seed, _u(132.0, tonika=0), 3.0)  # 132 = 3,1% od 128, za daleko
    assert not pl.podobne(seed, _u(128.0, tonika=2), 3.0)  # D = 10B, nie sasiad 8A
    assert not pl.podobne(seed, seed, 3.0)                 # sam siebie nie dodajemy


def test_filtr_kolekcji():
    utwory = [_u(120, gatunek="House", data=100), _u(140, gatunek="Techno", data=200)]
    assert pl.filtr_kolekcji(utwory, None) == utwory
    assert [u["bpm"] for u in pl.filtr_kolekcji(utwory, {"gatunek": "techno"})] == [140]
    assert [u["bpm"] for u in pl.filtr_kolekcji(utwory, {"data_od": 150})] == [140]
    assert [u["bpm"] for u in pl.filtr_kolekcji(utwory, {"bpm_max": 130})] == [120]
