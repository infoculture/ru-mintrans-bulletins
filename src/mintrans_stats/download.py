"""Скачивание выпусков бюллетеня по каталогу из ``crawl``.

Вложения (``catalog/files.jsonl``) сохраняются как
``<data_dir>/files/{document_id}/{file_id}.{ext}``, где ``ext`` определяется по содержимому
(``filetypes.detect_type``). Манифест — ``catalog/downloads.jsonl``.

Манифест переписывается при каждом запуске, но сведения о файле, который уже был скачан
(исходное имя, ``Content-Type``, время скачивания), переносятся из прошлого манифеста, если
содержимое не изменилось. Если файл скачан заново и изменился, прежний хеш сохраняется в
``previous_sha256``.
"""

from __future__ import annotations

import contextlib
import logging
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from .config import Settings
from .filetypes import detect_type
from .http import BlockedError, FetchError, MintransClient, MintransError, sha256_file
from .utils import read_jsonl, utc_now_iso, write_json, write_jsonl
from .validate import validate_row

logger = logging.getLogger(__name__)

KNOWN_EXTENSIONS = frozenset(
    {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "rtf", "odt", "ods", "odp", "zip",
     "rar", "7z", "csv", "txt", "jpg", "jpeg", "png", "tif", "tiff"}
)  # fmt: skip

CONTENT_TYPE_EXT = {
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/zip": "zip",
    "application/x-rar-compressed": "rar",
    "application/x-rar": "rar",
    "application/vnd.rar": "rar",
    "text/csv": "csv",
}

FILENAME_STAR_RE = re.compile(r"filename\*\s*=\s*(?:[\w-]+'[^']*')?([^;]+)", re.IGNORECASE)
FILENAME_RE = re.compile(r'filename\s*=\s*"?([^";]+)"?', re.IGNORECASE)
FILE_ID_RE = re.compile(r"/file/(\d+)")
DOC_ID_RE = re.compile(r"/documents/\d+/(\d+)")
TEMP_SUFFIXES = frozenset({".part", ".tmp"})

# Сведения о файле, которые известны только из ответа сервера при скачивании
HEADER_FIELDS = ("original_filename", "content_type", "last_modified")


class NotAFileError(FetchError):
    """Сервер вернул веб-страницу вместо файла."""


def original_filename(content_disposition: str) -> str:
    """Имя файла из ``Content-Disposition`` (``filename*`` приоритетнее)."""
    if not content_disposition:
        return ""
    match = FILENAME_STAR_RE.search(content_disposition)
    if match:
        return unquote(match.group(1).strip().strip('"'))
    match = FILENAME_RE.search(content_disposition)
    if not match:
        return ""
    name = match.group(1).strip()
    # сервер может отдавать UTF-8 байты в заголовке, которые httpx декодирует как latin-1
    with contextlib.suppress(UnicodeEncodeError, UnicodeDecodeError):
        name = name.encode("latin-1").decode("utf-8")
    return name


def header_meta(headers: dict[str, str]) -> dict[str, str]:
    return {
        "original_filename": original_filename(headers.get("content-disposition", "")),
        "content_type": headers.get("content-type", ""),
        "last_modified": headers.get("last-modified", ""),
    }


def determine_extension(
    detected: str, hint: str = "", *, content_disposition: str = "", content_type: str = ""
) -> str:
    """Расширение файла: тип по содержимому, затем имя из заголовка, ``Content-Type``, сайт.

    ``detected`` — результат ``detect_type``. Для OLE-контейнера неизвестного вида и для
    файлов без сигнатуры (CSV, TXT) используются остальные источники.
    """
    hint = (hint or "").strip().lower()
    if detected and detected != "ole":
        return detected
    name = original_filename(content_disposition)
    by_name = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if detected == "ole":
        for candidate in (by_name, hint):
            if candidate in {"doc", "xls", "ppt"}:
                return candidate
        return "doc"
    if by_name in KNOWN_EXTENSIONS:
        return by_name
    by_type = CONTENT_TYPE_EXT.get(content_type.split(";")[0].strip().lower(), "")
    if by_type:
        return by_type
    return hint if hint in KNOWN_EXTENSIONS else "bin"


