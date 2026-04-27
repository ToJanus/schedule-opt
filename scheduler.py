#!/usr/bin/env python3
from __future__ import annotations

import argparse
import configparser
import csv
import datetime as dt
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

NON_PERSON_COLUMNS = {
    "data",
    "date",
    "dzien",
    "dzień",
    "slot",
    "przedzial",
    "przedział",
    "start",
    "end",
    "godz_od",
    "godz_do",
    "typ_dnia",
}

DEFAULT_PENALTIES = {
    "uncovered_slot": 10000.0,
    "zero_violation": 3000.0,
    "blank_fallback_first": 180.0,
    "pref2_as_first": 120.0,
    "no_free_sunday": 220.0,
    "weekend_both_days": 220.0,
    "change_between_slots": 35.0,
    "order_change_between_slots": 55.0,
    "fair_total_difference": 25.0,
    "fair_first_difference": 35.0,
    "first_assignment": 8.0,
    "second_assignment": -6.0,
}

LOGGER = logging.getLogger("schedule_opt")


@dataclass
class SlotRow:
    """Reprezentuje jeden wiersz wejścia: konkretny dzień i przedział godzin."""

    idx: int
    raw: dict
    date: dt.date
    start_hour: int
    end_hour: int
    duty_day: dt.date


def normalize(value: str) -> str:
    """Normalizuje tekst (trim + lowercase) do porównań nazw kolumn."""

    return (value or "").strip().lower()


def parse_date(value: str) -> dt.date:
    """Parsuje datę z kilku wspieranych formatów (YYYY-MM-DD, DD.MM.YYYY, ...)."""

    value = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Nie mogę sparsować daty: '{value}'")


def parse_hours(row: dict) -> Tuple[int, int]:
    """Odczytuje przedział godzin ze slot/start-end/godz_od-godz_do."""

    def parse_hour_value(value: str) -> int:
        token = (value or "").strip()
        if not token:
            raise ValueError("Pusta wartość godziny.")

        if ":" in token:
            hour_part, minute_part = token.split(":", 1)
            if not hour_part.isdigit() or not minute_part.isdigit():
                raise ValueError(f"Niepoprawny format godziny: '{value}'")
            hour = int(hour_part)
            minute = int(minute_part)
            if hour == 24 and minute == 0:
                return 24
            if hour < 0 or hour > 23 or minute != 0:
                raise ValueError(f"Nieobsługiwana godzina: '{value}'. Dozwolone pełne godziny HH:00.")
            return hour

        return int(token)

    normalized_row = {normalize(k): (v or "").strip() for k, v in row.items()}
    if normalized_row.get("start") and normalized_row.get("end"):
        return parse_hour_value(normalized_row["start"]), parse_hour_value(normalized_row["end"])
    if normalized_row.get("godz_od") and normalized_row.get("godz_do"):
        return parse_hour_value(normalized_row["godz_od"]), parse_hour_value(normalized_row["godz_do"])

    slot_text = normalized_row.get("slot") or normalized_row.get("przedzial") or normalized_row.get("przedział")
    if slot_text:
        clean = slot_text.replace(" ", "")
        if "-" in clean:
            start_str, end_str = clean.split("-", 1)
            return parse_hour_value(start_str), parse_hour_value(end_str)

    raise ValueError(
        "Brak informacji o przedziale godzin. Użyj kolumn (start,end) lub 'slot'/'przedzial' np. 0-8."
    )


def get_date_value(row: dict) -> str:
    """Zwraca wartość kolumny daty z obsługą aliasów data/date/dzien."""

    for key in row.keys():
        if normalize(key) in {"data", "date", "dzien", "dzień"}:
            return row[key]
    raise ValueError("Brak kolumny daty (oczekiwano 'data' albo 'date').")


def infer_people_columns(headers: List[str]) -> List[str]:
    """Wykrywa kolumny osób jako wszystkie kolumny nienależące do metadanych."""

    people_columns = [header for header in headers if normalize(header) not in NON_PERSON_COLUMNS]
    if not people_columns:
        raise ValueError("Nie znaleziono kolumn osób.")
    return people_columns


def pref_code(value: str) -> str:
    """Normalizuje komórkę preferencji do jednego z: 0/1/2/P/empty."""

    normalized = (value or "").strip().upper()
    if normalized in {"0", "1", "2", "P"}:
        return normalized
    return ""


