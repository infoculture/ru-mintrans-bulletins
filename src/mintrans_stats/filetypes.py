"""Тип файла по содержимому.

Тип на сайте (CSS-класс вложения) и ``Content-Type`` бывают неточны, поэтому расширение
сохраняемого файла определяется по самому файлу:

* PDF, RAR, 7z, RTF — по сигнатуре;
* ZIP — по оглавлению: ``word/`` → docx, ``xl/`` → xlsx, ``ppt/`` → pptx, OpenDocument —
  по ``mimetype``;
* OLE (Word/Excel 97–2003) — по потокам в корне контейнера: ``WordDocument`` → doc,
  ``Workbook``/``Book`` → xls, ``PowerPoint Document`` → ppt. Встроенные объекты (например,
  диаграммы Excel внутри бюллетеня в .doc) лежат во вложенных хранилищах и не мешают;
* HTML — страница вместо файла (ошибка сервера).
"""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path

import olefile

OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06")
SIGNATURES = (
    (b"%PDF", "pdf"),
    (b"Rar!\x1a\x07", "rar"),
    (b"7z\xbc\xaf\x27\x1c", "7z"),
    (b"{\\rtf", "rtf"),
)
ODF_MIMETYPES = {
    b"application/vnd.oasis.opendocument.text": "odt",
    b"application/vnd.oasis.opendocument.spreadsheet": "ods",
    b"application/vnd.oasis.opendocument.presentation": "odp",
}
OLE_ROOT_STREAMS = (
    ("WordDocument", "doc"),
    ("Workbook", "xls"),
    ("Book", "xls"),
    ("PowerPoint Document", "ppt"),
)


def _zip_kind(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            if "mimetype" in names:
                kind = ODF_MIMETYPES.get(zf.read("mimetype").strip())
                if kind:
                    return kind
    except (zipfile.BadZipFile, OSError):
        return "zip"
    for prefix, kind in (("word/", "docx"), ("xl/", "xlsx"), ("ppt/", "pptx")):
        if any(name.startswith(prefix) for name in names):
            return kind
    return "zip"


def _ole_kind(path: Path) -> str:
    try:
        with olefile.OleFileIO(path) as ole:
            for stream, kind in OLE_ROOT_STREAMS:
                if ole.exists(stream):
                    return kind
    except (OSError, ValueError, IndexError, struct.error):
        pass  # повреждённый или усечённый контейнер
    return "ole"


def _looks_like_html(head: bytes) -> bool:
    text = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    return text.startswith((b"<!doctype html", b"<html"))


def detect_type(path: Path) -> str:
    """Тип файла по содержимому или пустая строка, если не распознан.

    Возвращает расширение (``pdf``, ``doc``, ``xlsx``…), ``html`` для веб-страницы и
    ``ole`` для OLE-контейнера неизвестного вида.
    """
    with path.open("rb") as fh:
        head = fh.read(512)
    for signature, kind in SIGNATURES:
        if head.startswith(signature):
            return kind
    if head.startswith(ZIP_SIGNATURES):
        return _zip_kind(path)
    if head.startswith(OLE_SIGNATURE):
        return _ole_kind(path)
    if _looks_like_html(head):
        return "html"
    return ""
