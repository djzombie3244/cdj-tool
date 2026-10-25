#!/bin/bash
# CDJ Tool - build the Intel Mac (x86_64) installer from the Apple Silicon app bundle.
# Run on an Apple Silicon Mac with Xcode command line tools and internet access (pip downloads the Intel
# NumPy and SciPy wheels; ffmpeg is cross-compiled from the source archive that ships in the bundle).
#
#   tools/build_intel.sh [--app "/path/CDJ Tool.app"] [--min-macos 10.15]
#
# Result: dist/CDJ-Tool-<version>-intel.dmg and .zip (SHA256SUMS.txt gets two more lines).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_IN="$HOME/Applications/CDJ Tool.app"
MINOS="10.15"
while [ $# -gt 0 ]; do
  case "$1" in
    --app) APP_IN="$2"; shift 2;;
    --min-macos) MINOS="$2"; shift 2;;
    *) echo "Unknown option: $1"; exit 2;;
  esac
done
die() { echo "ERROR: $*" >&2; exit 1; }
[ "$(uname)" = "Darwin" ] || die "Run this on a Mac."
[ -d "$APP_IN" ] || die "App bundle not found: $APP_IN (use --app)"
command -v swiftc >/dev/null || die "swiftc not found. Run: xcode-select --install"
command -v python3 >/dev/null || die "python3 not found"
WORK="$(mktemp -d /tmp/cdj-intel.XXXXXX)"; trap 'rm -rf "$WORK"' EXIT
APP="$WORK/CDJ Tool.app"; RES="$APP/Contents/Resources"; SP="$RES/python/site-packages"
FWK="$APP/Contents/Frameworks/Python3Bundled.framework/Versions/3.9"

echo "==> Copying the bundle"
ditto "$APP_IN" "$APP"

echo "==> Checking that the bundled Python has an Intel slice"
lipo -archs "$FWK/Python3" | grep -q x86_64 || die "The bundled Python has no x86_64 slice (needs the python.org universal2 build)"
lipo -archs "$FWK/bin/python3.9" | grep -q x86_64 || die "python3.9 has no x86_64 slice"
for so in "$FWK"/lib/python3.9/lib-dynload/*.so; do
  lipo -archs "$so" | grep -q x86_64 || die "$(basename "$so") has no x86_64 slice"
done

echo "==> Adding an Intel slice to the Python.app launcher stub"
# python3.9 (universal) starts Resources/Python.app/Contents/MacOS/Python, which ships as arm64 only.
STUB="$FWK/Resources/Python.app/Contents/MacOS/Python"
if [ -f "$STUB" ] && ! lipo -archs "$STUB" | grep -q x86_64; then
  cat > "$WORK/pystub.c" <<'CEOF'
#include <dlfcn.h>
#include <stdio.h>
int main(int argc, char **argv) {
    void *h = dlopen("@executable_path/../../../../Python3", RTLD_LAZY | RTLD_GLOBAL);
    if (!h) { fprintf(stderr, "dlopen failed: %s\n", dlerror()); return 1; }
    int (*run)(int, char **) = (int (*)(int, char **))dlsym(h, "Py_BytesMain");
    if (!run) { fprintf(stderr, "Py_BytesMain not found\n"); return 1; }
    return run(argc, argv);
}
CEOF
  clang -arch x86_64 -mmacosx-version-min="$MINOS" -O2 "$WORK/pystub.c" -o "$WORK/pystub-x86_64" || die "could not compile the Intel Python stub"
  lipo -create "$STUB" "$WORK/pystub-x86_64" -output "$WORK/pystub-universal" || die "lipo failed"
  cp "$WORK/pystub-universal" "$STUB"; chmod 755 "$STUB"
fi

echo "==> Replacing NumPy and SciPy with the Intel wheels"
rm -rf "$SP/numpy" "$SP/scipy" "$SP"/numpy-*.dist-info "$SP"/scipy-*.dist-info "$SP"/numpy.libs "$SP"/scipy.libs
python3 -m pip install --quiet --target "$SP" --no-deps --only-binary=:all: --python-version 3.9 --implementation cp \
  --platform macosx_10_9_x86_64 --platform macosx_10_13_x86_64 --platform macosx_11_0_x86_64 --platform macosx_10_15_x86_64 \
  --platform macosx_12_0_x86_64 --platform macosx_14_0_x86_64 numpy==2.0.2 scipy==1.13.1 \
  || die "pip could not fetch the Intel wheels (check the internet connection)"
rm -rf "$SP"/bin
while IFS= read -r -d '' so; do
  lipo -archs "$so" 2>/dev/null | grep -q x86_64 || die "Not an Intel binary: $so"
done < <(find "$SP/numpy" "$SP/scipy" -type f \( -name '*.so' -o -name '*.dylib' \) -print0)

echo "==> Building the Intel ffmpeg (LGPL-only, cross-compiled)"
TAR="$RES/Licenses/ffmpeg-7.1-source.tar.xz"; [ -f "$TAR" ] || die "ffmpeg source archive not found in the bundle"
BIN="$SP/imageio_ffmpeg/binaries"
CDJ_FFMPEG_ARCH=x86_64 bash "$ROOT/tools/build_ffmpeg.sh" "$TAR" "$BIN/ffmpeg-macos-x86_64-v7.1"
lipo -archs "$BIN/ffmpeg-macos-x86_64-v7.1" | grep -q x86_64 || die "ffmpeg is not an Intel binary"
rm -f "$BIN/ffmpeg-macos-aarch64-v7.1"

echo "==> Compiling the Intel launcher (macOS $MINOS or newer)"
swiftc -O -target "x86_64-apple-macos$MINOS" "$ROOT/src/cdjtool/CDJToolApp.swift" -o "$APP/Contents/MacOS/CDJTool" \
  -framework AppKit -framework WebKit || die "The launcher did not compile for macOS $MINOS; try --min-macos 11.0"

echo "==> Building the installer"
CDJ_ARCH=x86_64 CDJ_MIN_MACOS="$MINOS" bash "$ROOT/tools/build_installer.sh" --app "$APP"