def load_penalties(config_path: Path) -> Dict[str, float]:
    """Wczytuje wagi kar z pliku INI, z fallbackiem do domyślnych wartości."""

    penalties = dict(DEFAULT_PENALTIES)
    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")
    if "penalties" not in parser:
        LOGGER.warning("Brak sekcji [penalties] w %s. Używam wartości domyślnych.", config_path)
        return penalties

    for key in penalties:
        if key in parser["penalties"]:
            penalties[key] = parser["penalties"].getfloat(key)
    LOGGER.info("Wczytano konfigurację kar z %s", config_path)
    return penalties


def load_csv(input_path: Path, separator: str = ";"):
    """Wczytuje wejściowy CSV i zwraca dane wejściowe z mapą preferencji per slot/osoba."""

    with input_path.open("r", encoding="utf-8-sig", newline="") as file_handle:
        reader = csv.DictReader(file_handle, delimiter=separator)
        headers = reader.fieldnames or []
        people_columns = infer_people_columns(headers)

        slot_rows: List[SlotRow] = []
        input_rows: List[dict] = []
        preferences_by_slot_person: Dict[Tuple[int, str], str] = {}

        for slot_idx, input_row in enumerate(reader):
            date_value = parse_date(get_date_value(input_row))
            start_hour, end_hour = parse_hours(input_row)
            duty_day = date_value if start_hour >= 8 else date_value - dt.timedelta(days=1)

            slot_rows.append(
                SlotRow(
                    idx=slot_idx,
                    raw=input_row,
                    date=date_value,
                    start_hour=start_hour,
                    end_hour=end_hour,
                    duty_day=duty_day,
                )
            )
            input_rows.append(input_row)
            for person in people_columns:
                preferences_by_slot_person[(slot_idx, person)] = pref_code(input_row.get(person, ""))

    LOGGER.info(
        "Wczytano CSV: %s | sloty=%d | osoby=%d (%s)",
        input_path,
        len(slot_rows),
        len(people_columns),
        ", ".join(people_columns),
    )
    return headers, people_columns, slot_rows, input_rows, preferences_by_slot_person


