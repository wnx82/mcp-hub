from __future__ import annotations

import unittest
from pathlib import Path

import tools.cloudflare
import tools.dsm
import tools.playbooks
import tools.ssh
from tools.registry import domain_for_tool, registered_domains


class DomainModuleTests(unittest.TestCase):
    def test_registry_assigns_extracted_domains(self) -> None:
        self.assertEqual("ssh", domain_for_tool("remote_exec"))
        self.assertEqual("cloudflare", domain_for_tool("cloudflare_dns_list"))
        self.assertEqual("dsm", domain_for_tool("dsm_health"))
        self.assertEqual("playbooks", domain_for_tool("audit_host"))
        self.assertEqual("core", domain_for_tool("not_yet_extracted"))
        self.assertGreaterEqual(len(registered_domains()), 4)

    def test_ssh_builder_preserves_argument_boundaries(self) -> None:
        host = {"hostname": "example.test", "user": "operator", "port": 2222}
        command = tools.ssh.wrap_remote_command(host, "printf '%s' \"$HOME\"", False)
        argv = tools.ssh.ssh_argv(
            host,
            command,
            ssh_key=Path("/tmp/test-key"),
            control_path="/tmp/control/%r@%h:%p",
        )
        self.assertEqual("operator@example.test", argv[-2])
        self.assertEqual(command, argv[-1])
        self.assertIn("2222", argv)

    def test_cloudflare_paths_and_config_extraction(self) -> None:
        path = tools.cloudflare.tunnel_config_path("account", "tunnel")
        self.assertEqual(
            "/accounts/account/cfd_tunnel/tunnel/configurations",
            path,
        )
        config = tools.cloudflare.extract_tunnel_config(
            {"data": {"result": {"config": {"ingress": []}}}}
        )
        self.assertEqual({"ingress": []}, config)

    def test_dsm_parameter_encoding_is_protocol_specific(self) -> None:
        encoded = tools.dsm.encode_params(
            {"enabled": True, "items": [1, 2], "type": "url"},
            json_style=True,
        )
        self.assertEqual("true", encoded["enabled"])
        self.assertEqual("[1, 2]", encoded["items"])
        self.assertEqual('"url"', encoded["type"])


if __name__ == "__main__":
    unittest.main()
