"""Поиск по документам: ``/documents?type={type}&q={текст}&page=N``.

Поиск идёт по названиям документов (подстрока, без морфологии), по 4 документа на странице.
``type=0`` — все типы; пустой ``q`` с конкретным ``type`` даёт полный список документов типа
(например, 11 — «Отчёты»).
"""

from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import urlencode

from ..config import BASE_URL
from ..http import MintransClient
from .listing import DocumentRef, iter_listing


def search_url(query: str = "", type_id: int = 0, page: int = 1) -> str:
    params: dict[str, str | int] = {"type": type_id}
    if query:
        params["q"] = query
    if page > 1:
        params["page"] = page
    return f"{BASE_URL}/documents?{urlencode(params)}"


def iter_search(
    client: MintransClient, query: str = "", type_id: int = 0
) -> Iterator[tuple[int, list[DocumentRef]]]:
    """Все страницы выдачи: пары (номер страницы, документы на ней)."""
    label = f"поиск «{query}»" if query else f"документы типа {type_id}"
    return iter_listing(client, lambda page: search_url(query, type_id, page), label)
