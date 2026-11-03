"""Разбор чисел, сносок и периодов в таблицах статистики.

Числа на сайте записаны по-русски: пробел — разделитель тысяч, запятая — десятичный
разделитель, сноска — цифра со скобкой вплотную к числу («101,02)» = 101,0 и сноска 2).
Периоды: «Январь – сентябрь 2025 г.», «9 месяцев 2025 года», «I полугодие», «2024 год»,
«Декабрь 2011 года в % к декабрю 2010 г.».
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MONTHS = {
    "январ": 1,
    "феврал": 2,
    "март": 3,
    "апрел": 4,
    "ма": 5,
    "июн": 6,
    "июл": 7,
    "август": 8,
    "сентябр": 9,
    "октябр": 10,
    "ноябр": 11,
    "декабр": 12,
}
MONTH_WORD = (
    r"(январ\w*|феврал\w*|март\w*|апрел\w*|ма[йяе]\w*|июн\w*|июл\w*|август\w*|"
    r"сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)"
)
DASH = r"\s*[-–—‒−]+\s*"
YEAR_RE = re.compile(r"((?:19|20)\d\d)\s*(?:г\b|год|гг)?")

NUMBER_RE = re.compile(
    r"^(?P<sign>[-−–‒]?)\s*(?P<int>\d{1,3}(?:[  ]\d{3})+|\d+)(?:[.,](?P<frac>\d+?))?"
    r"\s*(?P<note>\d{1,2}\)|\*+\)?)?\s*%?$"
)
FOOTNOTE_RE = re.compile(r"(?:[\s,]*(?:\d{1,2}\)|\*+))+[\s,]*$")
SPACED_NOTE_RE = re.compile(r"^(.*[.,]\d+)\s(\d)$")
LETTER_DOT_RE = re.compile(r"(?<=[А-Яа-яЁё])\.(?=[А-Яа-яЁё])")
EMPTY_VALUES = frozenset({"", "-", "–", "—", "‒", "...", "…", "х", "x", "Х", "X", "*"})


def month_number(word: str) -> int | None:
    word = word.lower()
    for prefix, number in MONTHS.items():
        if word.startswith(prefix):
            # «ма» — только май/мая/мае, не «март»
            if prefix == "ма" and word.startswith("март"):
                continue
            return number
    return None


def parse_number(raw: str) -> tuple[float | None, str]:
    """Число и сноска из ячейки: «7 670,1» → (7670.1, ""), «101,02)» → (101.0, "2)")."""
    text = (raw or "").strip().replace("\xa0", " ")
    if text in EMPTY_VALUES:
        return None, ""
    if "," in text and "." in text.split(",")[0]:
        text = text.replace(".", " ")  # «6.910,1»: точка вместо пробела-разделителя тысяч
    spaced_note = SPACED_NOTE_RE.match(text)
    if spaced_note:  # «2375,9 1»: сноска через пробел после дробной части
        text = f"{spaced_note.group(1)}{spaced_note.group(2)})"
    match = NUMBER_RE.match(text)
    if not match:
        return None, ""
    digits = match.group("int").replace(" ", "").replace(" ", "")
    frac = match.group("frac")
    value = float(f"{digits}.{frac}" if frac else digits)
    if match.group("sign"):
        value = -value
    return value, match.group("note") or ""


def clean_label(text: str) -> str:
    """Подпись без артефактов PDF: точки вместо пробелов между словами («в.том.числе»)."""
    return re.sub(r"\s+", " ", LETTER_DOT_RE.sub(" ", text or "")).strip()


def strip_footnote(label: str) -> tuple[str, str]:
    """Подпись строки без сносок: «внутренний водный1),2)» → («внутренний водный», «1),2)»)."""
    label = clean_label(label)
    match = FOOTNOTE_RE.search(label)
    if not match:
        return label, ""
    return label[: match.start()].strip(" ,"), match.group(0).strip(" ,")


@dataclass(frozen=True)
class Period:
    year: int
    month_from: int
    month_to: int

    @property
    def label(self) -> str:
        if self.month_from == self.month_to:
            return f"{self.year}-{self.month_to:02d}"
        return f"{self.year}-{self.month_from:02d}..{self.month_to:02d}"


def parse_period(text: str, *, default_year: int | None = None) -> Period | None:
    """Первый период в тексте: диапазон месяцев, «9 месяцев», полугодие, квартал, год, месяц."""
    low = (text or "").lower().replace("ё", "е")
    year_match = YEAR_RE.search(low)
    year = int(year_match.group(1)) if year_match else default_year
    if year is None:
        return None
    head = low[: year_match.end()] if year_match else low

    rng = re.search(MONTH_WORD + DASH + MONTH_WORD, head)
    if rng:
        start, end = month_number(rng.group(1)), month_number(rng.group(2))
        if start and end:
            return Period(year, start, end)
    if re.search(r"\b9\s*месяц", head):
        return Period(year, 1, 9)
    if re.search(r"\b(i|1)\s*полугоди", head) or "полугоди" in head:
        return Period(year, 1, 6)
    quarter = re.search(r"\b(i{1,3}|iv|[1-4])\s*квартал", head)
    if quarter:
        q = {"i": 1, "ii": 2, "iii": 3, "iv": 4}.get(quarter.group(1)) or int(quarter.group(1))
        return Period(year, 3 * q - 2, 3 * q)
    single = re.search(MONTH_WORD, head)
    if single and month_number(single.group(1)):
        month = month_number(single.group(1))
        return Period(year, month, month)
    if year_match and re.search(r"(?:19|20)\d\d\s*(?:год\b|года?\b|г\.?$|г\.?\s)", head + " "):
        return Period(year, 1, 12)
    if year_match:
        return Period(year, 1, 12)
    return None


def is_percent_header(text: str) -> bool:
    """Столбец в процентах к базе: «… в % к январю – сентябрю 2024 г.», «в процентах»."""
    low = (text or "").lower()
    return bool(re.search(r"в\s*%|в\s+процент|%\s*к\b|темп", low))
