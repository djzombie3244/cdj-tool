#!/bin/bash
# CDJ Tool - publish this folder as a GitHub repository (and optionally a release).
#   tools/publish-github.sh [repo-name] [public|private]
# Needs git; uses the GitHub CLI (`brew install gh`) when available.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="${1:-cdj-tool}"
VIS="${2:-public}"
VERSION="$(grep -m1 '^## ' CHANGELOG.md | sed -E 's/^## ([0-9][0-9.]*).*/\1/')"

command -v git >/dev/null || { echo "git is not installed (run: xcode-select --install)"; exit 1; }

echo "==> Checking for personal data"
if grep -rla -e "Dj_Zombie" -e "gcred" src/cdjtool/wzorzec >/dev/null 2>&1; then
  echo "The template contains personal data. Aborting."; exit 1
fi
if grep -rIl --exclude-dir=.git --exclude-dir=dist -e "$HOME" . >/dev/null 2>&1; then
  echo "Files mentioning your home folder path were found:"
  grep -rIl --exclude-dir=.git --exclude-dir=dist -e "$HOME" . || true
  echo "Remove those mentions and run again."; exit 1
fi

if [ -z "$(git config user.name || true)" ]; then read -r -p "Your name for commits (e.g. Wiktor Bakowicz): " N; git config --global user.name "$N"; fi
if [ -z "$(git config user.email || true)" ]; then read -r -p "Your e-mail for commits (GitHub e-mail or noreply): " E; git config --global user.email "$E"; fi

[ -d .git ] || git init -b main
git add -A
git diff --cached --quiet || git commit -m "CDJ Tool $VERSION"

if command -v gh >/dev/null 2>&1; then
  gh auth status >/dev/null 2>&1 || gh auth login
  gh repo create "$REPO" "--$VIS" --source . --remote origin --push \
     --description "Prepare USB sticks for Pioneer CDJ-2000 on a Mac without rekordbox (offline macOS app) / Przygotuj pendrive dla CDJ-2000 na Macu bez rekordboxa"
  gh repo edit --add-topic cdj-2000,rekordbox,dj,macos,usb,beatgrid,pioneer || true
  if ls dist/CDJ-Tool-"$VERSION".dmg >/dev/null 2>&1; then
    read -r -p "Create release v$VERSION with the files from dist/? [y/N] " A
    if [ "$A" = "y" ] || [ "$A" = "Y" ]; then
      gh release create "v$VERSION" dist/CDJ-Tool-"$VERSION".dmg dist/CDJ-Tool-"$VERSION".zip dist/SHA256SUMS.txt \
         --title "CDJ Tool $VERSION" --notes-file CHANGELOG.md
    fi
  else
    echo "No installer in dist/ yet. Build it with tools/build_installer.sh, then run:"
    echo "  gh release create v$VERSION dist/CDJ-Tool-$VERSION.dmg dist/CDJ-Tool-$VERSION.zip dist/SHA256SUMS.txt --title \"CDJ Tool $VERSION\" --notes-file CHANGELOG.md"
  fi
  gh repo view --web || true
else
  cat <<MSG

GitHub CLI (gh) not found. Manual way:
 1. Create an EMPTY repository named "$REPO" at https://github.com/new (no README, no license).
 2. Run:
      git remote add origin https://github.com/<your-login>/$REPO.git
      git push -u origin main
 3. Attach dist/CDJ-Tool-$VERSION.dmg to a release on the repository page (Releases -> Draft a new release).
Or install gh:  brew install gh   and run this script again.
MSG
fi
