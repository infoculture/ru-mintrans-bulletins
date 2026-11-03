"""Бюллетень «Транспорт России»: все таблицы в длинном формате и ключевые ряды.

Выход в ``<data_dir>/datasets/``:

* ``bulletin_values.csv`` — каждая числовая ячейка каждой таблицы всех выпусков: выпуск и его
  период, таблица и подпись, единица, подпись строки, шапка столбца и её период, мера
  (значение или процент к базе), число;
* ``bulletin_core.csv`` — ключевые показатели (перевозки и обороты пассажиров и грузов по
  видам транспорта) из всех выпусков, с приведёнными названиями видов транспорта;
* ``bulletin_core_series.csv`` — тот же ряд без повторов: для каждого показателя, вида и
  периода берётся значение из самого позднего выпуска (последний пересмотр);
* ``bulletin_checks.csv`` — проверки: итог против суммы видов транспорта, расхождения одного
  и того же значения в разных выпусках.
"""

from __future__ import annotations

import logging
import re
import statistics
from collections import defaultdict

from ..classify import is_bulletin
from ..config import Settings
from ..utils import read_jsonl, utc_now_iso, write_json
from .parsing import Period, clean_label, is_percent_header, parse_period
from .tables import iter_cells, read_table, write_csv

logger = logging.getLogger(__name__)

# Тема таблицы по подписи: (показатель, шаблон, исключения)
CORE_TOPICS: tuple[tuple[str, str, str], ...] = (
    ("passengers_carried", r"перевоз\w*\s+пассажир|перевезено\s+пассажир", r"тариф|индекс"),
    ("passenger_turnover", r"пассажирооборот", r"тариф|индекс"),
    (
        "freight_carried",
        r"перевоз\w*\s+грузов|перевезено\s+грузов",
        r"коммерческ|федеральн\w+\s+округ|крупных\s+и\s+средних|крайнего\s+севера|портов|"
        r"приравненн|отправлени",
    ),
    (
        "freight_turnover",
        r"грузооборот",
        r"коммерческ|федеральн\w+\s+округ|крупных\s+и\s+средних|портов",
    ),
)
_CORE = tuple(
    (name, re.compile(rx, re.IGNORECASE), re.compile(ex, re.IGNORECASE))
    for name, rx, ex in CORE_TOPICS
)
# Ключевые таблицы — сводные «по видам транспорта» / «транспорта общего пользования»;
# таблицы по одному виду («на водном транспорте») смешивают показатели и единицы
CORE_SCOPE_RE = re.compile(r"видам\s+транспорта|общего\s+пользования", re.IGNORECASE)

# Вид транспорта по подписи строки; порядок важен («автобус» раньше «автомобильн»)
MODE_RULES: tuple[tuple[str, str], ...] = (
    # «Транспорт – всего» в выпусках 2021+ и «Транспорт всех отраслей экономики» раньше
    ("total", r"^(?:транспорт\s*[-–—‒]?\s*всего|всего|транспорт\s+всех\s+отраслей)"),
    # итог по отраслям Минтранса — другой охват, чем «всего»
    ("total_mintrans", r"^транспорт\s+отраслей\s+минтранса"),
    ("rail_industrial", r"^промышленн\w*\s+железнодорожн"),
    ("bus", r"^автомобильн\w*\s*\(автобусн|^автобусн"),
    ("road", r"^автомобильн"),
    ("rail", r"^железнодорожн"),
    ("air", r"^воздушн"),
    ("sea", r"^морск"),
    ("inland_water", r"^внутренн\w*\s+водн|^речн"),
    ("water", r"^водн"),  # морской и внутренний водный вместе (выпуски 2011–2012)
    ("tram", r"^трамва"),
    ("trolleybus", r"^троллейбус"),
    ("metro", r"^метрополитен"),
    ("urban_electric", r"^городск\w*\s+электрическ"),
    # склеенная строка «транспорт других ведомств ...трубопроводный» — ищем слово где угодно
    ("pipeline", r"трубопровод"),
)
_MODES = tuple((name, re.compile(rx, re.IGNORECASE)) for name, rx in MODE_RULES)
# Виды отраслей Минтранса и прочих ведомств; подвиды «из них …» в суммы не входят
MINTRANS_MODES = frozenset(
    {"bus", "road", "rail", "air", "sea", "inland_water", "water", "tram", "trolleybus",
     "metro", "urban_electric"}
)  # fmt: skip
OTHER_MODES = frozenset({"pipeline", "rail_industrial"})

