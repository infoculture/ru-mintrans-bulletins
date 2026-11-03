"""База DuckDB с данными бюллетеня «Транспорт России».

Собирается из каталога (``catalog/*.jsonl``) и нормализованных наборов
(``datasets/bulletin_*.csv``) командой ``build-db``; сеть не нужна. Файл базы пишется рядом
во временный файл и подменяет прежний только после успешной сборки. Каждая таблица базы
выгружается ещё и в Parquet (``data/parquet/{таблица}.parquet``): из этих файлов
``open_parquet`` поднимает ту же базу в памяти, без сборки.

Таблицы и представления описаны в ``docs/database.md``; описания есть и в самой базе
(``COMMENT ON``): ``SELECT table_name, comment FROM duckdb_tables()``.
"""

from __future__ import annotations

import logging
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
# Таблицы в порядке загрузки (сначала те, на которые ссылаются другие)
TABLES = (
    "issues", "files", "source_tables", "indicators", "transport_modes", "table_values",
    "core_observations", "core_series", "checks", "build_info",
)  # fmt: skip

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

TABLES_SQL = """
CREATE TABLE issues (
    document_id   INTEGER PRIMARY KEY,
    title         VARCHAR NOT NULL,
    url           VARCHAR NOT NULL,
    published_at  DATE,
    listed_date   DATE,
    report_period VARCHAR NOT NULL,
    report_year   INTEGER NOT NULL,
    month_from    INTEGER NOT NULL,
    month_to      INTEGER NOT NULL,
    in_section    BOOLEAN NOT NULL,
    files_count   INTEGER
);
CREATE TABLE files (
    file_id           INTEGER PRIMARY KEY,
    document_id       INTEGER NOT NULL REFERENCES issues (document_id),
    title             VARCHAR,
    url               VARCHAR NOT NULL,
    site_type         VARCHAR,
    detected_type     VARCHAR,
    original_filename VARCHAR,
    size_bytes        BIGINT,
    sha256            VARCHAR,
    local_path        VARCHAR,
    downloaded_at     TIMESTAMP
);
CREATE TABLE source_tables (
    table_id    VARCHAR PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES issues (document_id),
    file_id     INTEGER NOT NULL REFERENCES files (file_id),
    member      VARCHAR,
    location    VARCHAR,
    caption     VARCHAR,
    n_rows      INTEGER,
    n_cols      INTEGER,
    csv_path    VARCHAR
);
CREATE TABLE indicators (
    indicator VARCHAR PRIMARY KEY,
    name      VARCHAR NOT NULL,
    unit      VARCHAR NOT NULL
);
CREATE TABLE transport_modes (
    mode       VARCHAR PRIMARY KEY,
    name       VARCHAR NOT NULL,
    mode_group VARCHAR NOT NULL
);
CREATE TABLE table_values (
    document_id     INTEGER NOT NULL,
    report_period   VARCHAR NOT NULL,
    report_year     INTEGER NOT NULL,
    report_month_to INTEGER NOT NULL,
    table_id        VARCHAR NOT NULL,
    location        VARCHAR,
    caption         VARCHAR,
    topic           VARCHAR,
    unit            VARCHAR,
    row_index       INTEGER NOT NULL,
    row_label       VARCHAR,
    row_note        VARCHAR,
    mode            VARCHAR,
    col_index       INTEGER NOT NULL,
    col_header      VARCHAR,
    col_period      VARCHAR,
    col_year        INTEGER,
    col_month_from  INTEGER,
    col_month_to    INTEGER,
    measure         VARCHAR NOT NULL,
    value           DOUBLE,
    value_raw       VARCHAR,
    value_note      VARCHAR
);
CREATE TABLE core_observations (
    indicator     VARCHAR NOT NULL,
    mode          VARCHAR NOT NULL,
    period        VARCHAR NOT NULL,
    year          INTEGER NOT NULL,
    month_from    INTEGER NOT NULL,
    month_to      INTEGER NOT NULL,
    value         DOUBLE NOT NULL,
    unit          VARCHAR,
    row_label     VARCHAR,
    document_id   INTEGER NOT NULL,
    report_period VARCHAR NOT NULL,
    table_id      VARCHAR NOT NULL,
    flags         VARCHAR
);
CREATE TABLE core_series (
    indicator     VARCHAR NOT NULL,
    mode          VARCHAR NOT NULL,
    period        VARCHAR NOT NULL,
    year          INTEGER NOT NULL,
    month_from    INTEGER NOT NULL,
    month_to      INTEGER NOT NULL,
    value         DOUBLE NOT NULL,
    unit          VARCHAR,
    row_label     VARCHAR,
    document_id   INTEGER NOT NULL,
    report_period VARCHAR NOT NULL,
    table_id      VARCHAR NOT NULL,
    flags         VARCHAR,
    PRIMARY KEY (indicator, mode, period)
);
CREATE TABLE checks (
    "check"   VARCHAR NOT NULL,
    indicator VARCHAR,
    mode      VARCHAR,
    period    VARCHAR,
    table_id  VARCHAR,
    expected  DOUBLE,
    actual    DOUBLE,
    diff_pct  DOUBLE,
    ok        BOOLEAN NOT NULL
);
CREATE TABLE build_info (
    key   VARCHAR PRIMARY KEY,
    value VARCHAR
);
"""

