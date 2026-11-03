import json

import duckdb

from mintrans_stats.database import build_database, open_database, open_parquet
from mintrans_stats.normalize.bulletin import run_bulletin
from test_bulletin import two_bulletins  # noqa: F401 — фикстура


def _rewrite(path, update):
    rows = [update(json.loads(line)) for line in path.open(encoding="utf-8")]
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")


def _catalog(settings):
    """Дополнить каталог фикстуры полями, которые пишут crawl и download."""
    catalog = settings.catalog_dir
    docs = []
    for row in map(json.loads, (catalog / "downloads.jsonl").open(encoding="utf-8")):
        doc_id = row["document_id"]
        docs.append(
            {
                "document_id": doc_id,
                "document_url": f"https://mintrans.gov.ru/documents/7/{doc_id}",
                "title": row["document_title"],
                "published_at": "2024-08-01",
                "listed_date": None,
                "sources": [{"kind": "section", "section_id": 180}] if doc_id == "100" else
                           [{"kind": "search", "query": "бюллетень"}],
                "files_count": 1,
            }
        )  # fmt: skip
    (catalog / "documents.jsonl").write_text(
        "".join(json.dumps(d, ensure_ascii=False) + "\n" for d in docs), "utf-8"
    )

    def with_file(row):
        file_id = row["document_id"] + "1"
        return {
            **row,
            "file_id": file_id,
            "file_url": f"https://mintrans.gov.ru/file/{file_id}",
            "detected_type": "docx",
            "saved_file_size_bytes": 1024,
            "downloaded_at": "2026-11-03T09:16:26Z",
        }

    _rewrite(catalog / "downloads.jsonl", with_file)
    _rewrite(catalog / "tables.jsonl", lambda t: {**t, "file_id": t["document_id"] + "1"})


def test_build_database(two_bulletins):  # noqa: F811
    settings = two_bulletins
    _catalog(settings)
    run_bulletin(settings)
    summary = build_database(settings)
    path = settings.data_dir / "bulletin.duckdb"
    assert summary["database"] == str(path) and path.is_file()
    assert summary["rows"]["issues"] == 2 and summary["rows"]["files"] == 2
    assert summary["rows"]["source_tables"] == 2

    con = duckdb.connect(str(path), read_only=True)
    issues = con.execute(
        "SELECT document_id, report_period, month_to, in_section FROM issues ORDER BY 1"
    ).fetchall()
    assert issues == [(100, "2023-01..06", 6, True), (200, "2024-01..06", 6, False)]
    total = con.execute(
        "SELECT value, indicator_name, mode_name, document_id FROM series "
        "WHERE indicator = 'freight_carried' AND mode = 'total' AND period = '2023-01..06'"
    ).fetchone()
    # значение за 2023 год из более позднего выпуска (пересмотр)
    assert total == (
        4188.9, "Перевезено грузов", "Транспорт – всего (всех отраслей экономики)", 200
    )  # fmt: skip
    assert con.execute("SELECT count(*) FROM annual_series").fetchone()[0] == 0
    counts = con.execute(
        "SELECT (SELECT count(*) FROM table_values), count(*), count(file_url) "
        "FROM values_with_source"
    ).fetchone()
    assert counts[0] == counts[1] == counts[2] > 0
    assert con.execute(
        "SELECT ok FROM checks WHERE \"check\" = 'unit_suspect' AND mode = 'air'"
    ).fetchone() == (False,)
    comment = con.execute("SELECT comment FROM duckdb_tables() WHERE table_name = 'issues'")
    assert comment.fetchone()[0].startswith("Выпуски бюллетеня")
    con.close()

    again = build_database(settings)  # пересборка подменяет файл целиком
    assert again["rows"] == summary["rows"]
    assert not path.with_suffix(".tmp.duckdb").exists()


def test_parquet_export_opens_as_same_database(two_bulletins):  # noqa: F811
    settings = two_bulletins
    _catalog(settings)
    run_bulletin(settings)
    summary = build_database(settings)
    parquet = settings.data_dir / "parquet"
    assert summary["parquet_dir"] == str(parquet)
    assert {p.stem for p in parquet.glob("*.parquet")} >= {"issues", "table_values", "checks"}

    con = open_parquet(parquet)
    query = "SELECT count(*), sum(value) FROM series"
    from_parquet = con.execute(query).fetchone()
    comment = con.execute("SELECT comment FROM duckdb_tables() WHERE table_name = 'checks'")
    assert comment.fetchone()[0].startswith("Проверки")
    con.close()
    with duckdb.connect(str(settings.data_dir / "bulletin.duckdb"), read_only=True) as db:
        assert db.execute(query).fetchone() == from_parquet

    (settings.data_dir / "bulletin.duckdb").unlink()  # без файла базы — из Parquet
    con = open_database(settings.data_dir)
    assert con.execute(query).fetchone() == from_parquet
    con.close()