UNIT_RULES: tuple[tuple[str, str], ...] = (
    ("млрд пасс.-км", r"млрд\w*\.?\s*пасс|миллиард\w*\s+пассажиро"),
    ("млрд т-км", r"тонно[-‐‑]?\s*километр"),
    ("млрд т-км", r"млрд\w*\.?\s*т(?:онно)?[-‐‑\s]?км|миллиард\w*\s+тонно"),
    ("млн чел.", r"млн\.?\s*чел|миллион\w*\s+человек"),
    ("тыс. чел.", r"тыс\.?\s*чел|тысяч\w*\s+человек"),
    ("млн т", r"млн\.?\s*т(?:онн)?\b|миллион\w*\s+тонн"),
    ("тыс. т", r"тыс\.?\s*т(?:онн)?\b|тысяч\w*\s+тонн"),
    ("%", r"в\s+процент|\(%\)|в\s*%"),
)
_UNITS = tuple((name, re.compile(rx, re.IGNORECASE)) for name, rx in UNIT_RULES)

VALUE_FIELDS = [
    "document_id", "report_period", "report_year", "report_month_to", "table_id", "location",
    "caption", "topic", "unit", "row_index", "row_label", "row_note", "mode", "col_index",
    "col_header", "col_period", "col_year", "col_month_from", "col_month_to", "measure", "value",
    "value_raw", "value_note",
]  # fmt: skip
CORE_FIELDS = [
    "indicator", "mode", "period", "year", "month_from", "month_to", "value", "unit",
    "row_label", "document_id", "report_period", "table_id", "flags",
]  # fmt: skip
UNIT_JUMP = 100  # во столько раз значение отличается от медианы ряда — вероятно, другая единица


def classify_topic(caption: str) -> str | None:
    if not CORE_SCOPE_RE.search(caption):
        return None
    for name, rx, exclude in _CORE:
        if rx.search(caption) and not exclude.search(caption):
            return name
    return None


LEAD_IN_RE = re.compile(r"^(?:в\s*том\s*числе|в\s*т\.\s*ч\.)\s*:?\s*", re.IGNORECASE)


def classify_mode(label: str) -> str | None:
    """Вид транспорта по подписи строки; «в том числе: железнодорожный» → rail."""
    text = LEAD_IN_RE.sub("", label.strip().lower())
    for name, rx in _MODES:
        if rx.search(text):
            return name
    return None


def detect_unit(text: str) -> str:
    for name, rx in _UNITS:
        if rx.search(text or ""):
            return name
    return ""


def bulletin_documents(settings: Settings) -> dict[str, dict]:
    """Выпуски бюллетеня: document_id → {title, period}."""
    docs: dict[str, dict] = {}
    for row in read_jsonl(settings.catalog_dir / "downloads.jsonl"):
        if row["document_id"] in docs or not is_bulletin(row["document_title"]):
            continue
        period = parse_period(row["document_title"])
        if period is None:
            logger.warning("не удалось определить период выпуска: %s", row["document_title"])
            continue
        docs[row["document_id"]] = {"title": row["document_title"], "period": period}
    return docs


def value_rows(settings: Settings, docs: dict[str, dict]) -> list[dict]:
    rows: list[dict] = []
    for table in read_jsonl(settings.catalog_dir / "tables.jsonl"):
        doc = docs.get(table.get("document_id") or "")
        if doc is None:
            continue
        report: Period = doc["period"]
        caption = clean_label(table["caption"])
        topic = classify_topic(caption)
        unit = detect_unit(caption)
        for cell in iter_cells(read_table(settings.data_dir, table["csv_path"])):
            if cell.value is None:
                continue
            col_period = parse_period(cell.col_header, default_year=None)
            percent = is_percent_header(cell.col_header)
            rows.append(
                {
                    "document_id": table["document_id"],
                    "report_period": report.label,
                    "report_year": report.year,
                    "report_month_to": report.month_to,
                    "table_id": table["table_id"],
                    "location": table["location"],
                    "caption": caption,
                    "topic": topic or "",
                    "unit": "%" if percent else unit,
                    "row_index": cell.row_index,
                    "row_label": cell.row_label,
                    "row_note": cell.row_note,
                    "mode": classify_mode(cell.row_label) or "",
                    "col_index": cell.col_index,
                    "col_header": cell.col_header,
                    "col_period": col_period.label if col_period else "",
                    "col_year": col_period.year if col_period else "",
                    "col_month_from": col_period.month_from if col_period else "",
                    "col_month_to": col_period.month_to if col_period else "",
                    "measure": "percent" if percent else "value",
                    "value": cell.value,
                    "value_raw": cell.value_raw,
                    "value_note": cell.value_note,
                }
            )
    return rows


