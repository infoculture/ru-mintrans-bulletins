"""Сбор каталога выпусков информационно-статистического бюллетеня «Транспорт России».

Источники:

* раздел «Статистика» (``/ministry/results/180/documents``) — в нём почти все выпуски;
* поиск по названиям (``/documents?type=0&q=...``) — выпуски, опубликованные вне раздела
  (например, «Январь–сентябрь 2017»).

В каталог попадают только документы, чьё название похоже на выпуск бюллетеня
(``classify.is_bulletin``); остальные документы раздела и выдачи поиска лишь считаются.

Результат в ``<data_dir>/catalog/``:

* ``documents.jsonl`` — выпуски с источниками, где они найдены;
* ``files.jsonl`` — строка на каждое вложение выпусков (вход для ``download``);
* ``runs/crawl-<время>.json`` — сводка запуска.

Списки и выдача поиска запрашиваются заново при каждом запуске, карточки документов берутся
из кеша HTML (``refresh=True`` — перекачать). Поэтому после обрыва повторный запуск быстро
доходит до места остановки.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

from .classify import is_bulletin
from .config import STATISTICS_SECTION_ID, Settings
from .http import BlockedError, MintransClient, MintransError
from .sources.documents import DocumentPage, parse_document
from .sources.listing import DocumentRef
from .sources.results import iter_section
from .sources.search import iter_search
from .utils import utc_now_iso, write_json, write_jsonl
from .validate import ValidationError, validate_row

logger = logging.getLogger(__name__)

DEFAULT_SECTIONS: tuple[int, ...] = (STATISTICS_SECTION_ID,)
DEFAULT_QUERIES: tuple[str, ...] = ("бюллетень", "Транспорт России")
"""Поисковые подстроки (поиск сайта ищет подстроку в названии, без морфологии)."""


@dataclass
class Discovered:
    ref: DocumentRef
    sources: list[dict] = field(default_factory=list)


def discover_documents(
    client: MintransClient,
    *,
    sections: tuple[int, ...] | list[int],
    queries: tuple[str, ...] | list[str],
) -> tuple[dict[str, Discovered], dict[str, dict]]:
    """Обойти списки и поиск. Возвращает найденные документы и статистику по источникам."""
    found: dict[str, Discovered] = {}
    stats: dict[str, dict] = {}

    def collect(source: dict, pages) -> None:
        key = ":".join(str(v) for v in source.values())
        total = new = 0
        for _page, refs in pages:
            for ref in refs:
                total += 1
                item = found.get(ref.document_id)
                if item is None:
                    item = found[ref.document_id] = Discovered(ref)
                    new += 1
                if source not in item.sources:
                    item.sources.append(source)
        stats[key] = {"documents": total, "new": new}
        logger.info("%s: документов %s, новых %s", key, total, new)

    for section_id in sections:
        collect({"kind": "section", "section_id": section_id}, iter_section(client, section_id))
    for query in queries:
        collect({"kind": "search", "query": query}, iter_search(client, query, 0))
    return found, stats


def document_record(item: Discovered, page: DocumentPage | None, crawled_at: str) -> dict:
    title = page.title if page and page.title else item.ref.title
    return {
        "document_id": item.ref.document_id,
        "document_url": item.ref.url,
        "type_id": item.ref.type_id,
        "title": title,
        "published_at": page.published_at.isoformat() if page and page.published_at else None,
        "listed_date": item.ref.listed_date.isoformat() if item.ref.listed_date else None,
        "description": page.description if page else "",
        "sources": item.sources,
        "files_count": len(page.attachments) if page else None,
        "crawled_at": crawled_at if page else None,
    }


def file_rows(document: dict, page: DocumentPage) -> list[dict]:
    """Строки «документ — файл»; поля совместимы с выгрузкой первой версии парсера.

    У части вложений на сайте ссылка без текста (только размер) — тогда названием файла
    служит название документа, а ``file_title_from_document`` = ``true``.
    """
    return [
        {
            "document_id": document["document_id"],
            "document_title": document["title"],
            "document_url": document["document_url"],
            "published_at": document["published_at"],
            "listed_date": document["listed_date"],
            "file_id": att.file_id,
            "file_title": att.title or document["title"],
            "file_title_from_document": not att.title,
            "file_url": att.url,
            "file_type": att.file_type,
            "file_size": att.size_text,
        }
        for att in page.attachments
    ]


def run_crawl(
    client: MintransClient,
    settings: Settings,
    *,
    sections: tuple[int, ...] | list[int] = DEFAULT_SECTIONS,
    queries: tuple[str, ...] | list[str] = DEFAULT_QUERIES,
    refresh: bool = False,
) -> dict:
    started = utc_now_iso()
    catalog = settings.catalog_dir
    found, source_stats = discover_documents(client, sections=sections, queries=queries)
    bulletins = [item for item in found.values() if is_bulletin(item.ref.title)]

    documents: list[dict] = []
    files: list[dict] = []
    errors: list[dict] = []
    for idx, item in enumerate(bulletins, start=1):
        page: DocumentPage | None = None
        try:
            html = client.get_text(item.ref.url, refresh=refresh)
            page = parse_document(html, item.ref.url, fallback_title=item.ref.title)
        except BlockedError:
            raise
        except MintransError as exc:
            errors.append({"url": item.ref.url, "error": str(exc)})
            logger.error("документ %s: %s", item.ref.document_id, exc)
        record = document_record(item, page, utc_now_iso())
        documents.append(record)
        if page is None:
            continue
        rows = file_rows(record, page)
        try:
            for row in rows:
                validate_row(row)
        except ValidationError as exc:
            errors.append({"url": item.ref.url, "error": f"неполные данные карточки: {exc}"})
            logger.error("документ %s: %s", item.ref.document_id, exc)
            continue
        files.extend(rows)
        if not rows:
            logger.warning("выпуск без файлов: %s", item.ref.url)
        logger.info(
            "[%s/%s] выпуск %s: файлов %s", idx, len(bulletins), item.ref.document_id, len(rows)
        )

    documents.sort(key=lambda d: int(d["document_id"]), reverse=True)
    order = {d["document_id"]: n for n, d in enumerate(documents)}
    files.sort(key=lambda f: order[f["document_id"]])  # порядок файлов внутри документа сохранён
    write_jsonl(catalog / "documents.jsonl", documents)
    write_jsonl(catalog / "files.jsonl", files)

    summary = {
        "command": "crawl",
        "started_at": started,
        "finished_at": utc_now_iso(),
        "sources": source_stats,
        "documents_found": len(found),
        "bulletins": len(documents),
        "bulletins_outside_sections": sorted(
            (d["document_id"] for d in documents if not _from_section(d)), key=int
        ),
        "bulletins_without_files": [d["document_id"] for d in documents if d["files_count"] == 0],
        "files": len(files),
        "file_types": dict(sorted(Counter(f["file_type"] or "?" for f in files).items())),
        "errors": errors,
        "http_requests": client.requests_made,
        "catalog_dir": str(catalog),
    }
    write_json(catalog / "runs" / f"crawl-{started.replace(':', '')}.json", summary)
    return summary


def _from_section(document: dict) -> bool:
    return any(s["kind"] == "section" for s in document["sources"])
