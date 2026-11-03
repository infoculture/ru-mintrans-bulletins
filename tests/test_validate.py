import hashlib
import json

import pytest

from mintrans_stats.validate import ValidationError, validate_jsonl, validate_row

VALID = {
    "document_title": "Doc",
    "document_url": "https://mintrans.gov.ru/documents/7/1",
    "file_title": "File",
    "file_url": "https://mintrans.gov.ru/file/1",
    "file_type": "pdf",
    "file_size": "100 kb",
}


def test_valid_row():
    validate_row(VALID)


def test_file_size_is_optional():
    validate_row({**VALID, "file_size": ""})


@pytest.mark.parametrize("field", ["file_type", "document_title", "file_url"])
def test_empty_required_field(field):
    with pytest.raises(ValidationError):
        validate_row({**VALID, field: " "})


def test_relative_url_rejected():
    with pytest.raises(ValidationError):
        validate_row({**VALID, "file_url": "/file/1"})


def test_download_fields_required_on_demand():
    with pytest.raises(ValidationError):
        validate_row(VALID, require_download_fields=True)


def test_validate_jsonl_checks_files_relative_to_data_dir(tmp_path):
    data_dir = tmp_path / "data"
    (data_dir / "files").mkdir(parents=True)
    content = b"%PDF"
    (data_dir / "files" / "a.pdf").write_bytes(content)
    good = {
        **VALID,
        "local_path": "files/a.pdf",
        "sha256": hashlib.sha256(content).hexdigest(),
        "saved_file_size_bytes": len(content),
        "downloaded_at": "2026-11-03T00:00:00Z",
    }
    bad = {**good, "local_path": "files/missing.pdf"}
    corrupt = {**good, "sha256": "0" * 64}
    path = tmp_path / "rows.jsonl"
    path.write_text(
        "\n".join(json.dumps(r) for r in (good, bad, corrupt)) + "\n\n{oops\n", encoding="utf-8"
    )
    count, errors = validate_jsonl(
        path, data_dir=data_dir, require_download_fields=True, check_files=True
    )
    assert count == 4
    assert len(errors) == 3
    assert "нет файла" in errors[0]
    assert "sha256" in errors[1]
    assert errors[2].startswith("строка 5:")
