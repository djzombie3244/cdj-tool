# Installing CDJ Tool (offline)

🇵🇱 [Po polsku](INSTALL.pl.md)

The app bundle contains everything it needs (Python, NumPy, SciPy, ffmpeg). **No internet connection is
required**, neither to install nor to run it.

## Requirements

- Mac with **Apple Silicon** (M1, M2, M3, M4 ...) and **macOS 14 Sonoma or newer**.
- Intel Macs are not supported by the released app.

## Option A: drag and drop

1. Copy `CDJ-Tool-<version>.dmg` to the Mac (download, USB stick, AirDrop...).
2. Double-click the DMG. Drag **CDJ Tool** onto the **Applications** shortcut.
3. Eject the DMG.
4. Open **Applications**, **right-click CDJ Tool, choose Open**, and confirm **Open** in the dialog.
   You only need to do this once.

## Option B: one script

Inside the DMG is `Install CDJ Tool.command`. Right-click it, choose **Open**. It copies the app to
`/Applications` and removes the download quarantine flag, so the app starts normally.

## Why does macOS warn about the app?

The app is signed with an *ad-hoc* signature, not with an Apple Developer ID, and is not notarized.
Notarization requires a paid Apple Developer account. macOS therefore shows
"CDJ Tool cannot be opened because the developer cannot be verified" on the first start.

If a right-click **Open** does not offer the button, use one of these:

- **System Settings → Privacy & Security**, scroll down to the message about CDJ Tool and click **Open Anyway**.
- Or in Terminal: `xattr -dr com.apple.quarantine "/Applications/CDJ Tool.app"`

Verify what you downloaded: the release page lists a SHA-256 checksum. Compare with
`shasum -a 256 CDJ-Tool-<version>.dmg`.

## First start

The first start takes a few seconds while the app checks its components. Choose the language in the
top-right corner. Then follow the 4 steps in the window.

## Where the app keeps files

| What | Where |
|---|---|
| Log | `~/Library/Application Support/CDJ-Tool/CDJ-Tool.log` |
| Backups of a replaced `PIONEER` folder or of the database | `~/Documents/CDJ-Tool-kopie/` |

## Uninstall

Drag **CDJ Tool** from Applications to the Trash and, if you like, delete
`~/Library/Application Support/CDJ-Tool`. Backups in `~/Documents/CDJ-Tool-kopie` are yours to keep or delete.

## Troubleshooting

- *The window stays empty / the app closes:* open the log file above and send it with a bug report.
- *The stick cannot be ejected:* wait a few seconds after the write finishes and press **Eject** again; the app prevents Spotlight from indexing the stick.
- *The player shows an error code:* note the code and the track format and open an issue (the template in `.github/ISSUE_TEMPLATE`).
