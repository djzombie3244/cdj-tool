# Notatki o formacie pendrive

🇬🇧 [In English](FORMAT.md)

Te notatki opisują, co zapisuje CDJ Tool i co zostało sprawdzone. Pochodzą z porównywania eksportów
z rekordboxa z wynikiem tego narzędzia, fragment po fragmencie (podmieniasz jeden plik, tabelę albo stronę
w działającym eksporcie i sprawdzasz, czy odtwarzacz nadal go wczytuje). Szersze tło: publiczna analiza
Deep Symmetry <https://djl-analysis.deepsymmetry.org/>.

**Poziom weryfikacji:** „sprawdzone” znaczy przetestowane na prawdziwym CDJ-2000 (nie NXS). Reszta pochodzi z eksportów
rekordboxa i publicznej dokumentacji.

## Układ pendrive

```
/PIONEER/rekordbox/export.pdb              baza utworów
/PIONEER/USBANLZ/Pxxx/XXXXXXXX/ANLZ0000.DAT   siatka beatów, waveform przeglądowy
/PIONEER/USBANLZ/Pxxx/XXXXXXXX/ANLZ0000.EXT   waveform szczegółowy (PWV3)
/PIONEER/{DEVSETTING,MYSETTING,MYSETTING2,DJMMYSETTING}.DAT, CDJ/, USBANLZ/USBMNG.DAT   ustawienia (z szablonu)
/Contents/<artysta>/<album>/<plik>         kopie plików audio
```

- Nazwy folderów i plików są w Unicode **NFC** (macOS zwraca NFD); człon nazwy pliku jest ucinany do **44 znaków** (sprawdzone: hash ścieżki i zapisana ścieżka muszą się zgadzać).
- Nazwa folderu analizy pochodzi z hasha ścieżki utworu (UTF-16BE, mnożenie i dodawanie, modulo 200003; `Pxxx` wybierane z bitów hasha). Odtwarzacz czyta ścieżkę z bazy, więc przy kolizji dwóch utworów działa inny wolny folder; CDJ Tool robi to automatycznie.

## export.pdb

- Strony po 4096 bajtów. Strona 0 to nagłówek pliku z listą tabel (typ, pierwsza strona, ostatnia strona, strona „kandydat na pustą”). Każda tabela to łańcuch: strona indeksowa i strony danych.
- Używane tabele: 0 utwory, 1 gatunki, 2 artyści, 3 albumy, 4 wytwórnie, 5 tonacje, 6 kolory, 7 drzewo playlist, 8 wpisy playlist. Tabele 1, 4, 5, 6 i pozostałe są kopiowane z szablonu bez zmian. Tabele 0, 2, 3, 7, 8 są budowane od nowa.
- Strona danych: 40-bajtowy nagłówek, sterta wierszy, a od końca strony rośnie indeks każdej grupy 16 wierszy (offsety wierszy w odwrotnej kolejności, maska obecności i słowo flag).
- Ostatnia strona danych tabeli ma flagę `0x24` i **nie** jest wpisana do strony indeksowej tabeli; wszystkie wcześniejsze strony danych mają `0x34` i są wpisane (wpisy `strona << 3`; wpis przed ogonem ma flagę `3`). Sprawdzone.
- Istniejące strony tabeli są używane ponownie dla nowej zawartości (bez osieroconych stron). Nieużywane strony są zerowane.
- Strona indeksowa mieści około 1009 stron danych, czyli mniej więcej 10 000 utworów. CDJ Tool odmawia zapisu większej liczby.
- `index_shift` wiersza to `0x20 * (numer wiersza na stronie)`.

### Wiersz utworu

94 stałe bajty (little endian), potem 21 offsetów napisów (`uint16`, względem początku wiersza) i same napisy.
Pola stałe po kolei: subtype (`0x24`), index_shift, bitmask, sample_rate, composer_id, file_size, suma kontrolna (używane jest CRC32 ścieżki), dwa nieznane słowa, artwork_id, key_id, original_artist_id, label_id, remixer_id, bitrate, track_number, tempo (BPM × 100), genre_id, album_id, artist_id, id, disc, play_count, year, sample_depth, duration (s), nieznane, color, rating, file_type, nieznane.

