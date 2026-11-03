from datetime import date

from conftest import fixture_html
from mintrans_stats.sources.listing import (
    DocumentRef,
    document_ref_from_href,
    last_page,
    parse_listing,
)
from mintrans_stats.sources.results import listing_url


def test_parse_listing_first_page():
    refs = parse_listing(fixture_html("results_180_page1.html"))
    assert len(refs) == 9
    assert refs[0] == DocumentRef(
        document_id="15038",
        url="https://mintrans.gov.ru/documents/7/15038",
        title="ТРАНСПОРТ РОССИИ. Информационно-статистический бюллетень. 9 месяцев 2025 года",
        listed_date=date(2025, 12, 11),
    )
    assert refs[1].listed_date == date(2025, 9, 8)
    assert len({r.document_id for r in refs}) == 9


def test_parse_listing_last_page():
    refs = parse_listing(fixture_html("results_180_page9.html"))
    assert [r.document_id for r in refs] == ["990"]


def test_last_page_from_first_and_last_pages():
    assert last_page(fixture_html("results_180_page1.html")) == 9
    # на последней странице её номер есть только в активном пункте без href
    assert last_page(fixture_html("results_180_page9.html")) == 9


def test_last_page_without_pagination():
    assert last_page("<html><body><p>нет страниц</p></body></html>") == 1


def test_parse_listing_fallback_without_cards():
    html = """
    <div class="content">
      <a href="/documents/11/15714?type=11">Отчёт</a>
      <a href="/documents?type=7">Все материалы</a>
      <a href="/documents/11/15714">Дубль</a>
    </div>"""
    refs = parse_listing(html)
    assert [(r.document_id, r.url, r.title) for r in refs] == [
        ("15714", "https://mintrans.gov.ru/documents/11/15714", "Отчёт")
    ]


def test_document_ref_from_href():
    assert document_ref_from_href("/documents/7/990").document_id == "990"
    assert document_ref_from_href("/documents/11/15714?type=11").type_id == "11"
    assert (
        document_ref_from_href("https://mintrans.gov.ru/documents/11/15714/?type=11").url
        == "https://mintrans.gov.ru/documents/11/15714"
    )
    assert document_ref_from_href("/documents?type=7") is None
    assert document_ref_from_href("/file/552522") is None


def test_listing_url():
    assert listing_url(180, 1) == "https://mintrans.gov.ru/ministry/results/180/documents"
    assert listing_url(181, 3) == "https://mintrans.gov.ru/ministry/results/181/documents?page=3"
