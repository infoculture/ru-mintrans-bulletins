import zipfile
from pathlib import Path

import pytest

from mintrans_stats import filetypes
from mintrans_stats.filetypes import detect_type

FILES = Path(__file__).parent / "fixtures" / "files"


def write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def make_zip(tmp_path: Path, name: str, entries: dict[str, bytes]) -> Path:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        for entry, data in entries.items():
            zf.writestr(entry, data)
    return path


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"%PDF-1.7\n...", "pdf"),
        (b"Rar!\x1a\x07\x01\x00...", "rar"),
        (b"7z\xbc\xaf\x27\x1c...", "7z"),
        (b"{\\rtf1\\ansi ...", "rtf"),
        (b"\xef\xbb\xbf  <!DOCTYPE html><html>...", "html"),
        (b"<html><body>404</body></html>", "html"),
        ("Период;Наименование\n".encode("cp1251"), ""),
    ],
)
def test_signatures(tmp_path, data, expected):
    assert detect_type(write(tmp_path, "f.bin", data)) == expected


def test_zip_containers(tmp_path):
    assert (
        detect_type(make_zip(tmp_path, "a", {"[Content_Types].xml": b"", "word/document.xml": b""}))
        == "docx"
    )
    assert (
        detect_type(make_zip(tmp_path, "b", {"[Content_Types].xml": b"", "xl/workbook.xml": b""}))
        == "xlsx"
    )
    assert detect_type(make_zip(tmp_path, "c", {"ppt/presentation.xml": b""})) == "pptx"
    odt = {"mimetype": b"application/vnd.oasis.opendocument.text", "content.xml": b""}
    assert detect_type(make_zip(tmp_path, "d", odt)) == "odt"
    assert detect_type(make_zip(tmp_path, "e", {"report/1.doc": b"x"})) == "zip"
    assert detect_type(write(tmp_path, "broken", b"PK\x03\x04 not really a zip")) == "zip"


def test_real_ole_document():
    assert detect_type(FILES / "bulletin_table.doc") == "doc"


def test_ole_checks_only_root_streams(monkeypatch, tmp_path):
    """Бюллетень в .doc со встроенной диаграммой Excel: Workbook лежит во вложенном хранилище."""

    class FakeOle:
        def __init__(self, path):
            self.root = {"WordDocument", "ObjectPool"}  # ObjectPool/_123/Workbook — не в корне

        def exists(self, name):
            return name in self.root

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(filetypes.olefile, "OleFileIO", FakeOle)
    path = write(tmp_path, "bulletin", filetypes.OLE_SIGNATURE + b"\0" * 504)
    assert detect_type(path) == "doc"


def test_unknown_ole_and_unknown_bytes(tmp_path):
    assert detect_type(write(tmp_path, "ole", filetypes.OLE_SIGNATURE + b"\0" * 504)) == "ole"
    assert detect_type(write(tmp_path, "x", b"\x00\x01\x02")) == ""
