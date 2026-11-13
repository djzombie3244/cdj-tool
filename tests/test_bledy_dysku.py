"""Bledy wejscia/wyjscia nosnika: jasny komunikat zamiast "Nieoczekiwany blad"; postep kopiowania w tle."""
import errno
import os
import time

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")

import silnik  # noqa: E402


def test_io_error_on_stick_gets_clear_message(tmp_path):
    usb, muz = tmp_path / "usb", tmp_path / "muz"
    usb.mkdir(); muz.mkdir()
    a = {"pendrive": str(usb), "folder_mp3": str(muz)}
    e = silnik.blad_nosnika(OSError(errno.EIO, "Input/output error", str(usb / "PIONEER" / "x")), a)
    assert e.code == "PENDRIVE_IO" and "usb" in e.user_message
    e = silnik.blad_nosnika(OSError(errno.EFAULT, "Bad address", str(usb / "PIONEER" / "USBANLZ")), a)
    assert e.code == "PENDRIVE_IO"
    e = silnik.blad_nosnika(OSError(errno.EIO, "Input/output error", str(muz / "a.mp3")), a)
    assert e.code == "DYSK_ZRODLO"
    assert silnik.blad_nosnika(OSError(errno.ENOSPC, "No space", str(usb / "a")), a).code == "PENDRIVE_PELNY"
    assert silnik.blad_nosnika(OSError(errno.EACCES, "Permission denied", str(usb / "a")), a) is None
    assert silnik.blad_nosnika(ValueError("x"), a) is None


def test_vanished_stick_is_reported_as_disconnected(tmp_path):
    muz = tmp_path / "muz"
    muz.mkdir()
    a = {"pendrive": str(tmp_path / "zniknal"), "folder_mp3": str(muz)}
    e = silnik.blad_nosnika(OSError(errno.ENOENT, "No such file", str(tmp_path / "zniknal" / "PIONEER")), a)
    assert e.code == "DYSK_ODLACZONY"


def test_background_copy_reports_byte_progress(tmp_path):
    src = tmp_path / "a.bin"
    src.write_bytes(os.urandom(3 * 1024 * 1024))
    usb = tmp_path / "usb"
    usb.mkdir()
    k = silnik.KopiarkaTla(str(usb))
    k.dodaj({"src": str(src), "pend": "/muz/a.bin", "meta": {"rozmiar": src.stat().st_size}})
    widziane = []
    k.zakoncz(postep=lambda z, r: widziane.append((z, r)))
    if not widziane:  # watek skonczyl przed pierwszym odczytem postepu
        widziane.append((k.zapisane, k.w_kolejce))
    assert widziane[-1] == (src.stat().st_size, src.stat().st_size)
    assert (usb / "muz" / "a.bin").read_bytes() == src.read_bytes()
