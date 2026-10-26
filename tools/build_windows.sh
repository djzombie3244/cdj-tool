#!/bin/bash
# Builds the Windows package on a Mac/Linux box with internet: dist/CDJ-Tool-1.0.1-windows.zip
# Needs: python3, curl, unzip, zip, pip (to download win_amd64 wheels), sips (macOS) for the icon.
set -euo pipefail
cd "$(dirname "$0")/.."
VER=$(grep -m1 '^## ' CHANGELOG.md | sed -E 's/^## *\[?v?([0-9][0-9.]*).*/\1/')
W=build/windows; PKG=$W/CDJ-Tool-$VER-windows; APP=$PKG/app
rm -rf "$W"; mkdir -p "$APP/src" "$APP/site-packages" "$W/dl"
PYZIP=python-3.9.13-embed-amd64.zip
curl -fL -o "$W/dl/$PYZIP" "https://www.python.org/ftp/python/3.9.13/$PYZIP"
mkdir -p "$APP/python" && unzip -q "$W/dl/$PYZIP" -d "$APP/python"
printf 'python39.zip\n.\n' > "$APP/python/python39._pth"
python3 -m pip download --no-deps --only-binary=:all: --platform win_amd64 --python-version 3.9 \
  --implementation cp --abi cp39 numpy==2.0.2 scipy==1.13.1 -d "$W/dl"
for w in "$W"/dl/*.whl; do unzip -q -o "$w" -d "$APP/site-packages"; done
FFURL=$(curl -fsSL https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest | python3 -c "
import json,sys,re
a=[x['browser_download_url'] for x in json.load(sys.stdin)['assets']]
c=[u for u in a if re.search(r'win64-lgpl-\\d[\\d.]*\\.zip$',u)]
c.sort(key=lambda u:(('n7.1' in u),u))
print(c[-1])")
echo "ffmpeg: $FFURL"
FF=ffmpeg-win.zip
curl -fL -o "$W/dl/$FF" "$FFURL"
unzip -q "$W/dl/$FF" -d "$W/dl/ff"
mkdir -p "$APP/ffmpeg"; cp "$W"/dl/ff/*/bin/ffmpeg.exe "$APP/ffmpeg/"
cp "$W"/dl/ff/*/LICENSE.txt "$APP/ffmpeg/FFMPEG-LICENSE.txt" 2>/dev/null || true
for f in app.py audio_stream.py beatthis.py cue.py exceptions.py gen_dat.py logging_config.py pdb_writer.py silnik.py takt.py ui.html \
         winplat.py worker.py config.json; do cp "src/cdjtool/$f" "$APP/src/"; done
cp -R src/cdjtool/wzorzec "$APP/src/"
bash tools/fetch_models.sh >/dev/null
mkdir -p "$APP/src/models"; cp build/models/beat_this.onnx build/models/mel-filterbank.bin "$APP/src/models/"
unzip -q -o build/ort/onnxruntime-1.19.2-cp39-cp39-win_amd64.whl -d "$APP/site-packages"
# ONNX Runtime needs the VC++ runtime next to its DLLs (clean Windows installs may lack it)
for d in "$APP"/site-packages/numpy.libs/msvcp140*.dll; do cp "$d" "$APP/site-packages/onnxruntime/capi/msvcp140.dll"; done
cp "$APP/python/vcruntime140.dll" "$APP/python/vcruntime140_1.dll" "$APP/site-packages/onnxruntime/capi/" 2>/dev/null || true
cp LICENSE THIRD_PARTY_NOTICES.md "$APP/" 2>/dev/null || true
mkdir -p "$APP/Licenses" && cp licenses/* "$APP/Licenses/" 2>/dev/null || true
cp tools/windows/CDJ-Tool.vbs "$APP/"
cp tools/windows/Install.bat tools/windows/Uninstall.bat tools/windows/README-Windows.txt "$PKG/"
# icon: PNG-in-ICO (valid since Windows Vista)
if command -v sips >/dev/null; then sips -z 256 256 src/cdjtool/AppIcon.png --out "$W/icon256.png" >/dev/null; else cp src/cdjtool/AppIcon.png "$W/icon256.png"; fi
python3 - "$W/icon256.png" "$APP/CDJ-Tool.ico" <<'PY'
import struct, sys
png = open(sys.argv[1], "rb").read()
open(sys.argv[2], "wb").write(struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22) + png)
PY
find "$PKG" -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
mkdir -p dist; rm -f "dist/CDJ-Tool-$VER-windows.zip"
(cd "$W" && zip -qr "../../dist/CDJ-Tool-$VER-windows.zip" "CDJ-Tool-$VER-windows")
shasum -a 256 "dist/CDJ-Tool-$VER-windows.zip" | tee -a dist/SHA256SUMS.txt
ls -lh "dist/CDJ-Tool-$VER-windows.zip"
