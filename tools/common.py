"""Shared helpers for tool responses and configuration validation."""

from __future__ import annotations

from typing import Any


def tool_error(message: str, *, hint: str | None = None, **details: Any) -> dict[str, Any]:
    """Build a compact error payload while omitting empty optional fields."""
    payload: dict[str, Any] = {"error": message}
    if hint:
        payload["hint"] = hint
    for key, value in details.items():
        if value is not None:
            payload[key] = value
    return payload


def missing_config(setting: str, *, hint: str | None = None, **details: Any) -> dict[str, Any]:
    """Return a standard missing-configuration payload for one setting."""
    return tool_error(
        f"{setting} is not configured",
        hint=hint or f"set {setting} in .env or the service environment file",
        **details,
    )
