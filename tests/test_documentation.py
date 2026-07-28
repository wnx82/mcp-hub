from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DocumentationTests(unittest.TestCase):
    def test_troubleshooting_cast_is_valid_asciinema_v2(self) -> None:
        lines = (ROOT / "docs" / "troubleshooting.cast").read_text(
            encoding="utf-8"
        ).splitlines()
        header = json.loads(lines[0])
        self.assertEqual(2, header["version"])
        self.assertGreater(len(lines), 10)
        events = [json.loads(line) for line in lines[1:]]
        self.assertTrue(all(len(event) == 3 and event[1] == "o" for event in events))
        transcript = "".join(event[2] for event in events)
        self.assertIn("diagnose_service", transcript)
        self.assertIn("plan_mutation", transcript)
        self.assertIn("confirm_mutation", transcript)


if __name__ == "__main__":
    unittest.main()
