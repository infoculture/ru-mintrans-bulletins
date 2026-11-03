-- Таблицы базы бюллетеня «Транспорт России» (DuckDB). Описание — docs/database.md.
-- Порядок таблиц — порядок загрузки: сначала те, на которые ссылаются другие.
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
