from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from cryoet_organizer.safe_delete import (
    DeletionCancelled,
    QuarantineTransaction,
    identifier_matches_name,
    matching_files,
    processed_items_rewrite,
)


class SafeDeleteTests(unittest.TestCase):
    def test_identifier_boundary_does_not_match_numbered_sibling(self) -> None:
        self.assertTrue(identifier_matches_name("prefix_TS_1_suffix.mrc", ["TS_1"]))
        self.assertFalse(identifier_matches_name("prefix_TS_10_suffix.mrc", ["TS_1"]))

    def test_matching_files_ignores_existing_quarantine(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wanted = root / "TS_1_reconstruction.mrc"
            sibling = root / "TS_10_reconstruction.mrc"
            trashed = root / ".cryopal_trash" / "old" / "TS_1_old.mrc"
            wanted.write_text("wanted")
            sibling.write_text("sibling")
            trashed.parent.mkdir(parents=True)
            trashed.write_text("old")

            self.assertEqual(matching_files(root, ["TS_1"]), [wanted])

    def test_processed_items_rewrite_uses_same_boundary_rule(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "processed_items.json"
            path.write_text(json.dumps([{"Path": "TS_1.mrc"}, {"Path": "TS_10.mrc"}]))

            rewritten = processed_items_rewrite(path, ["TS_1"])
            self.assertIsNotNone(rewritten)
            text, removed = rewritten or ("", 0)

            self.assertEqual(removed, 1)
            self.assertEqual(json.loads(text), [{"Path": "TS_10.mrc"}])

    def test_cancelled_transaction_restores_every_file_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            first = root / "TS_1.mrc"
            manifest = root / "processed_items.json"
            first.write_text("data")
            original_manifest = json.dumps([{"Path": "TS_1.mrc"}])
            manifest.write_text(original_manifest)
            cancel = threading.Event()
            transaction = QuarantineTransaction(cancel_event=cancel)
            transaction.add_file(first, root=root)
            transaction.add_rewrite(manifest, "[]\n", root=root)
            cancel.set()

            with self.assertRaises(DeletionCancelled):
                transaction.execute()

            self.assertEqual(first.read_text(), "data")
            self.assertEqual(manifest.read_text(), original_manifest)

    def test_successful_transaction_leaves_recoverable_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "TS_1.mrc"
            source.write_text("data")
            transaction = QuarantineTransaction()
            transaction.add_file(source, root=root)

            self.assertEqual(transaction.execute(), 1)
            self.assertFalse(source.exists())
            recoverable = root / ".cryopal_trash" / transaction.transaction_id / source.name
            self.assertEqual(recoverable.read_text(), "data")

    def test_file_symlink_is_quarantined_without_moving_its_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "root"
            root.mkdir()
            target = root / "target.mrc"
            target.write_text("data", encoding="utf-8")
            link = root / "TS_1.mrc"
            link.symlink_to(target)

            transaction = QuarantineTransaction()
            transaction.add_file(link, root=root)
            transaction.execute()

            self.assertTrue(target.exists())
            self.assertFalse(link.exists())
            recoverable = root / ".cryopal_trash" / transaction.transaction_id / link.name
            self.assertTrue(recoverable.is_symlink())


if __name__ == "__main__":
    unittest.main()
