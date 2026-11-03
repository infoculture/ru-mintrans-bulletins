from pathlib import Path

from mintrans_stats.config import Settings


def test_from_env(monkeypatch):
    monkeypatch.setenv("MINTRANS_PROXY", "socks5h://127.0.0.1:1080")
    monkeypatch.setenv("MINTRANS_DELAY", "2.5")
    monkeypatch.setenv("MINTRANS_DATA_DIR", "/tmp/mt")
    monkeypatch.setenv("MINTRANS_CACHE_HTML", "0")
    s = Settings.from_env()
    assert s.proxy == "socks5h://127.0.0.1:1080"
    assert s.delay_s == 2.5
    assert s.data_dir == Path("/tmp/mt")
    assert s.cache_html is False
    assert s.files_dir == Path("/tmp/mt/files")


def test_defaults_without_env(monkeypatch):
    for name in ("MINTRANS_PROXY", "MINTRANS_DELAY", "MINTRANS_DATA_DIR", "MINTRANS_CACHE_HTML"):
        monkeypatch.delenv(name, raising=False)
    s = Settings.from_env()
    assert s.proxy is None
    assert s.delay_s == 1.0
    assert s.cache_html is True


def test_with_overrides_ignores_none():
    s = Settings(delay_s=3.0).with_overrides(delay_s=None, proxy="socks5h://x:1")
    assert s.delay_s == 3.0
    assert s.proxy == "socks5h://x:1"
