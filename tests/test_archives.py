import shutil
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

import pytest

from mintrans_stats.extract.archives import (
    ArchiveError,
    safe_member_path,
    unpack,
    zip_member_name,
)


def zip_with_cp866_name(path: Path, name: str, data: bytes) -> None:
    """ZIP с именем в CP866 без флага UTF-8 — как у архивов из русской Windows."""
    raw = name.encode("cp866")
    placeholder = "x" * len(raw)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(placeholder, data)
    path.write_bytes(path.read_bytes().replace(placeholder.encode(), raw))


def test_safe_member_path():
    assert safe_member_path("a/b.doc") == PurePosixPath("a/b.doc")
    assert safe_member_path("\\\\dir\\file.xls") == PurePosixPath("dir/file.xls")
    assert safe_member_path("/etc/passwd") == PurePosixPath("etc/passwd")
    assert safe_member_path("../evil") is None
    assert safe_member_path("a/../../evil") is None
    assert safe_member_path("") is None


def test_zip_member_name_cp866(tmp_path):
    archive = tmp_path / "a.zip"
    zip_with_cp866_name(archive, "Отчет за II квартал/24 0.doc", b"{\\rtf1}")
    with zipfile.ZipFile(archive) as zf:
        assert zip_member_name(zf.infolist()[0]) == "Отчет за II квартал/24 0.doc"


def test_unpack_zip_with_cp866_and_nested_archive(tmp_path):
    inner = tmp_path / "inner.zip"
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("таблица.csv", "a;b\n1;2\n")
    outer = tmp_path / "outer.zip"
    raw = "Отчет/1.doc".encode("cp866")
    placeholder = "x" * len(raw)
    with zipfile.ZipFile(outer, "w") as zf:
        zf.writestr(placeholder, b"{\\rtf1}")
        zf.write(inner, "вложенный.zip")
    outer.write_bytes(outer.read_bytes().replace(placeholder.encode(), raw))

    members = unpack(outer, tmp_path / "out")
    names = sorted(m.member for m in members)
    assert names == ["Отчет/1.doc", "вложенный.zip!таблица.csv"]
    assert all(m.path.is_file() and (tmp_path / "out") in m.path.parents for m in members)


def test_unpack_zip_skips_path_traversal(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../escape.txt", "x")
        zf.writestr("ok.txt", "y")
    members = unpack(archive, tmp_path / "out")
    assert [m.member for m in members] == ["ok.txt"]
    assert not (tmp_path / "escape.txt").exists()


def test_unpack_refuses_non_archive(tmp_path):
    path = tmp_path / "a.pdf"
    path.write_bytes(b"%PDF-1.4")
    with pytest.raises(ArchiveError):
        unpack(path, tmp_path / "out")


@pytest.mark.skipif(shutil.which("bsdtar") is None, reason="нужен bsdtar")
def test_unpack_with_bsdtar_7z(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "отчёт.txt").write_text("данные", encoding="utf-8")
    archive = tmp_path / "a.7z"
    subprocess.run(
        ["bsdtar", "--format", "7zip", "-cf", str(archive), "-C", str(src), "отчёт.txt"],
        check=True,
    )
    members = unpack(archive, tmp_path / "out")
    assert [m.member for m in members] == ["отчёт.txt"]
    assert members[0].path.read_text(encoding="utf-8") == "данные"
