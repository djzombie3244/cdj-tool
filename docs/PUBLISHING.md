# Publishing on GitHub / Publikacja na GitHub

## English

1. Create a free account at https://github.com (if you do not have one).
2. Install the GitHub CLI and log in: `brew install gh && gh auth login` (choose GitHub.com, HTTPS, browser login).
3. In the project folder run `tools/publish-github.sh cdj-tool`. It creates the repository, makes the first commit under your git identity and pushes.
   Without the CLI: create an empty repository on github.com, then
   `git init -b main && git add . && git commit -m "CDJ Tool 1.0.0" && git remote add origin https://github.com/<you>/cdj-tool.git && git push -u origin main`.
4. Build the installer (`tools/build_installer.sh`) and attach it to a release:
   `gh release create v1.0.0 dist/CDJ-Tool-1.0.0.dmg dist/CDJ-Tool-1.0.0.zip dist/SHA256SUMS.txt --title "CDJ Tool 1.0.0" --notes-file CHANGELOG.md`.
   Do not commit the DMG into the repository, attach it to the release.
5. In the repository **Settings → General** add a description and topics (`cdj-2000`, `rekordbox`, `dj`, `macos`, `usb`).

## Polski

1. Załóż darmowe konto na https://github.com (jeśli go nie masz).
2. Zainstaluj GitHub CLI i zaloguj się: `brew install gh && gh auth login` (GitHub.com, HTTPS, logowanie przez przeglądarkę).
3. W folderze projektu uruchom `tools/publish-github.sh cdj-tool`. Skrypt tworzy repozytorium, robi pierwszy commit z Twoją tożsamością gita i wysyła kod.
   Bez CLI: utwórz puste repozytorium na github.com, potem
   `git init -b main && git add . && git commit -m "CDJ Tool 1.0.0" && git remote add origin https://github.com/<ty>/cdj-tool.git && git push -u origin main`.
4. Zbuduj instalator (`tools/build_installer.sh`) i dołącz go do wydania:
   `gh release create v1.0.0 dist/CDJ-Tool-1.0.0.dmg dist/CDJ-Tool-1.0.0.zip dist/SHA256SUMS.txt --title "CDJ Tool 1.0.0" --notes-file CHANGELOG.md`.
   Nie wrzucaj DMG do repozytorium, dołącz go do wydania.
5. W **Settings → General** repozytorium dodaj opis i tematy (`cdj-2000`, `rekordbox`, `dj`, `macos`, `usb`).

Before publishing / Przed publikacją: check that no personal data is in the folder (`git grep -i "/Users/"`), review `README.md`.
