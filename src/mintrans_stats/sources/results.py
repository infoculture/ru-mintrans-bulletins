"""Списки документов разделов «Результаты деятельности» (/ministry/results/{id}/documents).

Раздел «Статистика» — id 180; 181 — бюджетное финансирование, 192 — судебная практика,
207 — контроль деятельности. На странице до 9 документов.
"""

from __future__ import annotations

from collections.abc import Iterator

from ..config import section_url
from ..http import MintransClient
from .listing import DocumentRef, iter_listing


def listing_url(section_id: int, page: int) -> str:
    base = section_url(section_id)
    return base if page == 1 else f"{base}?page={page}"


def iter_section(
    client: MintransClient, section_id: int
) -> Iterator[tuple[int, list[DocumentRef]]]:
    """Все страницы раздела: пары (номер страницы, документы на ней)."""
    return iter_listing(client, lambda page: listing_url(section_id, page), f"раздел {section_id}")
