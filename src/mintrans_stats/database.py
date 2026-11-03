"""База DuckDB с данными бюллетеня «Транспорт России».

Собирается из каталога (``catalog/*.jsonl``) и нормализованных наборов
(``datasets/bulletin_*.csv``) командой ``build-db``; сеть не нужна. Файл базы пишется рядом
во временный файл и подменяет прежний только после успешной сборки. Каждая таблица базы
выгружается ещё и в Parquet (``data/parquet/{таблица}.parquet``): из этих файлов
``open_parquet`` поднимает ту же базу в памяти, без сборки.

Таблицы и представления описаны в ``docs/database.md``, их SQL — в ``sql/schema.sql``,
``sql/views.sql``, ``sql/comments.sql``; описания есть и в самой базе (``COMMENT ON``):
``SELECT table_name, comment FROM duckdb_tables()``.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

import duckdb

from . import __version__
from .classify import is_bulletin
from .config import Settings
from .normalize.parsing import parse_period
from .utils import read_jsonl, utc_now_iso, write_json

logger = logging.getLogger(__name__)

DEFAULT_DB_NAME = "bulletin.duckdb"
PARQUET_DIR_NAME = "parquet"
# Схема, представления и описания — в файлах sql/: их же использует архив Parquet без пакета
SQL_DIR = Path(__file__).parent / "sql"
# Файл базы читается DuckDB 1.0 и новее, а не только той версией, что его записала
STORAGE_COMPATIBILITY = "v1.0.0"


def read_sql(name: str) -> str:
    return (SQL_DIR / name).read_text(encoding="utf-8")


# Таблицы в порядке загрузки (как в schema.sql: сначала те, на которые ссылаются другие)
TABLES = tuple(re.findall(r"^CREATE TABLE (\w+)", read_sql("schema.sql"), flags=re.MULTILINE))

INDICATORS = [
    ("passengers_carried", "Перевезено пассажиров", "млн чел."),
    ("passenger_turnover", "Пассажирооборот", "млрд пасс.-км"),
    ("freight_carried", "Перевезено грузов", "млн т"),
    ("freight_turnover", "Грузооборот", "млрд т-км"),
]
# (код, название, группа): total — итоги, mintrans — виды отраслей Минтранса, other — прочие
TRANSPORT_MODES = [
    ("total", "Транспорт – всего (всех отраслей экономики)", "total"),
    ("total_mintrans", "Транспорт отраслей Минтранса России", "total"),
    ("rail", "Железнодорожный (общего пользования)", "mintrans"),
    ("road", "Автомобильный", "mintrans"),
    ("bus", "Автомобильный (автобусный)", "mintrans"),
    ("air", "Воздушный", "mintrans"),
    ("sea", "Морской", "mintrans"),
    ("inland_water", "Внутренний водный", "mintrans"),
    ("water", "Водный (морской и внутренний вместе, 2011–2012)", "mintrans"),
    ("tram", "Трамвайный", "mintrans"),
    ("trolleybus", "Троллейбусный", "mintrans"),
    ("metro", "Метрополитен", "mintrans"),
    ("urban_electric", "Городской электрический", "mintrans"),
    ("rail_industrial", "Промышленный железнодорожный", "other"),
    ("pipeline", "Трубопроводный", "other"),
]


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _add_views_and_comments(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(read_sql("views.sql"))
    con.execute(read_sql("comments.sql"))


def export_parquet(con: duckdb.DuckDBPyConnection, out_dir: Path) -> dict[str, int]:
    """Выгрузить каждую таблицу в ``{out_dir}/{таблица}.parquet``; вернуть размеры файлов.

    Файлы пишутся в соседний временный каталог, который затем подменяет прежний.
    """
    tmp = out_dir.with_name(out_dir.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    for table in TABLES:
        target = tmp / f"{table}.parquet"
        con.execute(f"COPY {table} TO {_quote(str(target))} (FORMAT parquet, COMPRESSION zstd)")
    shutil.rmtree(out_dir, ignore_errors=True)
    tmp.replace(out_dir)
    return {p.name: p.stat().st_size for p in sorted(out_dir.glob("*.parquet"))}


def open_parquet(parquet_dir: Path) -> duckdb.DuckDBPyConnection:
    """База в памяти из Parquet: те же таблицы, представления и описания, что в файле базы."""
    con = duckdb.connect()
    con.execute(read_sql("schema.sql"))
    for table in TABLES:
        path = parquet_dir / f"{table}.parquet"
        if not path.is_file():
            raise FileNotFoundError(f"{path}: нет файла Parquet, запустите mintrans-stats build-db")
        con.execute(f"INSERT INTO {table} SELECT * FROM read_parquet({_quote(str(path))})")
    _add_views_and_comments(con)
    return con


def open_database(data_dir: Path) -> duckdb.DuckDBPyConnection:
    """Файл базы (только чтение), если он собран, иначе база в памяти из Parquet."""
    db = data_dir / DEFAULT_DB_NAME
    if db.is_file():
        return duckdb.connect(str(db), read_only=True)
    return open_parquet(data_dir / PARQUET_DIR_NAME)


def _issue_rows(settings: Settings) -> list[tuple]:
    rows = []
    for doc in read_jsonl(settings.catalog_dir / "documents.jsonl"):
        period = parse_period(doc["title"])
        if not is_bulletin(doc["title"]) or period is None:
            continue
        rows.append(
            (
                int(doc["document_id"]),
                doc["title"],
                doc["document_url"],
                doc.get("published_at"),
                doc.get("listed_date"),
                period.label,
                period.year,
                period.month_from,
                period.month_to,
                any(s["kind"] == "section" for s in doc.get("sources", [])),
                doc.get("files_count"),
            )
        )
    return rows


def _file_rows(settings: Settings, issues: set[int]) -> list[tuple]:
    rows = []
    for f in read_jsonl(settings.catalog_dir / "downloads.jsonl"):
        if int(f["document_id"]) not in issues:
            continue
        rows.append(
            (
                int(f["file_id"]),
                int(f["document_id"]),
                f.get("file_title"),
                f["file_url"],
                f.get("file_type"),
                f.get("detected_type"),
                f.get("original_filename"),
                f.get("saved_file_size_bytes"),
                f.get("sha256"),
                f.get("local_path"),
                f.get("downloaded_at"),
            )
        )
    return rows


def _table_rows(settings: Settings, files: set[int]) -> list[tuple]:
    return [
        (
            t["table_id"],
            int(t["document_id"]),
            int(t["file_id"]),
            t.get("member"),
            t.get("location"),
            t.get("caption"),
            t.get("n_rows"),
            t.get("n_cols"),
            t.get("csv_path"),
        )
        for t in read_jsonl(settings.catalog_dir / "tables.jsonl")
        if t.get("file_id") and int(t["file_id"]) in files
    ]


def _load_csv(con: duckdb.DuckDBPyConnection, table: str, path: Path) -> int:
    """Загрузить CSV в готовую таблицу; типы берутся из схемы таблицы."""
    if not path.is_file():
        raise FileNotFoundError(f"{path}: сначала запустите mintrans-stats normalize")
    con.execute(
        f"INSERT INTO {table} BY NAME SELECT * FROM read_csv(?, header = true, "
        "all_varchar = true, nullstr = '')",
        [str(path)],
    )
    return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def build_database(
    settings: Settings, path: Path | None = None, parquet_dir: Path | None = None
) -> dict:
    started = utc_now_iso()
    path = path or settings.data_dir / DEFAULT_DB_NAME
    parquet_dir = parquet_dir or settings.data_dir / PARQUET_DIR_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.duckdb")
    tmp.unlink(missing_ok=True)
    datasets = settings.data_dir / "datasets"

    con = duckdb.connect(str(tmp), config={"storage_compatibility_version": STORAGE_COMPATIBILITY})
    try:
        con.execute(read_sql("schema.sql"))
        issues = _issue_rows(settings)
        con.executemany(f"INSERT INTO issues VALUES ({', '.join('?' * 11)})", issues)
        issue_ids = {r[0] for r in issues}
        files = _file_rows(settings, issue_ids)
        con.executemany(f"INSERT INTO files VALUES ({', '.join('?' * 11)})", files)
        tables = _table_rows(settings, {r[0] for r in files})
        con.executemany(f"INSERT INTO source_tables VALUES ({', '.join('?' * 9)})", tables)
        con.executemany("INSERT INTO indicators VALUES (?, ?, ?)", INDICATORS)
        con.executemany("INSERT INTO transport_modes VALUES (?, ?, ?)", TRANSPORT_MODES)
        counts = {
            "issues": len(issues),
            "files": len(files),
            "source_tables": len(tables),
            "table_values": _load_csv(con, "table_values", datasets / "bulletin_values.csv"),
            "core_observations": _load_csv(
                con, "core_observations", datasets / "bulletin_core.csv"
            ),
            "core_series": _load_csv(con, "core_series", datasets / "bulletin_core_series.csv"),
            "checks": _load_csv(con, "checks", datasets / "bulletin_checks.csv"),
        }
        _add_views_and_comments(con)
        info = {
            "built_at": utc_now_iso(),
            "mintrans_stats_version": __version__,
            "duckdb_version": duckdb.__version__,
            "source": "https://mintrans.gov.ru/ministry/results/180/documents",
            "periods": f"{min(r[5] for r in issues)} — {max(r[5] for r in issues)}"
            if issues
            else "",
            **{f"rows.{k}": str(v) for k, v in counts.items()},
        }
        con.executemany("INSERT INTO build_info VALUES (?, ?)", list(info.items()))
        con.execute("CHECKPOINT")
        parquet = export_parquet(con, parquet_dir)
    finally:
        con.close()
    tmp.replace(path)

    summary = {
        "command": "build-db",
        "started_at": started,
        "finished_at": utc_now_iso(),
        "database": str(path),
        "size_bytes": path.stat().st_size,
        "parquet_dir": str(parquet_dir),
        "parquet_bytes": sum(parquet.values()),
        "rows": counts,
    }
    write_json(settings.catalog_dir / "runs" / f"build-db-{started.replace(':', '')}.json", summary)
    return summary