def optimize_schedule(
    people_columns,
    slot_rows: List[SlotRow],
    preferences_by_slot_person,
    mip_gap: float,
    penalties,
    random_seed: int | None = None,
):
    """Buduje i rozwiązuje model Gurobi z miękkimi regułami i karami."""

    import gurobipy as gp
    from gurobipy import GRB

    LOGGER.info("Buduję model Gurobi: sloty=%d, osoby=%d", len(slot_rows), len(people_columns))
    model = gp.Model("dyzury")

    slot_ids = [row.idx for row in slot_rows]
    people = list(people_columns)
    duty_days = sorted({row.duty_day for row in slot_rows})

    row_by_id = {row.idx: row for row in slot_rows}
    slots_by_duty_day = {day: [row.idx for row in slot_rows if row.duty_day == day] for day in duty_days}

    x_first = model.addVars(slot_ids, people, vtype=GRB.BINARY, name="x_first")
    x_second = model.addVars(slot_ids, people, vtype=GRB.BINARY, name="x_second")

    # Slacki tylko tam, gdzie mogą realnie ratować wykonalność modelu.
    # Dla górnych limitów "<=2" slack nie jest potrzebny: nie powodują one infeasible przy poprawnym modelu.
    slack_uncovered = model.addVars(slot_ids, lb=0.0, vtype=GRB.CONTINUOUS, name="slack_uncovered")
    slack_zero_first = model.addVars(slot_ids, people, lb=0.0, vtype=GRB.CONTINUOUS, name="slack_zero_first")
    slack_zero_second = model.addVars(slot_ids, people, lb=0.0, vtype=GRB.CONTINUOUS, name="slack_zero_second")
    slack_pref2_first = model.addVars(slot_ids, people, lb=0.0, vtype=GRB.CONTINUOUS, name="slack_pref2_first")
    slack_sunday_free = model.addVars(people, lb=0.0, vtype=GRB.CONTINUOUS, name="slack_no_free_sunday")

    # Zmienna "awaryjna" dla pustych preferencji: może aktywować się tylko, gdy nikt nie dał 1/2/P.
    allow_blank_fallback = model.addVars(slot_ids, vtype=GRB.BINARY, name="allow_blank_fallback")

    on_duty_day = model.addVars(duty_days, people, vtype=GRB.BINARY, name="on_duty_day")
    delta_change = model.addVars(slot_ids, people, lb=0.0, vtype=GRB.CONTINUOUS, name="delta_change")
    delta_order_first = model.addVars(slot_ids, people, lb=0.0, vtype=GRB.CONTINUOUS, name="delta_order_first")
    delta_order_second = model.addVars(slot_ids, people, lb=0.0, vtype=GRB.CONTINUOUS, name="delta_order_second")

    sunday_days = [day for day in duty_days if day.weekday() == 6]
    weekend_ids = sorted(
        {f"{day.isocalendar().year}-{day.isocalendar().week}" for day in duty_days if day.weekday() in {5, 6}}
    )
    day_to_weekend = {day: f"{day.isocalendar().year}-{day.isocalendar().week}" for day in duty_days if day.weekday() in {5, 6}}
    slack_weekend_both = model.addVars(people, weekend_ids, lb=0.0, vtype=GRB.CONTINUOUS, name="slack_weekend_both")

    # 1) Pokrycie slotu i ograniczenia pozycji 1/2.
    for slot_id in slot_ids:
        model.addConstr(
            gp.quicksum(x_first[slot_id, person] + x_second[slot_id, person] for person in people)
            + slack_uncovered[slot_id]
            >= 1,
            name=f"cover_{slot_id}",
        )
        model.addConstr(
            gp.quicksum(x_first[slot_id, person] + x_second[slot_id, person] for person in people) <= 2,
            name=f"max2_{slot_id}",
        )
        model.addConstr(gp.quicksum(x_first[slot_id, person] for person in people) <= 1, name=f"one_first_{slot_id}")
        model.addConstr(gp.quicksum(x_second[slot_id, person] for person in people) <= 1, name=f"one_second_{slot_id}")
        model.addConstr(
            gp.quicksum(x_second[slot_id, person] for person in people)
            <= gp.quicksum(x_first[slot_id, person] for person in people),
            name=f"second_requires_first_{slot_id}",
        )
        for person in people:
            model.addConstr(x_first[slot_id, person] + x_second[slot_id, person] <= 1, name=f"not_both_{slot_id}_{person}")

    # 2) Maksymalnie dwie osoby w całej dobie dyżurowej (8:00->8:00).
    for day in duty_days:
        slots_in_day = slots_by_duty_day[day]
        for person in people:
            for slot_id in slots_in_day:
                model.addConstr(
                    x_first[slot_id, person] + x_second[slot_id, person] <= on_duty_day[day, person],
                    name=f"link_day_{day}_{slot_id}_{person}",
                )
        model.addConstr(gp.quicksum(on_duty_day[day, person] for person in people) <= 2, name=f"max2day_{day}")

    # 3) Preferencje wejściowe 0/1/2/P/puste.
    for slot_id in slot_ids:
        willing_people_count = sum(
            1 for person in people if preferences_by_slot_person[(slot_id, person)] in {"1", "2", "P"}
        )
        # Jeśli ktoś w danym slocie jest chętny (1/2/P), to puste NIE mogą być użyte jako pierwszy.
        # Jeśli nikt nie jest chętny, allow_blank_fallback może się włączyć i pozwolić na wybór pustej komórki.
        blank_fallback_upper_bound = 0 if willing_people_count > 0 else 1
        model.addConstr(
            allow_blank_fallback[slot_id] <= blank_fallback_upper_bound,
            name=f"blank_fallback_enabled_only_without_willing_{slot_id}",
        )

        for person in people:
            code = preferences_by_slot_person[(slot_id, person)]
            if code == "0":
                model.addConstr(x_first[slot_id, person] <= slack_zero_first[slot_id, person])
                model.addConstr(x_second[slot_id, person] <= slack_zero_second[slot_id, person])
            elif code == "":
                model.addConstr(x_second[slot_id, person] == 0, name=f"blank_no_second_{slot_id}_{person}")
                model.addConstr(x_first[slot_id, person] <= allow_blank_fallback[slot_id], name=f"blank_fallback_{slot_id}_{person}")
            elif code == "2":
                model.addConstr(x_first[slot_id, person] <= slack_pref2_first[slot_id, person])
            # code 1/P: bez dodatkowych ograniczeń

    # 4) Co najmniej jedna wolna niedziela dla każdej osoby (miękko).
    if sunday_days:
        for person in people:
            model.addConstr(
                gp.quicksum(on_duty_day[day, person] for day in sunday_days)
                <= len(sunday_days) - 1 + slack_sunday_free[person],
                name=f"free_sunday_{person}",
            )

    # 5) W jednym weekendzie osoba nie powinna mieć dyżuru i w sobotę i w niedzielę.
    for person in people:
        for weekend_id in weekend_ids:
            saturday_days = [day for day in duty_days if day.weekday() == 5 and day_to_weekend.get(day) == weekend_id]
            sunday_days_local = [day for day in duty_days if day.weekday() == 6 and day_to_weekend.get(day) == weekend_id]
            saturday_work = gp.quicksum(on_duty_day[day, person] for day in saturday_days)
            sunday_work = gp.quicksum(on_duty_day[day, person] for day in sunday_days_local)
            model.addConstr(
                saturday_work + sunday_work <= 1 + slack_weekend_both[person, weekend_id],
                name=f"weekend_once_{person}_{weekend_id}",
            )

    # 6) Stabilność: kara za zmiany osób pomiędzy kolejnymi slotami tej samej doby.
    for day in duty_days:
        slots_in_day_sorted = sorted(slots_by_duty_day[day], key=lambda slot_id: (row_by_id[slot_id].date, row_by_id[slot_id].start_hour))
        for pos in range(len(slots_in_day_sorted) - 1):
            current_slot, next_slot = slots_in_day_sorted[pos], slots_in_day_sorted[pos + 1]
            for person in people:
                lhs_plus = (x_first[next_slot, person] + x_second[next_slot, person]) - (
                    x_first[current_slot, person] + x_second[current_slot, person]
                )
                lhs_minus = (x_first[current_slot, person] + x_second[current_slot, person]) - (
                    x_first[next_slot, person] + x_second[next_slot, person]
                )
                model.addConstr(delta_change[next_slot, person] >= lhs_plus)
                model.addConstr(delta_change[next_slot, person] >= lhs_minus)

                # Dodatkowo pilnujemy stabilności kolejności (1/2) pomiędzy slotami
                # tej samej doby dyżurowej. Przekładka 1<->2 dostaje karę i jest
                # dopuszczana tylko wtedy, gdy naprawdę pomaga spełnić inne ograniczenia.
                first_plus = x_first[next_slot, person] - x_first[current_slot, person]
                first_minus = x_first[current_slot, person] - x_first[next_slot, person]
                second_plus = x_second[next_slot, person] - x_second[current_slot, person]
                second_minus = x_second[current_slot, person] - x_second[next_slot, person]
                model.addConstr(delta_order_first[next_slot, person] >= first_plus)
                model.addConstr(delta_order_first[next_slot, person] >= first_minus)
                model.addConstr(delta_order_second[next_slot, person] >= second_plus)
                model.addConstr(delta_order_second[next_slot, person] >= second_minus)

    # 7) Fairness: wyrównujemy liczbę łącznych dyżurów i liczbę pozycji "1" pomiędzy osobami.
    count_first = {person: gp.quicksum(x_first[slot_id, person] for slot_id in slot_ids) for person in people}
    count_second = {person: gp.quicksum(x_second[slot_id, person] for slot_id in slot_ids) for person in people}
    count_total = {person: count_first[person] + count_second[person] for person in people}

    diff_total = {}
    diff_first = {}
    pairs = []
    for i in range(len(people)):
        for j in range(i + 1, len(people)):
            p, q = people[i], people[j]
            pairs.append((p, q))
            diff_total[p, q] = model.addVar(lb=0.0, name=f"diff_total_{p}_{q}")
            diff_first[p, q] = model.addVar(lb=0.0, name=f"diff_first_{p}_{q}")
            model.addConstr(diff_total[p, q] >= count_total[p] - count_total[q])
            model.addConstr(diff_total[p, q] >= count_total[q] - count_total[p])
            model.addConstr(diff_first[p, q] >= count_first[p] - count_first[q])
            model.addConstr(diff_first[p, q] >= count_first[q] - count_first[p])

    # Funkcja celu (ważona suma kar):
    # - najpierw krytyczne braki (nieobsadzony slot, złamanie "0"),
    # - potem naruszenia preferencji i reguł weekendowych,
    # - następnie jakość (stabilność, fairness),
    # - na końcu globalna preferencja: mniej pozycji "1", więcej "2".
    objective = gp.LinExpr()

    # Krytyczne: brak obsady slotu i naruszenia twardego "0".
    objective += penalties["uncovered_slot"] * gp.quicksum(slack_uncovered[slot_id] for slot_id in slot_ids)
    objective += penalties["zero_violation"] * gp.quicksum(
        slack_zero_first[slot_id, person] + slack_zero_second[slot_id, person]
        for slot_id in slot_ids
        for person in people
    )

    # Preferencje lokalne: puste tylko awaryjnie jako pierwszy, "2" jako pierwszy z karą.
    objective += penalties["blank_fallback_first"] * gp.quicksum(
        x_first[slot_id, person]
        for slot_id in slot_ids
        for person in people
        if preferences_by_slot_person[(slot_id, person)] == ""
    )
    objective += penalties["pref2_as_first"] * gp.quicksum(
        slack_pref2_first[slot_id, person] for slot_id in slot_ids for person in people
    )

    # Reguły weekendowe/niedzielne jako miękkie ograniczenia.
    objective += penalties["no_free_sunday"] * gp.quicksum(slack_sunday_free[person] for person in people)
    objective += penalties["weekend_both_days"] * gp.quicksum(
        slack_weekend_both[person, weekend_id] for person in people for weekend_id in weekend_ids
    )

    # Jakość grafiku: stabilność i sprawiedliwość.
    objective += penalties["change_between_slots"] * gp.quicksum(
        delta_change[slot_id, person] for slot_id in slot_ids for person in people
    )
    objective += penalties["order_change_between_slots"] * gp.quicksum(
        delta_order_first[slot_id, person] + delta_order_second[slot_id, person]
        for slot_id in slot_ids
        for person in people
    )
    objective += penalties["fair_total_difference"] * gp.quicksum(diff_total[pair] for pair in pairs)
    objective += penalties["fair_first_difference"] * gp.quicksum(diff_first[pair] for pair in pairs)

    # Globalna preferencja udziału pozycji 1 i 2.
    objective += penalties["first_assignment"] * gp.quicksum(
        x_first[slot_id, person] for slot_id in slot_ids for person in people
    )
    objective += penalties["second_assignment"] * gp.quicksum(
        x_second[slot_id, person] for slot_id in slot_ids for person in people
    )

    model.setObjective(objective, GRB.MINIMIZE)
    model.Params.MIPGap = mip_gap
    model.Params.PoolSearchMode = 2
    model.Params.PoolSolutions = 200
    model.Params.PoolGap = 0.0
    LOGGER.info("Start optymalizacji (MIPGap=%s)", mip_gap)
    model.optimize()
    selected_solution_number = 0
    if model.SolCount > 1 and hasattr(model, "ObjVal"):
        best_obj = float(model.ObjVal)
        best_solution_numbers = []
        for solution_number in range(model.SolCount):
            model.Params.SolutionNumber = solution_number
            if abs(float(model.PoolObjVal) - best_obj) <= 1e-6:
                best_solution_numbers.append(solution_number)
        if len(best_solution_numbers) > 1:
            rng = random.Random(random_seed)
            selected_solution_number = rng.choice(best_solution_numbers)
        model.Params.SolutionNumber = selected_solution_number
        LOGGER.info(
            "Wybrano rozwiązanie z puli: #%d (najlepszych ex aequo: %d, wszystkich w puli: %d)",
            selected_solution_number,
            len(best_solution_numbers),
            model.SolCount,
        )
    LOGGER.info(
        "Koniec optymalizacji. Status=%s, ObjVal=%s",
        model.Status,
        getattr(model, "ObjVal", "n/a"),
    )

    return model, x_first, x_second, on_duty_day, selected_solution_number


