"""HTTP-клиент для mintrans.gov.ru.

Сайт доступен только с российских IP, поэтому клиент умеет ходить через прокси
(``Settings.proxy``), не трогая глобальные ``HTTP(S)_PROXY``. Ограничения сайта
и туннеля учитываются так:

* запросы идут не чаще одного в ``delay_s`` секунд;
* 5xx, таймауты и обрывы соединения повторяются с экспоненциальной паузой;
* 403/429 и страница блокировки WAF останавливают работу (``BlockedError``):
  обходить блокировку сменой IP или усилением напора нельзя;
* скачивание файлов идёт потоком в ``.part`` и докачивается через ``Range``.
"""

from __future__ import annotations

import hashlib
import logging
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote_plus, urlparse

import httpx

from .config import Settings

logger = logging.getLogger(__name__)

BLOCK_MARKERS = ("Access to resource was blocked",)
RETRY_STATUSES = frozenset({500, 502, 503, 504, 520, 521, 522, 524})
CHUNK_SIZE = 64 * 1024


class MintransError(Exception):
    """Базовая ошибка обращения к сайту."""


class BlockedError(MintransError):
    """Сайт ответил 403/429 или страницей блокировки: продолжать нельзя."""


class NotFoundError(MintransError):
    """Страница или файл не найдены (404/410)."""


class FetchError(MintransError):
    """Запрос не удался после всех попыток или вернул неожиданный статус."""


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    size: int
    sha256: str
    status: int
    resumed: bool
    headers: dict[str, str]


def cache_path_for(url: str, root: Path) -> Path:
    """Путь в кеше для URL: ``<root>/<host>/<path>[__<query>].html``."""
    parsed = urlparse(url)
    path = parsed.path.strip("/") or "index"
    if parsed.path.endswith("/"):
        path = f"{path}/index"
    name = path
    if parsed.query:
        # query приводится к одному виду: «q=бюллетень» и «q=%D0%B1…» дают один файл
        query = quote(unquote_plus(parsed.query), safe="=&")
        name = f"{path}__{query}".replace("&", "__")
    return root / parsed.netloc / f"{name}.html"


