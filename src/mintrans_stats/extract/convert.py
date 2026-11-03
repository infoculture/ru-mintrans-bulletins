"""Конвертация старых форматов Word (doc, rtf) в docx через LibreOffice без интерфейса.

LibreOffice ищется в ``MINTRANS_SOFFICE``, затем в ``PATH`` (``soffice``/``libreoffice``),
затем в стандартном месте macOS. Конвертация идёт пачками — запуск ``soffice`` дорогой.
Используется отдельный профиль LibreOffice, чтобы не мешать открытому у пользователя
LibreOffice и не трогать его настройки. В новом профиле действуют настройки безопасности по
умолчанию, при которых LibreOffice не исполняет макросы из документов.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

MACOS_SOFFICE = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
BATCH_SIZE = 20
TIMEOUT_PER_FILE_S = 120


class ConversionError(Exception):
    """LibreOffice недоступен или не смог сконвертировать файл."""


def find_soffice() -> str | None:
    candidates = [os.environ.get("MINTRANS_SOFFICE"), shutil.which("soffice"),
                  shutil.which("libreoffice"), MACOS_SOFFICE]  # fmt: skip
    return next((c for c in candidates if c and Path(c).is_file()), None)


def convert_to_docx(sources: list[Path], out_dir: Path, profile_dir: Path) -> dict[Path, Path]:
    """Сконвертировать файлы в docx; вернуть {исходный файл: docx} для удачных.

    Имена результатов совпадают с исходными с заменой расширения, поэтому у исходных файлов
    в одной пачке должны быть разные основы имён — вызывающий код это обеспечивает.
    """
    soffice = find_soffice()
    if soffice is None:
        raise ConversionError(
            "LibreOffice не найден: установите его (brew install --cask libreoffice) "
            "или задайте MINTRANS_SOFFICE"
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
    result: dict[Path, Path] = {}
    for start in range(0, len(sources), BATCH_SIZE):
        batch = sources[start : start + BATCH_SIZE]
        cmd = [
            soffice,
            f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
            "--headless",
            "--norestore",
            "--nologo",
            "--convert-to",
            "docx:MS Word 2007 XML",
            "--outdir",
            str(out_dir),
            *[str(p) for p in batch],
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=TIMEOUT_PER_FILE_S * len(batch)
            )
        except subprocess.TimeoutExpired as exc:
            logger.error("LibreOffice не уложился во время на пачке из %s файлов", len(batch))
            raise ConversionError("LibreOffice: превышено время конвертации") from exc
        if proc.returncode != 0:
            logger.warning("LibreOffice вернул %s: %s", proc.returncode, proc.stderr.strip()[:300])
        for src in batch:
            target = out_dir / f"{src.stem}.docx"
            if target.is_file() and target.stat().st_size > 0:
                result[src] = target
            else:
                logger.warning("не сконвертирован: %s", src)
    return result
