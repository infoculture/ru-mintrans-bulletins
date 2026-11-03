# Сайт Минтранса отвечает только российским IP; см. docs/operations.md
MINTRANS_PROXY ?=

.PHONY: venv collect build test notebook notebook-run all

venv:
	uv sync

# каталог выпусков и их файлы (нужна сеть с российским IP)
collect:
	MINTRANS_PROXY=$(MINTRANS_PROXY) uv run mintrans-stats crawl
	MINTRANS_PROXY=$(MINTRANS_PROXY) uv run mintrans-stats download
	uv run mintrans-stats validate --require-download

# таблицы из файлов (нужны LibreOffice и bsdtar), длинный формат, база DuckDB и Parquet
build:
	uv run mintrans-stats extract
	uv run mintrans-stats normalize
	uv run mintrans-stats build-db

test:
	uv run pytest -q

# Тетрадка с примерами: зависимости группы notebook и JupyterLab
notebook:
	uv run --group notebook jupyter lab docs/examples.ipynb

# Пересчитать тетрадку и сохранить результаты ячеек в файл
notebook-run:
	uv run --group notebook jupyter nbconvert --to notebook --execute --inplace \
		--ExecutePreprocessor.record_timing=False docs/examples.ipynb

all: collect build test
