"""Команда ``extract``: таблицы и текст из скачанных выпусков бюллетеня.

Вход — манифест ``catalog/downloads.jsonl``. Каждый файл, а у архива — каждый файл внутри,
становится «элементом» (item):

* архивы распаковываются в ``extracted/archives/{слаг}/``;
* doc и rtf конвертируются в docx через LibreOffice (``extracted/converted/{слаг}.docx``);
* таблицы пишутся в ``tables/{слаг}/t{NNN}.csv`` (UTF-8), текст — в ``extracted/text/{слаг}.txt``.

Индексы:

* ``catalog/extracted.jsonl`` — элемент: источник, тип, статус, число таблиц, длина текста;
* ``catalog/tables.jsonl`` — таблица: элемент, место (страница/лист), подпись, размер, путь CSV.

Элемент, у которого не изменился исходный файл (sha256), повторно не обрабатывается —
записи берутся из прошлых индексов (``force=True`` — обработать заново).
"""

from __future__ import annotations

import csv
import json
import logging
import re
import shutil
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Settings
from ..filetypes import detect_type
from ..utils import read_jsonl, utc_now_iso, write_json, write_jsonl
from .archives import ARCHIVE_TYPES, ArchiveError, Member, unpack
from .convert import ConversionError, convert_to_docx
from .readers import READERS, Content

logger = logging.getLogger(__name__)

CONVERT_TO_DOCX = frozenset({"doc", "rtf", "odt"})


@dataclass
class Item:
    item_id: str
    slug: str
    path: Path
    kind: str
    source: dict = field(default_factory=dict)
    converted: Path | None = None


def _slug(text: str) -> str:
    return re.sub(r"[^\w.-]+", "_", text).strip("_")


def _member_kind(path: Path) -> str:
    kind = detect_type(path)
    if kind in ("", "ole"):
        ext = path.suffix.lstrip(".").lower()
        return ext if ext in READERS or ext in CONVERT_TO_DOCX else kind
    return kind


def collect_items(settings: Settings, *, refresh_archives: bool) -> tuple[list[Item], list[dict]]:
    """Элементы для обработки и ошибки распаковки."""
    data = settings.data_dir
    items: list[Item] = []
    errors: list[dict] = []

    files_manifest = settings.catalog_dir / "downloads.jsonl"
    if files_manifest.is_file():
        for row in read_jsonl(files_manifest):
            path = data / row["local_path"]
            kind = row.get("detected_type") or detect_type(path)
            base_id = f"{row['document_id']}/{row['file_id']}"
            source = {
                "document_id": row["document_id"],
                "file_id": row["file_id"],
                "local_path": row["local_path"],
                "member": None,
                "source_sha256": row["sha256"],
            }
            if kind in ARCHIVE_TYPES:
                dest = data / "extracted" / "archives" / _slug(base_id)
                try:
                    members = (
                        _unpacked_members(dest, row["sha256"]) if not refresh_archives else None
                    )
                    if members is None:
                        members = unpack(path, dest, kind=kind)
                        _save_members(dest, row["sha256"], members)
                except (ArchiveError, OSError) as exc:
                    errors.append({"item_id": base_id, "error": f"распаковка: {exc}"})
                    logger.error("архив %s: %s", base_id, exc)
                    continue
                for n, member in enumerate(members, start=1):
                    items.append(
                        Item(
                            item_id=f"{base_id}#{n}",
                            slug=f"{_slug(base_id)}-m{n:02d}",
                            path=member.path,
                            kind=_member_kind(member.path),
                            source={**source, "member": member.member},
                        )
                    )
            else:
                items.append(Item(base_id, _slug(base_id), path, kind, source))
    return items, errors


MEMBERS_FILE = ".members.json"


def _save_members(dest: Path, sha256: str, members: list[Member]) -> None:
    """Запомнить состав распакованного архива: номера членов (#n) должны быть стабильны."""
    listing = [[m.path.relative_to(dest).as_posix(), m.member] for m in members]
    (dest / MEMBERS_FILE).write_text(
        json.dumps({"sha256": sha256, "members": listing}, ensure_ascii=False), encoding="utf-8"
    )


