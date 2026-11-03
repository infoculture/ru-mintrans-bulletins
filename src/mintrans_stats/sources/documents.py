"""Карточка документа /documents/{type}/{id}.

Структура страницы::

    <h1>Название</h1>
    <span class="date-span dark">Опубликовано 1 Января 2010</span>
    <div class="text-block">…описание…</div>
    <ul class="download-list">
      <li class="download-list-item pdf">
        <a href="/file/395019" download>Название файла <span>(794 kb)</span></a>
      </li>
    </ul>
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from ..config import BASE_URL
from ..utils import normalize_text, parse_ru_date

FILE_PATH_RE = re.compile(r"^/file/(\d+)/?$")
SIZE_IN_PARENS_RE = re.compile(r"\(\s*([^()]*?\d[^()]*?)\s*\)\s*$")
TITLE_SUFFIX_RE = re.compile(r"\s*\|\s*Министерство транспорта Российской Федерации\s*$")
IGNORED_LI_CLASSES = frozenset({"download-list-item", "item", "active"})


@dataclass(frozen=True)
class Attachment:
    file_id: str
    url: str
    title: str
    size_text: str
    """Размер как на сайте, например ``2493 kb``; пустая строка, если не указан."""
    file_type: str
    """Тип из CSS-класса вложения (``pdf``, ``doc``, ``rar``…); пустая строка, если нет."""


@dataclass(frozen=True)
class DocumentPage:
    document_id: str
    url: str
    title: str
    published_at: date | None
    description: str
    attachments: list[Attachment] = field(default_factory=list)


def file_id_from_href(href: str) -> str | None:
    match = FILE_PATH_RE.match(urlparse(urljoin(BASE_URL, href.strip())).path)
    return match.group(1) if match else None


def _split_title_and_size(link: Tag) -> tuple[str, str]:
    """Текст ссылки без размера и сам размер (из ``<span>`` или скобок в конце)."""
    size = ""
    span = link.find("span")
    if span is not None:
        span_text = normalize_text(span.get_text(" "))
        match = SIZE_IN_PARENS_RE.search(span_text)
        if match:
            size = normalize_text(match.group(1))
            span.extract()
    title = normalize_text(link.get_text(" "))
    if not size:
        match = SIZE_IN_PARENS_RE.search(title)
        if match and re.search(r"[kKкК][bBбБ]|[mMмМ][bBбБ]|байт|bytes?", match.group(1)):
            size = normalize_text(match.group(1))
            title = normalize_text(title[: match.start()])
    return title, size


def _file_type(li: Tag) -> str:
    for cls in li.get("class") or []:
        cls = str(cls).strip().lower().lstrip(".")
        if cls and cls not in IGNORED_LI_CLASSES:
            return cls
    return ""


def parse_attachments(soup: BeautifulSoup) -> list[Attachment]:
    items: list[tuple[Tag, Tag]] = []
    for li in soup.select("ul.download-list li.download-list-item"):
        link = next((a for a in li.find_all("a", href=True) if file_id_from_href(a["href"])), None)
        if link is not None:
            items.append((li, link))
    if not items:
        # Запасной путь: любые ссылки /file/{id} в контенте.
        scope = soup.select_one(".content") or soup
        for link in scope.find_all("a", href=True):
            if file_id_from_href(link["href"]):
                items.append((link.find_parent("li") or link, link))

    attachments: list[Attachment] = []
    seen: set[str] = set()
    for li, link in items:
        file_id = file_id_from_href(link["href"])
        if file_id is None or file_id in seen:
            continue
        seen.add(file_id)
        title, size = _split_title_and_size(link)
        attachments.append(
            Attachment(
                file_id=file_id,
                url=f"{BASE_URL}/file/{file_id}",
                title=title,
                size_text=size,
                file_type=_file_type(li) if li.name == "li" else "",
            )
        )
    return attachments


def parse_document(html: str, url: str, *, fallback_title: str = "") -> DocumentPage:
    soup = BeautifulSoup(html, "lxml")
    content = soup.select_one(".content") or soup

    title = ""
    h1 = content.find("h1")
    if h1 is not None:
        title = normalize_text(h1.get_text(" "))
    if not title and soup.title is not None:
        title = TITLE_SUFFIX_RE.sub("", normalize_text(soup.title.get_text(" ")))
    title = title or fallback_title

    published_at = None
    for span in content.select(".date-span"):
        text = span.get_text(" ")
        if "Опубликовано" in text:
            published_at = parse_ru_date(text)
            break

    text_block = content.select_one(".text-block")
    description = normalize_text(text_block.get_text(" ")) if text_block else ""

    document_id = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    return DocumentPage(
        document_id=document_id,
        url=url,
        title=title,
        published_at=published_at,
        description=description,
        attachments=parse_attachments(soup),
    )
