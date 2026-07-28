from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest import mock

YAML_AVAILABLE = importlib.util.find_spec("yaml") is not None
FIXTURES = Path(__file__).parent / "fixtures" / "config"


@unittest.skipUnless(YAML_AVAILABLE, "PyYAML is not installed")
class ConfigurationFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        global config
        import config

    def test_coherent_example_configuration_loads(self) -> None:
        with (
            mock.patch.object(config, "HOSTS_FILE", FIXTURES / "hosts.example.yaml"),
            mock.patch.object(config, "TOPOLOGY_FILE", FIXTURES / "topology.example.yaml"),
            mock.patch.object(config, "ENDPOINTS_FILE", FIXTURES / "endpoints.example.yaml"),
        ):
            hosts = config.load_hosts()
            topology = config.load_topology()
            endpoints, intermittent = config.load_endpoints()

        self.assertEqual({"hypervisor", "storage"}, set(hosts))
        self.assertEqual("services", topology["hypervisor"]["guests"]["ct/100"]["name"])
        self.assertEqual("dashboard", endpoints[0]["name"])
        self.assertEqual("backup", intermittent[0]["name"])
        self.assertTrue(topology["_do_not_touch"])


if __name__ == "__main__":
    unittest.main()