class MintransClient:
    """Синхронный клиент с ограничением частоты, повторами и кешем HTML."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self._sleep = sleep
        self._clock = clock
        self._lock = threading.Lock()
        self._last_request_at: float | None = None
        self.requests_made = 0
        timeout = httpx.Timeout(settings.read_timeout_s, connect=settings.connect_timeout_s)
        self._http = httpx.Client(
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            },
            timeout=timeout,
            follow_redirects=True,
            proxy=settings.proxy,
            transport=transport,
            trust_env=False,
        )

    def __enter__(self) -> MintransClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # --- ограничение частоты и повторы -------------------------------------------------

    def _throttle(self) -> None:
        with self._lock:
            now = self._clock()
            if self._last_request_at is not None:
                wait = self.settings.delay_s - (now - self._last_request_at)
                if wait > 0:
                    self._sleep(wait)
            self._last_request_at = self._clock()
            self.requests_made += 1

    def _backoff(self, attempt: int) -> float:
        base = self.settings.backoff_base_s * 2 ** (attempt - 1)
        return min(base, self.settings.backoff_max_s) + random.uniform(0, 1)

    def _with_retries(
        self, url: str, func: Callable[[], object], attempts: int | None = None
    ) -> object:
        max_attempts = attempts or self.settings.max_attempts
        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            self._throttle()
            try:
                return func()
            except (BlockedError, NotFoundError):
                raise
            except (httpx.TimeoutException, httpx.TransportError, _RetryableStatus) as exc:
                last_error = exc
                if attempt == max_attempts:
                    break
                pause = self._backoff(attempt)
                logger.warning(
                    "попытка %s/%s для %s не удалась: %s; пауза %.1f с",
                    attempt,
                    max_attempts,
                    url,
                    exc,
                    pause,
                )
                self._sleep(pause)
        raise FetchError(f"не удалось получить {url}: {last_error}") from last_error

    @staticmethod
    def _check_status(response: httpx.Response) -> None:
        status = response.status_code
        if status in (403, 429):
            raise BlockedError(f"HTTP {status} на {response.url}: сайт ограничил доступ")
        if status in (404, 410):
            raise NotFoundError(f"HTTP {status}: {response.url}")
        if status in RETRY_STATUSES:
            raise _RetryableStatus(f"HTTP {status}")
        if status >= 400:
            raise FetchError(f"HTTP {status} на {response.url}")

    # --- публичные методы ---------------------------------------------------------------

    def get(self, url: str, *, timeout: float | None = None) -> httpx.Response:
        """GET с повторами. Тело ответа загружено целиком."""

        def do() -> httpx.Response:
            kwargs = {"timeout": timeout} if timeout is not None else {}
            response = self._http.get(url, **kwargs)
            self._check_status(response)
            if "html" in response.headers.get("content-type", "") and any(
                marker in response.text for marker in BLOCK_MARKERS
            ):
                raise BlockedError(f"страница блокировки на {url}")
            return response

        return self._with_retries(url, do)  # type: ignore[return-value]

    def get_text(self, url: str, *, refresh: bool = False, timeout: float | None = None) -> str:
        """HTML-страница как текст.

        При включённом кеше (``Settings.cache_html``) страница сохраняется на диск, а
        повторный вызов читает её оттуда без сети. ``refresh=True`` всегда идёт на сайт
        и обновляет копию — так обходятся списки, где появляются новые документы.
        """
        use_cache = self.settings.cache_html
        cached = cache_path_for(url, self.settings.raw_html_dir)
        if use_cache and not refresh and cached.is_file():
            return cached.read_text(encoding="utf-8")
        text = self.get(url, timeout=timeout).text
        if use_cache:
            cached.parent.mkdir(parents=True, exist_ok=True)
            tmp = cached.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(cached)
        return text

    def fetch_headers(self, url: str, *, attempts: int | None = None) -> dict[str, str]:
        """Заголовки ответа на GET; тело файла не скачивается."""

        def do() -> dict[str, str]:
            with self._http.stream("GET", url) as response:
                self._check_status(response)
                return {k.lower(): v for k, v in response.headers.items()}

        return self._with_retries(url, do, attempts)  # type: ignore[return-value]

    def download(
        self,
        url: str,
        target: Path,
        *,
        timeout: float | None = None,
        attempts: int | None = None,
    ) -> DownloadResult:
        """Скачать файл потоком в ``target``; недокачанный ``target.part`` докачивается.

        ``timeout`` и ``attempts`` переопределяют настройки для «висящих» адресов.
        """
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        state: dict[str, object] = {"resumed": False}

        def do() -> DownloadResult:
            offset = part.stat().st_size if part.exists() else 0
            headers = {"Range": f"bytes={offset}-"} if offset else {}
            kwargs = {"timeout": timeout} if timeout is not None else {}
            with self._http.stream("GET", url, headers=headers, **kwargs) as response:
                if response.status_code == 416 and offset:
                    # .part уже содержит весь файл (или устарел) — начинаем заново
                    part.unlink()
                    raise _RetryableStatus("HTTP 416, .part сброшен")
                self._check_status(response)
                if offset and response.status_code == 206:
                    mode = "ab"
                    state["resumed"] = True
                else:
                    mode = "wb"
                with part.open(mode) as fh:
                    for chunk in response.iter_bytes(CHUNK_SIZE):
                        fh.write(chunk)
                status = response.status_code
                resp_headers = {k.lower(): v for k, v in response.headers.items()}
            head = part.read_bytes()[:2048].decode("utf-8", errors="ignore")
            if any(marker in head for marker in BLOCK_MARKERS):
                part.unlink()
                raise BlockedError(f"страница блокировки вместо файла {url}")
            part.replace(target)
            return DownloadResult(
                path=target,
                size=target.stat().st_size,
                sha256=sha256_file(target),
                status=status,
                resumed=bool(state["resumed"]),
                headers=resp_headers,
            )

        return self._with_retries(url, do, attempts)  # type: ignore[return-value]


class _RetryableStatus(Exception):
    """Внутренний сигнал: статус ответа стоит повторить."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
