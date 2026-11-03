import datetime as dt
import zipfile
from pathlib import Path

from mintrans_stats.extract.readers import clean_cell, read_docx, trim

FILES = Path(__file__).parent / "fixtures" / "files"
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def make_docx(path: Path, body: str) -> Path:
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<w:document {W}><w:body>{body}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", xml)
    return path


def p(text: str) -> str:
    return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"


def tc(text: str, props: str = "") -> str:
    return f"<w:tc><w:tcPr>{props}</w:tcPr>{p(text)}</w:tc>"


def test_clean_cell_and_trim():
    assert clean_cell(5.0) == "5"
    assert clean_cell(101.02) == "101.02"
    assert clean_cell(dt.datetime(2015, 7, 22)) == "2015-07-22"
    assert clean_cell("  7\xa0670,1 \n") == "7 670,1"
    assert clean_cell(None) == ""
    assert trim([["", "", ""], ["", "a", ""], ["", "", "b"], ["", "", ""]]) == [
        ["a", ""],
        ["", "b"],
    ]
    assert trim([["", ""]]) == []


def test_read_docx_tables_with_caption_and_merges(tmp_path):
    body = (
        p("Перевозки пассажиров (миллионов человек)")
        + "<w:tbl>"
        + "<w:tr>" + tc("") + tc("2024", '<w:gridSpan w:val="2"/>') + "</w:tr>"
        + "<w:tr>" + tc("Транспорт", '<w:vMerge w:val="restart"/>')
        + tc("7 670,1") + tc("99,9") + "</w:tr>"
        + "<w:tr>" + tc("", "<w:vMerge/>") + tc("1") + tc("2") + "</w:tr>"
        + "</w:tbl>"
        + p("Текст после таблицы")
    )  # fmt: skip
    content = read_docx(make_docx(tmp_path / "a.docx", body))
    assert len(content.tables) == 1
    table = content.tables[0]
    assert table.caption == "Перевозки пассажиров (миллионов человек)"
    assert table.rows == [["", "2024", ""], ["Транспорт", "7 670,1", "99,9"], ["", "1", "2"]]
    assert "Текст после таблицы" in content.text


def test_read_real_pdf_table_with_caption():
    from mintrans_stats.extract.readers import read_pdf

    content = read_pdf(FILES / "bulletin_2025_09_p8.pdf")  # страница 8 выпуска за 9 месяцев 2025
    assert "Перевозки грузов по видам транспорта" in content.text
    assert len(content.tables) == 2
    freight = content.tables[1]
    assert freight.location == "page 1"
    assert freight.caption == "1.2.1. Перевозки грузов по видам транспорта1) (миллионов тонн)"
    assert freight.rows[1] == ["Транспорт – всего", "6 908,2", "6 896,8", "99,8"]


def test_docx_caption_joins_heading_and_unit_and_skips_page_numbers():
    from mintrans_stats.extract.readers import docx_caption

    assert docx_caption(["1.3. Перевозки грузов по видам транспорта", "(миллионов тонн)"]) == (
        "1.3. Перевозки грузов по видам транспорта (миллионов тонн)"
    )
    assert docx_caption(["Заголовок раздела", "27"]) == "Заголовок раздела"
    long = "Число происшествий на транспорте и количество погибших и раненых в январе-сентябре"
    assert docx_caption(["в % к аналогичному периоду предыдущего года", long]) == long
    assert docx_caption(["15"]) == ""
