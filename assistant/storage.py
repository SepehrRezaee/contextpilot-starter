"""Durable local state: one SQLite connection, short explicit transactions, no cache."""
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .schemas import Context

PREFERENCE_VALUES = {"length": {"concise", "detailed"}, "format": {"bullets", "paragraphs"},
                     "introductions": {"omit", "include"}}


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    """SQLite store shared by long-term preferences and the action ledger.

    The connection is opened once. Reopening it per operation cost 1-9 ms on Windows
    (every close of the last WAL connection checkpoints the database); a kept
    connection reads in microseconds and commits in well under a millisecond.

    ``synchronous=FULL`` is deliberate: an approval and its SENDING intent must be on
    disk before the customer-facing POST, otherwise a power loss could forget that a
    comment was (possibly) published and invite a duplicate.

    Never ``await`` inside a ``connection()`` block: the single connection carries one
    transaction at a time.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, timeout=5, isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        for pragma in ("journal_mode=WAL", "synchronous=FULL", "foreign_keys=ON", "busy_timeout=5000"):
            self._db.execute("PRAGMA " + pragma)
        self.script("""
            CREATE TABLE IF NOT EXISTS preferences (
                tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, key TEXT NOT NULL,
                value TEXT NOT NULL, source_session TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'explicit_user_instruction',
                PRIMARY KEY (tenant_id, user_id, key));
        """)

    def script(self, sql: str) -> None:
        """Idempotent schema statements; run outside ``connection()`` (it would commit it)."""
        with self._lock:
            self._db.executescript(sql)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __del__(self):
        try:
            self._db.close()
        except Exception:
            pass

    @contextmanager
    def connection(self, *, immediate=False):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield self._db
            except BaseException:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise
            else:
                self._db.execute("COMMIT")

    # --- long-term memory: explicit writing preferences, scoped by tenant and user ---

    def preferences(self, context: Context) -> list[dict]:
        with self.connection() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT key,value,source_session,created_at,updated_at,source FROM preferences WHERE tenant_id=? AND user_id=? ORDER BY key",
                (context.tenant_id, context.user_id))]

    def upsert_preferences(self, context: Context, updates: dict[str, str]):
        if any(key not in PREFERENCE_VALUES or value not in PREFERENCE_VALUES[key] for key, value in updates.items()):
            raise ValueError("unsupported preference")
        now = timestamp()
        with self.connection(immediate=True) as connection:
            for key, value in updates.items():
                connection.execute("""INSERT INTO preferences
                    (tenant_id,user_id,key,value,source_session,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?) ON CONFLICT(tenant_id,user_id,key) DO UPDATE SET
                    value=excluded.value,source_session=excluded.source_session,updated_at=excluded.updated_at""",
                    (context.tenant_id, context.user_id, key, value, context.session_id, now, now))

    def forget(self, context: Context, keys: tuple[str, ...] | None = None) -> int:
        """Delete this user's preferences in this tenant; ``keys`` narrows it to some dimensions."""
        if keys is not None and any(key not in PREFERENCE_VALUES for key in keys):
            raise ValueError("unsupported preference")
        sql, params = "DELETE FROM preferences WHERE tenant_id=? AND user_id=?", [context.tenant_id, context.user_id]
        if keys is not None:
            sql += f" AND key IN ({','.join('?' for _ in keys)})"
            params.extend(keys)
        with self.connection(immediate=True) as connection:
            return connection.execute(sql, params).rowcount
