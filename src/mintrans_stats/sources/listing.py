"""Общий разбор страниц-списков документов.

Одна и та же вёрстка используется в разделах «Результатов деятельности»
(``/ministry/results/{id}/documents``) и в поиске по документам (``/documents?type=&q=``):
карточки ``.document-list .document-list-item`` (дата, ссылка ``/documents/{type}/{id}``,
файлы) и блок пагинации ``.pagination`` со ссылками ``?page=N``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from ..config import BASE_URL
from ..http import MintransClient
from ..utils import normalize_text, parse_ru_date

logger = logging.getLogger(__name__)

DOC_PATH_RE = re.compile(r"^/documents/(\d+)/(\d+)/?$")


@dataclass(frozen=True)
class DocumentRef:
    """Ссылка на документ со страницы списка."""

    document_id: str
    url: str
    title: str
    listed_date: date | None = None

    @property
    def type_id(self) -> str:
        """Тип документа из URL (``/documents/{type}/{id}``); сайт его не проверяет."""
        return urlparse(self.url).path.split("/")[2]


def document_ref_from_href(
    href: str, title: str = "", listed_date: date | None = None
) -> DocumentRef | None:
    """``DocumentRef`` из ссылки на карточку или ``None``, если это не карточка документа.

    ID документа на сайте глобальный, тип в URL (``/documents/7/...``) сайт игнорирует,
    поэтому тип не фильтруется; query-параметры (``?type=7``) отбрасываются.
    """
    path = urlparse(urljoin(BASE_URL, href.strip())).path
    match = DOC_PATH_RE.match(path)
    if not match:
        return None
    type_id, doc_id = match.groups()
    return DocumentRef(
        document_id=doc_id,
        url=f"{BASE_URL}/documents/{type_id}/{doc_id}",
        title=title,
        listed_date=listed_date,
    )


def parse_listing(html: str) -> list[DocumentRef]:
    """Документы со страницы списка в порядке появления, без повторов."""
    soup = BeautifulSoup(html, "lxml")
    refs: list[DocumentRef] = []
    seen: set[str] = set()

    for item in soup.select(".document-list .document-list-item"):
        link = next(
            (a for a in item.find_all("a", href=True) if document_ref_from_href(a["href"])),
            None,
        )
        if link is None:
            continue
        date_el = item.select_one(".date-span")
        ref = document_ref_from_href(
            link["href"],
            title=normalize_text(link.get_text(" ")),
            listed_date=parse_ru_date(date_el.get_text(" ")) if date_el else None,
        )
        if ref and ref.document_id not in seen:
            seen.add(ref.document_id)
            refs.append(ref)

    if refs:
        return refs

    # Запасной путь, если вёрстка карточек изменится: любые ссылки на документы в контенте.
    scope = soup.select_one(".content") or soup
    for a in scope.find_all("a", href=True):
        ref = document_ref_from_href(a["href"], title=normalize_text(a.get_text(" ")))
        if ref and ref.document_id not in seen:
            seen.add(ref.document_id)
            refs.append(ref)
    return refs


def last_page(html: str) -> int:
    """Номер последней страницы по блоку пагинации; 1, если пагинации нет."""
    soup = BeautifulSoup(html, "lxml")
    pages = {1}
    for a in soup.select(".pagination a[href]"):
        values = parse_qs(urlparse(a["href"]).query).get("page", [])
        pages.update(int(v) for v in values if v.isdigit())
    # у текущей страницы пустой href, её номер есть только в тексте
    for item in soup.select(".pagination .pagination-item.active"):
        text = normalize_text(item.get_text(" "))
        if text.isdigit():
            pages.add(int(text))
    return max(pages)


def iter_listing(
    client: MintransClient, page_url: Callable[[int], str], label: str
) -> Iterator[tuple[int, list[DocumentRef]]]:
    """Обойти все страницы списка: пары (номер страницы, документы на ней).

    Списки всегда запрашиваются с сайта заново (``refresh=True``), чтобы увидеть новые
    документы; копии страниц при этом обновляются в кеше.
    """
    first = client.get_text(page_url(1), refresh=True)
    total = last_page(first)
    logger.info("%s: страниц %s", label, total)
    yield 1, parse_listing(first)
    for page in range(2, total + 1):
        yield page, parse_listing(client.get_text(page_url(page), refresh=True))