- `file_type`: 1 dla MP3, 4 dla M4A/AAC, 11 dla WAV, 12 dla AIFF. Sprawdzone dla MP3; pozostałe wartości pochodzą z dokumentacji i są testowane.
- Napisy (`DeviceSQL`): krótki ASCII ma 1-bajtowy nagłówek `((len+1) << 1) | 1`; długi ASCII `0x40` i UTF-16LE `0x90` mają 4-bajtowy nagłówek z długością całkowitą.
- **Sprawdzone, ważne:** każdy *długi* napis (`0x40` lub `0x90`) musi zaczynać się od offsetu podzielnego przez 4 w obrębie wiersza. rekordbox dopycha zerami. Bez dopełnienia CDJ-2000 pokazuje **E-8709** dla utworów z tekstem spoza ASCII (np. litery z akcentami w tytule).

## Pliki ANLZ

Sekcje z tagami, big endian. Plik zaczyna się od `PMAI` (długość nagłówka 28, długość pliku).

| Plik | Zapisywane tagi |
|---|---|
| `ANLZ0000.DAT` | `PPTH` (ścieżka pliku audio, UTF-16BE), `PVBR` (zaślepka tabeli VBR + liczba próbek), `PQTZ` (siatka beatów: numer beatu, tempo, czas w ms), `PCOB` (puste listy cue pamięci i hot cue), `PWAV` (400-bajtowy waveform przeglądowy), `PWV2` (100-bajtowy mini waveform) |
| `ANLZ0000.EXT` | `PPTH`, `PWV3` (waveform szczegółowy, **150 kolumn na sekundę**, po 1 bajcie: 3 bity jasności, 5 bitów wysokości), listy `PCOB`, `PQT2` (siatka beatów z czasami w ułamkach milisekundy; dzięki niej BPM pokazuje się od razu, bajt w bajt jak w eksporcie Rekordboxa) |

- **Sprawdzone:** zwykły CDJ-2000 (nie NXS) potrzebuje pliku `.EXT` z `PWV3`; bez niego odtwarzacz pokazuje „no waveform data, analyse in rekordbox”, mimo że `PWAV` istnieje.
- Dla plików innych niż MP3 liczba próbek w `PVBR` to liczba próbek; czy odtwarzacz wymaga czegoś więcej dla AAC/WAV/AIFF, jest nadal weryfikowane.

## Siatka beatów

CDJ Tool nie kopiuje siatki rekordboxa, liczy własną:

1. Dekodowanie do mono 22,05 kHz, filtr dolnoprzepustowy (zakres stopy).
2. Obwiednia początków uderzeń; tempo z profilu złożonego zwiniętego modulo okres beatu (przedziały 1 ms), z preferencją dla całkowitych BPM.
3. Pierwszy beat to początek narastania stopy (< 120 Hz). Stałe w `config.json` uwzględniają przesunięcie używane przez odtwarzacz.

Faza z rekordboxa nie jest idealnym wzorcem (różni się od rzeczywistego początku stopy o mniej więcej −80..+30 ms),
więc „identyczna z rekordboxem” nie jest celem. Sprawdzaj każdą siatkę na odtwarzaczu.

## Przygotowanie pendrive

CDJ-2000 czyta FAT32 z tablicą partycji MBR. Opcjonalne formatowanie uruchamia `diskutil eraseDisk FAT32 <NAZWA> MBR <dysk>` po przepisaniu nazwy woluminu, wyłącznie dla zewnętrznych dysków fizycznych do 2 TB.

## Otwarte pytania

- Zachowanie innych odtwarzaczy (NXS, NXS2, CDJ-3000, XDJ), które czytają dodatkowe pliki (`exportExt.pdb`, `exportLibrary.db`).
- Czy odtwarzacz wymaga określonej zawartości `PVBR` dla plików innych niż MP3.
- 24-bitowy WAV `WAVE_FORMAT_EXTENSIBLE` na CDJ-2000 (prawdopodobnie nieakceptowany).
