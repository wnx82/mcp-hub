"""SSH command construction isolated from the MCP server runtime."""
from __future__ import annotations

import shlex
from pathlib import Path

from tools.registry import register_domain

register_domain(
    "ssh",
    {
        "remote_exec",
        "fleet_exec",
        "batch_exec",
        "ssh_reset_control",
        "system_info",
        "read_file",
        "service_ctl",
        "journal_query",
        "apt_status",
    },
)


def wrap_remote_command(host_info: dict, command: str, as_root: bool) -> str:
    """Wrap a command for the target login shell without local expansion."""
    shell = str(host_info.get("shell") or "bash").lower()
    if shell in {"powershell", "pwsh", "windows"}:
        return command
    interpreter = "sh" if shell in {"sh", "ash", "dash", "busybox"} else "bash"
    inner = interpreter + " -c " + shlex.quote(command)
    if as_root and host_info.get("user") != "root":
        return "sudo -n " + inner
    return inner


def ssh_argv(
    host_info: dict,
    remote_command: str,
    *,
    ssh_key: Path,
    control_path: str,
) -> list[str]:
    """Build the multiplexed, non-interactive OpenSSH argument vector."""
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ControlMaster=auto",
        "-o",
        f"ControlPath={control_path}",
        "-o",
        "ControlPersist=60m",
        "-p",
        str(host_info.get("port", 22)),
        "-i",
        str(ssh_key),
        f"{host_info['user']}@{host_info['hostname']}",
        remote_command,
    ]