def write_output(output_path: Path, headers, people_columns, input_rows, x_first, x_second, selected_solution_number: int):
    """Zapisuje wynik w układzie identycznym jak wejście, z wartościami 1/2/puste."""

    with output_path.open("w", encoding="utf-8", newline="") as file_handle:
        writer = csv.DictWriter(file_handle, fieldnames=headers, delimiter=";")
        writer.writeheader()
        for slot_idx, input_row in enumerate(input_rows):
            output_row = dict(input_row)
            for person in people_columns:
                is_first = int(round(x_first[slot_idx, person].X))
                if selected_solution_number > 0:
                    is_first = int(round(x_first[slot_idx, person].Xn))
                    is_second = int(round(x_second[slot_idx, person].Xn))
                else:
                    is_second = int(round(x_second[slot_idx, person].X))
                output_row[person] = "1" if is_first == 1 else ("2" if is_second == 1 else "")
            writer.writerow(output_row)
    LOGGER.info("Zapisano wynikowy CSV do %s", output_path)


def summarize(people_columns, slot_rows: List[SlotRow], x_first, x_second, on_duty_day, selected_solution_number: int):
    """Wypisuje podsumowanie per osoba: liczba 1, liczba 2, liczba dni dyżurowych."""

    duty_days = sorted({row.duty_day for row in slot_rows})
    print("\n=== Podsumowanie osób ===")
    for person in people_columns:
        first_count = sum(int(round(x_first[row.idx, person].X)) for row in slot_rows)
        if selected_solution_number > 0:
            first_count = sum(int(round(x_first[row.idx, person].Xn)) for row in slot_rows)
            second_count = sum(int(round(x_second[row.idx, person].Xn)) for row in slot_rows)
            duty_days_count = sum(int(round(on_duty_day[day, person].Xn)) for day in duty_days)
        else:
            second_count = sum(int(round(x_second[row.idx, person].X)) for row in slot_rows)
            duty_days_count = sum(int(round(on_duty_day[day, person].X)) for day in duty_days)
        print(f"{person:>8}: 1-ek={first_count:3d}, 2-ek={second_count:3d}, dni_dyzurowe={duty_days_count:3d}")


