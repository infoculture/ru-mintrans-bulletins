import json
import shutil
import zipfile
from pathlib import Path

import pytest

from mintrans_stats.extract import convert
from mintrans_stats.extract.convert import ConversionError, convert_to_docx, find_soffice
from mintrans_stats.extract.readers import read_docx
from mintrans_stats.extract.run import run_extract
from mintrans_stats.http import sha256_file
from test_readers import make_docx, p

FILES = Path(__file__).parent / "fixtures" / "files"


def add_file(settings, document_id, file_id, src: Path, kind: str) -> dict:
    target = settings.files_dir / document_id / f"{file_id}.{kind}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, target)
    return {
        "document_id": document_id,
        "file_id": file_id,
        "local_path": target.relative_to(settings.data_dir).as_posix(),
        "detected_type": kind,
        "sha256": sha256_file(target),
    }


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.open(encoding="utf-8")]


@pytest.fixture
def small_site(settings, tmp_path):
    docx = make_docx(
        tmp_path / "t.docx",
        p("Таблица 1") + "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>a</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>1</w:t></w:r></w:p></w:tc></w:tr></w:tbl>",
    )
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(docx, "isb_2012/таблица.docx")
        zf.writestr("readme.txt", "просто текст")
    rows = [
        add_file(settings, "15038", "552522", FILES / "bulletin_2025_09_p8.pdf", "pdf"),
        add_file(settings, "3192", "398905", archive, "zip"),
    ]
    write_jsonl(settings.catalog_dir / "downloads.jsonl", rows)
    return settings


def test_run_extract_end_to_end(small_site):
    settings = small_site
    summary = run_extract(settings)
    records = {r["item_id"]: r for r in read_jsonl(settings.catalog_dir / "extracted.jsonl")}
    assert set(records) == {"15038/552522", "3192/398905#1", "3192/398905#2"}
    pdf = records["15038/552522"]
    assert pdf["status"] == "ok" and pdf["tables"] == 2 and not pdf["needs_ocr"]
    members = {r["member"]: r for r in records.values() if r["member"]}
    # члены архива нумеруются по отсортированным путям
    assert members["isb_2012/таблица.docx"]["item_id"] == "3192/398905#1"
    assert members["readme.txt"]["status"] == "unsupported"
    member = members["isb_2012/таблица.docx"]
    assert member["kind"] == "docx" and member["tables"] == 1

    tables = read_jsonl(settings.catalog_dir / "tables.jsonl")
    assert len(tables) == 3
    freight = next(t for t in tables if t["caption"].startswith("1.2.1."))
    assert freight["table_id"] == "15038/552522:t002" and freight["location"] == "page 1"
    docx_table = next(t for t in tables if t["item_id"] == "3192/398905#1")
    assert docx_table["caption"] == "Таблица 1"
    csv_file = settings.data_dir / docx_table["csv_path"]
    assert csv_file.read_text(encoding="utf-8").strip() == "a,1"
    assert summary["tables"] == 3 and summary["errors"] == []
    assert summary["files_without_tables"] == []

    again = run_extract(settings)
    assert again["items_reused"] == 3 and again["tables"] == 3
    assert run_extract(settings, force=True)["items_reused"] == 0


def test_run_extract_reports_broken_file(small_site):
    settings = small_site
    broken = settings.files_dir / "15038" / "552522.pdf"
    broken.write_bytes(b"%PDF-1.4 broken")
    rows = read_jsonl(settings.catalog_dir / "downloads.jsonl")
    rows[0]["sha256"] = sha256_file(broken)
    write_jsonl(settings.catalog_dir / "downloads.jsonl", rows)
    summary = run_extract(settings)
    assert any(e["item_id"] == "15038/552522" for e in summary["errors"])
    assert "15038/552522" in summary["files_without_tables"]


def test_convert_without_libreoffice(monkeypatch, tmp_path):
    monkeypatch.setattr(convert, "find_soffice", lambda: None)
    with pytest.raises(ConversionError):
        convert_to_docx([tmp_path / "a.doc"], tmp_path / "out", tmp_path / "profile")


@pytest.mark.skipif(find_soffice() is None, reason="нужен LibreOffice")
def test_convert_real_doc_with_libreoffice(tmp_path):
    src = tmp_path / "bulletin_table.doc"
    shutil.copyfile(FILES / "bulletin_table.doc", src)
    result = convert_to_docx([src], tmp_path / "out", tmp_path / "profile")
    assert result[src].suffix == ".docx" and result[src].stat().st_size > 0
    content = read_docx(result[src])
    assert content.tables[0].caption == "1.3. Перевозки грузов по видам транспорта (миллионов тонн)"
    assert content.tables[0].rows[1] == ["Транспорт всех отраслей экономики", "5012,3", "5100,3",
                                         "101,8"]  # fmt: skip
