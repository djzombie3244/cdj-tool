#!/bin/bash
# CDJ Tool - build the minimal LGPL ffmpeg (MP3, AAC, WAV, AIFF, FLAC in; raw float PCM out) from the unmodified
# FFmpeg 7.1 source archive. Works offline if you already have the archive.
#
#   tools/build_ffmpeg.sh <ffmpeg-7.1-source.tar.xz> <output-binary>
#   CDJ_FFMPEG_ARCH=x86_64 tools/build_ffmpeg.sh ...   (cross-build for Intel Macs on Apple Silicon)
set -euo pipefail
TAR="${1:?usage: build_ffmpeg.sh ffmpeg-7.1-source.tar.xz output-binary}"
OUT="${2:?usage: build_ffmpeg.sh ffmpeg-7.1-source.tar.xz output-binary}"
[ -f "$TAR" ] || { echo "Archive not found: $TAR"; exit 1; }
if ! command -v clang >/dev/null 2>&1 || ! command -v make >/dev/null 2>&1; then
  echo "Missing Xcode command line tools. Run: xcode-select --install"; exit 1
fi
B="$(mktemp -d /tmp/cdj-ffmpeg.XXXXXX)"
trap 'rm -rf "$B"' EXIT
tar -xf "$TAR" -C "$B"
cd "$B"/ffmpeg-7.1
XARGS=""
if [ "${CDJ_FFMPEG_ARCH:-}" = "x86_64" ]; then
  XARGS="--enable-cross-compile --arch=x86_64 --target-os=darwin --disable-x86asm --cc=\"clang -arch x86_64 -mmacosx-version-min=10.15\""
fi
eval ./configure $XARGS --disable-everything --disable-autodetect --disable-network --disable-shared \
 --enable-static --enable-ffmpeg --disable-ffplay --disable-ffprobe --disable-doc \
 --enable-demuxer=mp3,ffmetadata,wav,aiff,mov,aac,flac \
 --enable-decoder=mp3float,mp3,aac,flac,pcm_s16le,pcm_s16be,pcm_s24le,pcm_s24be \
 --enable-parser=mpegaudio,aac,aac_latm,flac \
 --enable-muxer=ffmetadata,pcm_f32le --enable-encoder=pcm_f32le \
 --enable-protocol=file --enable-avfilter --enable-filter=aresample,aformat --enable-swresample \
 --enable-avcodec --enable-avformat --enable-avutil > "$B/configure.log" 2>&1 \
 || { tail -20 "$B/configure.log"; exit 1; }
make -j"$(sysctl -n hw.ncpu 2>/dev/null || nproc)" > "$B/make.log" 2>&1 || { tail -20 "$B/make.log"; exit 1; }
cp ffmpeg "$OUT"
chmod 755 "$OUT"
echo "Built: $OUT"
"$OUT" -hide_banner -version | head -1
