from __future__ import annotations

import logging
from pathlib import Path

import pytest

from reefhue import bootstrap
from reefhue.core import logging_setup, paths


def test_env_overrides_are_used(tmp_path):
    assert paths.data_dir() == tmp_path / "appdata"
    assert paths.cache_dir() == tmp_path / "appcache"
    assert paths.log_dir() == tmp_path / "appdata" / "logs"
    assert paths.log_dir().is_dir()


def test_app_root_layout():
    root = paths.app_root()
    assert (root / "reefhue" / "__init__.py").is_file()
    assert paths.ffmpeg_dir() == root / "ffmpeg"
    assert (paths.resources_dir() / "themes" / "base.qss").is_file()


def test_clear_cache_removes_contents_only(tmp_path):
    cache = paths.cache_dir()
    (cache / "thumbs").mkdir()
    (cache / "thumbs" / "a.jpg").write_bytes(b"x" * 10)
    (cache / "b.bin").write_bytes(b"y" * 5)
    assert paths.cache_size_bytes(cache) == 15
    assert paths.clear_cache(cache) == 2
    assert cache.is_dir()
    assert list(cache.iterdir()) == []
    assert paths.cache_size_bytes(cache) == 0


def test_clear_cache_refuses_unsafe_targets():
    with pytest.raises(ValueError):
        paths.clear_cache(Path.home())
    with pytest.raises(ValueError):
        paths.clear_cache(Path(Path.home().anchor))


def test_setup_logging_writes_rotating_file(tmp_path):
    log_file = logging_setup.setup_logging(tmp_path / "logs")
    logging.getLogger("reefhue.test").info("hello log")
    for handler in logging.getLogger().handlers:
        handler.flush()
    text = log_file.read_text(encoding="utf-8")
    assert "hello log" in text
    assert "ReefHue" in text
    # idempotent: no duplicate handlers
    logging_setup.setup_logging(tmp_path / "logs")
    ours = [h for h in logging.getLogger().handlers if getattr(h, "_reefhue", False)]
    assert len(ours) == 2


def test_bootstrap_stamp(tmp_path):
    req = tmp_path / "requirements.txt"
    venv = tmp_path / "venv"
    venv.mkdir()
    req.write_text("numpy==1\n")
    assert bootstrap.needs_install(req, venv)
    bootstrap.write_stamp(req, venv)
    assert not bootstrap.needs_install(req, venv)
    req.write_text("numpy==2\n")
    assert bootstrap.needs_install(req, venv)
