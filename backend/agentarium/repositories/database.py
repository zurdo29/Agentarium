from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

SQLITE_BUSY_TIMEOUT_MS = 5000


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        self.is_sqlite = url.startswith("sqlite")
        connect_args = {"check_same_thread": False} if self.is_sqlite else {}
        self.engine = create_engine(url, connect_args=connect_args, future=True)
        if self.is_sqlite:
            event.listen(self.engine, "connect", self._configure_sqlite_connection)
        self.session_factory = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            class_=Session,
        )

    @staticmethod
    def _configure_sqlite_connection(connection: object, _: object) -> None:
        cursor = connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        # WAL lets readers (e.g. a separate CLI process) and the writer
        # proceed without blocking each other; busy_timeout makes a writer
        # that does momentarily contend retry instead of failing outright.
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        cursor.close()

    def create_all(self) -> None:
        from . import tables  # noqa: F401

        if self.is_sqlite:
            # Versioned migration + pre-migration backup (P3.2) instead of
            # the old implicit-order ALTER TABLE sweep -- see
            # docs/decisions/0032-*.md. Imported lazily, like `tables` above:
            # backup.py imports Base/Database from this module at load time,
            # so a top-level import here would be circular.
            from .backup import upgrade

            upgrade(self)
        else:
            Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()


def engine_name(engine: Engine) -> str:
    return engine.dialect.name
