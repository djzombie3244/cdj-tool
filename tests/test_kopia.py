"""Kopia zapasowa folderu PIONEER: nieczytelny plik analizy nie zatrzymuje zapisu, brak bazy tak."""
import os
import shutil

import silnik
from exceptions import CDJError


def _pioneer(tmp_path):
    p = tmp_path / "usb" / "PIONEER"
    (p / "rekordbox").mkdir(parents=True)
    (p / "rekordbox" / "export.pdb").write_bytes(b"pdb")
    (p / "USBANLZ" / "P001").mkdir(parents=True)
    (p / "USBANLZ" / "P001" / "ANLZ0000.DAT").write_bytes(b"dat")
    (p / "USBANLZ" / "P001" / "ANLZ0000.EXT").write_bytes(b"ext")
    return p


def test_unreadable_analysis_file_is_skipped(tmp_path, monkeypatch):
    p = _pioneer(tmp_path)
    monkeypatch.setattr(os.path, "expanduser", lambda x: str(tmp_path / "home"))
    prawdziwy = shutil.copy2

    def copy2(src, dst, **kw):
        if str(src).endswith(".EXT"):
            raise OSError(5, "Input/output error")
        return prawdziwy(src, dst, **kw)
    monkeypatch.setattr(shutil, "copy2", copy2)
    pominiete = []
    cel = silnik.kopia_zapasowa(str(p), str(tmp_path / "usb"), pominiete)
    assert os.path.isfile(os.path.join(cel, "PIONEER", "rekordbox", "export.pdb"))
    assert [os.path.basename(a) for a, _ in pominiete] == ["ANLZ0000.EXT"]


def test_unreadable_database_stops_with_details(tmp_path, monkeypatch):
    p = _pioneer(tmp_path)
    monkeypatch.setattr(os.path, "expanduser", lambda x: str(tmp_path / "home"))
    prawdziwy = shutil.copy2

    def copy2(src, dst, **kw):
        if str(src).endswith("export.pdb"):
            raise OSError(5, "Input/output error")
        return prawdziwy(src, dst, **kw)
    monkeypatch.setattr(shutil, "copy2", copy2)
    try:
        silnik.kopia_zapasowa(str(p), str(tmp_path / "usb"))
    except CDJError as e:
        assert "Input/output error" in e.user_message and "muzykę" in e.user_message
    else:
        raise AssertionError("brak bledu przy nieczytelnej bazie")


def test_disconnected_drive_stops_with_clear_message(tmp_path, monkeypatch):
    usb = tmp_path / "usb"
    a = usb / "muzyka"
    a.mkdir(parents=True)
    for i in range(3):
        (a / ("t%d.mp3" % i)).write_bytes(b"x")

    def znika(folder, root, nr, rel, *r, **k):
        shutil.rmtree(str(a), ignore_errors=True)  # dysk odlaczony w trakcie
        raise OSError(2, "No such file or directory")
    monkeypatch.setattr(silnik, "_analizuj_jeden", znika)
    zd = []
    try:
        silnik.przetworz({"folder_mp3": str(a), "pendrive": str(usb), "playlista": "T", "kopiuj_mp3": False,
                          "nie_usypiaj": False}, zd.append)
    except CDJError as e:
        assert e.code == "DYSK_ODLACZONY" and "odłączony" in e.user_message
    else:
        raise AssertionError("brak bledu po odlaczeniu dysku")


def test_disk_test_measures_and_warns_about_format(tmp_path, monkeypatch):
    usb, src = tmp_path / "usb", tmp_path / "src"
    usb.mkdir(); src.mkdir()
    (src / "a.mp3").write_bytes(b"x" * 10)
    monkeypatch.setattr(silnik, "system_plikow", lambda root: "exfat")
    w = silnik.test_dysku(str(usb), str(src), ["a.mp3"], rozmiar=1024 * 1024)
    assert w["zapis_mb_s"] > 0 and w["fs"] == "exfat"
    assert any("EXFAT" in o for o in w["ostrzezenia"])
    assert not (usb / ".cdj-tool-test.tmp").exists()
    monkeypatch.setattr(silnik, "system_plikow", lambda root: "msdos")
    assert not [o for o in silnik.test_dysku(str(usb), str(src), ["a.mp3"], rozmiar=1024 * 1024)["ostrzezenia"]
                if "format" in o]


def test_disk_test_stops_on_unreadable_source_or_readonly_stick(tmp_path, monkeypatch):
    usb, src = tmp_path / "usb", tmp_path / "src"
    usb.mkdir(); src.mkdir()
    monkeypatch.setattr(silnik, "system_plikow", lambda root: "msdos")
    try:
        silnik.test_dysku(str(usb), str(src), ["brak.mp3"], rozmiar=1024 * 1024)
    except CDJError as e:
        assert e.code == "DYSK_TEST" and "brak.mp3" in e.user_message
    else:
        raise AssertionError("brak bledu przy nieczytelnym zrodle")
    try:
        silnik.test_dysku(str(usb / "nie-ma"), str(src), ["brak.mp3"], rozmiar=1024 * 1024)
    except CDJError as e:
        assert e.code == "DYSK_TEST" and "zapisu" in e.user_message
    else:
        raise AssertionError("brak bledu przy nieudanym zapisie")
