"""Проверка строк JSONL и файлов на диске."""

from __future__ import annotations

import json
from pathlib import Path

from .config import BASE_URL
from .http import sha256_file

REQUIRED_FIELDS = ("document_title", "document_url", "file_title", "file_url", "file_type")
DOWNLOAD_FIELDS = ("local_path", "sha256", "saved_file_size_bytes", "downloaded_at")


class ValidationError(ValueError):
    """Ошибка в одной строке JSONL."""


def validate_row(row: dict, *, require_download_fields: bool = False) -> None:
    """Обязательные поля непусты, ссылки абсолютные и ведут на mintrans.gov.ru.

    ``file_size`` не обязателен: на сайте размер у вложения бывает не указан.
    """
    if not isinstance(row, dict):
        raise ValidationError("строка должна быть JSON-объектом")
    for name in REQUIRED_FIELDS:
        value = row.get(name)
        if value is None or not str(value).strip():
            raise ValidationError(f"пустое обязательное поле: {name}")
    for name in ("document_url", "file_url"):
        if not str(row[name]).startswith(f"{BASE_URL}/"):
            raise ValidationError(
                f"{name} должен быть абсолютной ссылкой на {BASE_URL}: {row[name]}"
            )
    if require_download_fields:
        for name in DOWNLOAD_FIELDS:
            value = row.get(name)
            if value is None or (isinstance(value, str) and not value.strip()):
                raise ValidationError(f"пустое поле скачивания: {name}")


def validate_jsonl(
    path: Path,
    *,
    data_dir: Path,
    require_download_fields: bool = False,
    check_files: bool = False,
) -> tuple[int, list[str]]:
    """Проверить все строки; при ``check_files`` сверить файлы с sha256 и размером.

    ``local_path`` в строках задан относительно ``data_dir``.
    """
    errors: list[str] = []
    count = 0
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            count += 1
            try:
                row = json.loads(line)
                validate_row(row, require_download_fields=require_download_fields)
            except (json.JSONDecodeError, ValidationError) as exc:
                errors.append(f"строка {line_no}: {exc}")
                continue
            if check_files and row.get("local_path"):
                errors.extend(_check_file(row, data_dir, line_no))
    return count, errors


def _check_file(row: dict, data_dir: Path, line_no: int) -> list[str]:
    full = data_dir / row["local_path"]
    if not full.is_file():
        return [f"строка {line_no}: нет файла {full}"]
    problems = []
    expected_size = row.get("saved_file_size_bytes")
    if expected_size is not None and full.stat().st_size != expected_size:
        problems.append(f"строка {line_no}: размер не совпадает для {row['local_path']}")
    expected_sha = row.get("sha256")
    if expected_sha and sha256_file(full) != expected_sha:
        problems.append(f"строка {line_no}: sha256 не совпадает для {row['local_path']}")
    return problems
