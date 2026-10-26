#!/bin/bash
# Downloads (once) the Beat This! ONNX model (MIT) and ONNX Runtime wheels into build/models and build/ort.
# Model: https://huggingface.co/musetric/beat-this-onnx (export of CPJKU/beat_this final0, verified against PyTorch).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
M="$ROOT/build/models"; O="$ROOT/build/ort"
mkdir -p "$M" "$O"
if [ ! -s "$M/beat_this.onnx" ]; then
  if [ -s "$ROOT/build/btfinal/beat_this.onnx" ]; then cp "$ROOT/build/btfinal/beat_this.onnx" "$M/beat_this.onnx"
  else curl -fL -o "$M/beat_this.onnx" https://huggingface.co/musetric/beat-this-onnx/resolve/main/beat_this.onnx; fi
fi
[ -s "$M/mel-filterbank.bin" ] || curl -fL -o "$M/mel-filterbank.bin" https://huggingface.co/musetric/beat-this-onnx/resolve/main/mel-filterbank.bin
SZ=$(stat -f%z "$M/beat_this.onnx" 2>/dev/null || stat -c%s "$M/beat_this.onnx")
[ "$SZ" -gt 150000000 ] || { echo "model file looks truncated ($SZ bytes)"; exit 1; }
for P in macosx_11_0_universal2 win_amd64; do
  if ! ls "$O"/onnxruntime-1.19.2-cp39-cp39-*"$P"*.whl >/dev/null 2>&1; then
    python3 -m pip download onnxruntime==1.19.2 --no-deps --only-binary=:all: --platform "$P" \
      --python-version 3.9 --implementation cp --abi cp39 -d "$O"
  fi
done
ls -l "$M" "$O"
