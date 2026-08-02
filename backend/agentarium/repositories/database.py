from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

SQLITE_BUSY_TIMEOUT_MS = 5000


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, url: str) -> None:
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

        Base.metadata.create_all(self.engine)
        if self.is_sqlite:
            self._ensure_work_item_columns()

    # Columns added to `work_items` after the table already existed in the
    # field. There is no Alembic in this backend and `create_all()` only
    # creates missing tables, not missing columns on existing ones, so any
    # such addition needs an explicit, idempotent ALTER TABLE here.
    _WORK_ITEM_COLUMN_MIGRATIONS: tuple[tuple[str, str], ...] = (
        ("version", "INTEGER NOT NULL DEFAULT 1"),
        ("owned_paths_json", "JSON NOT NULL DEFAULT '[]'"),
        ("shared_component", "VARCHAR(120)"),
        ("output_strategy", "VARCHAR(20) NOT NULL DEFAULT 'exclusive'"),
        ("split_depth", "INTEGER NOT NULL DEFAULT 0"),
    )

    # A plain `DEFAULT 0` would tell every task in an existing database that it
    # descends from no split, including the subtasks and consolidations a
    # previous version already created — which would let those split a second
    # time, the exact recursion `split_depth` exists to stop. These statements
    # run once, in the same transaction that adds the column, to reconstruct
    # the depth of lineages that predate it.
    #
    # They match on the literal title prefixes and the `split-<id>` sharing
    # group that `Orchestrator._attempt_split` wrote, because on a legacy row
    # that is the only surviving evidence. This is a one-time repair of rows
    # produced by a known code version, not runtime type inference: no control
    # flow reads a title (see ADR 0021, revisión 2026-08-02).
    _WORK_ITEM_COLUMN_BACKFILLS: dict[str, tuple[str, ...]] = {
        "split_depth": (
            """
            UPDATE work_items SET split_depth = 1
             WHERE split_depth = 0
               AND (title LIKE '[subtarea] %'
                    OR title LIKE 'Consolidar subtareas: %'
                    OR shared_component LIKE 'split-%')
            """,
        ),
    }

    def _ensure_work_item_columns(self) -> None:
        with self.engine.connect() as connection:
            columns = {
                row[1] for row in connection.execute(text("PRAGMA table_info(work_items)"))
            }
            for name, definition in self._WORK_ITEM_COLUMN_MIGRATIONS:
                if name in columns:
                    continue
                connection.execute(
                    text(f"ALTER TABLE work_items ADD COLUMN {name} {definition}")
                )
                for statement in self._WORK_ITEM_COLUMN_BACKFILLS.get(name, ()):
                    connection.execute(text(statement))
            connection.commit()

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
