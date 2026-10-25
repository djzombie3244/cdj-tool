# CDJ Tool

**Przygotuj pendrive dla odtwarzaczy Pioneer CDJ-2000 na Macu, bez rekordboxa.**
🇬🇧 [English version](README.md)

CDJ Tool analizuje utwory (BPM, siatka beatów, waveformy) i zapisuje pendrive, który zwykły **CDJ-2000**
wczytuje tak jak eksport z rekordboxa: baza utworów (`export.pdb`), pliki analizy (`ANLZ0000.DAT` / `.EXT`)
i kopie plików audio. To mała, natywna aplikacja na macOS, która działa całkowicie **offline**.

> **Status:** wersja 1.0.0. Sprawdzona na prawdziwym sprzęcie, CDJ-2000 (nie NXS), dla utworów MP3:
> baza, waveformy, wczytywanie. Obsługa AAC, WAV i AIFF jest nowsza i nadal testowana na sprzęcie
> (zobacz [Znane ograniczenia](#znane-ograniczenia)). Daj znać, co działa u Ciebie.

## Funkcje

- Analiza plików **MP3, AAC (.m4a), WAV i AIFF** (formaty, które gra CDJ-2000; FLAC i ALAC odtwarzacz nie obsługuje, więc są pomijane).
- Wykrywanie **BPM i siatki beatów**, **waveform przeglądowy i szczegółowy** (CDJ-2000 potrzebuje pliku `.EXT`, żeby pokazać waveform).
- **Dopisywanie do istniejącej biblioteki** na pendrive zamiast nadpisywania; playlisty, gatunki i wcześniejsza analiza zostają. Opcjonalne pełne zastąpienie (z kopią zapasową).
- Równoległa analiza dopasowana do szybkości dysku (maksymalnie 80 % zmierzonej przepustowości) z limitem pamięci RAM oraz szacowanym czasem do końca.
- Opcjonalne **formatowanie pendrive do FAT32 (MBR)** pod CDJ-2000.
- Niezawodny przycisk **wysuwania**.
- Interfejs w **6 językach**: Polski, English, Deutsch, Français, Español, Italiano.
- Bez konta, bez dostępu do sieci, bez telemetrii.

## Wymagania

- macOS 14 (Sonoma) lub nowszy, **Apple Silicon** (M1 lub nowszy).
- Pendrive w FAT32 (aplikacja potrafi go sformatować) i CDJ-2000.

## Instalacja (offline)

1. Pobierz `CDJ-Tool-<wersja>.dmg` ze strony [Releases](../../releases).
2. Otwórz plik i przeciągnij **CDJ Tool** do **Programy** (Applications).
3. Pierwsze uruchomienie wymaga jednego dodatkowego kroku, bo aplikacja nie jest notaryzowana przez Apple (poniżej).

Pełna instrukcja, obejście Gatekeepera i odinstalowanie: [docs/INSTALL.pl.md](docs/INSTALL.pl.md).

## Użycie

1. Wybierz folder z utworami i pendrive.
2. Poczekaj na automatyczny pomiar szybkości, w razie potrzeby wpisz nazwę playlisty.
3. Kliknij **Analizuj i zapisz na pendrive**, potem **Wysuń** i włóż pendrive do CDJ-2000.

Domyślnie nowe utwory są **dopisywane** do biblioteki, która już jest na pendrive. Zaznacz
*Zastąp całą dotychczasową zawartość*, aby zacząć od zera (stary folder `PIONEER` zostanie wcześniej
skopiowany do `Dokumenty/CDJ-Tool-kopie`).

## Znane ograniczenia

- Siatka beatów jest liczona heurystycznie (początek uderzenia stopy + histogram tempa). Dobrze radzi sobie
  z większością utworów four-on-the-floor, ale **nie jest identyczna z rekordboxem**; zawsze sprawdź ją na odtwarzaczu.
- Utwory ze zmiennym tempem i bez wyraźnej stopy nie są obsługiwane dobrze.
- Zapis bazy obsługuje około 10 000 utworów na pendrive; większe zbiory trzeba podzielić.
- Sprawdzone tylko na CDJ-2000. Inne modele (NXS, NXS2, CDJ-3000) mogą wymagać dodatkowych plików, których to narzędzie nie zapisuje.
- 24-bitowe pliki WAV z nagłówkiem `WAVE_FORMAT_EXTENSIBLE` mogą nie wczytać się na CDJ-2000; użyj zwykłego PCM.
- Aplikacja jest podpisana ad-hoc, nie notaryzowana.

## Jak to działa

Formaty plików poznano metodą inżynierii wstecznej: eksporty z rekordboxa porównywano z wynikiem tego
narzędzia, fragment po fragmencie. Notatki: [docs/FORMAT.pl.md](docs/FORMAT.pl.md).

## Budowanie ze źródeł

Zobacz [docs/BUILD.md](docs/BUILD.md). W skrócie: `src/cdjtool` to backend w Pythonie 3.9+ (NumPy, SciPy, ffmpeg)
z interfejsem w HTML, opakowany małym launcherem w Swifcie (`CDJToolApp.swift`).

```bash
pip install -r requirements-dev.txt
python -m pytest
```

## Zastrzeżenie

CDJ Tool to niezależny, nieoficjalny projekt. **Nie jest powiązany z firmami Pioneer DJ i AlphaTheta ani z zespołem
rekordbox, nie jest przez nie wspierany ani zatwierdzony.** *Pioneer DJ*, *CDJ* i *rekordbox* są znakami towarowymi
ich właścicieli i występują tu wyłącznie dla opisu zgodności. Narzędzie zapisuje pliki w formacie poznanym w ramach
badań nad interoperacyjnością; używasz go na własne ryzyko, **testuj na pendrive, który możesz wyczyścić**.
Oprogramowanie jest dostarczane „tak jak jest”, bez gwarancji (zobacz [LICENSE](LICENSE)).

## Autor

© 2026 **Wiktor Bakowicz**. Licencja [MIT](LICENSE). Składniki osób trzecich: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
Powstało z pomocą asystenta AI (Claude, Anthropic).
