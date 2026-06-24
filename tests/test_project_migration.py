from __future__ import annotations

import unittest

from cryoet_organizer.project import PROJECT_SCHEMA_VERSION, ProjectData


class ProjectMigrationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
