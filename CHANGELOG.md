# Changelog

## 1.0.4 - 2026
- Fixed CDJ Tool showing "nie uruchomił składnika analizy" after the Mac sleeps. The analysis component is now restarted automatically after a clean exit, and the idle watchdog waits for several checks before it quits, so a wake-up no longer closes it.
- When writing several folders at once, a folder with no supported audio files (for example one holding only samples in another format) is now skipped and listed on the final screen, instead of stopping the whole queue. The message also names the folder and no longer says "MP3" only; if the folder holds FLAC files while the FLAC option is off, it says so. Added a test.
- New look: colours taken from the app icon, larger Start button and waveform previews.

## 1.0.3 - 2026
- Fixed a bug where analysing fewer than 4 files at once could skip a track with "The weights and list don't have the same length" (or, rarely, use the wrong beat grid offset). The per-track caches added in 1.0.2 are now local to each analysis thread. Added a regression test. Libraries of 4 or more files were not affected.
- Documented the hardware test of optional FLAC on a CDJ-2000 with the RaftTone mod: tracks load and play, but hot cues load slowly and the player does not use the exported BPM/beat grid, so FLAC performs worse than MP3. It stays optional and experimental.

## 1.0.2 - 2026
- **Optional FLAC** (experimental) for a CDJ-2000 running the RaftTone mod, which plays FLAC (stereo, 16 or 24 bit, 44.1 or 48 kHz). Off by default, because a stock CDJ-2000 cannot play FLAC. Rebuild the bundled ffmpeg with `tools/build_ffmpeg.sh` to decode FLAC.
- Analysis is about 12 % faster per track with byte-identical results (the same filters and histograms are no longer computed twice; verified by tests), and ffmpeg decodes with one thread so parallel tracks do not fight over the CPU.
- Analysis runs in separate processes but uses at most half of the CPU cores, so the Mac stays cooler and responsive.
- Fixed the **Cancel** button (a proper confirmation dialog instead of the system one that the app window blocked).
- Waveform previews load in one batch request and are smaller.
- Formatting the stick: when it fails, the app now explains what to try (reconnect, format in Disk Utility, the stick may be faulty). After a successful format the old results list is cleared.
- Fixed language switching: the track info line and the format result are now translated when you change the language.
- Docs: screenshots (English and Polish) added to both READMEs.
- Docs: compatibility table (which players work, OneLibrary-only players are not supported), RaftTone mod section, licenses in plain words. License texts of ONNX Runtime and Beat This! are now shipped with the app. Added `SECURITY.md`, `CODE_OF_CONDUCT.md`, tests for FLAC and for the speed-ups.

## 1.0.1 - 2026
- Analysis cache: tracks analysed before (same file, size and date) are not analysed again, so re-exports and adding a few tracks to a large library take seconds. Cache is capped at 2 GB.
- Analysis runs at lower priority, so the Mac stays responsive during long runs.
- The main CUE and hot cue A sit at the start of the sound (like the CDJ Auto Cue), and Hot Cue Auto Load is off, so hot cues are only marked until you call them.
- Beat grid and the red line: the bar phase (which beat is beat 1) is now voted from the Beat This! neural network (CPJKU, ISMIR 2024, MIT; runs offline through ONNX Runtime), falling back to the old heuristic when the model is unavailable. Checked against Rekordbox on 15 tracks.
- HOT CUE and Memory Cue are generated automatically (start of the groove, drop, break, outro on 8-bar phrase boundaries) and written as PCOB (DAT and EXT) and PCO2 (EXT).
- Colour waveform: `PWV5` (detail) and `PWV4` (preview) are now written to `ANLZ0000.EXT` (low/mid/high bands as R/G/B, fitted to a Rekordbox export of 15 tracks), so the small overview waveform is no longer blue only.
- Faster analysis: the neural network now listens to the strongest ~30 s bass section of a track instead of the whole file (widening to 2, 4 sections or the whole track when the vote is unclear) (same bar phase on all 15 test tracks, falling back to the full track when the vote is unclear), about 4x faster overall. Added a "Mix in" memory cue 16 bars before the first drop.
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
