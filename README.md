# CDJ Tool

**Prepare a USB stick for Pioneer CDJ-2000 players on a Mac, without rekordbox.**
🇵🇱 [Wersja polska](README.pl.md)

CDJ Tool analyses your tracks (BPM, beat grid, waveforms) and writes a USB stick that a plain
**CDJ-2000** loads like a rekordbox export: track database (`export.pdb`), analysis files
(`ANLZ0000.DAT` / `.EXT`) and copies of your audio files. It is a small native macOS app that works
completely **offline**.

> **Status:** version 1.0.1. Verified on real hardware with a CDJ-2000 (non-NXS) for MP3 tracks:
> database, waveforms, loading. AAC, WAV and AIFF support is newer and is still being tested on
> hardware (see [Known limitations](#known-limitations)). Please report what works for you.

## Features

- Analysis of **MP3, AAC (.m4a), WAV and AIFF** files (the formats a CDJ-2000 plays; FLAC and ALAC are not supported by the player and are skipped).
- **BPM and beat grid** detection, **overview and detailed waveforms** (the CDJ-2000 needs the `.EXT` file to show waveforms).
- **Adds to an existing library** on the stick instead of overwriting it; playlists, genres and earlier analysis are kept. Optional full replace (with a backup).
- Parallel analysis tuned to your disk speed (uses at most 80 % of measured throughput) and a RAM limit; time-remaining estimate.
- Optional **format the stick as FAT32 (MBR)** for the CDJ-2000.
- Reliable **eject** button.
- Interface in **6 languages**: Polski, English, Deutsch, Français, Español, Italiano.
- No account, no network access, no telemetry.

## Requirements

- macOS 14 (Sonoma) or newer, **Apple Silicon** (M1 or newer).
- A USB stick formatted FAT32 (the app can format it for you), and a CDJ-2000.

## Install (offline)

1. Download `CDJ-Tool-<version>.dmg` from the [Releases](../../releases) page.
2. Open it and drag **CDJ Tool** to **Applications**.
3. The first start needs one extra step because the app is not notarized by Apple (see below).

Full instructions, Gatekeeper workaround and uninstall: [docs/INSTALL.md](docs/INSTALL.md).

## Use

1. Choose the folder with your tracks and the USB stick.
2. Wait for the automatic speed check, optionally name the playlist.
3. Click **Analyze and write to USB stick**, then **Eject** and put the stick into the CDJ-2000.

By default new tracks are **added** to the library already on the stick. Tick *Replace everything* to start
from scratch (the old `PIONEER` folder is first copied to `Documents/CDJ-Tool-kopie`).

## Known limitations

- Beat grids are computed by a heuristic (kick-drum onset + tempo histogram). They are good for most
  four-on-the-floor tracks but are **not identical to rekordbox**; always check the grid on the player.
- Variable-tempo tracks and tracks without a clear kick are not handled well.
- The database writer supports roughly 10,000 tracks per stick; larger sets must be split.
- Verified only on a CDJ-2000. Other models (NXS, NXS2, CDJ-3000) may need extra files that this tool does not write.
- 24-bit WAV files saved with the `WAVE_FORMAT_EXTENSIBLE` header may not load on a CDJ-2000; use plain PCM.
- The app is ad-hoc signed, not notarized.

## How it works

The file formats were reverse-engineered by comparing exports made by rekordbox with the output of this
tool, one byte range at a time. Notes are in [docs/FORMAT.md](docs/FORMAT.md).

## Building from source

See [docs/BUILD.md](docs/BUILD.md). In short: `src/cdjtool` is a Python 3.9+ backend (NumPy, SciPy, ffmpeg)
with an HTML interface, wrapped by a small Swift launcher (`CDJToolApp.swift`).

```bash
pip install -r requirements-dev.txt
python -m pytest
```

## Disclaimer

CDJ Tool is an independent, unofficial project. It is **not affiliated with, endorsed by or supported by**
Pioneer DJ, AlphaTheta or the rekordbox team. *Pioneer DJ*, *CDJ* and *rekordbox* are trademarks of their
respective owners and are used here only to describe compatibility. The tool writes files in a format
obtained by interoperability research; use it at your own risk and **test on a stick you can afford to wipe**.
The software is provided "as is", without warranty (see [LICENSE](LICENSE)).

## Author and credits

© 2026 **Wiktor Bakowicz**. Released under the [MIT License](LICENSE).
Third-party components are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
Written with the help of an AI assistant (Claude by Anthropic).
