"""Таблица «подписи строк × шапка столбцов» в длинный формат.

Типичная таблица статистики: первый столбец — показатель или вид транспорта, верхние строки —
шапка (иногда в несколько уровней), дальше — числа. Шапкой считаются строки до первой строки,
в которой есть хотя бы одно число вне первого столбца. Строка номеров граф («1, 2, 3, …» или
«А, Б, 1, 2, …») относится к шапке, но в подписи столбцов не входит.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

from .parsing import clean_label, parse_number, strip_footnote


@dataclass(frozen=True)
class Cell:
    row_index: int
    row_label: str
    row_note: str
    col_index: int
    col_header: str
    value: float | None
    value_raw: str
    value_note: str


def _has_number(row: list[str]) -> bool:
    return any(parse_number(c)[0] is not None for c in row[1:])


def is_numbering_row(row: list[str]) -> bool:
    """Строка номеров граф: возрастающие номера с 1–3, впереди могут быть буквы «А», «Б»."""
    if row and row[0].strip() not in ("", "1", "А", "A"):
        return False
    cells = [c.strip() for c in row if c.strip()]
    while cells and cells[0] in ("А", "Б", "В", "A", "B"):
        cells = cells[1:]
    if len(cells) < 3 or not all(c.isdigit() and len(c) <= 2 for c in cells):
        return False
    numbers = [int(c) for c in cells]
    return numbers[0] <= 3 and all(a < b for a, b in pairwise(numbers))


def split_header(rows: list[list[str]]) -> tuple[list[list[str]], list[list[str]]]:
    for i, row in enumerate(rows):
        if _has_number(row) and not is_numbering_row(row):
            return rows[:i], rows[i:]
    return rows, []


def column_headers(header_rows: list[list[str]], width: int) -> list[str]:
    """Подписи столбцов: тексты шапки сверху вниз через « / ».

    Объединённая ячейка верхнего уровня даёт пустые ячейки справа — они заполняются текстом
    слева, если под ними в шапке есть свой текст (иначе это просто пустой столбец).
    """
    filled = [row + [""] * (width - len(row)) for row in header_rows if not is_numbering_row(row)]
    for level, row in enumerate(filled[:-1]):
        below = filled[level + 1 :]
        for j in range(1, width):
            if not row[j] and row[j - 1] and any(r[j] for r in below):
                row[j] = row[j - 1]
    headers = []
    for j in range(width):
        parts: list[str] = []
        for row in filled:
            text = row[j].strip()
            text = clean_label(text)
            if text and (not parts or parts[-1] != text):
                parts.append(text)
        headers.append(" / ".join(parts))
    return headers


def iter_cells(rows: list[list[str]]) -> Iterator[Cell]:
    """Все непустые ячейки данных с подписью строки и столбца."""
    if not rows:
        return
    width = max(len(r) for r in rows)
    header_rows, data_rows = split_header(rows)
    headers = column_headers(header_rows, width)
    for i, row in enumerate(data_rows):
        if is_numbering_row(row):  # шапка, повторённая на новой странице
            continue
        label, note = strip_footnote(row[0] if row else "")
        for j in range(1, width):
            raw = row[j] if j < len(row) else ""
            if not raw.strip():
                continue
            value, value_note = parse_number(raw)
            yield Cell(i, label, note, j, headers[j], value, raw, value_note)


def read_table(data_dir: Path, csv_path: str) -> list[list[str]]:
    with (data_dir / csv_path).open(encoding="utf-8", newline="") as fh:
        return list(csv.reader(fh))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)
