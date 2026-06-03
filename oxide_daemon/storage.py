"""SQLite persistence for recorded command executions."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class StorageError(RuntimeError):
    """Raised when Oxide cannot persist a command record."""


class SQLiteCommandStore:
    """Small SQLite repository for command execution records."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_schema(self) -> None:
        try:
            with self._connect() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS command_runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        command_hash TEXT NOT NULL,
                        command TEXT NOT NULL,
                        input_snapshot TEXT NOT NULL,
                        output_snapshot TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        deps TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_command_runs_command_hash
                        ON command_runs(command_hash);

                    CREATE INDEX IF NOT EXISTS idx_command_runs_timestamp
                        ON command_runs(timestamp);
                    """
                )
        except sqlite3.Error as exc:
            raise StorageError(f"failed to initialize SQLite store: {exc}") from exc

    def insert_command_run(
        self,
        *,
        command_hash: str,
        command: str,
        input_snapshot: dict[str, Any],
        output_snapshot: dict[str, Any],
        timestamp: str,
        deps: dict[str, Any],
    ) -> int:
        """Persist a command run and return its row id."""

        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO command_runs (
                        command_hash,
                        command,
                        input_snapshot,
                        output_snapshot,
                        timestamp,
                        deps
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        command_hash,
                        command,
                        _json_dumps(input_snapshot),
                        _json_dumps(output_snapshot),
                        timestamp,
                        _json_dumps(deps),
                    ),
                )
                return int(cursor.lastrowid)
        except (TypeError, ValueError) as exc:
            raise StorageError(f"failed to serialize command record: {exc}") from exc
        except sqlite3.Error as exc:
            raise StorageError(f"failed to write command record: {exc}") from exc


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