def core_rows(values: list[dict]) -> list[dict]:
    """Ключевые показатели: тема известна, вид транспорта распознан, мера — значение, период есть.

    Строки до первой «всего» в таблице не берутся только если это не вид транспорта. Если в
    таблице одна и та же пара (вид, период) встречается несколько раз (например, «воздушный»
    и подвид в том же разделе), берётся первая — верхний уровень.
    """
    out: list[dict] = []
    seen: set[tuple] = set()
    for v in values:
        if not (v["topic"] and v["mode"] and v["measure"] == "value" and v["col_period"]):
            continue
        key = (v["table_id"], v["mode"], v["col_period"])
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "indicator": v["topic"],
                "mode": v["mode"],
                "period": v["col_period"],
                "year": v["col_year"],
                "month_from": v["col_month_from"],
                "month_to": v["col_month_to"],
                "value": v["value"],
                "unit": v["unit"],
                "row_label": v["row_label"],
                "document_id": v["document_id"],
                "report_period": v["report_period"],
                "table_id": v["table_id"],
            }
        )
    return out


def _report_key(row: dict) -> tuple:
    year, _, months = row["report_period"].partition("-")
    return int(year), months


def flag_unit_jumps(rows: list[dict]) -> None:
    """Пометить ``unit_suspect`` значения, превышающие медиану ряда в ``UNIT_JUMP`` раз и больше.

    Ряд — один показатель и вид транспорта за все годы; значения сравниваются в пересчёте на
    месяц периода, поэтому квартальные, полугодовые и годовые точки образуют общую выборку.
    Так ловятся строки, где выпуск дал тысячи тонн вместо миллионов без пометки. Значения не
    исправляются.
    """

    def per_month(r: dict) -> float:
        return r["value"] / (int(r["month_to"]) - int(r["month_from"]) + 1)

    groups: dict[tuple, list[float]] = defaultdict(list)
    for r in rows:
        if r["value"]:
            groups[(r["indicator"], r["mode"])].append(per_month(r))
    medians = {k: statistics.median(v) for k, v in groups.items()}
    for r in rows:
        median = medians.get((r["indicator"], r["mode"]))
        value = per_month(r) if r["value"] else 0
        # только завышение: «тысячи вместо миллионов»; малые значения бывают сезонными
        # (речной транспорт в I квартале), их не помечаем
        jump = bool(median and value and value / median >= UNIT_JUMP)
        r["flags"] = "unit_suspect" if jump else ""


def latest_series(core: list[dict]) -> list[dict]:
    """Для каждого (показатель, вид, период) — значение из самого позднего выпуска.

    Значение с меткой ``unit_suspect`` уступает значению без метки из более раннего выпуска.
    """
    best: dict[tuple, dict] = {}
    for row in core:
        key = (row["indicator"], row["mode"], row["period"])
        current = best.get(key)
        if current is None:
            best[key] = row
            continue
        rank = (not row["flags"], _report_key(row))
        if rank > (not current["flags"], _report_key(current)):
            best[key] = row
    return sorted(best.values(), key=lambda r: (r["indicator"], r["mode"], r["period"]))


