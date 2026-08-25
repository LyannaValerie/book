from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from booklib import v0
from booklib.core import atomic_write, grant_shared_group_access
from booklib.events import append_event
from booklib.index import reindex


class SharedPermissionsTests(unittest.TestCase):
    def assert_group_read_write(self, path: Path) -> None:
        mode = stat.S_IMODE(path.stat().st_mode)
        expected = stat.S_IRGRP | stat.S_IWGRP
        self.assertEqual(mode & expected, expected)

    def test_writers_grant_group_access_under_restrictive_umask(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            previous_umask = os.umask(0o077)
            try:
                atomic_path = root / "atomic" / "task.json"
                atomic_write(atomic_path, {"ok": True})

                legacy_path = root / "legacy" / "case.json"
                v0.atomic_write(legacy_path, {"ok": True})

                event_root = root / "event-store"
                append_event(event_root, "TASK_BEGIN", summary="permission test")
                event_path = event_root / "events" / "events.jsonl"

                index_root = root / "index-store"
                index_result = reindex(index_root)
                self.assertTrue(index_result["ok"])
                index_path = index_root / ".book" / "index.sqlite3"
            finally:
                os.umask(previous_umask)

            for path in (atomic_path, legacy_path, event_path, index_path):
                with self.subTest(path=path):
                    self.assert_group_read_write(path)

    def test_existing_shared_file_does_not_require_owner_only_fchmod(self) -> None:
        with tempfile.NamedTemporaryFile() as temporary:
            os.chmod(temporary.name, 0o660)
            with mock.patch("booklib.core.os.fchmod", side_effect=AssertionError("unexpected fchmod")):
                grant_shared_group_access(temporary.fileno())
            with mock.patch("booklib.v0.os.fchmod", side_effect=AssertionError("unexpected fchmod")):
                v0.grant_shared_group_access(temporary.fileno())


if __name__ == "__main__":
    unittest.main()
