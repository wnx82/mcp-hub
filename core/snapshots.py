"""Bounded SQLite persistence for reversible MCP mutations."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class SnapshotStore:
    """Store rollback payloads without coupling persistence to integrations."""

    def __init__(self, path: Path, *, retention_days: int = 7) -> None:
        self.path = path
        self.retention_days = max(1, retention_days)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS change_snapshot (
                    change_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    target TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    completed_at TEXT
                )
                """
            )
            cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
            connection.execute(
                "DELETE FROM change_snapshot WHERE created_at < ?",
                (cutoff.isoformat(),),
            )

    def create(
        self,
        *,
        profile: str,
        tool: str,
        target: str,
        state: dict[str, Any],
    ) -> str:
        change_id = uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO change_snapshot "
                "(change_id, created_at, profile, tool, target, state_json, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'ready')",
                (
                    change_id,
                    datetime.now(timezone.utc).isoformat(),
                    profile,
                    tool,
                    target,
                    json.dumps(state, separators=(",", ":"), default=str),
                ),
            )
        return change_id

    def get(self, change_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT change_id, created_at, profile, tool, target, state_json, "
                "status, completed_at FROM change_snapshot WHERE change_id = ?",
                (change_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "change_id": row[0],
            "created_at": row[1],
            "profile": row[2],
            "tool": row[3],
            "target": row[4],
            "state": json.loads(row[5]),
            "status": row[6],
            "completed_at": row[7],
        }

    def set_status(self, change_id: str, status: str) -> None:
        completed_at = (
            datetime.now(timezone.utc).isoformat()
            if status in {"rolled_back", "mutation_failed", "rollback_failed"}
            else None
        )
        with self._connect() as connection:
            connection.execute(
                "UPDATE change_snapshot SET status = ?, completed_at = ? "
                "WHERE change_id = ?",
                (status, completed_at, change_id),
            )
