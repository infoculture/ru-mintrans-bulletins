"""Распаковка архивов (zip, rar) с вложенными архивами.

Скачанные файлы — недоверенные данные, поэтому:

* члены архива распаковываются только внутрь целевого каталога: абсолютные пути и ``..``
  отбрасываются (защита от zip-slip), ссылки не создаются;
* ограничены глубина вложенности, число членов и суммарный размер (защита от «zip-бомб»);
* ничего из распакованного не исполняется.

Имена в ZIP из Windows часто записаны в CP866 без флага UTF-8 — Python читает их как CP437;
такие имена перекодируются. RAR распаковывается утилитой ``bsdtar`` (libarchive), которая
есть в macOS и в пакете ``libarchive-tools`` в Linux.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..filetypes import detect_type

logger = logging.getLogger(__name__)

ARCHIVE_TYPES = frozenset({"zip", "rar", "7z"})
MAX_DEPTH = 3
MAX_MEMBERS = 2000
MAX_TOTAL_BYTES = 2 * 1024**3
ZIP_UTF8_FLAG = 0x800


class ArchiveError(Exception):
    """Архив не удалось распаковать или он превышает ограничения."""


@dataclass(frozen=True)
class Member:
    path: Path
    """Распакованный файл на диске."""
    member: str
    """Путь внутри архива (для вложенных — через ``!``: ``a.zip!b/c.doc``)."""


def safe_member_path(name: str) -> PurePosixPath | None:
    """Относительный путь члена архива без ``..`` и корня; ``None`` для недопустимых имён.

    Имя приводится к NFC: bsdtar в macOS отдаёт имена в NFD («ё» из двух кодовых точек).
    """
    name = unicodedata.normalize("NFC", name.replace("\\", "/"))
    parts = [p for p in PurePosixPath(name).parts if p.strip("/") not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return PurePosixPath(*parts)


def zip_member_name(info: zipfile.ZipInfo) -> str:
    """Имя члена ZIP с исправлением CP866, записанного без флага UTF-8."""
    if info.flag_bits & ZIP_UTF8_FLAG:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp866")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def _unpack_zip(path: Path, dest: Path) -> list[Path]:
    out: list[Path] = []
    total = 0
    with zipfile.ZipFile(path) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_MEMBERS:
            raise ArchiveError(f"слишком много файлов в архиве: {len(infos)}")
        for info in infos:
            rel = safe_member_path(zip_member_name(info))
            if rel is None:
                logger.warning("пропущен член архива с недопустимым именем: %r", info.filename)
                continue
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise ArchiveError("суммарный размер распакованного превышает ограничение")
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            out.append(target)
    return out


def _unpack_with_bsdtar(path: Path, dest: Path) -> list[Path]:
    bsdtar = shutil.which("bsdtar")
    if bsdtar is None:
        raise ArchiveError("для RAR/7z нужна утилита bsdtar (libarchive)")
    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        # bsdtar по умолчанию не пишет вне каталога: отбрасывает «..» и абсолютные пути
        result = subprocess.run(
            [bsdtar, "-x", "-f", str(path), "-C", tmp, "--no-same-owner", "--no-same-permissions"],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            raise ArchiveError(f"bsdtar: {result.stderr.strip()[:300]}")
        files = [p for p in Path(tmp).rglob("*") if p.is_file() and not p.is_symlink()]
        if len(files) > MAX_MEMBERS:
            raise ArchiveError(f"слишком много файлов в архиве: {len(files)}")
        if sum(p.stat().st_size for p in files) > MAX_TOTAL_BYTES:
            raise ArchiveError("суммарный размер распакованного превышает ограничение")
        out: list[Path] = []
        for src in files:
            rel = safe_member_path(src.relative_to(tmp).as_posix())
            if rel is None:
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(src, target)
            out.append(target)
    return out


def unpack(path: Path, dest: Path, *, kind: str | None = None, depth: int = 0) -> list[Member]:
    """Распаковать архив в ``dest`` (каталог очищается), вложенные архивы — рекурсивно.

    Возвращает файлы-члены, не являющиеся архивами.
    """
    kind = kind or detect_type(path)
    if kind not in ARCHIVE_TYPES:
        raise ArchiveError(f"не архив: {path}")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    files = _unpack_zip(path, dest) if kind == "zip" else _unpack_with_bsdtar(path, dest)

    members: list[Member] = []
    for file in sorted(files):
        name = file.relative_to(dest).as_posix()
        inner = detect_type(file)
        if inner in ARCHIVE_TYPES and depth + 1 < MAX_DEPTH:
            nested_dest = file.with_name(file.name + ".unpacked")
            try:
                nested = unpack(file, nested_dest, kind=inner, depth=depth + 1)
            except (ArchiveError, zipfile.BadZipFile, OSError) as exc:
                logger.warning("вложенный архив %s не распакован: %s", name, exc)
                members.append(Member(file, name))
                continue
            members.extend(Member(m.path, f"{name}!{m.member}") for m in nested)
        else:
            members.append(Member(file, name))
    return members
