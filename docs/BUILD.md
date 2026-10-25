# Building / Budowanie

## Run from source (development) / Uruchomienie ze źródeł

```bash
pip install -r requirements-dev.txt
python -m pytest                       # tests / testy
CDJ_DEV=1 python3 src/cdjtool/app.py   # opens the interface in your browser on http://127.0.0.1:8766
```

Dev mode uses the Python you run it with (NumPy and SciPy must be installed) and needs `ffmpeg` in `PATH` (or set `CDJ_FFMPEG=/path/to/ffmpeg`). / Tryb deweloperski używa Pythona, którym go uruchamiasz (potrzebne NumPy i SciPy) i wymaga `ffmpeg` w `PATH`
(lub zmiennej `CDJ_FFMPEG`).

## Layout of the macOS app / Budowa aplikacji

```
CDJ Tool.app/Contents
  MacOS/CDJTool                    Swift launcher (src/cdjtool/CDJToolApp.swift): window + WKWebView
  Frameworks/Python3Bundled.framework   relocatable Python 3.9
  Resources/*.py, ui.html, config.json  the backend (src/cdjtool)
  Resources/wzorzec/               neutral USB template
  Resources/python/site-packages/  numpy, scipy, imageio_ffmpeg (+ ffmpeg-macos-aarch64-v7.1)
  Resources/Licenses/              licenses and the ffmpeg source archive
```

The launcher starts the bundled Python with `app.py`, which serves the UI on a random local port
(guarded by a per-run token) and runs `worker.py` for each export.

## Build the installer (DMG) / Budowanie instalatora

Run on a Mac (Apple Silicon) that has the app bundle prepared as above:

```bash
tools/build_installer.sh --app "/path/to/CDJ Tool.app" --version 1.0.0
```

It copies the bundle, injects the sources from `src/cdjtool`, installs the clean template, sets the
version and the author in `Info.plist`, signs the app (ad-hoc, or with your Developer ID if you set
`CDJ_SIGN_IDENTITY`), runs a self-test, creates `dist/CDJ-Tool-<version>.dmg` and `.zip` and a `SHA256SUMS.txt`.

## Rebuild ffmpeg / Przebudowa ffmpeg

```bash
tools/build_ffmpeg.sh "/path/to/ffmpeg-7.1-source.tar.xz" ffmpeg-macos-aarch64-v7.1
```

Needs Xcode command line tools (`xcode-select --install`). The configuration is the LGPL-only minimal
one described in `THIRD_PARTY_NOTICES.md`.

## Real signing and notarization / Prawdziwy podpis i notaryzacja

With a paid Apple Developer account:

```bash
export CDJ_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
tools/build_installer.sh --app "/path/to/CDJ Tool.app" --version 1.0.0
xcrun notarytool submit dist/CDJ-Tool-1.0.0.dmg --keychain-profile "notary" --wait
xcrun stapler staple dist/CDJ-Tool-1.0.0.dmg
```

Then users will not see the Gatekeeper warning. / Wtedy użytkownicy nie zobaczą ostrzeżenia Gatekeepera.