VIEWS_SQL = """
CREATE VIEW series AS
SELECT s.indicator, i.name AS indicator_name, s.mode, m.name AS mode_name, m.mode_group,
       s.period, s.year, s.month_from, s.month_to, s.value, s.unit, s.row_label, s.flags,
       s.document_id, s.report_period, s.table_id
FROM core_series s
JOIN indicators i USING (indicator)
JOIN transport_modes m USING (mode);

CREATE VIEW annual_series AS
SELECT * FROM series WHERE month_from = 1 AND month_to = 12;

CREATE VIEW values_with_source AS
SELECT v.*, i.title AS issue_title, i.url AS issue_url, t.file_id, f.url AS file_url,
       f.original_filename
FROM table_values v
JOIN issues i USING (document_id)
JOIN source_tables t USING (table_id)
JOIN files f ON f.file_id = t.file_id;
"""

_VIEWS = frozenset({"series", "annual_series", "values_with_source"})
COMMENTS = {
    "issues": "Выпуски бюллетеня: один документ сайта — один выпуск за период нарастающим итогом",
    "issues.report_period": "Период выпуска: 2025-01..09 — январь–сентябрь 2025",
    "issues.in_section": "Выпуск есть в разделе «Статистика» (иначе найден поиском по сайту)",
    "files": "Файлы выпусков (doc, docx, pdf, rar), скачанные с сайта",
    "files.site_type": "Тип файла по карточке документа на сайте",
    "files.detected_type": "Тип файла по содержимому",
    "source_tables": "Таблицы, извлечённые из файлов; CSV лежит в data/tables",
    "source_tables.member": "Файл внутри архива, если таблица из архива",
    "source_tables.location": "Место таблицы: page N (pdf) или body (docx)",
    "indicators": "Ключевые показатели: перевозки и обороты пассажиров и грузов",
    "transport_modes": "Виды транспорта; mode_group: total — итоги, mintrans — отрасли "
    "Минтранса, other — прочие ведомства",
    "table_values": "Каждая числовая ячейка каждой таблицы всех выпусков",
    "table_values.topic": "Ключевой показатель, если таблица сводная по видам транспорта",
    "table_values.mode": "Вид транспорта по подписи строки",
    "table_values.measure": "value — значение, percent — столбец «в % к …»",
    "table_values.col_period": "Период столбца по шапке: 2024, 2025-01..09",
    "table_values.value_raw": "Текст ячейки как в источнике",
    "table_values.value_note": "Сноска при числе: 2) или *",
    "core_observations": "Ключевые показатели из всех выпусков, с повторами одного периода",
    "core_observations.flags": "unit_suspect — значение в 100+ раз больше медианы ряда",
    "core_series": "Ключевые ряды без повторов: значение из самого позднего выпуска",
    "checks": "Проверки: total_vs_sum — итог против суммы видов; revision — пересмотры "
    "между выпусками; unit_suspect — вероятно другая единица",
    "build_info": "Сведения о сборке базы",
    "series": "core_series с названиями показателей и видов транспорта",
    "annual_series": "Годовые значения ключевых рядов (январь–декабрь)",
    "values_with_source": "table_values с выпуском, файлом и ссылками на источник",
}


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _add_views_and_comments(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(VIEWS_SQL)
    for name, text in COMMENTS.items():
        kind = "COLUMN" if "." in name else ("VIEW" if name in _VIEWS else "TABLE")
        con.execute(f"COMMENT ON {kind} {name} IS {_quote(text)}")


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
    con.execute(TABLES_SQL)
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

    con = duckdb.connect(str(tmp))
    try:
        con.execute(TABLES_SQL)
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
