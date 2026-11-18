"""Kopia utworu z poprawkami: nie wolno zapisac kopii na pendrive'ie ani w jego srodku."""
import os
import shutil
import tempfile

import edytor


def test_kopia_nie_trafia_na_pendrive():
    root = tempfile.mkdtemp()
    try:
        for cel in [root, os.path.join(root, "kopie"), os.path.join(root, "PIONEER")]:
            try:
                edytor.kopia(root, 1, {}, cel)
            except ValueError:
                continue
            raise AssertionError("kopia przyjeta na pendrive: %s" % cel)
    finally:
        shutil.rmtree(root)
