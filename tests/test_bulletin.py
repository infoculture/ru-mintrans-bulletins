import csv
import json
from pathlib import Path

import pytest

from mintrans_stats.normalize.bulletin import (
    classify_mode,
    classify_topic,
    detect_unit,
    run_bulletin,
)


@pytest.mark.parametrize(
    ("caption", "topic"),
    [
        ("1.1. Перевозки пассажиров по видам транспорта (млн. человек)", "passengers_carried"),
        ("1.2. Пассажирооборот транспорта общего пользования", "passenger_turnover"),
        ("1.2.1. Перевозки грузов по видам транспорта1) (миллионов тонн)", "freight_carried"),
        ("1.4. ГРУЗООБОРОТ ПО ВИДАМ ТРАНСПОРТА (млрд. тонно‐километров)", "freight_turnover"),
        ("Коммерческие перевозки грузов по видам транспорта", None),
        ("Перевозка грузов и грузооборот на водном транспорте", None),
        ("2.4. Индексы тарифов на услуги пассажирского транспорта", None),
    ],
)
def test_classify_topic(caption, topic):
    assert classify_topic(caption) == topic


@pytest.mark.parametrize(
    ("label", "mode"),
    [
        ("Транспорт – всего", "total"),
        ("Транспорт всех отраслей экономики", "total"),
        ("Транспорт отраслей Минтранса России", "total_mintrans"),
        ("в том числе: автомобильный (автобусный)", "bus"),
        ("автомобильный", "road"),
        ("железнодорожный общего пользования", "rail"),
        ("промышленный железнодорожный", "rail_industrial"),
        ("воздушный, из него:", "air"),
        ("внутренний водный", "inland_water"),
        ("водный", "water"),
        ("метрополитенный", "metro"),
        ("транспорт других министерств и ведомств ...трубопроводный", "pipeline"),
        ("из него внутренние перевозки", None),
        ("в том числе:", None),
    ],
)
def test_classify_mode(label, mode):
    assert classify_mode(label) == mode


def test_detect_unit():
    assert detect_unit("(миллионов тонн)") == "млн т"
    assert detect_unit("(млрд. тонно‐километров)") == "млрд т-км"
    assert detect_unit("Пассажирооборот (миллиардов пассажиро-километров)") == "млрд пасс.-км"
    assert detect_unit("(млн. человек)") == "млн чел."
    assert detect_unit("без единиц") == ""


def _add_table(settings, tables, document_id, n, caption, rows):
    rel = f"tables/{document_id}/t{n:03d}.csv"
    path = settings.data_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerows(rows)
    tables.append(
        {
            "table_id": f"{document_id}/1:t{n:03d}",
            "document_id": document_id,
            "location": "body",
            "caption": caption,
            "csv_path": rel,
        }
    )


def _jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )


CAPTION = "1.3. Перевозки грузов по видам транспорта (миллионов тонн)"


@pytest.fixture
def two_bulletins(settings):
    title = "ТРАНСПОРТ РОССИИ. Информационно-статистический бюллетень. I полугодие {} года"
    downloads = [
        {"document_id": "100", "document_title": title.format(2023)},
        {"document_id": "200", "document_title": title.format(2024)},
    ]
    tables: list[dict] = []
    header = [
        "",
        "I полугодие {a} г.",
        "I полугодие {b} г.",
        "I полугодие {b} г. в % к I полугодию {a} г.",
    ]
    h23 = [c.format(a=2022, b=2023) for c in header]
    h24 = [c.format(a=2023, b=2024) for c in header]
    _add_table(settings, tables, "100", 1, CAPTION, [
        h23,
        ["Транспорт – всего", "4 056,6", "4 114,4", "101,4"],
        ["в том числе транспорт отраслей Минтранса России", "3 501,8", "3 583,0", "102,3"],
        ["железнодорожный", "615,6", "620,7", "100,8"],
        ["автомобильный", "2 835,7", "2 909,8", "102,6"],
        ["воздушный", "0,344", "0,211", "61,4"],
        ["прочие", "50,156", "52,289", "104,2"],
        ["трубопроводный", "554,8", "531,4", "95,8"],
    ])  # fmt: skip
    _add_table(settings, tables, "200", 1, CAPTION, [
        h24,
        ["Транспорт – всего", "4 188,9", "4 466,3", "106,6"],
        ["в том числе транспорт отраслей Минтранса России", "3 657,5", "3 924,0", "107,3"],
        ["железнодорожный", "620,7", "601,6", "96,9"],
        ["автомобильный", "2 982,5", "3 268,5", "109,6"],
        ["воздушный", "211", "218,4", "103,5"],  # выпуск дал тыс. т без пометки
        ["трубопроводный", "554,8", "531,4", "95,8"],
    ])  # fmt: skip
    _jsonl(settings.catalog_dir / "downloads.jsonl", downloads)
    _jsonl(settings.catalog_dir / "tables.jsonl", tables)
    return settings


def _read(path):
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_run_bulletin_series_and_checks(two_bulletins):
    settings = two_bulletins
    summary = run_bulletin(settings)
    assert summary["bulletins"] == 2
    out = settings.data_dir / "datasets"

    values = _read(out / "bulletin_values.csv")
    percent = [v for v in values if v["measure"] == "percent"]
    assert percent and all(v["unit"] == "%" for v in percent)

    series = {(r["mode"], r["period"]): r for r in _read(out / "bulletin_core_series.csv")}
    # значение 2023 года из более позднего выпуска (пересмотр)
    assert series[("total", "2023-01..06")]["value"] == "4188.9"
    assert series[("total", "2023-01..06")]["document_id"] == "200"
    assert ("air", "2023-01..06") in series

    checks = _read(out / "bulletin_checks.csv")
    kinds = {(c["check"], c["mode"], c["period"]): c for c in checks}
    assert kinds[("total_vs_sum", "total", "2024-01..06")]["ok"] == "True"
    assert kinds[("unit_suspect", "air", "2023-01..06")]["ok"] == "False"
    assert kinds[("revision", "total", "2023-01..06")]["ok"] == "False"  # 4114,4 → 4188,9
    assert summary["checks"]["unit_suspect"]["failed"] >= 1


def test_flag_unit_jumps_marks_thousands_among_millions():
    from mintrans_stats.normalize.bulletin import flag_unit_jumps, latest_series

    def row(value, year, month_to, report):
        return {
            "indicator": "freight_carried", "mode": "air", "value": value, "year": year,
            "month_from": 1, "month_to": month_to, "period": f"{year}-01..{month_to:02d}",
            "report_period": report, "table_id": "t",
        }  # fmt: skip

    rows = [row(1.0 + i / 10, 2010 + i, 12, f"{2010 + i}-01..12") for i in range(10)]
    rows += [row(0.211, 2023, 6, "2023-01..06"), row(211.0, 2023, 6, "2024-01..06")]
    flag_unit_jumps(rows)
    assert [r["flags"] for r in rows[-2:]] == ["", "unit_suspect"]
    best = {r["period"]: r for r in latest_series(rows)}
    assert best["2023-01..06"]["value"] == 0.211  # помеченное значение уступает раннему