def file_path(files_dir: Path, document_id: str, file_id: str, ext: str) -> Path:
    return files_dir / document_id / f"{file_id}.{ext}"


def legacy_file_name(document_id: str, file_id: str, ext: str) -> str:
    """Имя файла в раскладке первой версии: ``files/doc_{document}__file_{file}.{ext}``."""
    return f"doc_{document_id}__file_{file_id}.{ext}"


def _first_complete(paths) -> Path | None:
    for path in sorted(paths):
        if path.suffix not in TEMP_SUFFIXES and path.is_file() and path.stat().st_size > 0:
            return path
    return None


def find_existing(files_dir: Path, document_id: str, file_id: str, hint: str = "") -> Path | None:
    """Уже скачанный файл: в текущей раскладке или в раскладке первой версии.

    Файл из старой раскладки переносится в ``files/{document_id}/``. Расширение у найденного
    файла сверяется с содержимым и при расхождении исправляется.
    """
    path = _first_complete((files_dir / document_id).glob(f"{file_id}.*"))
    if path is None:
        path = _first_complete(files_dir.glob(legacy_file_name(document_id, file_id, "*")))
    if path is None:
        return None
    ext = determine_extension(detect_type(path), hint or path.suffix.lstrip("."))
    target = file_path(files_dir, document_id, file_id, ext)
    if path != target:
        target.parent.mkdir(parents=True, exist_ok=True)
        path.replace(target)
        logger.info("перенесён %s → %s", path.name, target.relative_to(files_dir))
    return target


def load_manifest(path: Path, key: str) -> dict[str, dict]:
    """Прошлый манифест: строки по значению поля ``key``; пустой, если файла нет."""
    if not path.is_file():
        return {}
    return {row[key]: row for row in read_jsonl(path) if row.get(key)}


def carry_over(previous: dict | None, sha256: str) -> dict:
    """Сведения из прошлого манифеста, если содержимое файла не изменилось."""
    if not previous or previous.get("sha256") != sha256:
        return {}
    kept = {k: previous[k] for k in HEADER_FIELDS if previous.get(k)}
    if previous.get("downloaded_at"):
        kept["downloaded_at"] = previous["downloaded_at"]
    return kept


def _id_from(row: dict, field: str, url_field: str, pattern: re.Pattern) -> str:
    """ID из поля строки, а для старых выгрузок без него — из URL."""
    if row.get(field):
        return str(row[field])
    match = pattern.search(urlparse(row[url_field]).path)
    if not match:
        raise ValueError(f"не удалось определить {field} из {row[url_field]}")
    return match.group(1)


def _fetch(client: MintransClient, url: str, part_target: Path, **kwargs) -> tuple[str, dict]:
    """Скачать файл и вернуть (тип по содержимому, заголовки). HTML вместо файла — ошибка."""
    result = client.download(url, part_target, **kwargs)
    detected = detect_type(part_target)
    if detected == "html":
        part_target.unlink()
        raise NotAFileError(f"сервер вернул веб-страницу вместо файла: {url}")
    return detected, result.headers


