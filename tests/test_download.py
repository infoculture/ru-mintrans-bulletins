import json
import shutil
from pathlib import Path

import httpx
import pytest

from mintrans_stats.download import (
    determine_extension,
    find_existing,
    original_filename,
    run_download,
)

OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 100
FILES = Path(__file__).parent / "fixtures" / "files"


@pytest.mark.parametrize(
    ("detected", "hint", "kwargs", "expected"),
    [
        ("pdf", "bin", {}, "pdf"),
        ("docx", "doc", {}, "docx"),  # сайт пишет doc, а внутри docx — верим содержимому
        ("ole", "xls", {}, "xls"),
        ("ole", "", {"content_disposition": 'attachment; filename="t.xls"'}, "xls"),
        ("ole", "", {}, "doc"),
        ("", "bin", {"content_disposition": 'attachment; filename="table.csv"'}, "csv"),
        ("", "bin", {"content_type": "application/msword"}, "doc"),
        ("", "pdf", {}, "pdf"),
        ("", "weird", {}, "bin"),
    ],
)
def test_determine_extension(detected, hint, kwargs, expected):
    assert determine_extension(detected, hint, **kwargs) == expected


def test_original_filename_variants():
    assert original_filename("attachment; filename*=UTF-8''%D0%BE%D1%82%D1%87%D0%B5%D1%82.pdf") == (
        "отчет.pdf"
    )
    mojibake = "отчет.pdf".encode().decode("latin-1")
    assert original_filename(f'attachment; filename="{mojibake}"') == "отчет.pdf"
    assert original_filename("") == ""


def test_find_existing_ignores_partial(tmp_path):
    (tmp_path / "1").mkdir()
    (tmp_path / "1" / "2.pdf.part").write_bytes(b"%PDF")
    assert find_existing(tmp_path, "1", "2") is None
    (tmp_path / "1" / "2.pdf").write_bytes(b"%PDF-1.4")
    assert find_existing(tmp_path, "1", "2") == tmp_path / "1" / "2.pdf"


def test_find_existing_migrates_legacy_layout_and_fixes_extension(tmp_path):
    legacy = tmp_path / "doc_3192__file_398905.xls"  # сайт назвал xls, внутри Word
    shutil.copy(FILES / "bulletin_table.doc", legacy)
    found = find_existing(tmp_path, "3192", "398905", "xls")
    assert found == tmp_path / "3192" / "398905.doc"
    assert found.is_file() and not legacy.exists()


ROW = {
    "document_id": "15038",
    "document_title": "ТРАНСПОРТ РОССИИ. Информационно-статистический бюллетень. 9 месяцев 2025",
    "document_url": "https://mintrans.gov.ru/documents/7/15038",
    "file_id": "552522",
    "file_title": "Файл",
    "file_url": "https://mintrans.gov.ru/file/552522",
    "file_type": "bin",
    "file_size": "1 kb",
}


def write_rows(path, rows):
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )


def read_manifest(settings):
    return [json.loads(line) for line in (settings.catalog_dir / "downloads.jsonl").open()]


def pdf_handler(calls, body=b"%PDF-1.4 data"):
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(
            200, content=body, headers={"content-disposition": 'attachment; filename="r.pdf"'}
        )

    return handler


def test_run_download_detects_type_and_keeps_meta_on_rerun(make_client, settings, tmp_path):
    source = tmp_path / "in.jsonl"
    write_rows(source, [ROW])
    calls: list[str] = []
    client = make_client(pdf_handler(calls))

    summary = run_download(client, settings, input_jsonl=source)
    assert summary["downloaded"] == 1 and summary["failed"] == 0
    first = read_manifest(settings)[0]
    assert first["local_path"] == "files/15038/552522.pdf"
    assert first["detected_type"] == "pdf"
    assert first["original_filename"] == "r.pdf"
    assert (settings.data_dir / first["local_path"]).read_bytes() == b"%PDF-1.4 data"
    assert not list((settings.files_dir / "15038").glob("*.download*"))

    summary = run_download(client, settings, input_jsonl=source)
    assert summary["skipped"] == 1 and summary["downloaded"] == 0
    assert len(calls) == 1
    second = read_manifest(settings)[0]
    # повторный запуск не теряет сведения из заголовков и исходное время скачивания
    assert second["original_filename"] == "r.pdf"
    assert second["downloaded_at"] == first["downloaded_at"]
    assert second["verified_at"] >= first["verified_at"]


def test_run_download_records_changed_file(make_client, settings, tmp_path):
    source = tmp_path / "in.jsonl"
    write_rows(source, [ROW])
    calls: list[str] = []
    run_download(make_client(pdf_handler(calls)), settings, input_jsonl=source)
    old_sha = read_manifest(settings)[0]["sha256"]

    summary = run_download(
        make_client(pdf_handler(calls, b"%PDF-1.4 new version")),
        settings,
        input_jsonl=source,
        resume=False,
    )
    assert summary["changed"] == 1
    row = read_manifest(settings)[0]
    assert row["previous_sha256"] == old_sha and row["sha256"] != old_sha


def test_run_download_rejects_html_instead_of_file(make_client, settings, tmp_path):
    source = tmp_path / "in.jsonl"
    write_rows(source, [ROW])
    client = make_client(lambda r: httpx.Response(200, text="<!DOCTYPE html><html>Ошибка</html>"))
    summary = run_download(client, settings, input_jsonl=source)
    assert summary["failed"] == 1 and "веб-страницу" in summary["errors"][0]["error"]
    assert not [p for p in settings.files_dir.rglob("*") if p.is_file()]


def test_run_download_fill_meta_for_existing(make_client, settings, tmp_path):
    target = settings.files_dir / "15038" / "552522.pdf"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"%PDF-1.4 data")
    source = tmp_path / "in.jsonl"
    write_rows(source, [ROW])
    calls: list[str] = []
    summary = run_download(
        make_client(pdf_handler(calls)), settings, input_jsonl=source, fill_meta=True
    )
    assert summary["skipped"] == 1 and summary["meta_filled"] == 1 and len(calls) == 1
    assert read_manifest(settings)[0]["original_filename"] == "r.pdf"
    assert target.read_bytes() == b"%PDF-1.4 data"  # файл не перекачан


def test_run_download_limit_keeps_other_manifest_rows(make_client, settings, tmp_path):
    second = {**ROW, "file_id": "2", "file_url": "https://mintrans.gov.ru/file/2"}
    source = tmp_path / "in.jsonl"
    write_rows(source, [ROW, second])
    client = make_client(pdf_handler([]))
    run_download(client, settings, input_jsonl=source)
    run_download(client, settings, input_jsonl=source, limit=1)
    assert {r["file_id"] for r in read_manifest(settings)} == {"552522", "2"}


def test_run_download_stops_on_block(make_client, settings, tmp_path):
    source = tmp_path / "in.jsonl"
    second = {**ROW, "file_id": "2", "file_url": "https://mintrans.gov.ru/file/2"}
    source.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in (ROW, second)), encoding="utf-8"
    )
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429)

    summary = run_download(make_client(handler), settings, input_jsonl=source)
    assert summary["failed"] == 1 and len(calls) == 1
