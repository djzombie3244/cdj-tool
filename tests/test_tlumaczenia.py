"""Tlumaczenia interfejsu: kazdy klucz z I18N ma wpis we wszystkich jezykach, a kazdy data-i18n w HTML ma tekst."""
import json
import os
import re
import shutil
import subprocess

import pytest

HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "cdjtool", "ui.html")


def _i18n():
    if shutil.which("node") is None:
        pytest.skip("node not available")
    s = open(HTML, encoding="utf-8").read()
    m = re.search(r"const I18N = (\{.*?\n\});", s, re.S)
    assert m, "nie znaleziono I18N w ui.html"
    js = "const I18N = " + m.group(1) + ";\nconsole.log(JSON.stringify(I18N));"
    wynik = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True)
    return json.loads(wynik.stdout)


def test_wszystkie_jezyki_maja_te_same_klucze():
    i18n = _i18n()
    assert set(i18n) == {"pl", "en", "de", "fr", "es", "it"}, set(i18n)
    pl = set(i18n["pl"])
    for lang, slowa in i18n.items():
        brak = pl - set(slowa)
        nadmiar = set(slowa) - pl
        assert not brak and not nadmiar, (lang, sorted(brak), sorted(nadmiar))


def test_kazdy_data_i18n_w_html_ma_tekst():
    i18n = _i18n()
    s = open(HTML, encoding="utf-8").read()
    klucze = set(re.findall(r'data-i18n="([^"]+)"', s))
    # HTML-owe klucze moga siedziec w czesci wspolnej kodu; sprawdzamy, ze znane sa we wszystkich jezykach
    brak = sorted(k for k in klucze if any(k not in slowa for slowa in i18n.values()))
    assert not brak, brak


def test_nowe_teksty_sa_niepuste():
    i18n = _i18n()
    for lang, slowa in i18n.items():
        for klucz in ("oSortPl", "sortNone", "sortBpm", "sortKey", "trackLimit"):
            assert klucz in slowa and slowa[klucz].strip(), (lang, klucz)
        assert "{n}" in slowa["trackLimit"], lang
