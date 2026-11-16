from __future__ import annotations

from pathlib import Path

import pytest

from clipper.cli import doctor
from clipper.core.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def test_every_setting_is_documented() -> None:
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    docs = (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    for name in Settings.model_fields:
        var = f"CLIPPER_{name.upper()}"
        assert f"{var}=" in env, f"{var} missing from .env.example"
        assert var in docs, f"{var} missing from docs/configuration.md"


def test_env_example_parses(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            k, v = line.split("=", 1)
            monkeypatch.setenv(k, v)
    monkeypatch.setenv("CLIPPER_DATA_DIR", str(tmp_path))
    s = Settings()
    assert s.job_retry_backoff_seconds == [30, 120, 600]
    assert s.db_url.startswith("sqlite:///")  # empty DATABASE_URL falls back to SQLite


def test_doctor_reports_structure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from clipper.core import config

    monkeypatch.setenv("CLIPPER_DATA_DIR", str(tmp_path))
    config.get_settings.cache_clear()
    try:
        rep = doctor()
    finally:
        config.get_settings.cache_clear()
    names = {c["check"] for c in rep["checks"]}
    assert {"python", "ffmpeg", "ffprobe", "data_dir", "database", "gpu"} <= names
    if "ffmpeg_features" in names:
        feat = next(c for c in rep["checks"] if c["check"] == "ffmpeg_features")
        assert feat["status"] in {"ok", "fail"}
    assert rep["status"] in {"ok", "warn", "fail"}
