# Third-party notices / Składniki osób trzecich

The CDJ Tool source code is MIT-licensed (see `LICENSE`). The app bundle distributed in the releases additionally
contains the components below, each under its own free license. The full license texts are shipped inside the app in
`CDJ Tool.app/Contents/Resources/Licenses/` and in the `licenses/` folder of this repository.
*Kod CDJ Tool jest na licencji MIT. Aplikacja zawiera też poniższe składniki, każdy na własnej wolnej licencji;
pełne teksty są w folderze `Licenses` aplikacji i w katalogu `licenses/` repozytorium.*

| Component | Version | License | Notes |
|---|---|---|---|
| Python | 3.9 (bundled framework) | PSF License | runs the backend |
| NumPy | bundled | BSD-3-Clause | signal processing |
| SciPy | bundled | BSD-3-Clause | filters |
| ONNX Runtime | 1.19.2 | MIT (© Microsoft) | runs the neural network; `ONNXRuntime-LICENSE.txt`, `ONNXRuntime-ThirdPartyNotices.txt` |
| Beat This! model (weights `final0`, ONNX export by *musetric*, with its mel filterbank) | n/a | MIT (© 2024 Institute of Computational Perception, JKU Linz) | bar-phase (downbeat) detection; `BeatThis-LICENSE.txt` |
| imageio-ffmpeg | 0.6.0 | BSD-2-Clause | only used as the holder of the ffmpeg binary path |
| FFmpeg | 7.1 | LGPL-2.1-or-later | custom minimal static build, no GPL parts and no third-party libraries; unmodified upstream sources |

## Beat This!

Downbeat detection uses the pretrained **Beat This!** model. If you use it in research, please cite:
F. Foscarin, J. Schlüter, G. Widmer, *Beat this! Accurate beat tracking without DBN postprocessing*, ISMIR 2024.
Source: https://github.com/CPJKU/beat_this

## FFmpeg (LGPL)

The bundled `ffmpeg` binary is built from the unmodified FFmpeg 7.1 source release with
`--disable-everything` and only the demuxers, decoders, parsers, filters and muxers listed in
`Licenses/ffmpeg-build.txt` (MP3, AAC, WAV, AIFF and FLAC input; raw float PCM output). The matching source
archive is included in the app (`Licenses/ffmpeg-7.1-source.tar.xz`), and `tools/build_ffmpeg.sh` rebuilds
the binary from it. You may replace the binary with your own build of FFmpeg.

## Interoperability data

`src/cdjtool/wzorzec/` contains a minimal template of a rekordbox-compatible USB export (database
structure and default settings files) with all track, artist, album and playlist data removed and no
personal data. It is needed so the generated stick has the same structure as an export made by rekordbox.
It was produced by `tools/sanitize_template.py`. *rekordbox*, *Pioneer DJ* and *CDJ* are trademarks of their owners.

## Not included

CDJ Tool does not contain, download or distribute any AlphaTheta firmware or any part of the RaftTone CDJ-2000
firmware mod; those are separate projects. *CDJ Tool nie zawiera firmware'u AlphaTheta ani moda RaftTone.*
