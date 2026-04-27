# schedule-opt (gurobipy)

Skrypt `scheduler.py` optymalizuje grafik dyżurów na podstawie CSV wejściowego.

## Proponowany format CSV

Separator: `;`

Minimalnie wymagane kolumny:
- `data` (lub `date`) – data dnia,
- `slot` (np. `0-8`, `8-16`, `16-24`) **albo** para kolumn `start` i `end`,
- dowolna liczba kolumn osób (np. `AB`, `CD`, `EF`) – nazwy kolumn są dynamiczne.

Przykład nagłówka:

```csv
data;slot;AB;CD;EF
2026-05-01;0-8;1;2;
2026-05-01;8-16;P;0;
2026-05-01;16-24;1;;2
```

## Znaczenie wartości wejściowych

- `1` – pełna dostępność (może być 1-szy lub 2-gi),
- `2` – preferuje bycie 2-gim, ale może być 1-szym z karą,
- puste – domyślnie brak chęci dyżuru; może być użyte tylko awaryjnie jako `1`, gdy **nikt** nie ma `1/2/P` w tym slocie,
- `0` – blokada (urlop/zakaz), ale możliwe naruszenie przez slack z bardzo dużą karą,
- `P` – traktowane jak `1` (przydatne dla slotu 8-16 w dni robocze).

## Konfiguracja kar (INI)

Wagi kar są wydzielone do pliku `penalties.ini` (sekcja `[penalties]`).
Możesz uruchomić skrypt z własnym plikiem:

```bash
python scheduler.py input.csv output.csv --penalties moja_konfiguracja.ini
```

## Uruchomienie

```bash
python scheduler.py input.csv output.csv --mip-gap 0.01
```

Przydatne opcje:
- `--penalties penalties.ini` – własne wagi kar,
- `--verbose` – bardziej szczegółowe logowanie (DEBUG) kroków działania.

## Co optymalizujemy

Model dąży do:
1. Obsady każdego slotu co najmniej 1 osobą (najwyższy priorytet).
2. Maksymalnie 2 osób na slot i maks. 2 osób w całej „dobie dyżurowej” (8:00–8:00) — bez slacków dla tych limitów.
3. Respektowania preferencji `0/1/2/puste` przez system kar.
4. Ograniczeń weekendowo-niedzielnych:
   - każda osoba ma mieć co najmniej jedną wolną niedzielę (miękko),
   - w jednym weekendzie dana osoba nie powinna mieć dyżuru i w sobotę, i w niedzielę.
5. Stabilności obsady w obrębie doby dyżurowej (kara za „piłę”).
6. Sprawiedliwości: wyrównywania liczby dyżurów i liczby pozycji `1` między osobami.
7. Preferowania większej liczby pozycji `2` i mniejszej liczby pozycji `1` globalnie.

## Wynik

Plik wyjściowy ma taki sam układ jak wejściowy. W kolumnach osób pojawiają się:
- `1` – pierwsza kolejność,
- `2` – druga kolejność,
- puste – brak dyżuru.

Dodatkowo skrypt wypisuje:
- raport użytych slacków (`slack_* > 0`),
- podsumowanie per osoba: liczba `1`, liczba `2`, liczba dni dyżurowych.
