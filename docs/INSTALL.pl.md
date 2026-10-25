# Instalacja CDJ Tool (offline)

🇬🇧 [In English](INSTALL.md)

Aplikacja zawiera wszystko, czego potrzebuje (Python, NumPy, SciPy, ffmpeg). **Połączenie z internetem nie jest
potrzebne**, ani do instalacji, ani do działania.

## Wymagania

- Mac z **Apple Silicon** (M1, M2, M3, M4 ...) i **macOS 14 Sonoma lub nowszy**.
- Komputery Mac z procesorem Intel nie są obsługiwane przez wydaną aplikację.

## Wariant A: przeciągnij i upuść

1. Skopiuj `CDJ-Tool-<wersja>.dmg` na Maca (pobranie, pendrive, AirDrop...).
2. Kliknij dwukrotnie plik DMG. Przeciągnij **CDJ Tool** na skrót **Applications** (Programy).
3. Wysuń obraz DMG.
4. Otwórz **Programy**, kliknij **CDJ Tool prawym przyciskiem, wybierz Otwórz** i potwierdź **Otwórz** w oknie.
   Robisz to tylko raz.

## Wariant B: jeden skrypt

W DMG jest plik `Install CDJ Tool.command`. Kliknij go prawym przyciskiem i wybierz **Otwórz**. Skopiuje aplikację do
`/Applications` i usunie znacznik kwarantanny z pobrania, dzięki czemu aplikacja uruchomi się normalnie.

## Dlaczego macOS ostrzega przed aplikacją?

Aplikacja ma podpis *ad-hoc*, a nie podpis Apple Developer ID, i nie jest notaryzowana. Notaryzacja wymaga płatnego
konta Apple Developer. Dlatego macOS przy pierwszym uruchomieniu pokazuje komunikat, że nie można zweryfikować
programisty.

Jeśli prawy przycisk → **Otwórz** nie pokazuje przycisku, użyj jednego z tych sposobów:

- **Ustawienia systemowe → Prywatność i ochrona**, przewiń do komunikatu o CDJ Tool i kliknij **Otwórz mimo to**.
- Albo w Terminalu: `xattr -dr com.apple.quarantine "/Applications/CDJ Tool.app"`

Sprawdź pobrany plik: na stronie wydania jest suma kontrolna SHA-256. Porównaj ją z wynikiem
`shasum -a 256 CDJ-Tool-<wersja>.dmg`.

## Pierwsze uruchomienie

Pierwsze uruchomienie trwa kilka sekund, bo aplikacja sprawdza swoje składniki. Język wybierzesz w prawym górnym
rogu. Potem wykonaj 4 kroki w oknie.

## Gdzie aplikacja trzyma pliki

| Co | Gdzie |
|---|---|
| Dziennik | `~/Library/Application Support/CDJ-Tool/CDJ-Tool.log` |
| Kopie zapasowe zastąpionego folderu `PIONEER` lub bazy | `~/Documents/CDJ-Tool-kopie/` (Dokumenty) |

## Odinstalowanie

Przeciągnij **CDJ Tool** z Programów do Kosza i, jeśli chcesz, usuń `~/Library/Application Support/CDJ-Tool`.
Kopie zapasowe w `~/Documents/CDJ-Tool-kopie` możesz zostawić lub usunąć.

## Rozwiązywanie problemów

- *Okno jest puste / aplikacja się zamyka:* otwórz plik dziennika powyżej i dołącz go do zgłoszenia błędu.
- *Nie da się wysunąć pendrive:* poczekaj kilka sekund po zakończeniu zapisu i naciśnij **Wysuń** ponownie; aplikacja blokuje indeksowanie pendrive przez Spotlight.
- *Odtwarzacz pokazuje kod błędu:* zanotuj kod i format utworu i zgłoś problem (szablon w `.github/ISSUE_TEMPLATE`).
