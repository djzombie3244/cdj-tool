#!/usr/bin/env python3
"""Własne wyjątki CDJ Tool z obsługą wielojęzyczną."""


class CDJError(Exception):
    """Bazowa klasa dla wszystkich błędów CDJ Tool."""
    
    def __init__(self, message: str, user_message: str = None, code: str = None):
        self.message = message
        self.user_message = user_message or message  # Dla UI
        self.code = code or self.__class__.__name__
        super().__init__(self.message)


class FFmpegError(CDJError):
    """FFmpeg nie znaleziony lub się nie uruchamia."""
    
    def __init__(self, details=""):
        super().__init__(
            f"FFmpeg error: {details}",
            user_message="Program FFmpeg nie znaleziony lub błąd. Zainstaluj: brew install ffmpeg",
            code="FFMPEG_ERROR"
        )


class InvalidMP3Error(CDJError):
    """Plik MP3 uszkodzony lub nieobsługiwany."""
    
    def __init__(self, filename: str, reason: str):
        super().__init__(
            f"Invalid MP3: {filename} - {reason}",
            user_message=f"Plik '{filename}' nie jest ważnym MP3. {reason}",
            code="INVALID_MP3"
        )


class DiskSpaceError(CDJError):
    """Za mało miejsca na pendrive."""
    
    def __init__(self, needed_mb: float, available_mb: float):
        super().__init__(
            f"Not enough space: need {needed_mb}MB, have {available_mb}MB",
            user_message=f"Za mało miejsca. Potrzeba {needed_mb:.1f} MB, dostępne {available_mb:.1f} MB.",
            code="DISK_SPACE"
        )


class PathTooLongError(CDJError):
    """Ścieżka pliku zbyt długa dla CDJ."""
    
    def __init__(self, path: str, max_len: int = 255):
        super().__init__(
            f"Path too long: {path} ({len(path)} > {max_len})",
            user_message=f"Ścieżka pliku zbyt długa (> {max_len} znaków). Skróć nazwy artysty/albumu.",
            code="PATH_TOO_LONG"
        )


class ProcessTimeoutError(CDJError):
    """Proces (FFmpeg, analiza) się zawieszył."""
    
    def __init__(self, process_name: str, timeout_sec: int):
        super().__init__(
            f"{process_name} timeout after {timeout_sec}s",
            user_message=f"{process_name} zajęło zbyt długo (>{timeout_sec}s). Spróbuj ponownie.",
            code="TIMEOUT"
        )


class BPMConfidenceError(CDJError):
    """BPM wyznaczony z niską pewnością."""
    
    def __init__(self, filename: str, bpm: float, confidence: float):
        super().__init__(
            f"Low confidence BPM: {filename} ({bpm} BPM, confidence={confidence:.3f})",
            user_message=f"Nie mogę wiarygodnie wyznaczyć BPM '{filename}'. Sprawdź utwór ręcznie.",
            code="LOW_BPM_CONFIDENCE"
        )


class NoAudioFilesError(CDJError):
    """Folder nie zawiera obsługiwanych plików audio (kolejka folderów go pomija)."""

    def __init__(self, folder: str, tylko_flac: bool = False):
        import os
        nazwa = os.path.basename(folder.rstrip("/\\")) or folder
        tekst = f"W folderze „{nazwa}” nie ma obsługiwanych plików audio (MP3, AAC, WAV, AIFF)."
        if tylko_flac:
            tekst += " Są w nim pliki FLAC: zaznacz opcję FLAC, jeśli Twój CDJ-2000 ma mod."
        super().__init__(f"No audio files in {folder}", user_message=tekst, code="NO_AUDIO_FILES")
