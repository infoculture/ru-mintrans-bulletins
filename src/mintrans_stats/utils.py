"""Общие помощники: нормализация текста, даты, запись JSON/JSONL."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from datetime import UTC, date, datetime
from pathlib import Path

RU_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}
RU_DATE_RE = re.compile(r"(\d{1,2})\s+([А-Яа-яЁё]+)\s+(\d{4})")


def normalize_text(text: str) -> str:
    """Схлопнуть пробелы (включая неразрывные) и обрезать края."""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def parse_ru_date(text: str) -> date | None:
    """«11 Декабря 2025» → ``date(2025, 12, 11)``; ``None``, если даты нет."""
    match = RU_DATE_RE.search(text or "")
    if not match:
        return None
    day, month_name, year = match.groups()
    month = RU_MONTHS.get(month_name.lower())
    if not month:
        return None
    try:
        return date(int(year), month, int(day))
    except ValueError:
        return None


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    """Атомарно записать JSONL; вернуть число строк."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    count = 0
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    tmp.replace(path)
    return count


def read_jsonl(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)
