from datetime import date

from conftest import fixture_html
from mintrans_stats.sources.listing import last_page, parse_listing
from mintrans_stats.sources.search import search_url


def test_search_url():
    assert search_url("бюллетень") == (
        "https://mintrans.gov.ru/documents?type=0"
        "&q=%D0%B1%D1%8E%D0%BB%D0%BB%D0%B5%D1%82%D0%B5%D0%BD%D1%8C"
    )
    assert search_url("1-торги", page=2).endswith("&page=2")
    assert search_url(type_id=11, page=3) == "https://mintrans.gov.ru/documents?type=11&page=3"


def test_search_results_page():
    html = fixture_html("search_bulletin_page1.html")
    refs = parse_listing(html)
    assert [r.document_id for r in refs] == ["15038", "14772", "14478", "14303"]
    assert refs[0].listed_date == date(2025, 12, 11)
    assert all(r.type_id == "7" for r in refs)
    assert last_page(html) == 14