def run_download(
    client: MintransClient,
    settings: Settings,
    *,
    input_jsonl: Path,
    output_jsonl: Path | None = None,
    summary_path: Path | None = None,
    resume: bool = True,
    limit: int | None = None,
    fill_meta: bool = False,
) -> dict:
    """Скачать вложения документов и записать манифест.

    ``fill_meta`` — для уже скачанных файлов без исходного имени запросить заголовки ответа
    (без повторного скачивания тела).
    """
    started = utc_now_iso()
    files_dir = settings.files_dir
    output_jsonl = output_jsonl or settings.catalog_dir / "downloads.jsonl"
    summary_path = summary_path or (
        settings.catalog_dir / "runs" / f"download-{started.replace(':', '')}.json"
    )
    manifest = load_manifest(output_jsonl, "file_url")
    rows = list(read_jsonl(input_jsonl))
    if limit is not None:
        rows = rows[:limit]

    enriched: list[dict] = []
    stats = {"downloaded": 0, "changed": 0, "skipped": 0, "meta_filled": 0, "failed": 0}
    errors: list[dict] = []
    for idx, row in enumerate(rows, start=1):
        url = row.get("file_url", "")
        try:
            validate_row(row)
            document_id = _id_from(row, "document_id", "document_url", DOC_ID_RE)
            file_id = _id_from(row, "file_id", "file_url", FILE_ID_RE)
            hint = row.get("file_type") or ""
            previous = manifest.get(url)
            existing = find_existing(files_dir, document_id, file_id, hint) if resume else None
            meta: dict = {}
            if existing is not None:
                target = existing
                sha256 = sha256_file(target)
                meta = carry_over(previous, sha256)
                if fill_meta and not meta.get("original_filename"):
                    meta.update(header_meta(client.fetch_headers(url)))
                    stats["meta_filled"] += 1
                stats["skipped"] += 1
                logger.info("[%s/%s] уже есть %s", idx, len(rows), target.relative_to(files_dir))
            else:
                tmp = files_dir / document_id / f"{file_id}.download"
                detected, headers = _fetch(client, url, tmp)
                meta = header_meta(headers)
                ext = determine_extension(
                    detected,
                    hint,
                    content_disposition=headers.get("content-disposition", ""),
                    content_type=headers.get("content-type", ""),
                )
                target = file_path(files_dir, document_id, file_id, ext)
                for stale in (files_dir / document_id).glob(f"{file_id}.*"):
                    if stale not in (tmp, target) and stale.suffix not in TEMP_SUFFIXES:
                        stale.unlink()  # тот же файл с прежним расширением
                tmp.replace(target)
                sha256 = sha256_file(target)
                if previous and previous.get("sha256") and previous["sha256"] != sha256:
                    meta["previous_sha256"] = previous["sha256"]
                    stats["changed"] += 1
                stats["downloaded"] += 1
                logger.info(
                    "[%s/%s] скачан %s (%s байт)",
                    idx,
                    len(rows),
                    target.relative_to(files_dir),
                    target.stat().st_size,
                )
            now = utc_now_iso()
            new_row = {
                **row,
                "detected_type": target.suffix.lstrip("."),
                "original_filename": "",
                "content_type": "",
                "last_modified": "",
                **meta,
                "local_path": target.relative_to(settings.data_dir).as_posix(),
                "sha256": sha256,
                "saved_file_size_bytes": target.stat().st_size,
                "downloaded_at": meta.get("downloaded_at") or now,
                "verified_at": now,
            }
            validate_row(new_row, require_download_fields=True)
            enriched.append(new_row)
        except MintransError as exc:
            stats["failed"] += 1
            errors.append({"file_url": url, "error": str(exc)})
            logger.error("[%s/%s] ошибка %s: %s", idx, len(rows), url, exc)
            if isinstance(exc, BlockedError):
                logger.error("сайт ограничил доступ — скачивание остановлено")
                break
        except (ValueError, OSError) as exc:
            stats["failed"] += 1
            errors.append({"file_url": url, "error": str(exc)})
            logger.error("[%s/%s] ошибка %s: %s", idx, len(rows), url, exc)

    # строки прошлого манифеста, которых нет во входе (например, при --limit), не теряются
    seen = {r["file_url"] for r in enriched}
    attempted = {r.get("file_url") for r in rows}
    kept = [r for u, r in manifest.items() if u not in seen and u not in attempted]
    written = write_jsonl(output_jsonl, enriched + kept)
    summary = {
        "command": "download",
        "started_at": started,
        "finished_at": utc_now_iso(),
        "input_jsonl": str(input_jsonl),
        "output_jsonl": str(output_jsonl),
        "files_dir": str(files_dir),
        "total": len(rows),
        "rows_written": written,
        **stats,
        "errors": errors,
        "http_requests": client.requests_made,
    }
    write_json(summary_path, summary)
    return summary
