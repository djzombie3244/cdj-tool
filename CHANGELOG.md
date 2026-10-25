# Changelog

## 1.0.1 - 2026

- Several source folders: add as many folders as you like; they are written one after another, each as its own playlist (named after the folder) into the same library.
- BPM shows on the CDJ-2000 immediately (no own analysis by the player): the `PQT2` tag is now written to `ANLZ0000.EXT`, byte-identical to a Rekordbox export (verified on 15 tracks).
- Beat grid: the red line (beat 1 of the bar) is placed where the main beat starts after an intro or breakdown (kick-start detection with a novelty-vote fallback). Verified on a CDJ-2000.
- Beat times written to the millisecond like Rekordbox; empty `s4` string in track rows like Rekordbox.
- Note: the CDJ-2000 Folder view does not use the analysis data; use All Tracks or Playlist.

## 1.0.0 - 2026

First public release.

- Analysis of MP3, AAC (.m4a), WAV and AIFF: BPM, beat grid, overview and detailed waveforms.
- Writes `export.pdb`, `ANLZ0000.DAT` and `ANLZ0000.EXT` loadable by a CDJ-2000.
- Adds new tracks to the library already on the stick (default) or replaces it with a backup.
- Fixed `E-8709` caused by unaligned long strings in track rows of the database.
- Speed measurement, parallel analysis limited to 80 % of throughput, RAM limit, time estimate.
- Optional FAT32 (MBR) formatting, reliable eject.
- Interface in Polish, English, German, French, Spanish and Italian.
- Offline macOS app (Apple Silicon, macOS 14+), ad-hoc signed.
