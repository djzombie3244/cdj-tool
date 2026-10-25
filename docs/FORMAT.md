# Notes on the USB export format

🇵🇱 [Po polsku](FORMAT.pl.md)

These notes describe what CDJ Tool writes and what was verified. They come from comparing exports made by
rekordbox with this tool's output, one byte range at a time (swap one file, table or page in a working
export and see whether the player still loads it). For deeper background see the public analysis by
Deep Symmetry: <https://djl-analysis.deepsymmetry.org/>.

**Verification level:** "verified" means tested on a real CDJ-2000 (non-NXS). Everything else is
derived from rekordbox exports and public documentation.

## Layout of the stick

```
/PIONEER/rekordbox/export.pdb              track database
/PIONEER/USBANLZ/Pxxx/XXXXXXXX/ANLZ0000.DAT   beat grid, overview waveform
/PIONEER/USBANLZ/Pxxx/XXXXXXXX/ANLZ0000.EXT   detailed waveform (PWV3)
/PIONEER/{DEVSETTING,MYSETTING,MYSETTING2,DJMMYSETTING}.DAT, CDJ/, USBANLZ/USBMNG.DAT   settings (from the template)
/Contents/<artist>/<album>/<file>          copies of the audio files
```

- Folder and file names are Unicode **NFC** (macOS returns NFD); file name stems are cut to **44 characters** (verified: the path hash and the stored path must agree).
- The analysis folder name comes from a hash of the track path (UTF-16BE, multiply-add, modulo 200003; `Pxxx` is picked from bits of the hash). The player reads the path from the database, so a different free folder works when two tracks collide; CDJ Tool does this automatically.

## export.pdb

- 4096-byte pages. Page 0 is the file header with the table list (type, first page, last page, "empty candidate" page). Each table is a chain: index page followed by data pages.
- Tables used: 0 tracks, 1 genres, 2 artists, 3 albums, 4 labels, 5 keys, 6 colors, 7 playlist tree, 8 playlist entries. Tables 1, 4, 5, 6 and the others are copied from the template unchanged. Tables 0, 2, 3, 7, 8 are rebuilt.
- Data page: 40-byte header, row heap, then for every group of 16 rows an index (row offsets in reverse order, a presence bitmask and a flag word) growing from the end of the page.
- The last data page of a table carries flag `0x24` and is **not** listed in the table's index page; all earlier data pages are `0x34` and are listed (`page << 3` entries; the entry before the tail has flag `3`). Verified.
- Existing pages of a table are reused for the new content (no orphaned pages). Unused pages are zeroed.
- The index page can hold about 1009 data pages, i.e. roughly 10,000 tracks. CDJ Tool refuses to write more.
- Row `index_shift` is `0x20 * (row number within its page)`.

### Track row

94 fixed bytes (little endian) followed by 21 string offsets (`uint16` each, relative to the row start) and the strings.
Fixed fields in order: subtype (`0x24`), index_shift, bitmask, sample_rate, composer_id, file_size, checksum (CRC32 of the path is used), two unknown words, artwork_id, key_id, original_artist_id, label_id, remixer_id, bitrate, track_number, tempo (BPM × 100), genre_id, album_id, artist_id, id, disc, play_count, year, sample_depth, duration (s), unknown, color, rating, file_type, unknown.

- `file_type` is 1 for MP3, 4 for M4A/AAC, 11 for WAV, 12 for AIFF. Verified for MP3; the other values come from documentation and are being tested.
- Strings (`DeviceSQL`): short ASCII has a 1-byte header `((len+1) << 1) | 1`; long ASCII `0x40` and UTF-16LE `0x90` have a 4-byte header with a total length.
- **Verified, important:** every *long* string (`0x40` or `0x90`) must start at an offset divisible by 4 inside the row. rekordbox pads with zeros. Without the padding a CDJ-2000 shows **E-8709** for tracks with non-ASCII text (for example accented letters in the title).

## ANLZ files

Big-endian tagged sections. The file starts with `PMAI` (header length 28, file length).

| File | Tags written |
|---|---|
| `ANLZ0000.DAT` | `PPTH` (path of the audio file, UTF-16BE), `PVBR` (VBR seek table placeholder + sample count), `PQTZ` (beat grid: beat number, tempo, time in ms), `PCOB` (empty memory and hot cue lists), `PWAV` (400-byte overview waveform), `PWV2` (100-byte mini waveform) |
| `ANLZ0000.EXT` | `PPTH`, `PWV3` (detailed waveform, **150 columns per second**, 1 byte each: 3 bits whiteness, 5 bits height), `PCOB` lists, `PQT2` (beat grid with sub-millisecond beat times; makes the BPM appear instantly, byte-identical to a Rekordbox export) |

- **Verified:** a plain CDJ-2000 (not NXS) needs the `.EXT` file with `PWV3`; without it the player shows "no waveform data, analyse in rekordbox" even though `PWAV` exists.
- For files that are not MP3 the `PVBR` sample count is the number of samples; whether the player requires anything more for AAC/WAV/AIFF is still being verified.

## Beat grid

CDJ Tool does not copy rekordbox's grid, it computes its own:

1. Decode to mono 22.05 kHz, low-pass the signal (kick region).
2. Build an onset envelope; estimate the tempo from a composite profile folded modulo the beat period (1 ms bins), with a preference for integer BPM.
3. The first beat is the start of the rise of the kick (< 120 Hz). Constants in `config.json` account for the offset the player uses.

rekordbox's own phase is not a perfect reference (it differs from the true kick onset by roughly −80..+30 ms),
so "identical to rekordbox" is not the goal. Check each grid on the player.

## Stick preparation

CDJ-2000 reads FAT32 with an MBR partition table. The optional format function runs `diskutil eraseDisk FAT32 <NAME> MBR <disk>` after a typed confirmation of the volume name, only for external physical disks up to 2 TB.

## Known open questions

- Behaviour of other players (NXS, NXS2, CDJ-3000, XDJ) that read additional files (`exportExt.pdb`, `exportLibrary.db`).
- Whether the player needs a specific `PVBR` content for non-MP3 files.
- `WAVE_FORMAT_EXTENSIBLE` 24-bit WAV on a CDJ-2000 (probably not accepted).
