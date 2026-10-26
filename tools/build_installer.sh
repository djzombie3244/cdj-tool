#!/bin/bash
# CDJ Tool - build the offline installer (DMG + ZIP) from a prepared app bundle.
# Author: Wiktor Bakowicz (MIT). Run on a Mac with Apple Silicon. No internet needed.
#
#   tools/build_installer.sh [--app "/path/CDJ Tool.app"] [--version 1.0.0] [--out dist]
#
# Environment: CDJ_ARCH (arm64 default, or x86_64 for the Intel build), CDJ_MIN_MACOS (default 14.0),
#              CDJ_SIGN_IDENTITY  Developer ID identity to sign with (default: ad-hoc signature).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/src/cdjtool"
APP_IN="$HOME/Applications/CDJ Tool.app"
VERSION="$(grep -m1 '^## ' "$ROOT/CHANGELOG.md" | sed -E 's/^## ([0-9][0-9.]*).*/\1/')"
OUT="$ROOT/dist"
AUTHOR="Wiktor Bakowicz"
ARCH="${CDJ_ARCH:-arm64}"
MINOS="${CDJ_MIN_MACOS:-14.0}"
if [ "$ARCH" = "x86_64" ]; then FFNAME="ffmpeg-macos-x86_64-v7.1"; SUF="-intel"; RUN=(arch -x86_64); else FFNAME="ffmpeg-macos-aarch64-v7.1"; SUF=""; RUN=(); fi

while [ $# -gt 0 ]; do
  case "$1" in
    --app) APP_IN="$2"; shift 2;;
    --version) VERSION="$2"; shift 2;;
    --out) OUT="$2"; shift 2;;
    *) echo "Unknown option: $1"; exit 2;;
  esac
done

die() { echo "ERROR: $*" >&2; exit 1; }
[ "$(uname)" = "Darwin" ] || die "This script must run on macOS."
for c in ditto hdiutil codesign shasum xattr file; do command -v "$c" >/dev/null || die "Missing tool: $c"; done
PB=/usr/libexec/PlistBuddy
[ -x "$PB" ] || die "Missing PlistBuddy"
[ -d "$APP_IN" ] || die "App bundle not found: $APP_IN (use --app)"
[ -f "$APP_IN/Contents/MacOS/CDJTool" ] || die "Not a CDJ Tool bundle: $APP_IN"
[ -n "$VERSION" ] || die "Could not read the version from CHANGELOG.md (use --version)"

WORK="$(mktemp -d /tmp/cdj-installer.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT
APP="$WORK/CDJ Tool.app"
RES="$APP/Contents/Resources"
PLIST="$APP/Contents/Info.plist"

echo "==> Copying the app bundle"
ditto "$APP_IN" "$APP"

echo "==> Installing sources from src/cdjtool"
for f in app.py silnik.py pdb_writer.py gen_dat.py takt.py beatthis.py cue.py audio_stream.py exceptions.py logging_config.py worker.py ui.html config.json AppIcon.png; do
  [ -f "$SRC/$f" ] || die "Missing source file: $f"
  cp "$SRC/$f" "$RES/$f"
done
rm -rf "$RES/wzorzec"
cp -R "$SRC/wzorzec" "$RES/wzorzec"

