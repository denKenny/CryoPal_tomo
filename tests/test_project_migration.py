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


if __name__ == "__main__":
    unittest.main()
