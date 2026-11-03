from datetime import date

from conftest import fixture_html
from mintrans_stats.sources.documents import Attachment, file_id_from_href, parse_document


def test_parse_document_single_file():
    page = parse_document(
        fixture_html("document_15038.html"), "https://mintrans.gov.ru/documents/7/15038"
    )
    assert page.document_id == "15038"
    assert page.title == (
        "ТРАНСПОРТ РОССИИ. Информационно-статистический бюллетень. 9 месяцев 2025 года"
    )
    assert page.published_at == date(2025, 12, 11)
    assert page.attachments == [
        Attachment(
            file_id="552522",
            url="https://mintrans.gov.ru/file/552522",
            title="ТРАНСПОРТ РОССИИ. Информационно-статистический бюллетень. 9 месяцев 2025 года",
            size_text="2493 kb",
            file_type="pdf",
        )
    ]


def test_parse_document_two_files_and_archive():
    page = parse_document(
        fixture_html("document_12182.html"), "https://mintrans.gov.ru/documents/7/12182"
    )
    assert page.title == (
        "ТРАНСПОРТ РОССИИ. Информационно-статистический бюллетень. Январь-сентябрь 2022 года."
    )
    assert page.published_at == date(2022, 12, 16)
    assert [(a.file_id, a.size_text, a.file_type) for a in page.attachments] == [
        ("488228", "741 kb", "pdf"),
        ("488229", "1839 kb", "doc"),
    ]
    # размер вынесен в size_text и не остаётся в названии
    assert not any(a.title.endswith("kb)") for a in page.attachments)

    archive = parse_document(
        fixture_html("document_3192.html"), "https://mintrans.gov.ru/documents/7/3192"
    )
    assert [(a.file_id, a.file_type) for a in archive.attachments] == [("398905", "rar")]


def test_parse_document_fallback_links_and_title_tag():
    html = """
    <html><head><title>Справка | Министерство транспорта Российской Федерации</title></head>
    <body><div class="content">
      <p><a href="/file/1">Таблица (12 kB)</a></p>
      <p><a href="/file/2">Без размера</a></p>
    </div></body></html>"""
    page = parse_document(html, "https://mintrans.gov.ru/documents/7/1")
    assert page.title == "Справка"
    assert page.published_at is None
    assert [(a.file_id, a.title, a.size_text, a.file_type) for a in page.attachments] == [
        ("1", "Таблица", "12 kB", ""),
        ("2", "Без размера", "", ""),
    ]


def test_file_id_from_href():
    assert file_id_from_href("/file/552522") == "552522"
    assert file_id_from_href("https://mintrans.gov.ru/file/552522") == "552522"
    assert file_id_from_href("/documents/7/1") is None


def test_parse_document_link_without_text():
    """На сайте бывает ссылка на файл только с размером, без названия."""
    page = parse_document(
        fixture_html("document_9103.html"), "https://mintrans.gov.ru/documents/7/9103"
    )
    assert page.published_at == date(2017, 12, 11)
    assert [(a.file_id, a.title, a.size_text, a.file_type) for a in page.attachments] == [
        ("408859", "", "1388 kb", "doc")
    ]
