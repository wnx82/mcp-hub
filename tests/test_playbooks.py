from __future__ import annotations

import unittest

from tools.playbooks import (
    backup_chain_command,
    host_audit_command,
    parse_sections,
    service_diagnostic_command,
)


class PlaybookHelperTests(unittest.TestCase):
    def test_service_command_is_bounded_and_rejects_shell_input(self) -> None:
        command = service_diagnostic_command("example@worker.service", 500)
        self.assertIn("-n 200", command)
        with self.assertRaises(ValueError):
            service_diagnostic_command("example; reboot", 10)

    def test_backup_command_requires_absolute_path_and_quotes_it(self) -> None:
        command = backup_chain_command("/mnt/backup store")
        self.assertIn("'/mnt/backup store'", command)
        with self.assertRaises(ValueError):
            backup_chain_command("relative/path")

    def test_host_audit_has_no_mutating_systemctl_operation(self) -> None:
        command = host_audit_command()
        self.assertIn("systemctl --failed", command)
        self.assertNotIn("systemctl restart", command)

    def test_section_parser_returns_structured_observations(self) -> None:
        parsed = parse_sections(
            "===ACTIVE===\nfailed\n===STATE===\nResult=exit-code\n",
            ("ACTIVE", "STATE"),
        )
        self.assertEqual("failed", parsed["active"])
        self.assertEqual("Result=exit-code", parsed["state"])


if __name__ == "__main__":
    unittest.main()
