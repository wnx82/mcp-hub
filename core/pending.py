"""SQLite persistence for short-lived pending multi-request operations."""
from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class PendingOperationStore:
    """Persist opaque confirmation handles independently from transport state."""

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
                CREATE TABLE IF NOT EXISTS pending_operation (
                    token TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
            connection.execute(
                "DELETE FROM pending_operation WHERE created_at < ?",
                (cutoff.isoformat(),),
            )
            connection.execute(
                "DELETE FROM pending_operation WHERE expires_at <= ?",
                (datetime.now(timezone.utc).isoformat(),),
            )

    def create(
        self,
        *,
        kind: str,
        profile: str,
        payload: dict[str, Any],
        ttl_seconds: int,
    ) -> tuple[str, int]:
        ttl = max(1, int(ttl_seconds))
        token = secrets.token_urlsafe(24)
        created_at = datetime.now(timezone.utc)
        expires_at = created_at + timedelta(seconds=ttl)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO pending_operation "
                "(token, created_at, expires_at, kind, profile, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    token,
                    created_at.isoformat(),
                    expires_at.isoformat(),
                    kind,
                    profile,
                    json.dumps(payload, separators=(",", ":"), default=str),
                ),
            )
        return token, ttl

    def get(self, token: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT token, created_at, expires_at, kind, profile, payload_json "
                "FROM pending_operation WHERE token = ?",
                (token,),
            ).fetchone()
        if row is None:
            return None
        return {
            "token": row[0],
            "created_at": row[1],
            "expires_at": row[2],
            "kind": row[3],
            "profile": row[4],
            "payload": json.loads(row[5]),
        }

    def pop(self, token: str) -> dict[str, Any] | None:
        pending = self.get(token)
        if pending is None:
            return None
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM pending_operation WHERE token = ?",
                (token,),
            )
        return pending

    def purge_expired(self) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM pending_operation WHERE expires_at <= ?",
                (datetime.now(timezone.utc).isoformat(),),
            )
        return int(cursor.rowcount or 0)
