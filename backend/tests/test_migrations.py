from __future__ import annotations

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from clipper.core.config import Settings
from clipper.db import migrate
from clipper.db.models import Base
from clipper.db.session import Database


def test_upgrade_downgrade_roundtrip(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True)
    migrate.upgrade(settings.db_url)
    db = Database(settings.db_url)
    assert {"projects", "jobs"} <= set(inspect(db.engine).get_table_names())
    command.downgrade(migrate.alembic_config(settings.db_url), "base")
    assert "jobs" not in inspect(db.engine).get_table_names()
    migrate.upgrade(settings.db_url)
    assert migrate.current_revision(db) == "0001"
    db.dispose()


def test_models_match_migrations(settings: Settings) -> None:
    """Fails if someone changes a model without writing a migration."""
    settings.data_dir.mkdir(parents=True)
    migrate.upgrade(settings.db_url)
    db = Database(settings.db_url)
    with db.engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    db.dispose()
    assert diff == []
