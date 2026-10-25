# Third-party notices / Składniki osób trzecich

The CDJ Tool source code is MIT-licensed (see `LICENSE`). The macOS app bundle distributed in the
releases additionally contains the following components, each under its own license. The full license
texts are shipped inside the app in `CDJ Tool.app/Contents/Resources/Licenses/`.

| Component | Version | License | Notes |
|---|---|---|---|
| Python | 3.9 (bundled framework) | PSF License | runs the backend |
| NumPy | bundled | BSD-3-Clause | signal processing |
| SciPy | bundled | BSD-3-Clause | filters |
| imageio-ffmpeg | 0.6.0 | BSD-2-Clause | only used as the holder of the ffmpeg binary path |
| FFmpeg | 7.1 | LGPL-2.1-or-later | custom minimal static build, no GPL parts and no third-party libraries; unmodified upstream sources |

## FFmpeg (LGPL)

The bundled `ffmpeg` binary is built from the unmodified FFmpeg 7.1 source release with
`--disable-everything` and only the demuxers, decoders, parsers, filters and muxers listed in
`Licenses/ffmpeg-build.txt` (MP3, AAC, WAV, AIFF input; raw float PCM output). The matching source
archive is included in the app (`Licenses/ffmpeg-7.1-source.tar.xz`), and `tools/build_ffmpeg.sh` rebuilds
the binary from it. You may replace the binary with your own build of FFmpeg.

## Interoperability data

`src/cdjtool/wzorzec/` contains a minimal template of a rekordbox-compatible USB export (database
structure and default settings files) with all track, artist, album and playlist data removed and no
personal data. It is needed so the generated stick has the same structure as an export made by rekordbox.
It was produced by `tools/sanitize_template.py`. *rekordbox* and *Pioneer DJ* are trademarks of their owners.
