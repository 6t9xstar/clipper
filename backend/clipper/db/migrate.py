"""Programmatic Alembic: `clipper migrate` and app startup both use this (no alembic.ini needed by users)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from clipper.db.session import Database

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def upgrade(url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(url), revision)


def current_revision(db: Database) -> str | None:
    if not inspect(db.engine).has_table("alembic_version"):
        return None
    with db.engine.connect() as c:
        row = c.exec_driver_sql("SELECT version_num FROM alembic_version").first()
    return row[0] if row else None
