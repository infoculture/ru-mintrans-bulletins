import pytest

from mintrans_stats.normalize.parsing import (
    Period,
    clean_label,
    is_percent_header,
    parse_number,
    parse_period,
    strip_footnote,
)


@pytest.mark.parametrize(
    ("raw", "value", "note"),
    [
        ("7 670,1", 7670.1, ""),
        ("101,02)", 101.0, "2)"),  # сноска вплотную к числу
        ("24 487,11)", 24487.1, "1)"),
        ("6.910,1", 6910.1, ""),  # точка вместо пробела в PDF
        ("98.7", 98.7, ""),
        ("1,477", 1.477, ""),
        ("-0,5", -0.5, ""),
        ("−3,2", -3.2, ""),
        ("980,1*", 980.1, "*"),
        ("2375,9 1", 2375.9, "1)"),  # сноска через пробел
        ("...", None, ""),
        ("-", None, ""),
        ("в 2,1 р.", None, ""),
    ],
)
def test_parse_number(raw, value, note):
    assert parse_number(raw) == (value, note)


def test_strip_footnote_and_clean_label():
    assert strip_footnote("Транспорт – всего2)") == ("Транспорт – всего", "2)")
    assert strip_footnote("внутренний.водный1),2)") == ("внутренний водный", "1),2)")
    assert strip_footnote("внутренний водный,2)") == ("внутренний водный", "2)")
    assert strip_footnote("морской") == ("морской", "")
    assert clean_label("в.том.числе:") == "в том числе:"


@pytest.mark.parametrize(
    ("text", "period"),
    [
        ("Информационно-статистический бюллетень. 9 месяцев 2025 года", Period(2025, 1, 9)),
        ("I полугодие 2025 года", Period(2025, 1, 6)),
        ("1 квартал 2025 года", Period(2025, 1, 3)),
        ("III квартал 2014 года", Period(2014, 7, 9)),
        ("ТРАНСПОРТ РОССИИ. 2024 год", Period(2024, 1, 12)),
        ("(январь-декабрь 2011 года)", Period(2011, 1, 12)),
        ("Январь – сентябрь 2020 года", Period(2020, 1, 9)),
        ("Январь ‒- сентябрь 2025 г. в % к январю ‒ сентябрю 2024 г.", Period(2025, 1, 9)),
        ("Декабрь 2011 года в % к декабрю 2010", Period(2011, 12, 12)),
        ("май 2016 г.", Period(2016, 5, 5)),
        ("март 2016 г.", Period(2016, 3, 3)),
        ("2021.г.", Period(2021, 1, 12)),
    ],
)
def test_parse_period(text, period):
    assert parse_period(text) == period


def test_parse_period_without_year():
    assert parse_period("Наименование") is None
    assert Period(2025, 1, 9).label == "2025-01..09"
    assert Period(2016, 5, 5).label == "2016-05"


def test_is_percent_header():
    assert is_percent_header("Январь-сентябрь 2025 г. в % к январю-сентябрю 2024 г.")
    assert is_percent_header("в процентах")
    assert not is_percent_header("Январь – сентябрь 2025 г.")
