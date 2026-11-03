"""Настройки парсера: адреса сайта, сеть, пути к данным.

Значения по умолчанию переопределяются переменными окружения ``MINTRANS_*``
и затем флагами командной строки.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

BASE_URL = "https://mintrans.gov.ru"
STATISTICS_SECTION_ID = 180

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


def section_url(section_id: int = STATISTICS_SECTION_ID) -> str:
    """URL списка документов раздела «Результаты деятельности»."""
    return f"{BASE_URL}/ministry/results/{section_id}/documents"


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return float(value) if value else default


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value else default


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if not value:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


@dataclass(frozen=True)
class Settings:
    """Сетевые параметры и пути. Неизменяемый объект: правки через ``with_overrides``."""

    proxy: str | None = None
    """SOCKS/HTTP-прокси только для запросов парсера, например ``socks5h://127.0.0.1:1080``."""
    delay_s: float = 1.0
    """Минимальный интервал между началами запросов к сайту."""
    connect_timeout_s: float = 20.0
    read_timeout_s: float = 60.0
    max_attempts: int = 4
    """Сколько раз пробовать запрос при 5xx и сетевых ошибках."""
    backoff_base_s: float = 2.0
    backoff_max_s: float = 60.0
    user_agent: str = DEFAULT_USER_AGENT
    data_dir: Path = field(default_factory=lambda: Path("data"))
    cache_html: bool = True

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            proxy=os.environ.get("MINTRANS_PROXY") or None,
            delay_s=_env_float("MINTRANS_DELAY", cls.delay_s),
            connect_timeout_s=_env_float("MINTRANS_CONNECT_TIMEOUT", cls.connect_timeout_s),
            read_timeout_s=_env_float("MINTRANS_TIMEOUT", cls.read_timeout_s),
            max_attempts=_env_int("MINTRANS_MAX_ATTEMPTS", cls.max_attempts),
            user_agent=os.environ.get("MINTRANS_USER_AGENT") or DEFAULT_USER_AGENT,
            data_dir=Path(os.environ.get("MINTRANS_DATA_DIR") or "data"),
            cache_html=_env_bool("MINTRANS_CACHE_HTML", True),
        )

    def with_overrides(self, **changes) -> Settings:
        """Копия с заменой полей; ``None`` в значении означает «не менять»."""
        return replace(self, **{k: v for k, v in changes.items() if v is not None})

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def raw_html_dir(self) -> Path:
        return self.data_dir / "raw" / "html"

    @property
    def catalog_dir(self) -> Path:
        return self.data_dir / "catalog"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"
