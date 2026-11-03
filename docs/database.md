# База DuckDB бюллетеня «Транспорт России»

Файл `data/bulletin.duckdb` содержит все данные бюллетеня в одной базе:
- выпуски и их файлы;
- извлечённые таблицы;
- каждое число из каждой таблицы;
- ключевые ряды по видам транспорта;
- результаты сверок.

Сборка не требует сети: база строится из каталога (`data/catalog/*.jsonl`) и нормализованных
наборов (`data/datasets/bulletin_*.csv`).

```bash
uv run mintrans-stats normalize   # если наборы ещё не собраны или изменились
uv run mintrans-stats build-db    # data/bulletin.duckdb; другой путь — --output
```

Пересборка пишет базу во временный файл и подменяет прежний только после успешного
завершения. Пока идёт сборка, открытые подключения к старой базе продолжают работать.

Каждая таблица базы выгружается и в Parquet: `data/parquet/{таблица}.parquet`, около 0,9 МБ.
Эти файлы хранятся в репозитории. Из них та же база с представлениями и описаниями
поднимается в памяти без сборки:

```python
from pathlib import Path
from mintrans_stats.database import open_database, open_parquet

con = open_parquet(Path("data/parquet"))   # всегда из Parquet
con = open_database(Path("data"))          # файл базы, если собран, иначе Parquet
```

