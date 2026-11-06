# Changelog

## 1.0.6 - 2026
- **Fixed for real: CDJ Tool no longer shows "nie uruchomił składnika analizy" after the Mac sleeps.** The fix from 1.0.4 never reached the Apple Silicon installers: the build copied the launcher from an old app bundle instead of compiling it from the source. `build_installer.sh` now always compiles the launcher from `CDJToolApp.swift`.
- In the Mac/Windows app the analysis component no longer stops itself when the window has been quiet for a minute (after sleep, WebKit pauses the page and that looked like a closed window). It now stops only together with the app. If it still ends unexpectedly, the app starts it again (up to 5 tries in a row) instead of showing an error.
- The *Eject* button falls back to the selected stick when the job did not report one, and the error message shows the path it received.
- **Key (test version):** CDJ Tool writes the musical key of each track to the stick (classic notation such as Am, or Camelot such as 8A); a CDJ-2000 with firmware 4.33 shows KEY when browsing. When a file already has a key in its tags (TKEY/initialkey, e.g. from the store), that key is used, so the stick matches the store and rekordbox; files without one are analysed (pitch-class profiles, which like other detectors sometimes confuse relative keys or keys a fifth apart). Keys already in the library are kept when tracks are added. On 15 test tracks the keys matched a rekordbox export 15/15.
- **Playlists that mirror your folders:** subfolders become playlist folders, so the Playlist view on the CDJ shows your folder structure with BPM, key and waveform right away. (The Folder view of a CDJ-2000 measures BPM itself and ignores both the database and BPM tags in the file; tested on the deck.) Option *Subfolders as playlist folders*, on by default.
- Fixed: tags containing `#`, `=`, `;` or `\` were read with an extra backslash (e.g. a title "Track #1" appeared as "Track \#1").
- **Beat grid lines up with the music much better.** Checked against a rekordbox export of 15 tracks: the median grid error dropped from 83 ms to 9 ms. Two causes were fixed: (1) the grid phase now comes from the Beat This! network (the strongest cluster of its beats, so it ignores beats it also marks between the real ones), placing the line on the kick's attack instead of the peak of its bass; the previous energy heuristic stays as a fallback; (2) MP3 files without gapless (LAME) information are played by the CDJ about 47 ms earlier than ffmpeg decodes them, so for those files the grid, waveforms and cues are now moved together by that amount. Tracks where rekordbox made a dynamic (wandering) grid still differ by up to ~80 ms, because CDJ Tool writes a constant tempo.
- **For the RaftTone mod v1.1:** every track now gets a full set of 6 hot cues (A–F). The first ones follow the music in time order: start, groove, first drop, first break, then outro, last drop and last break; when a track has fewer such points, the rest are taken from its memory cues and phrase starts (every 16 bars), as far from the others as possible. Hot cues A–C are the same as before. On the deck the mod froze while trying to save an empty D–F slot, so the bank is never left empty. The DAT file keeps A–C (all the CDJ-2000 firmware knows) and the EXT file holds all six; the mod shows D–F from it (checked on a deck). Option *Hot Cue Auto Load* marks tracks "ON" like rekordbox (used when the player's UTILITY is set to rekordbox SETTING; off by default).
- **Faster analysis:** the first tempo search runs on the 2 minutes with the strongest bass instead of the whole track (the final 0.001 BPM pass still uses the whole track; same BPM on all 15 test tracks), and tracks where the network marks beats in between the real ones now use 8 fragments before falling back to the whole track. Analysis time on the 15 test tracks dropped by about 40%.
- **3-band waveform (test version):** option *3-band waveform* also writes `ANLZ0000.2EX`, the third analysis file rekordbox exports (PWV7 detail and PWV6 preview with low, mid and high bands, plus PWVC and PVDI). Tag layout matches a rekordbox export; band scales were fitted to rekordbox on 15 tracks. For the RaftTone mod's 3BAND mode and CDJ-3000 players over LINK; a stock CDJ-2000 ignores the file. Off by default (about 190 KB more per track).
- **Edits made on the deck are kept:** the RaftTone mod v1.1 saves beat grid edits and its D–F hot cue bank in its own files (`PIONEER/MODGRID.BIN`, `PIONEER/MODHCUE.BIN`), keyed by each track's analysis folder, which depends only on the track's path on the stick. CDJ Tool never changes these files, and keeps them even when it replaces the stick contents, so the edits survive writing the stick again.
- Checked on an Intel Mac and on Windows as well as on Apple Silicon. README and install notes now list all three downloads; new screenshots.
- Build: the DMG step retries when macOS reports the disk image as busy.

## 1.0.5 - 2026
- **Background copy:** each track is copied to the stick as soon as its analysis finishes, while the next tracks are still being analysed. The write step at the end only copies what is left. Files are written under a temporary name and renamed when complete, and the database is still written last, so stopping a run never leaves a cut-off file under a name the previous library uses. Used only when the stick has room for all files; option *Copy each track in the background* (on by default).
- **Keep awake:** the computer does not go to sleep while CDJ Tool analyses and writes (macOS: `caffeinate`, Windows: `SetThreadExecutionState`). The block ends with the job, also when it is cancelled. Option *Keep the computer awake* (on by default). Closing a laptop lid still puts it to sleep.
- The free-space check no longer counts audio files that are already on the stick with the same size (they are not copied again).
- Tests for the background copy (same files on the stick with and without it, no temporary files left).

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
