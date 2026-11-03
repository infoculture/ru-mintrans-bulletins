"""Чтение таблиц и текста из выпусков бюллетеня: docx (в том числе сконвертированные doc) и pdf.

Каждый ``read_*`` возвращает ``Content``: список таблиц (строки — списки строк-ячеек) и
полный текст. Таблицы не интерпретируются: шапки, объединённые ячейки и единицы измерения
остаются как в источнике — нормализация рядов делается отдельно.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

logger = logging.getLogger(__name__)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"
CAPTION_MAX_CHARS = 300
PDF_CAPTION_ZONE_PT = 60


@dataclass
class Table:
    rows: list[list[str]]
    location: str
    """Где таблица: ``page 3`` или ``body``."""
    caption: str = ""
    """Текст непосредственно перед таблицей (заголовок), если есть."""


@dataclass
class Content:
    tables: list[Table] = field(default_factory=list)
    text: str = ""


def clean_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, dt.datetime):
        return value.date().isoformat() if value.time() == dt.time() else value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()


def trim(rows: list[list[str]]) -> list[list[str]]:
    """Убрать пустые строки и пустые столбцы по краям, выровнять длину строк."""
    rows = [r for r in rows if any(c for c in r)]
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    keep = [i for i in range(width) if any(r[i] for r in rows)]
    if not keep:
        return []
    first, last = keep[0], keep[-1]
    return [r[first : last + 1] for r in rows]


def _rows_to_text(rows: list[list[str]]) -> str:
    return "\n".join("\t".join(r) for r in rows)


# --- Word (docx) ---------------------------------------------------------------------------


def _para_text(p: etree._Element) -> str:
    parts = []
    for node in p.iter(f"{W}t", f"{W}tab", f"{W}br"):
        parts.append((node.text or "") if node.tag == f"{W}t" else " ")
    return clean_cell("".join(parts))


def _cell_text(tc: etree._Element) -> str:
    return clean_cell(" ".join(t for p in tc.iter(f"{W}p") if (t := _para_text(p))))


def _docx_table(tbl: etree._Element) -> list[list[str]]:
    """Строки таблицы; ``gridSpan`` даёт пустые ячейки справа, продолжение ``vMerge`` — пустую."""
    rows = []
    for tr in tbl.iterchildren(f"{W}tr"):
        row: list[str] = []
        for tc in tr.iterchildren(f"{W}tc"):
            props = tc.find(f"{W}tcPr")
            span = 1
            continued = False
            if props is not None:
                grid = props.find(f"{W}gridSpan")
                if grid is not None:
                    span = int(grid.get(f"{W}val", "1") or 1)
                vmerge = props.find(f"{W}vMerge")
                continued = vmerge is not None and vmerge.get(f"{W}val") != "restart"
            row.append("" if continued else _cell_text(tc))
            row.extend([""] * (span - 1))
        rows.append(row)
    return rows


PAGE_NUMBER_RE = re.compile(r"^\d{1,3}$")
SHORT_CAPTION_CHARS = 60


def docx_caption(paragraphs: list[str]) -> str:
    """Подпись таблицы из абзацев между предыдущей таблицей и этой.

    Обычно это заголовок и строка единиц: «1.3. Перевозки грузов…» + «(миллионов тонн)».
    Если последний абзац короткий (единицы, «в процентах»), к нему добавляется предыдущий.
    Номера страниц (абзац из одних цифр) пропускаются.
    """
    useful = [p for p in paragraphs if not PAGE_NUMBER_RE.match(p)]
    if not useful:
        return ""
    last = useful[-1]
    if len(useful) > 1 and (len(last) < SHORT_CAPTION_CHARS or last.startswith("(")):
        last = f"{useful[-2]} {last}"
    return last[:CAPTION_MAX_CHARS]


def read_docx(path: Path) -> Content:
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml")
    parser = etree.XMLParser(huge_tree=True, resolve_entities=False, no_network=True)
    root = etree.fromstring(xml, parser)
    body = root.find(f"{W}body")
    content = Content()
    texts: list[str] = []
    since_table: list[str] = []
    if body is None:
        return content
    for node in body.iterchildren():
        if node.tag == f"{W}p":
            text = _para_text(node)
            if text:
                texts.append(text)
                since_table.append(text)
        elif node.tag == f"{W}tbl":
            rows = trim(_docx_table(node))
            if rows:
                content.tables.append(Table(rows, "body", caption=docx_caption(since_table)))
                texts.append(_rows_to_text(rows))
            since_table = []
    content.text = "\n".join(texts)
    return content


# --- PDF -----------------------------------------------------------------------------------


def read_pdf(path: Path) -> Content:
    import pdfplumber

    content = Content()
    texts = []
    with pdfplumber.open(path) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            # сканы ABBYY FineReader дублируют символы (жирный шрифт наложением)
            page = page.dedupe_chars()
            texts.append(page.extract_text() or "")
            for found in page.find_tables():
                rows = trim([[clean_cell(c) for c in row] for row in found.extract()])
                if not rows or len(rows[0]) < 2:
                    continue  # рамка вокруг абзаца или врезки, а не таблица
                top = found.bbox[1]
                caption = ""
                if top > 1:
                    zone = page.crop((0, max(0, top - PDF_CAPTION_ZONE_PT), page.width, top))
                    lines = [ln for ln in (zone.extract_text() or "").splitlines() if ln.strip()]
                    caption = clean_cell(" ".join(lines[-2:]))[:CAPTION_MAX_CHARS]
                content.tables.append(Table(rows, f"page {number}", caption=caption))
    content.text = "\f".join(texts)
    return content


READERS = {
    "docx": read_docx,
    "pdf": read_pdf,
}
