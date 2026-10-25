#!/usr/bin/env python3
"""Proces roboczy: uruchamiany przez aplikację, wypisuje zdarzenia jako linie '@@{json}'."""

import json
import os
import sys

sys.dont_write_bytecode = True

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
site_packages = os.environ.get("CDJ_SITE_PACKAGES")
if site_packages:
    sys.path.insert(0, site_packages)


def emit(ev):
    """Wyślij event do parent'a (app.py)."""
    print("@@" + json.dumps(ev, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    try:
        import silnik
        from exceptions import CDJError
        
        if len(sys.argv) < 2:
            emit({"typ": "blad", "tekst": "Missing arguments", "kod": "INVALID_ARGS"})
            sys.exit(1)
        
        args = json.loads(sys.argv[1])
        silnik.przetworz(args, emit)
    
    except CDJError as e:
        emit({"typ": "blad", "tekst": e.user_message, "kod": e.code})
        sys.exit(1)
    
    except Exception as e:
        emit({"typ": "blad", "tekst": f"Nieoczekiwany błąd: {str(e) or e.__class__.__name__}", "kod": "UNKNOWN"})
        sys.exit(1)
