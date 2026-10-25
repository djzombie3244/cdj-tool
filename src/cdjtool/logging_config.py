#!/usr/bin/env python3
"""Strukturowane logowanie dla UI."""

import json
import sys
from enum import Enum
from datetime import datetime


class LogLevel(Enum):
    DEBUG = 0
    INFO = 1
    WARN = 2
    ERROR = 3


def log(level: LogLevel, message: str, context: dict = None):
    """Wyślij ustrukturyzowaną linię logowania."""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "level": level.name,
        "message": message,
        "context": context or {}
    }
    print(json.dumps(entry, ensure_ascii=False), file=sys.stderr, flush=True)


# Convenience functions
def info(msg, ctx=None):
    log(LogLevel.INFO, msg, ctx)


def warn(msg, ctx=None):
    log(LogLevel.WARN, msg, ctx)


def error(msg, ctx=None):
    log(LogLevel.ERROR, msg, ctx)


def debug(msg, ctx=None):
    log(LogLevel.DEBUG, msg, ctx)
