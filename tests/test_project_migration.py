from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cryoet_organizer.project import (
    PROJECT_SCHEMA_VERSION,
    ProjectData,
    ProjectFormatError,
    load_project,
    save_project,
    ts_name_match_score,
)


class ProjectMigrationTests(unittest.TestCase):
    def test_ts_name_matching_requires_identifier_boundaries(self) -> None:
        self.assertIsNone(ts_name_match_score("TS10_reconstruction", "TS1"))
        self.assertIsNotNone(ts_name_match_score("prefix_TS1_reconstruction", "TS1"))

    def test_v1_payload_migrates_file_registry_roles_into_state(self) -> None:
        payload = {
            "name": "Legacy",
            "schema_version": 1,
            "metadata": {
                "file_registry_patterns": {
                    "ts_tomogram": {"filename_pattern": "*.mrc"},
                },
                "file_registry_overrides": {
                    "DS": {"ts_angle_file": {"TS1": "/tmp/test.tlt"}},
                },
                "file_registry_role_order": ["ts_tomogram", "custom_role"],
            },
            "datasets": [],
        }

        project = ProjectData.from_dict(payload)

        self.assertEqual(project.schema_version, PROJECT_SCHEMA_VERSION)
        self.assertIn("tomogram", project.state.file_registry_patterns)
        self.assertNotIn("ts_tomogram", project.state.file_registry_patterns)
        self.assertEqual(
            project.state.file_registry_overrides["DS"]["angle_file"]["TS1"],
            "/tmp/test.tlt",
        )
        self.assertEqual(
            project.state.file_registry_role_order,
            ["tomogram", "custom_role"],
        )

    def test_metadata_only_v3_payload_is_promoted_into_typed_state(self) -> None:
        payload = {
            "name": "Half migrated",
            "schema_version": 3,
            "metadata": {
                "appearance": {"main_background": "#101010"},
                "slurm_profiles": [{"name": "GPU", "partition": "gpu"}],
                "job_default_overrides": {
                    "Particles/Group/job": {"field": {"enabled": "true", "value": "1"}}
                },
            },
            "datasets": [],
        }

        project = ProjectData.from_dict(payload)

        self.assertEqual(project.state.appearance["main_background"], "#101010")
        self.assertEqual(project.state.slurm_profiles[0]["name"], "GPU")
        self.assertIn("Particles/Group/job", project.state.job_default_overrides)

    def test_duplicate_dataset_names_are_rejected_on_load(self) -> None:
        payload = {
            "name": "Broken",
            "schema_version": PROJECT_SCHEMA_VERSION,
            "datasets": [
                {
                    "dataset_name": "DatasetA",
                    "sample": "S1",
                    "pixel_size": 1.0,
                    "exposure": 1.0,
                    "tomogram_x": 1,
                    "tomogram_y": 1,
                    "tomogram_z": 1,
                    "raw_frames_folder": "/tmp/raw-a",
                    "mdocs_folder": "/tmp/mdoc-a",
                },
                {
                    "dataset_name": "dataseta",
                    "sample": "S2",
                    "pixel_size": 1.0,
                    "exposure": 1.0,
                    "tomogram_x": 1,
                    "tomogram_y": 1,
                    "tomogram_z": 1,
                    "raw_frames_folder": "/tmp/raw-b",
                    "mdocs_folder": "/tmp/mdoc-b",
                },
            ],
        }

        with self.assertRaisesRegex(ValueError, "Duplicate dataset names are not supported"):
            ProjectData.from_dict(payload)

    def test_future_schema_is_rejected_without_downgrade(self) -> None:
        payload = {
            "name": "From the future",
            "schema_version": PROJECT_SCHEMA_VERSION + 1,
            "state": {"future_field": {"must": "survive"}},
        }

        with self.assertRaisesRegex(ProjectFormatError, "newer release"):
            ProjectData.from_dict(payload)
        self.assertEqual(payload["schema_version"], PROJECT_SCHEMA_VERSION + 1)
        self.assertIn("future_field", payload["state"])

    def test_v2_shortcuts_are_migrated_from_metadata(self) -> None:
        project = ProjectData.from_dict(
            {
                "name": "Legacy shortcuts",
                "schema_version": 2,
                "metadata": {
                    "shortcuts": [
                        {"title": "Inspect", "script": "echo ok", "color": "#88ccff"}
                    ]
                },
            }
        )

        self.assertEqual(project.state.shortcuts[0]["title"], "Inspect")
        self.assertNotIn("shortcuts", project.metadata)

    def test_string_false_is_not_treated_as_true(self) -> None:
        project = ProjectData.from_dict(
            {
                "schema_version": PROJECT_SCHEMA_VERSION,
                "dataset_sort_descending": "false",
                "datasets": [],
            }
        )
        self.assertFalse(project.dataset_sort_descending)

    def test_malformed_dataset_collections_and_nonfinite_values_are_rejected(self) -> None:
        with self.assertRaisesRegex(ProjectFormatError, "datasets.*list of objects"):
            ProjectData.from_dict({"schema_version": PROJECT_SCHEMA_VERSION, "datasets": {"DS": {}}})

        with self.assertRaisesRegex(ProjectFormatError, "pixel_size.*finite"):
            ProjectData.from_dict(
                {
                    "schema_version": PROJECT_SCHEMA_VERSION,
                    "datasets": [{"dataset_name": "DS", "pixel_size": float("nan")}],
                }
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "nonstandard.cryopal.json"
            path.write_text('{"schema_version": 8, "datasets": [], "metadata": {"value": NaN}}')
            with self.assertRaisesRegex(ProjectFormatError, "non-standard numeric value"):
                load_project(path)

    def test_save_rotates_recovery_backups(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "project.cryopal.json"
            project = ProjectData(name="one")
            save_project(path, project)
            project.name = "two"
            save_project(path, project)
            project.name = "three"
            save_project(path, project)

            self.assertEqual(ProjectData.from_dict(json.loads(path.read_text())).name, "three")
            self.assertEqual(
                ProjectData.from_dict(json.loads(Path(f"{path}.bak1").read_text())).name,
                "two",
            )
            self.assertEqual(
                ProjectData.from_dict(json.loads(Path(f"{path}.bak2").read_text())).name,
                "one",
            )


if __name__ == "__main__":
    unittest.main()
