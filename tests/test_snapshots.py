from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.snapshots import SnapshotStore


class SnapshotStoreTests(unittest.TestCase):
    def test_snapshot_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory) / "state.db")
            change_id = store.create(
                profile="operator",
                tool="ct_write_file",
                target="prox:104:/etc/example",
                state={"exists": False},
            )
            snapshot = store.get(change_id)
            self.assertEqual("ready", snapshot["status"])
            self.assertEqual({"exists": False}, snapshot["state"])

            store.set_status(change_id, "rolled_back")
            snapshot = store.get(change_id)
            self.assertEqual("rolled_back", snapshot["status"])
            self.assertIsNotNone(snapshot["completed_at"])

    def test_unknown_snapshot_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory) / "state.db")
            self.assertIsNone(store.get("missing"))


if __name__ == "__main__":
    unittest.main()
