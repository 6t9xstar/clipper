from __future__ import annotations

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from clipper.core.config import Settings
from clipper.db import migrate
from clipper.db.models import Base
from clipper.db.session import Database


def test_upgrade_downgrade_roundtrip(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True)
    migrate.upgrade(settings.db_url)
    db = Database(settings.db_url)
    assert {"projects", "jobs", "assets"} <= set(inspect(db.engine).get_table_names())
    command.downgrade(migrate.alembic_config(settings.db_url), "base")
    assert "jobs" not in inspect(db.engine).get_table_names()
    migrate.upgrade(settings.db_url)
    head = ScriptDirectory.from_config(migrate.alembic_config(settings.db_url)).get_current_head()
    assert head and migrate.current_revision(db) == head
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