echo "==> Beat This! model and ONNX Runtime (universal2, runs on Apple Silicon and Intel)"
bash "$ROOT/tools/fetch_models.sh" >/dev/null || die "Could not fetch the model/ONNX Runtime (needs internet once)"
mkdir -p "$RES/models"
cp "$ROOT/build/models/beat_this.onnx" "$ROOT/build/models/mel-filterbank.bin" "$RES/models/"
mkdir -p "$RES/Licenses"
cp "$ROOT"/licenses/* "$RES/Licenses/"   # ONNX Runtime and Beat This! (MIT requires the notice to travel with the app)
SP="$RES/python/site-packages"
rm -rf "$SP"/onnxruntime "$SP"/onnxruntime-*
unzip -q -o "$(ls "$ROOT"/build/ort/onnxruntime-1.19.2-cp39-cp39-macosx_11_0_universal2.whl)" -d "$SP"

echo "==> Cleaning"
find "$APP" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$APP" \( -name ".DS_Store" -o -name "._*" -o -name "*.save" \) -type f -delete 2>/dev/null || true
# personal or credential-like files must never ship
for bad in djprofile.nxs gcred.dat; do
  find "$RES" -name "$bad" -type f -delete
done
if grep -rla -e "Dj_Zombie" -e "gcred" "$RES/wzorzec" >/dev/null 2>&1; then die "Template still contains personal data"; fi

echo "==> Info.plist (version $VERSION, author $AUTHOR)"
setkey() { "$PB" -c "Set :$1 $2" "$PLIST" 2>/dev/null || "$PB" -c "Add :$1 string $2" "$PLIST"; }
setkey CFBundleShortVersionString "$VERSION"
setkey CFBundleVersion "$VERSION"
setkey NSHumanReadableCopyright "\"© 2026 $AUTHOR. MIT License.\""
setkey CFBundleGetInfoString "\"CDJ Tool $VERSION, © 2026 $AUTHOR\""
setkey LSApplicationCategoryType "public.app-category.music"
setkey LSMinimumSystemVersion "$MINOS"

echo "==> Checking the bundled ffmpeg"
FF="$APP/Contents/Resources/python/site-packages/imageio_ffmpeg/binaries/$FFNAME"
[ -x "$FF" ] || die "ffmpeg not found in the bundle"
FMTS="$(${RUN[@]+"${RUN[@]}"} "$FF" -hide_banner -demuxers 2>/dev/null || true)"
for d in " wav " " aiff " "mov,mp4" " mp3 "; do
  echo "$FMTS" | grep -q -- "$d" || die "The bundled ffmpeg lacks the '$d' demuxer. Run tools/build_ffmpeg.sh first."
done

echo "==> Repairing framework layout (symlinks)"
# A copy made without preserving symlinks leaves Versions/Current and the top-level
# Python3/Headers/Resources as full copies; codesign then calls the bundle "ambiguous".
for fw in "$APP"/Contents/Frameworks/*.framework; do
  [ -d "$fw/Versions" ] || continue
  ver="$(ls "$fw/Versions" | grep -v '^Current$' | head -1)"
  [ -n "$ver" ] || continue
  rm -rf "$fw/Versions/Current"
  ln -s "$ver" "$fw/Versions/Current"
  for item in $(ls "$fw" | grep -v '^Versions$'); do
    rm -rf "$fw/$item"
    ln -s "Versions/Current/$item" "$fw/$item"
  done
  rm -rf "$fw/Versions/$ver/_CodeSignature"
done

echo "==> Signing"
xattr -cr "$APP"
if [ -n "${CDJ_SIGN_IDENTITY:-}" ]; then
  ID="$CDJ_SIGN_IDENTITY"; OPTS=(--options runtime --timestamp)
  echo "    using identity: $ID"
else
  ID="-"; OPTS=()
  echo "    ad-hoc signature (set CDJ_SIGN_IDENTITY for a Developer ID)"
fi
# inside-out: every Mach-O file, then the frameworks, then the whole app.
# Errors of single files are shown; the final --deep signature of the app decides.
find "$APP" -type f -print0 | while IFS= read -r -d '' f; do
  if file -b "$f" | grep -q "Mach-O"; then
    out="$(codesign --force ${OPTS[@]+"${OPTS[@]}"} --sign "$ID" "$f" 2>&1)" || echo "    note: $f: $out"
  fi
done
for fw in "$APP"/Contents/Frameworks/*.framework; do
  [ -d "$fw" ] || continue
  out="$(codesign --force ${OPTS[@]+"${OPTS[@]}"} --sign "$ID" "$fw" 2>&1)" || echo "    note: $fw: $out"
done
out="$(codesign --force --deep ${OPTS[@]+"${OPTS[@]}"} --sign "$ID" "$APP" 2>&1)" || die "codesign of the app failed: $out"
codesign --verify --deep --strict "$APP" || die "Signature verification failed"

echo "==> Self-test (bundled Python, offline)"
FWK="$APP/Contents/Frameworks/Python3Bundled.framework/Versions/3.9"
SELFTEST="$WORK/selftest.py"
cat > "$SELFTEST" <<'PYEOF'
import os, sys, tempfile, wave
res, site = sys.argv[1], sys.argv[2]
sys.path.insert(0, res); sys.path.insert(0, site)
import numpy as np, scipy
import pdb_writer, silnik
lib = pdb_writer.czytaj_biblioteke(open(os.path.join(res, "wzorzec", "export.pdb"), "rb").read())
assert len(lib["utwory"]) == 1, "template is not neutral"
d = tempfile.mkdtemp(); a = os.path.join(d, "a"); usb = os.path.join(d, "usb"); os.mkdir(a); os.mkdir(usb)
sr, bpm = 44100, 128.0
y = np.zeros(sr * 20)
for k in range(int(20 * bpm / 60)):
    i0 = int((0.3 + k * 60 / bpm) * sr); t = np.arange(int(0.12 * sr)) / sr
    y[i0:i0 + len(t)] += np.sin(2 * np.pi * (110 - 400 * t) * t) * np.exp(-t * 25)
with wave.open(os.path.join(a, "test.wav"), "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
    w.writeframes((np.clip(y, -1, 1) * 30000).astype("<i2").tobytes())
ev = []
silnik.przetworz({"folder_mp3": a, "pendrive": usb, "playlista": "T", "kopiuj_mp3": True, "zastap_pioneer": False}, ev.append)
assert any(e.get("typ") == "koniec" and e.get("ok") for e in ev), ev[-3:]
t = [e for e in ev if e.get("typ") == "utwor"][0]
assert abs(t["bpm"] - bpm) < 0.5, t["bpm"]
lib = pdb_writer.czytaj_biblioteke(open(os.path.join(usb, "PIONEER", "rekordbox", "export.pdb"), "rb").read())
assert len(lib["utwory"]) == 1
import beatthis
assert beatthis.dostepny(), "Beat This! model not usable: %s" % beatthis.blad()
print("self-test OK, detected BPM %.2f, Beat This! available" % t["bpm"])
PYEOF
env -i HOME="$HOME" PATH="/usr/bin:/bin" PYTHONHOME="$FWK" PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 CDJ_FFMPEG="$FF" \
  ${RUN[@]+"${RUN[@]}"} "$FWK/bin/python3.9" "$SELFTEST" "$RES" "$RES/python/site-packages" || die "Self-test failed"

echo "==> Creating the DMG and ZIP"
mkdir -p "$OUT"
STAGE="$WORK/dmg"; mkdir -p "$STAGE"
ditto "$APP" "$STAGE/CDJ Tool.app"
ln -s /Applications "$STAGE/Applications"
cp "$ROOT/README.md" "$STAGE/README-EN.md"
cp "$ROOT/README.pl.md" "$STAGE/README-PL.md"
cp "$ROOT/docs/INSTALL.md" "$STAGE/INSTALL-EN.md"
cp "$ROOT/docs/INSTALL.pl.md" "$STAGE/INSTALL-PL.md"
cp "$ROOT/LICENSE" "$ROOT/THIRD_PARTY_NOTICES.md" "$STAGE/"
cat > "$STAGE/Install CDJ Tool.command" <<'EOS'
#!/bin/bash
# Copies CDJ Tool to Applications and removes the quarantine flag (offline).
cd "$(dirname "$0")" || exit 1
echo "CDJ Tool - installation / instalacja"
DEST="/Applications"
[ -w "$DEST" ] || { DEST="$HOME/Applications"; mkdir -p "$DEST"; }
rm -rf "$DEST/CDJ Tool.app"
ditto "CDJ Tool.app" "$DEST/CDJ Tool.app" || { echo "Copy failed / Kopiowanie nie powiodło się"; exit 1; }
xattr -dr com.apple.quarantine "$DEST/CDJ Tool.app" 2>/dev/null || true
echo "Installed in / Zainstalowano w: $DEST"
open "$DEST/CDJ Tool.app"
EOS
chmod +x "$STAGE/Install CDJ Tool.command"

DMG="$OUT/CDJ-Tool-$VERSION$SUF.dmg"
rm -f "$DMG"
hdiutil create -volname "CDJ Tool $VERSION" -srcfolder "$STAGE" -fs HFS+ -format UDZO -ov "$DMG" >/dev/null
ZIP="$OUT/CDJ-Tool-$VERSION$SUF.zip"
rm -f "$ZIP"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"
( cd "$OUT" && if [ "$ARCH" = "x86_64" ]; then OP=">>"; else OP=">"; fi
  eval shasum -a 256 \"CDJ-Tool-$VERSION$SUF.dmg\" \"CDJ-Tool-$VERSION$SUF.zip\" $OP SHA256SUMS.txt )

echo
echo "Done / Gotowe:"
ls -lh "$OUT"/CDJ-Tool-"$VERSION"*"$SUF".* "$OUT"/SHA256SUMS.txt
cat "$OUT/SHA256SUMS.txt"