Parquet читается и без пакета, любым инструментом:
`duckdb.sql("SELECT * FROM 'data/parquet/core_series.parquet'")`, pandas, Polars, Arrow.
Файл базы и Parquet также выложены архивами в
[релизе](https://github.com/infoculture/ru-mintrans-bulletins/releases).

Состав на 03.11.2026 (DuckDB 2.0, около 8 МБ):
- 53 выпуска с периодами от «январь–декабрь 2011» до «январь–сентябрь 2025»;
- 54 файла;
- 2143 таблицы;
- 95 434 числа;
- 1912 точек ключевых рядов;
- 1578 сверок.

## Как открыть

Командная строка DuckDB (`brew install duckdb`):

```bash
duckdb -readonly data/bulletin.duckdb
```

Python:

```python
import duckdb

con = duckdb.connect("data/bulletin.duckdb", read_only=True)
df = con.sql("SELECT * FROM annual_series WHERE indicator = 'freight_carried'").df()  # pandas
rows = con.execute("SELECT count(*) FROM table_values").fetchone()
```

В DuckDB 2.0 у результата `con.sql(...)` нет `fetchone()`. Для одной строки используйте
`con.execute(...).fetchone()`, для таблиц — `.df()` или `.fetchall()`.

Описания таблиц и столбцов хранятся в самой базе:

```sql
SELECT table_name, comment FROM duckdb_tables();
SELECT table_name, column_name, data_type, comment FROM duckdb_columns() WHERE comment IS NOT NULL;
SELECT * FROM build_info;  -- когда и из чего собрана база
```

## Схема

```text
issues ──< files ──< source_tables ──< table_values
   │                                        │ (topic, mode)
   └──< core_observations ─→ core_series ─→ series, annual_series
                                 │
                indicators, transport_modes (справочники)
checks — сверки по core_observations
```

### issues — выпуски

| Столбец | Тип | Описание |
|---|---|---|
| `document_id` | INTEGER, PK | ID документа на сайте |
| `title`, `url` | VARCHAR | название и адрес карточки |
| `published_at`, `listed_date` | DATE | дата публикации на карточке и дата в списке |
| `report_period` | VARCHAR | период выпуска нарастающим итогом: `2025-01..09` |
| `report_year`, `month_from`, `month_to` | INTEGER | то же по частям |
| `in_section` | BOOLEAN | выпуск есть в разделе «Статистика»; `false` — найден только поиском (9103) |
| `files_count` | INTEGER | число файлов |

### files — файлы выпусков

`file_id` (PK), `document_id`, `title`, `url`, `site_type` (тип по карточке),
`detected_type` (по содержимому: doc, docx, pdf, rar), `original_filename`, `size_bytes`,
`sha256`, `local_path` (относительно `data/`), `downloaded_at`.

### source_tables — извлечённые таблицы

`table_id` (PK, `15038/552522:t005`), `document_id`, `file_id`, `member` (файл внутри
архива), `location` (`page N` или `body`), `caption` (заголовок над таблицей с единицей),
`n_rows`, `n_cols`, `csv_path`. Сама таблица — CSV в `data/tables/`.

### table_values — все числа всех таблиц

Одна строка — одна числовая ячейка таблицы.

| Столбец | Описание |
|---|---|
| `document_id`, `report_period`, `report_year`, `report_month_to` | выпуск |
| `table_id`, `location`, `caption` | таблица |
| `topic` | ключевой показатель, если таблица сводная по видам транспорта; иначе NULL |
| `unit` | единица по подписи таблицы или `%` для столбцов «в % к …» |
| `row_index`, `row_label`, `row_note` | строка: подпись без сноски и сноска |
| `mode` | вид транспорта по подписи строки (см. `transport_modes`) или NULL |
| `col_index`, `col_header` | столбец и его шапка (уровни через ` / `) |
| `col_period`, `col_year`, `col_month_from`, `col_month_to` | период столбца: `2024-01..12` |
| `measure` | `value` — значение, `percent` — процент к базе |
| `value`, `value_raw`, `value_note` | число, текст ячейки как в источнике, сноска |

Это основная таблица для показателей, которых нет в ключевых рядах: городской транспорт,
порты, аварийность, тарифы, инвестиции. Искать удобно по `caption` и `row_label` через
`ILIKE`.

### Ключевые ряды

Четыре показателя (`indicators`) по видам транспорта (`transport_modes`) из сводных таблиц
«по видам транспорта»:

| `indicator` | Название | Единица |
|---|---|---|
| `passengers_carried` | Перевезено пассажиров | млн чел. |
| `passenger_turnover` | Пассажирооборот | млрд пасс.-км |
| `freight_carried` | Перевезено грузов | млн т |
| `freight_turnover` | Грузооборот | млрд т-км |

- `core_observations` — все наблюдения из всех выпусков, 3292 строки. Один период обычно
  встречается дважды: как текущий в своём выпуске и как прошлогодний в следующем.
- `core_series` — ряд без повторов, 1912 строк, ключ (`indicator`, `mode`, `period`). Для
  каждого периода берётся значение из самого позднего выпуска, то есть последний пересмотр.
  `document_id` и `table_id` указывают на источник значения. `flags = 'unit_suspect'` —
  значение в 100+ раз больше медианы ряда, вероятно другая единица.
- Представление `series` — `core_series` с названиями показателя и вида транспорта и
  группой вида (`mode_group`): `total` — итоги, `mintrans` — отрасли Минтранса, `other` —
  трубопроводный и промышленный железнодорожный.
- Представление `annual_series` — только годовые значения (январь–декабрь), 2010–2024.

Виды транспорта (`transport_modes`):

| `mode` | Название |
|---|---|
| `total` | Транспорт – всего (всех отраслей экономики) |
| `total_mintrans` | Транспорт отраслей Минтранса России |
| `rail`, `rail_industrial` | Железнодорожный общего пользования, промышленный железнодорожный |
| `road`, `bus` | Автомобильный; автомобильный (автобусный) |
| `air`, `sea`, `inland_water` | Воздушный, морской, внутренний водный |
| `water` | Водный (морской и внутренний вместе, выпуски 2011–2012) |
| `tram`, `trolleybus`, `metro`, `urban_electric` | Городской электрический транспорт |
| `pipeline` | Трубопроводный |

### checks — сверки

| `check` | Что проверяет | `ok = false` |
|---|---|---|
| `total_vs_sum` | итог против суммы видов транспорта в той же таблице | расхождение больше 1% |
| `revision` | одно значение (показатель, вид, период) в разных выпусках | пересмотр больше 1% |
| `unit_suspect` | то же, но различие в 100+ раз | вероятна другая единица |

`expected` — эталон (итог или значение раннего выпуска), `actual` — сумма или значение
позднего выпуска, `diff_pct` — расхождение в процентах.

### values_with_source

`table_values` вместе с выпуском и файлом: `issue_title`, `issue_url`, `file_id`, `file_url`,
`original_filename`. По любому числу видно, откуда оно взято.

## Что важно знать о данных

- **Состав итогов менялся.**
  - В пассажирских таблицах до 2021 года был только итог по отраслям Минтранса
    (`total_mintrans`), с 2021 года есть и «Транспорт – всего» (`total`).
  - В грузовых таблицах до 2016 года оба итога включали промышленный железнодорожный
    транспорт (`rail_industrial`, около 3 млрд т в год), с 2017 года — нет. Поэтому итоги в
    2017 году падают примерно на 2,7 млрд т. Сопоставимый ряд —
    `total_mintrans − rail_industrial`; он совпадает с суммой видов.
  - Ряды с разным составом не склеиваются. Перед сравнением лет проверяйте `row_label` и
    `checks`.
- **Пересмотры.** В 310 случаях из 936 значение за один период в разных выпусках
  различается больше чем на 1%. Это и уточнения, и смена состава. В `core_series` стоит
  последнее значение.
- **Ошибки источника.**
  - Пассажирооборот за I квартал 2019 года: сумма видов 116,4 при итоге 111,4.
  - Воздушные грузы за I полугодие 2023 и 2024 годов в одном из выпусков даны как 211 и
    218,4 «млн т» вместо 0,211 и 0,218. В `core_series` стоят верные значения.
- **Периоды нарастающим итогом.** Значение за `2024-01..06` — это январь–июнь, а не
  II квартал. Квартал получается разностью соседних периодов (пример ниже).
- **Последний выпуск** — январь–сентябрь 2025 года, опубликован 11.12.2025.

## Примеры запросов

Годовые перевозки грузов по видам транспорта, столбец на вид:

```sql
PIVOT (
    SELECT year, mode, value FROM annual_series
    WHERE indicator = 'freight_carried'
      AND mode IN ('total', 'rail', 'road', 'pipeline', 'sea', 'inland_water', 'air')
)
ON mode USING first(value)
ORDER BY year;
```

Темп роста к прошлому году — пассажирооборот авиации:

```sql
SELECT year, value,
       round(100 * (value / lag(value) OVER (ORDER BY year) - 1), 1) AS yoy_pct
FROM annual_series
WHERE indicator = 'passenger_turnover' AND mode = 'air'
ORDER BY year;
```

Квартальные значения из нарастающего итога — грузооборот железной дороги:

```sql
SELECT period, value AS cumulative,
       round(value - coalesce(lag(value) OVER (PARTITION BY year ORDER BY month_to), 0), 1)
           AS quarter
FROM series
WHERE indicator = 'freight_turnover' AND mode = 'rail' AND year BETWEEN 2023 AND 2024
ORDER BY year, month_to;
```

Доля автомобильного транспорта в перевозках грузов отраслей Минтранса:

```sql
SELECT year,
       round(100 * max(value) FILTER (mode = 'road') /
             max(value) FILTER (mode = 'total_mintrans'), 1) AS road_share_pct
FROM annual_series
WHERE indicator = 'freight_carried'
GROUP BY year ORDER BY year;
```

Любой показатель из таблиц выпусков — перевалка грузов в морских портах:

```sql
SELECT report_period, col_period, value, unit, caption
FROM table_values
WHERE caption ILIKE '%перевалки грузов в морских портах%'
  AND row_label ILIKE 'всего%' AND measure = 'value'
ORDER BY report_period DESC, col_period DESC;
```

Откуда взято значение — ссылка на выпуск и файл:

```sql
SELECT report_period, col_header, value_raw, issue_url, file_url, original_filename, table_id
FROM values_with_source
WHERE topic = 'freight_carried' AND mode = 'rail'
  AND col_period = '2024-01..12' AND measure = 'value';
```

Несошедшиеся сверки и самые большие пересмотры:

```sql
SELECT * FROM checks WHERE NOT ok AND "check" <> 'revision';

SELECT indicator, mode, period, expected, actual, diff_pct
FROM checks WHERE "check" = 'revision' AND NOT ok
ORDER BY abs(diff_pct) DESC LIMIT 10;
```

Покрытие рядов по годам:

```sql
SELECT indicator, mode, min(year) AS first_year, max(year) AS last_year, count(*) AS years
FROM annual_series GROUP BY ALL ORDER BY indicator, mode;
```

Выгрузка в Parquet или CSV:

```sql
COPY (SELECT * FROM series) TO 'bulletin_series.parquet' (FORMAT parquet);
COPY (SELECT * FROM annual_series) TO 'bulletin_annual.csv' (HEADER);
```

Ещё примеры, с графиками, — в тетрадке [examples.ipynb](examples.ipynb).