def print_slack_report(model):
    """Wypisuje wykorzystane slacki, aby łatwo zobaczyć naruszone reguły."""

    print("\n=== Slack report (wartości > 0) ===")
    found = False
    for variable in model.getVars():
        if variable.VarName.startswith("slack_") and variable.X > 1e-6:
            found = True
            print(f"{variable.VarName}: {variable.X:.3f}")
    if not found:
        print("Brak użytych slacków.")
    LOGGER.info("Wypisano raport slacków.")


def setup_logging(verbose: bool):
    """Konfiguruje logowanie konsolowe."""

    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    # Gurobi i tak wypisuje swój własny log solvera na stdout.
    # Wyłączamy duplikaty przez logger Pythona "gurobipy" z prefiksem czasu.
    logging.getLogger("gurobipy").setLevel(logging.WARNING)


def main():
    """Parsuje argumenty CLI, uruchamia optymalizację i zapisuje wynik."""

    ap = argparse.ArgumentParser(description="Optymalizacja grafiku dyżurów (gurobipy).")
    ap.add_argument("input_csv", type=Path, help="Wejściowy plik CSV (separator ;)" )
    ap.add_argument("output_csv", type=Path, help="Wyjściowy plik CSV")
    ap.add_argument("--mip-gap", type=float, default=0.01, help="MIPGap (domyślnie 0.01)")
    ap.add_argument("--random-seed", type=int, default=None, help="Seed losowania rozwiązania z puli ex aequo.")
    ap.add_argument(
        "--penalties",
        type=Path,
        default=Path("penalties.ini"),
        help="Plik INI z wagami kar (sekcja [penalties])",
    )
    ap.add_argument("--verbose", action="store_true", help="Włącza bardziej szczegółowe logi (DEBUG).")
    args = ap.parse_args()
    setup_logging(args.verbose)
    LOGGER.info("Uruchomienie scheduler.py")

    penalties = load_penalties(Path(__file__).absolute().parent / args.penalties)
    headers, people_columns, slot_rows, input_rows, preferences_by_slot_person = load_csv(args.input_csv, separator=";")

    model, x_first, x_second, on_duty_day, selected_solution_number = optimize_schedule(
        people_columns=people_columns,
        slot_rows=slot_rows,
        preferences_by_slot_person=preferences_by_slot_person,
        mip_gap=args.mip_gap,
        penalties=penalties,
        random_seed=args.random_seed,
    )

    import gurobipy as gp

    if model.Status not in {gp.GRB.OPTIMAL, gp.GRB.SUBOPTIMAL, gp.GRB.TIME_LIMIT}:
        raise RuntimeError(f"Optymalizacja nie zakończyła się poprawnie (status={model.Status}).")

    write_output(args.output_csv, headers, people_columns, input_rows, x_first, x_second, selected_solution_number)
    print_slack_report(model)
    summarize(people_columns, slot_rows, x_first, x_second, on_duty_day, selected_solution_number)
    print(f"\nZapisano wynik do: {args.output_csv}")
    LOGGER.info("Zakończono działanie skryptu.")


if __name__ == "__main__":
    main()
