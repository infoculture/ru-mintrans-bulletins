"""Живые проверки сайта. Запуск: ``MINTRANS_PROXY=socks5h://127.0.0.1:1080 pytest -m network``."""

import pytest

from mintrans_stats.config import Settings
from mintrans_stats.http import MintransClient
from mintrans_stats.sources.documents import parse_document
from mintrans_stats.sources.listing import last_page, parse_listing
from mintrans_stats.sources.results import listing_url

pytestmark = pytest.mark.network


@pytest.fixture(scope="module")
def client():
    with MintransClient(Settings.from_env().with_overrides(cache_html=False)) as c:
        yield c


def test_statistics_section_structure(client):
    html = client.get_text(listing_url(180, 1))
    assert last_page(html) >= 9
    assert len(parse_listing(html)) == 9


def test_document_page_structure(client):
    url = "https://mintrans.gov.ru/documents/7/15038"
    page = parse_document(client.get_text(url), url)
    assert page.published_at is not None
    assert page.attachments and page.attachments[0].file_type == "pdf"
