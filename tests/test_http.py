from pathlib import Path

import httpx
import pytest

from mintrans_stats.http import (
    CHUNK_SIZE,
    BlockedError,
    FetchError,
    NotFoundError,
    cache_path_for,
    sha256_file,
)

URL = "https://mintrans.gov.ru/ministry/results/180/documents"


def html_response(status: int = 200, body: str = "<html>ok</html>") -> httpx.Response:
    return httpx.Response(status, text=body, headers={"content-type": "text/html; charset=UTF-8"})


def test_retries_server_errors_then_succeeds(make_client, fake_time):
    statuses = iter([500, 502, 200])
    calls = []

    def handler(request):
        calls.append(request.url)
        return html_response(next(statuses))

    client = make_client(handler)
    assert client.get(URL).text == "<html>ok</html>"
    assert len(calls) == 3
    backoffs = [s for s in fake_time.sleeps if s >= 2]
    assert len(backoffs) == 2 and backoffs[1] > backoffs[0]  # пауза растёт


def test_gives_up_after_max_attempts(make_client):
    calls = []

    def handler(request):
        calls.append(1)
        return html_response(503)

    with pytest.raises(FetchError):
        make_client(handler).get(URL)
    assert len(calls) == 3


def test_retries_transport_errors(make_client):
    attempts = iter([httpx.ConnectError("обрыв"), httpx.ReadTimeout("таймаут"), None])

    def handler(request):
        error = next(attempts)
        if error:
            raise error
        return html_response()

    assert make_client(handler).get(URL).status_code == 200


@pytest.mark.parametrize("status", [403, 429])
def test_blocking_statuses_stop_without_retry(make_client, status):
    calls = []

    def handler(request):
        calls.append(1)
        return html_response(status)

    with pytest.raises(BlockedError):
        make_client(handler).get(URL)
    assert len(calls) == 1


def test_waf_block_page_is_blocked_error(make_client):
    client = make_client(lambda r: html_response(200, "<h1>Access to resource was blocked</h1>"))
    with pytest.raises(BlockedError):
        client.get(URL)


def test_not_found_is_not_retried(make_client):
    calls = []

    def handler(request):
        calls.append(1)
        return html_response(404)

    with pytest.raises(NotFoundError):
        make_client(handler).get(URL)
    assert len(calls) == 1


def test_throttle_keeps_delay_between_requests(make_client, fake_time):
    client = make_client(lambda r: html_response())
    client.get(URL)
    client.get(URL)
    client.get(URL)
    assert fake_time.sleeps == [pytest.approx(1.0), pytest.approx(1.0)]
    assert client.requests_made == 3


def test_get_text_uses_cache_unless_refresh(make_client, settings):
    calls = []

    def handler(request):
        calls.append(1)
        return html_response(200, f"<html>версия {len(calls)}</html>")

    client = make_client(handler)
    url = "https://mintrans.gov.ru/documents/7/990"
    assert client.get_text(url) == "<html>версия 1</html>"
    assert client.get_text(url) == "<html>версия 1</html>"
    assert len(calls) == 1
    assert client.get_text(url, refresh=True) == "<html>версия 2</html>"
    assert cache_path_for(url, settings.raw_html_dir).read_text(encoding="utf-8") == (
        "<html>версия 2</html>"
    )


def test_get_text_without_cache(make_client, settings):
    calls = []

    def handler(request):
        calls.append(1)
        return html_response()

    client = make_client(handler, client_settings=settings.with_overrides(cache_html=False))
    client.get_text(URL)
    client.get_text(URL)
    assert len(calls) == 2
    assert not settings.raw_html_dir.exists()


def test_cache_path_for(tmp_path):
    root = tmp_path / "html"
    assert cache_path_for(URL + "?page=2", root) == (
        root / "mintrans.gov.ru/ministry/results/180/documents__page=2.html"
    )
    assert cache_path_for("https://mintrans.gov.ru/ministry/results/180/", root) == (
        root / "mintrans.gov.ru/ministry/results/180/index.html"
    )
    expected = "documents__type=0__q=%D0%B1%D1%8E%D0%BB%D0%BB%D0%B5%D1%82%D0%B5%D0%BD%D1%8C.html"
    assert cache_path_for("https://mintrans.gov.ru/documents?type=0&q=бюллетень", root).name == (
        expected
    )
    # уже закодированный query (как из urlencode) не кодируется второй раз
    encoded = "https://mintrans.gov.ru/documents?type=0&q=%D0%B1%D1%8E%D0%BB%D0%BB%D0%B5%D1%82%D0%B5%D0%BD%D1%8C"
    assert cache_path_for(encoded, root).name == expected
    assert (
        cache_path_for("https://mintrans.gov.ru/documents?q=a+b", root).name
        == "documents__q=a%20b.html"
    )


PAYLOAD = b"%PDF-1.4 " + bytes(range(256)) * 40


def test_download_full_file(make_client, tmp_path):
    def handler(request):
        return httpx.Response(
            200,
            content=PAYLOAD,
            headers={
                "content-type": "application/pdf",
                "content-disposition": 'attachment; filename="report.pdf"',
            },
        )

    target = tmp_path / "files" / "a.pdf"
    result = make_client(handler).download("https://mintrans.gov.ru/file/1", target)
    assert target.read_bytes() == PAYLOAD
    assert result.size == len(PAYLOAD)
    assert result.sha256 == sha256_file(target)
    assert result.headers["content-disposition"] == 'attachment; filename="report.pdf"'
    assert not result.resumed
    assert not Path(str(target) + ".part").exists()


def test_download_resumes_part_file_with_range(make_client, tmp_path):
    target = tmp_path / "files" / "a.pdf"
    target.parent.mkdir(parents=True)
    done = 1000
    (target.parent / "a.pdf.part").write_bytes(PAYLOAD[:done])
    seen_ranges = []

    def handler(request):
        seen_ranges.append(request.headers.get("range"))
        start = int(request.headers["range"].removeprefix("bytes=").rstrip("-"))
        return httpx.Response(206, content=PAYLOAD[start:])

    result = make_client(handler).download("https://mintrans.gov.ru/file/1", target)
    assert seen_ranges == [f"bytes={done}-"]
    assert result.resumed
    assert target.read_bytes() == PAYLOAD


def test_download_restarts_when_range_ignored(make_client, tmp_path):
    target = tmp_path / "a.pdf"
    (tmp_path / "a.pdf.part").write_bytes(b"stale")
    result = make_client(lambda r: httpx.Response(200, content=PAYLOAD)).download(
        "https://mintrans.gov.ru/file/1", target
    )
    assert not result.resumed
    assert target.read_bytes() == PAYLOAD


def test_download_retries_broken_stream_and_resumes(make_client, tmp_path):
    """Обрыв посреди файла: вторая попытка докачивает с последнего записанного блока."""
    big = PAYLOAD * 20  # больше двух блоков по CHUNK_SIZE

    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield big[: CHUNK_SIZE + 1000]
            raise httpx.ReadError("обрыв туннеля")

    ranges = []

    def handler(request):
        ranges.append(request.headers.get("range"))
        if len(ranges) == 1:
            return httpx.Response(200, stream=BrokenStream())
        start = int(request.headers["range"].removeprefix("bytes=").rstrip("-"))
        return httpx.Response(206, content=big[start:])

    target = tmp_path / "a.pdf"
    result = make_client(handler).download("https://mintrans.gov.ru/file/1", target)
    # записан только целый блок; хвост из буфера при обрыве теряется и докачивается
    assert ranges == [None, f"bytes={CHUNK_SIZE}-"]
    assert result.resumed
    assert target.read_bytes() == big
