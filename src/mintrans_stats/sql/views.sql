-- Представления базы; выполнять после загрузки таблиц из schema.sql.
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
