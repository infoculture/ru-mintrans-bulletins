"""Командная строка: ``mintrans-stats <команда>``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import httpx

from . import __version__
from .config import STATISTICS_SECTION_ID, Settings, section_url
from .crawl import DEFAULT_QUERIES, run_crawl
from .database import DEFAULT_DB_NAME, build_database
from .download import run_download
from .extract.run import run_extract
from .http import MintransClient, MintransError
from .normalize.bulletin import run_bulletin
from .utils import utc_now_iso
from .validate import validate_jsonl

logger = logging.getLogger("mintrans_stats")

IP_COUNTRY_URL = "https://ipinfo.io/country"


def _setup_logging(settings: Settings, command: str, level: str) -> Path:
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = settings.logs_dir / f"{command}-{utc_now_iso().replace(':', '')}.log"
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stderr),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    logging.basicConfig(level=getattr(logging, level.upper()), format=fmt, handlers=handlers)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return log_file


def _print_json(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_check(settings: Settings, args: argparse.Namespace) -> int:
    """Страна выхода через прокси и доступность раздела «Статистика»."""
    report: dict = {"proxy": settings.proxy}
    try:
        with httpx.Client(proxy=settings.proxy, trust_env=False, timeout=20) as http:
            report["exit_country"] = http.get(IP_COUNTRY_URL).text.strip()
    except httpx.HTTPError as exc:
        report["exit_country_error"] = str(exc)
    url = section_url(args.section)
    started = time.monotonic()
    try:
        with MintransClient(settings.with_overrides(max_attempts=2)) as client:
            response = client.get(url)
        report.update(
            {
                "url": url,
                "status": response.status_code,
                "bytes": len(response.content),
                "seconds": round(time.monotonic() - started, 2),
                "ok": True,
            }
        )
    except MintransError as exc:
        report.update({"url": url, "ok": False, "error": str(exc)})
    _print_json(report)
    if not report["ok"] and report.get("exit_country") not in (None, "RU"):
        print(
            "Сайт доступен только с российских IP: задайте MINTRANS_PROXY "
            "(например socks5h://127.0.0.1:1080) или флаг --proxy.",
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def cmd_crawl(settings: Settings, args: argparse.Namespace) -> int:
    with MintransClient(settings) as client:
        summary = run_crawl(
            client,
            settings,
            queries=() if args.no_search else (args.query or DEFAULT_QUERIES),
            refresh=args.refresh,
        )
    _print_json(summary)
    return 1 if summary["errors"] else 0


def cmd_download(settings: Settings, args: argparse.Namespace) -> int:
    with MintransClient(settings) as client:
        summary = run_download(
            client,
            settings,
            input_jsonl=Path(args.input) if args.input else settings.catalog_dir / "files.jsonl",
            resume=args.resume,
            limit=args.limit,
            fill_meta=args.fill_meta,
        )
    _print_json(summary)
    return 1 if summary["failed"] else 0


def cmd_extract(settings: Settings, args: argparse.Namespace) -> int:
    summary = run_extract(settings, force=args.force, limit=args.limit)
    _print_json(summary)
    return 1 if summary["errors"] else 0


def cmd_normalize(settings: Settings, args: argparse.Namespace) -> int:
    _print_json(run_bulletin(settings))
    return 0


def cmd_build_db(settings: Settings, args: argparse.Namespace) -> int:
    _print_json(build_database(settings, args.output, args.parquet_dir))
    return 0


def cmd_validate(settings: Settings, args: argparse.Namespace) -> int:
    path = Path(args.jsonl)
    count, errors = validate_jsonl(
        path,
        data_dir=settings.data_dir,
        require_download_fields=args.require_download,
        check_files=args.require_download,
    )
    _print_json({"jsonl": str(path), "rows": count, "errors": errors, "ok": not errors})
    return 1 if errors else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mintrans-stats",
        description="Бюллетень «Транспорт России» с сайта Минтранса России (mintrans.gov.ru)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--proxy",
        help="прокси только для запросов парсера, например socks5h://127.0.0.1:1080 "
        "(по умолчанию MINTRANS_PROXY)",
    )
    parser.add_argument("--delay", type=float, help="секунд между запросами (по умолчанию 1.0)")
    parser.add_argument("--data-dir", type=Path, help="каталог данных (по умолчанию ./data)")
    parser.add_argument("--no-cache", action="store_true", help="не сохранять и не читать кеш HTML")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING"])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="проверить доступ к сайту и страну выхода")
    p.add_argument("--section", type=int, default=STATISTICS_SECTION_ID)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("crawl", help="собрать каталог выпусков бюллетеня и их файлов")
    p.add_argument(
        "--query",
        action="append",
        help="поисковая подстрока вместо набора по умолчанию; можно повторять",
    )
    p.add_argument("--no-search", action="store_true", help="не искать выпуски вне раздела")
    p.add_argument("--refresh", action="store_true", help="перекачать карточки, игнорируя кеш")
    p.set_defaults(func=cmd_crawl)

    p = sub.add_parser("download", help="скачать файлы выпусков по каталогу из crawl")
    p.add_argument("--input", help="JSONL вложений (по умолчанию data/catalog/files.jsonl)")
    p.add_argument("--no-resume", dest="resume", action="store_false", help="качать заново")
    p.add_argument("--limit", type=int, help="обработать только первые N строк")
    p.add_argument(
        "--fill-meta",
        action="store_true",
        help="для уже скачанных файлов без исходного имени запросить заголовки ответа",
    )
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("extract", help="извлечь таблицы и текст из скачанных файлов")
    p.add_argument(
        "--force", action="store_true", help="обработать заново, игнорируя прошлые индексы"
    )
    p.add_argument("--limit", type=int, help="обработать только первые N элементов")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("normalize", help="бюллетень в длинном формате, ключевые ряды, сверки")
    p.set_defaults(func=cmd_normalize)

    p = sub.add_parser("build-db", help="собрать базу DuckDB и её Parquet-выгрузку")
    p.add_argument("--output", type=Path, help=f"файл базы (по умолчанию data/{DEFAULT_DB_NAME})")
    p.add_argument(
        "--parquet-dir", type=Path, help="каталог Parquet-выгрузки (по умолчанию data/parquet)"
    )
    p.set_defaults(func=cmd_build_db)

    p = sub.add_parser("validate", help="проверить JSONL и файлы на диске")
    p.add_argument("--jsonl", default="data/catalog/downloads.jsonl")
    p.add_argument(
        "--require-download",
        action="store_true",
        help="требовать поля скачивания и сверять файлы по sha256",
    )
    p.set_defaults(func=cmd_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.from_env().with_overrides(
        proxy=args.proxy, delay_s=args.delay, data_dir=args.data_dir
    )
    if args.no_cache:
        settings = settings.with_overrides(cache_html=False)
    _setup_logging(settings, args.command, args.log_level)
    try:
        return args.func(settings, args)
    except MintransError as exc:
        logger.error("%s", exc)
        return 2
