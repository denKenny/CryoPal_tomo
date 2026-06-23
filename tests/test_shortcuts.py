from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cryoet_organizer.project import PROJECT_SCHEMA_VERSION, ProjectData
from cryoet_organizer.shortcuts import (
    ShortcutDefinition,
    export_shortcuts,
    get_project_shortcuts,
    import_shortcuts,
    set_project_shortcuts,
)


class ShortcutsTests(unittest.TestCase):
    def test_shortcuts_use_typed_project_state(self) -> None:
        project = ProjectData()
        shortcuts = [
            ShortcutDefinition(
                title="Warp",
                script="cd /tmp\nconda activate warp\nWarpTools",
                color="#88ccff",
            )
        ]

        set_project_shortcuts(project, shortcuts)
        restored = get_project_shortcuts(project)

        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0].title, "Warp")
        self.assertEqual(project.state.shortcuts[0]["color"], "#88ccff")

    def test_shortcuts_import_export_roundtrip(self) -> None:
        shortcuts = [
            ShortcutDefinition(title="Open Warp", script="WarpTools", color="#ffcc88"),
            ShortcutDefinition(title="Open IMOD", script="3dmod", color="#ccff99"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            target = export_shortcuts(Path(tmpdir) / "shortcuts", shortcuts)
            restored = import_shortcuts(target)

        self.assertEqual([item.title for item in restored], ["Open Warp", "Open IMOD"])

    def test_v4_payload_migrates_shortcuts_state(self) -> None:
        payload = {
            "name": "Legacy",
            "schema_version": 4,
            "state": {},
            "datasets": [],
        }

        project = ProjectData.from_dict(payload)

        self.assertEqual(project.schema_version, PROJECT_SCHEMA_VERSION)
        self.assertEqual(project.state.shortcuts, [])


if __name__ == "__main__":
    unittest.main()