def checks(core: list[dict], *, tolerance_pct: float = 1.0) -> list[dict]:
    """Итог против суммы видов и расхождения между выпусками."""
    out: list[dict] = []
    by_table: dict[tuple, dict[str, float]] = defaultdict(dict)
    for row in core:
        if row.get("flags"):
            continue  # значение в другой единице исказит сумму; о нём есть отдельная проверка
        by_table[(row["table_id"], row["indicator"], row["period"])][row["mode"]] = row["value"]

    def add(table_id, indicator, period, mode, total, parts):
        diff_pct = (sum(parts) - total) / total * 100 if total else 0.0
        out.append(
            {
                "check": "total_vs_sum",
                "indicator": indicator,
                "mode": mode,
                "period": period,
                "table_id": table_id,
                "expected": total,
                "actual": round(sum(parts), 3),
                "diff_pct": round(diff_pct, 2),
                "ok": abs(diff_pct) <= tolerance_pct,
            }
        )

    def closest(total: float, variants: list[list[float]]) -> list[float]:
        return min(variants, key=lambda parts: abs(sum(parts) - total))

    for (table_id, indicator, period), modes in sorted(by_table.items()):
        mintrans_parts = [v for m, v in modes.items() if m in MINTRANS_MODES]
        industrial = [modes["rail_industrial"]] if "rail_industrial" in modes else []
        pipeline = [modes["pipeline"]] if "pipeline" in modes else []
        # промышленный ж/д транспорт в одних выпусках входит в отрасли Минтранса, в других —
        # в «транспорт других министерств и ведомств»: проверяем оба варианта
        industrial_in_mintrans = False
        if "total_mintrans" in modes and len(mintrans_parts) >= 2:
            parts = closest(modes["total_mintrans"], [mintrans_parts, mintrans_parts + industrial])
            industrial_in_mintrans = bool(industrial) and len(parts) > len(mintrans_parts)
            add(table_id, indicator, period, "total_mintrans", modes["total_mintrans"], parts)
        if "total" in modes:
            if "total_mintrans" in modes:
                base = [modes["total_mintrans"]]
                others = pipeline + ([] if industrial_in_mintrans else industrial)
                parts = base + others
            else:
                parts = closest(
                    modes["total"],
                    [mintrans_parts + pipeline, mintrans_parts + pipeline + industrial],
                )
            if len(parts) >= 2:
                add(table_id, indicator, period, "total", modes["total"], parts)
    by_key: dict[tuple, list[dict]] = defaultdict(list)
    for row in core:
        by_key[(row["indicator"], row["mode"], row["period"])].append(row)
    for (indicator, mode, period), rows in sorted(by_key.items()):
        values = {r["value"] for r in rows}
        if len(values) < 2:
            continue
        lo, hi = min(values), max(values)
        diff_pct = (hi - lo) / lo * 100 if lo else 0.0
        out.append(
            {
                "check": "unit_suspect" if lo and hi / lo >= UNIT_JUMP else "revision",
                "indicator": indicator,
                "mode": mode,
                "period": period,
                "table_id": ";".join(sorted({r["table_id"] for r in rows})),
                "expected": lo,
                "actual": hi,
                "diff_pct": round(diff_pct, 2),
                "ok": diff_pct <= tolerance_pct,
            }
        )
    return out


def run_bulletin(settings: Settings) -> dict:
    started = utc_now_iso()
    out_dir = settings.data_dir / "datasets"
    docs = bulletin_documents(settings)
    values = value_rows(settings, docs)
    core = core_rows(values)
    flag_unit_jumps(core)
    series = latest_series(core)
    check_rows = checks(core)
    write_csv(out_dir / "bulletin_values.csv", values, VALUE_FIELDS)
    write_csv(out_dir / "bulletin_core.csv", core, CORE_FIELDS)
    write_csv(out_dir / "bulletin_core_series.csv", series, CORE_FIELDS)
    check_fields = ["check", "indicator", "mode", "period", "table_id", "expected", "actual",
                    "diff_pct", "ok"]  # fmt: skip
    write_csv(out_dir / "bulletin_checks.csv", check_rows, check_fields)

    covered = sorted({r["report_period"] for r in core})
    missing_docs = sorted(
        (
            d["period"].label
            for d_id, d in docs.items()
            if d_id not in {r["document_id"] for r in core}
        ),
    )
    summary = {
        "command": "normalize bulletin",
        "started_at": started,
        "finished_at": utc_now_iso(),
        "bulletins": len(docs),
        "values": len(values),
        "values_with_period": sum(1 for v in values if v["col_period"]),
        "core_rows": len(core),
        "series_rows": len(series),
        "series_by_indicator": dict(
            sorted(
                {
                    ind: sum(1 for r in series if r["indicator"] == ind)
                    for ind in {r["indicator"] for r in series}
                }.items()
            )
        ),
        "bulletins_in_core": len(covered),
        "bulletins_without_core_rows": missing_docs,
        "checks": {
            name: {
                "total": sum(1 for c in check_rows if c["check"] == name),
                "failed": sum(1 for c in check_rows if c["check"] == name and not c["ok"]),
            }
            for name in ("total_vs_sum", "revision", "unit_suspect")
        },
        "series_unit_suspect": sum(1 for r in series if r["flags"]),
    }
    write_json(
        settings.catalog_dir / "runs" / f"normalize-bulletin-{started.replace(':', '')}.json",
        summary,
    )
    return summary
