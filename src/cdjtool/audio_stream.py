#!/usr/bin/env python3
"""Streaming audio decoder — unika wyczerpania RAM na dużych plikach."""

import numpy as np
import os
import re
import subprocess
import tempfile
import threading
from typing import Iterator, Optional


class AudioStream:
    """Iterator nad MP3, dekodowanie bez ładowania całości do RAM."""
    
    def __init__(self, filepath: str, sr: int = 22050, chunk_samples: int = 65536):
        """
        Args:
            filepath: Ścieżka do MP3
            sr: Sample rate (Hz)
            chunk_samples: Próbek na chunk (~3 sec przy 22 kHz)
        """
        self.filepath = filepath
        self.sr = sr
        self.chunk_samples = chunk_samples
        self._duration = None
        self._total_samples = None
        self.ffmpeg = os.environ.get("CDJ_FFMPEG", "ffmpeg")
    
    @property
    def duration(self) -> float:
        """Czas trwania w sekundach."""
        if self._duration is None:
            self._compute_duration()
        return self._duration
    
    @property
    def total_samples(self) -> int:
        """Całkowita liczba próbek."""
        if self._total_samples is None:
            self._compute_duration()
        return self._total_samples
    
    def _compute_duration(self):
        """Ekstrahuj czas z FFmpeg."""
        try:
            r = subprocess.run(
                [self.ffmpeg, "-v", "error", "-i", self.filepath],
                capture_output=True, text=True, timeout=10
            )
            m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", r.stderr)
            if m:
                h = int(m.group(1))
                m_val = int(m.group(2))
                s = float(m.group(3))
                self._duration = h * 3600 + m_val * 60 + s
                self._total_samples = int(self._duration * self.sr)
                return
        except Exception:
            pass
        raise ValueError(f"Nie można odczytać czasu trwania: {os.path.basename(self.filepath)}")
    
    def chunks(self) -> Iterator[np.ndarray]:
        """Zwraca kolejne chunki float32 audio."""
        with tempfile.TemporaryDirectory(prefix="cdj-audio-") as temp_dir:
            decoded = os.path.join(temp_dir, "samples.f32le")
            
            try:
                p = subprocess.Popen(
                    [self.ffmpeg, "-v", "error", "-i", self.filepath,
                     "-ac", "1", "-ar", str(self.sr), "-f", "f32le", decoded],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
                
                # Czytaj stderr w tle (zapobieg deadlock)
                stderr_lines = []
                def read_stderr():
                    try:
                        for line in p.stderr:
                            stderr_lines.append(line)
                    except Exception:
                        pass
                
                err_thread = threading.Thread(target=read_stderr, daemon=True)
                err_thread.start()
                
                # Czytaj plik chunk po chunk
                with open(decoded, "rb") as f:
                    while True:
                        data = f.read(self.chunk_samples * 4)  # 4 bytes per float32
                        if not data:
                            break
                        chunk = np.frombuffer(data, dtype="<f4")
                        if len(chunk) > 0:
                            yield chunk
                
                p.wait(timeout=300)
                if p.returncode != 0:
                    stderr = "".join(stderr_lines) if stderr_lines else "Unknown error"
                    raise RuntimeError(f"FFmpeg error: {stderr}")
            
            except subprocess.TimeoutExpired:
                p.kill()
                raise RuntimeError("FFmpeg timeout (>5 min)")
            except FileNotFoundError:
                raise RuntimeError(f"FFmpeg nie znaleziony: {self.ffmpeg}")
    
    def load_full(self) -> np.ndarray:
        """Załaduj całe audio do RAM (dla backward compatibility)."""
        chunks = list(self.chunks())
        return np.concatenate(chunks) if chunks else np.array([], dtype="<f4")
