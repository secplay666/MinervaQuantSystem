"""Business database (ADR-010): SQLite in WAL mode now, PostgreSQL later.

Only portable SQLAlchemy features are used.  Alembic owns the schema
(``migrations/``); :func:`upgrade` brings a database file to the head
revision and is called by every entry point before it opens a session.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import DateTime, Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

DEFAULT_DB = Path("data") / "app" / "app.sqlite"
MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Aware datetimes in, aware UTC datetimes out; stored as naive UTC so
    SQLite and PostgreSQL behave the same."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):  # type: ignore[no-untyped-def]
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime; pass an aware datetime (UTC)")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):  # type: ignore[no-untyped-def]
        return None if value is None else value.replace(tzinfo=timezone.utc)


class Base(DeclarativeBase):
    pass


def database_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def make_engine(path: Path) -> Engine:
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url(path))

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        for pragma in ("journal_mode=WAL", "foreign_keys=ON", "busy_timeout=10000", "synchronous=NORMAL"):
            cursor.execute(f"PRAGMA {pragma}")
        cursor.close()

    return engine


def upgrade(path: Path) -> None:
    """Create or migrate the database at ``path`` to the latest schema."""
    from alembic import command
    from alembic.config import Config

    path.parent.mkdir(parents=True, exist_ok=True)
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.set_main_option("sqlalchemy.url", database_url(path))
    command.upgrade(config, "head")


def open_database(path: Path) -> tuple[Engine, sessionmaker[Session]]:
    """Migrate, then return an engine and a session factory."""
    upgrade(path)
    engine = make_engine(path)
    return engine, sessionmaker(engine, expire_on_commit=False)
