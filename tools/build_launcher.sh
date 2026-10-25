#!/bin/bash
# Recompiles the Swift launcher (needs Xcode Command Line Tools: xcode-select --install)
# and installs it into the given app bundle, then re-signs the bundle (ad-hoc).
# Usage: tools/build_launcher.sh "/Applications/CDJ Tool.app" [more.app ...]
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
command -v swiftc >/dev/null || { echo "swiftc not found. Run: xcode-select --install"; exit 1; }
[ $# -ge 1 ] || { echo "Usage: $0 \"/path/CDJ Tool.app\" ..."; exit 1; }
OUT="$(mktemp -d /tmp/cdj-launcher.XXXXXX)"; trap 'rm -rf "$OUT"' EXIT
swiftc -O -target arm64-apple-macos14.0 "$HERE/src/cdjtool/CDJToolApp.swift" -o "$OUT/CDJTool" -framework AppKit -framework WebKit
for APP in "$@"; do
  [ -f "$APP/Contents/MacOS/CDJTool" ] || { echo "Not a CDJ Tool bundle: $APP"; exit 1; }
  cp "$OUT/CDJTool" "$APP/Contents/MacOS/CDJTool"
  # repair the Python framework layout (symlinks) so that codesign accepts the bundle
  for fw in "$APP"/Contents/Frameworks/*.framework; do
    [ -d "$fw/Versions" ] || continue
    ver="$(ls "$fw/Versions" | grep -v '^Current$' | head -1)"
    [ -n "$ver" ] || continue
    rm -rf "$fw/Versions/Current"; ln -s "$ver" "$fw/Versions/Current"
    for item in $(ls "$fw" | grep -v '^Versions$'); do rm -rf "$fw/$item"; ln -s "Versions/Current/$item" "$fw/$item"; done
    rm -rf "$fw/Versions/$ver/_CodeSignature"
  done
  xattr -cr "$APP"; codesign --force --deep --sign - "$APP"
  codesign --verify --deep --strict "$APP"
  echo "Launcher installed: $APP"
done
