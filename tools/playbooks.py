"""Pure builders and parsers for observation-only diagnostic playbooks."""
from __future__ import annotations

import re
import shlex
from typing import Iterable

_SERVICE_NAME = re.compile(r"^[A-Za-z0-9_.@:-]{1,200}$")


def service_diagnostic_command(service: str, journal_lines: int) -> str:
    """Build a bounded, read-only systemd observation command."""
    if not _SERVICE_NAME.fullmatch(service):
        raise ValueError("service must be a systemd unit name, not a shell expression")
    lines = max(1, min(int(journal_lines), 200))
    unit = shlex.quote(service)
    return "\n".join([
        "echo ===ACTIVE===",
        f"systemctl is-active {unit} 2>&1 || true",
        "echo ===STATE===",
        (
            f"systemctl show {unit} "
            "--property=LoadState,ActiveState,SubState,Result,ExecMainStatus,"
            "NRestarts,ActiveEnterTimestampMonotonic 2>&1 || true"
        ),
        "echo ===JOURNAL===",
        f"journalctl --no-pager -u {unit} -n {lines} 2>&1 || true",
    ])


def host_audit_command() -> str:
    """Build a portable, read-only host audit command."""
    return "\n".join([
        "echo ===UPTIME===",
        "uptime 2>&1 || true",
        "echo ===DISK===",
        "df -P -x tmpfs -x devtmpfs 2>&1 || true",
        "echo ===MEMORY===",
        "free -m 2>&1 || true",
        "echo ===FAILED_UNITS===",
        "systemctl --failed --no-legend --plain 2>&1 || true",
        "echo ===REBOOT_REQUIRED===",
        "test -f /var/run/reboot-required && cat /var/run/reboot-required || echo no",
    ])


def backup_chain_command(datastore: str) -> str:
    """Build a bounded filesystem-level backup freshness observation."""
    if not datastore.startswith("/"):
        raise ValueError("datastore must be an absolute path")
    quoted = shlex.quote(datastore)
    return "\n".join([
        "echo ===DATASTORE===",
        f"test -d {quoted} && echo present || echo missing",
        "echo ===SPACE===",
        f"df -P {quoted} 2>&1 || true",
        "echo ===LATEST===",
        (
            f"find {quoted} -mindepth 1 -maxdepth 6 -type d "
            r"-printf '%T@ %p\n' 2>/dev/null | sort -nr | head -50"
        ),
    ])


def parse_sections(output: str, names: Iterable[str]) -> dict[str, str]:
    """Parse command output separated by exact ===NAME=== markers."""
    expected = set(names)
    sections = {name: [] for name in expected}
    current: str | None = None
    for line in output.splitlines():
        marker = line.removeprefix("===").removesuffix("===")
        if line == f"==={marker}===" and marker in expected:
            current = marker
        elif current is not None:
            sections[current].append(line)
    return {name.lower(): "\n".join(lines).strip() for name, lines in sections.items()}
