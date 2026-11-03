import json

import httpx

from conftest import fixture_html
from mintrans_stats.crawl import run_crawl


def listing(items: list[tuple[str, str, str]]) -> str:
    cards = "".join(
        f'<div class="document-list-item"><span class="date-span">{d}</span>'
        f'<a href="{href}"><p class="document-text">{title}</p></a></div>'
        for href, title, d in items
    )
    return f'<div class="content"><div class="document-list">{cards}</div></div>'


SECTION = listing(
    [
        (
            "/documents/7/12182?type=7",
            "ТРАНСПОРТ РОССИИ. Информационно-статистический бюллетень. Январь-сентябрь 2022 года.",
            "16 Декабря 2022",
        ),
        (
            "/documents/7/990?type=7",
            "Результаты и основные направления деятельности на 2009–2011 гг.",
            "1 Января 2010",
        ),
    ]
)
SEARCH = listing(
    [
        (
            "/documents/7/9103?type=7",
            "ТРАНСПОРТ РОССИИ. Информационно-статистический бюллетень. Январь-сентябрь 2017 года",
            "11 Декабря 2017",
        ),
        (
            "/documents/7/12182?type=7",
            "ТРАНСПОРТ РОССИИ. Информационно-статистический бюллетень. Январь-сентябрь 2022 года.",
            "16 Декабря 2022",
        ),
        (
            "/documents/2/12650?type=2",
            "Приказ Минтранса России «Об утверждении Методик расчета показателей»",
            "14 Июля 2023",
        ),
    ]
)


def make_site(requests_log: list[str]):
    pages = {
        "/ministry/results/180/documents": SECTION,
        "/documents": SEARCH,
        "/documents/7/12182": fixture_html("document_12182.html"),
        "/documents/7/9103": fixture_html("document_9103.html"),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests_log.append(str(request.url))
        body = pages.get(request.url.path)
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    return handler


def read(path):
    return [json.loads(line) for line in path.open(encoding="utf-8")]


def test_run_crawl_keeps_only_bulletins(make_client, settings):
    log: list[str] = []
    summary = run_crawl(make_client(make_site(log)), settings, queries=["бюллетень"])

    documents = {d["document_id"]: d for d in read(settings.catalog_dir / "documents.jsonl")}
    assert list(documents) == ["12182", "9103"]  # по убыванию id; доклад и приказ отброшены
    assert not any("/documents/7/990" in url or "/documents/2/12650" in url for url in log)

    d12182 = documents["12182"]
    assert d12182["published_at"] == "2022-12-16" and d12182["files_count"] == 2
    assert d12182["sources"] == [
        {"kind": "section", "section_id": 180},
        {"kind": "search", "query": "бюллетень"},
    ]
    d9103 = documents["9103"]
    assert d9103["sources"] == [{"kind": "search", "query": "бюллетень"}]

    files = read(settings.catalog_dir / "files.jsonl")
    assert [(f["document_id"], f["file_id"], f["file_type"]) for f in files] == [
        ("12182", "488228", "pdf"),
        ("12182", "488229", "doc"),
        ("9103", "408859", "doc"),
    ]
    # у вложения 9103 на сайте ссылка без текста: название берётся у документа
    assert files[2]["file_title"] == d9103["title"] and files[2]["file_title_from_document"]
    assert files[2]["file_size"] == "1388 kb"

    assert summary["documents_found"] == 4 and summary["bulletins"] == 2
    assert summary["bulletins_outside_sections"] == ["9103"]
    assert summary["files"] == 3 and summary["errors"] == []
    assert summary["sources"]["section:180"] == {"documents": 2, "new": 2}
    assert summary["sources"]["search:бюллетень"] == {"documents": 3, "new": 2}


def test_run_crawl_uses_cached_cards_on_rerun(make_client, settings):
    log: list[str] = []
    client = make_client(make_site(log))
    run_crawl(client, settings, queries=[])
    first = len(log)
    run_crawl(client, settings, queries=[])
    assert len(log) - first == 1  # снова только список раздела, карточка из кеша


def test_run_crawl_records_missing_card(make_client, settings):
    log: list[str] = []
    handler = make_site(log)

    def broken(request):
        if request.url.path == "/documents/7/12182":
            return httpx.Response(404)
        return handler(request)

    summary = run_crawl(make_client(broken), settings, queries=[])
    assert summary["errors"] and "12182" in summary["errors"][0]["url"]
    assert summary["files"] == 0


def test_run_crawl_continues_after_broken_card(make_client, settings):
    """Недоступная карточка одного выпуска не останавливает обход остальных."""
    title = "ТРАНСПОРТ РОССИИ. Информационно-статистический бюллетень. {}"
    pages = {
        "/ministry/results/180/documents": listing(
            [
                ("/documents/7/1", title.format("2020 год"), "1 Января 2021"),
                ("/documents/7/12182", title.format("2022 год"), "16 Декабря 2022"),
            ]
        ),
        "/documents/7/12182": fixture_html("document_12182.html"),
    }

    def handler(request):
        body = pages.get(request.url.path)
        return httpx.Response(200, text=body) if body else httpx.Response(404)

    summary = run_crawl(make_client(handler), settings, queries=[])
    assert [e["url"] for e in summary["errors"]] == ["https://mintrans.gov.ru/documents/7/1"]
    assert summary["bulletins"] == 2 and summary["files"] == 2