def _unpacked_members(dest: Path, sha256: str) -> list[Member] | None:
    """Члены ранее распакованного архива, если он не изменился и файлы на месте."""
    try:
        saved = json.loads((dest / MEMBERS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if saved.get("sha256") != sha256:
        return None
    members = [Member(dest / rel, member) for rel, member in saved["members"]]
    return members if all(m.path.is_file() for m in members) else None


def _convert(items: list[Item], settings: Settings, force: bool) -> list[dict]:
    """Сконвертировать doc/rtf в docx пачкой; вернуть ошибки."""
    base = settings.data_dir / "extracted"
    out_dir = base / "converted"
    pending = []
    for item in items:
        if item.kind not in CONVERT_TO_DOCX:
            continue
        target = out_dir / f"{item.slug}.docx"
        if not force and target.is_file() and target.stat().st_mtime >= item.path.stat().st_mtime:
            item.converted = target
        else:
            pending.append(item)
    if not pending:
        return []
    staging = base / "_staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    staged = {}
    for item in pending:
        copy = staging / f"{item.slug}.{item.kind}"
        shutil.copyfile(item.path, copy)
        staged[copy] = item
    logger.info("конвертация в docx: файлов %s", len(staged))
    try:
        done = convert_to_docx(list(staged), out_dir, base / "_lo_profile")
    except ConversionError as exc:
        return [{"item_id": i.item_id, "error": str(exc)} for i in pending]
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    errors = []
    for copy, item in staged.items():
        if copy in done:
            item.converted = done[copy]
        else:
            errors.append({"item_id": item.item_id, "error": "LibreOffice не сконвертировал файл"})
    return errors


def _write_tables(content: Content, item: Item, settings: Settings) -> list[dict]:
    data = settings.data_dir
    table_dir = data / "tables" / item.slug
    if table_dir.exists():
        shutil.rmtree(table_dir)
    records = []
    for k, table in enumerate(content.tables, start=1):
        table_dir.mkdir(parents=True, exist_ok=True)
        path = table_dir / f"t{k:03d}.csv"
        with path.open("w", encoding="utf-8", newline="") as fh:
            csv.writer(fh).writerows(table.rows)
        records.append(
            {
                "table_id": f"{item.item_id}:t{k:03d}",
                "item_id": item.item_id,
                **{k2: item.source[k2] for k2 in ("document_id", "file_id", "member")},
                "location": table.location,
                "caption": table.caption,
                "n_rows": len(table.rows),
                "n_cols": max((len(r) for r in table.rows), default=0),
                "csv_path": path.relative_to(data).as_posix(),
            }
        )
    return records


def process_item(item: Item, settings: Settings) -> tuple[dict, list[dict]]:
    """Прочитать элемент: запись для extracted.jsonl и записи таблиц."""
    data = settings.data_dir
    record = {
        "item_id": item.item_id,
        **item.source,
        "kind": item.kind,
        "converted_path": item.converted.relative_to(data).as_posix() if item.converted else None,
        "status": "",
        "error": None,
        "tables": 0,
        "text_path": None,
        "text_chars": 0,
        "needs_ocr": False,
        "extracted_at": utc_now_iso(),
    }
    reader_kind = "docx" if item.converted else item.kind
    reader = READERS.get(reader_kind)
    if reader is None:
        record["status"] = "unsupported" if item.kind not in CONVERT_TO_DOCX else "error"
        if item.kind in CONVERT_TO_DOCX:
            record["error"] = "нет сконвертированного docx"
        return record, []
    try:
        content = reader(item.converted or item.path)
    except Exception as exc:  # повреждённые и нестандартные файлы не должны останавливать обработку
        logger.error("%s: %s: %s", item.item_id, type(exc).__name__, exc)
        record.update(status="error", error=f"{type(exc).__name__}: {exc}"[:500])
        return record, []
    if content.text:
        text_path = data / "extracted" / "text" / f"{item.slug}.txt"
        text_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text(content.text, encoding="utf-8")
        record["text_path"] = text_path.relative_to(data).as_posix()
    record["text_chars"] = len(content.text)
    # PDF без текстового слоя (скан без распознавания) — таблицы и текст только через OCR
    record["needs_ocr"] = item.kind == "pdf" and not content.text.strip("\f \n")
    tables = _write_tables(content, item, settings)
    record["tables"] = len(tables)
    record["status"] = "ok" if tables else "no_tables"
    return record, tables


def run_extract(
    settings: Settings,
    *,
    force: bool = False,
    limit: int | None = None,
) -> dict:
    started = utc_now_iso()
    t0 = time.monotonic()
    catalog = settings.catalog_dir
    extracted_path = catalog / "extracted.jsonl"
    tables_path = catalog / "tables.jsonl"
    previous = (
        {r["item_id"]: r for r in read_jsonl(extracted_path)} if extracted_path.is_file() else {}
    )
    previous_tables: dict[str, list[dict]] = {}
    if tables_path.is_file():
        for row in read_jsonl(tables_path):
            previous_tables.setdefault(row["item_id"], []).append(row)

    items, errors = collect_items(settings, refresh_archives=force)
    if limit is not None:
        items = items[:limit]

    def unchanged(item: Item) -> bool:
        prev = previous.get(item.item_id)
        return (
            not force
            and prev is not None
            and prev.get("source_sha256") == item.source["source_sha256"]
            and prev.get("status") in ("ok", "no_tables", "unsupported")
        )

    todo = [i for i in items if not unchanged(i)]
    todo_ids = {i.item_id for i in todo}
    errors.extend(_convert(todo, settings, force))

    records: list[dict] = []
    tables: list[dict] = []
    reused = 0
    for idx, item in enumerate(items, start=1):
        if item.item_id not in todo_ids:
            records.append(previous[item.item_id])
            tables.extend(previous_tables.get(item.item_id, []))
            reused += 1
            continue
        started_item = time.monotonic()
        record, item_tables = process_item(item, settings)
        records.append(record)
        tables.extend(item_tables)
        logger.info(
            "[%s/%s] %s (%s): %s, таблиц %s, %.1f с",
            idx,
            len(items),
            item.item_id,
            item.kind,
            record["status"],
            record["tables"],
            time.monotonic() - started_item,
        )

    if limit is None:
        out_records, out_tables = records, tables
    else:  # частичный запуск не стирает остальные записи индексов
        done = {r["item_id"] for r in records}
        out_records = records + [r for k, r in previous.items() if k not in done]
        out_tables = tables + [
            t for k, rows in previous_tables.items() if k not in done for t in rows
        ]
    write_jsonl(extracted_path, out_records)
    write_jsonl(tables_path, out_tables)

    status = Counter(r["status"] for r in records)
    top_without_tables = sorted(
        {r["item_id"].split("#")[0] for r in records if r["document_id"] and r["status"] != "ok"}
        - {r["item_id"].split("#")[0] for r in records if r["document_id"] and r["status"] == "ok"}
    )
    summary = {
        "command": "extract",
        "started_at": started,
        "finished_at": utc_now_iso(),
        "seconds": round(time.monotonic() - t0, 1),
        "items": len(records),
        "items_reused": reused,
        "kinds": dict(sorted(Counter(r["kind"] or "?" for r in records).items())),
        "status": dict(status),
        "tables": len(tables),
        "files_without_tables": top_without_tables,
        "needs_ocr": [r["item_id"] for r in records if r.get("needs_ocr")],
        "errors": errors
        + [{"item_id": r["item_id"], "error": r["error"]} for r in records if r["error"]],
    }
    write_json(catalog / "runs" / f"extract-{started.replace(':', '')}.json", summary)
    return summary
