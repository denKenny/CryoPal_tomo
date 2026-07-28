from __future__ import annotations

import unittest
from types import SimpleNamespace

from cryoet_organizer.executables import MTOOLS_EXECUTABLE
from cryoet_organizer.job_defaults import (
    command_parts_for_added_job_defaults,
    effective_job_default_fields,
    get_project_job_default_overrides,
    job_override_key,
    resolve_job_default,
    resolve_job_parameter_name,
)
from cryoet_organizer.project import ProjectData
from cryoet_organizer.tabs.processing_m import ProcessingMTab


class _TextStub:
    def __init__(self) -> None:
        self.value = ""

    def delete(self, _start: str, _end: str) -> None:
        self.value = ""

    def insert(self, _index: str, value: str) -> None:
        self.value = value

    def get(self, _start: str, _end: str) -> str:
        return self.value


class JobParameterOverrideTests(unittest.TestCase):
    def test_parameter_only_override_is_preserved_without_value_override(self) -> None:
        project = ProjectData()
        key = job_override_key("Processing", "Tilt series", "ts_ctf")
        project.state.job_default_overrides[key] = {
            "--device_list": {"parameter": "--gpu-list"},
            "--angpix": {"enabled": "true", "value": "8.5", "parameter": "--pixel-size"},
        }

        cleaned = get_project_job_default_overrides(project)

        self.assertEqual(cleaned[key]["--device_list"], {"parameter": "--gpu-list"})
        self.assertEqual(
            cleaned[key]["--angpix"],
            {"enabled": "true", "value": "8.5", "parameter": "--pixel-size"},
        )
        self.assertEqual(
            resolve_job_parameter_name(project, "Processing", "Tilt series", "ts_ctf", "--device_list", "--device_list"),
            "--gpu-list",
        )
        self.assertEqual(
            resolve_job_default(project, "Processing", "Tilt series", "ts_ctf", "--device_list", "0"),
            "0",
        )

    def test_mtools_command_preview_uses_parameter_override(self) -> None:
        project = ProjectData()
        key = job_override_key("Processing: M", "Refinement", "m_refine")
        project.state.job_default_overrides[key] = {"--devicelist": {"parameter": "--gpu-list"}}
        text = _TextStub()
        tab = ProcessingMTab.__new__(ProcessingMTab)
        tab.app = SimpleNamespace(project=project)
        tab._suspend_command_preview_updates = False
        tab.current_job = SimpleNamespace(
            executable=MTOOLS_EXECUTABLE,
            command="m_refine",
            group="Refinement",
            flags=(SimpleNamespace(name="--devicelist", widget="text"),),
        )
        tab.parameter_vars = {"--devicelist": SimpleNamespace(get=lambda: "0 1")}
        tab.command_text = text
        tab._update_population_summary = lambda: None

        ProcessingMTab._update_command_preview(tab)

        self.assertIn("MTools m_refine --gpu-list 0 1", text.value)
        self.assertNotIn("--devicelist", text.value)

    def test_custom_and_removed_default_fields_are_resolved(self) -> None:
        project = ProjectData()
        key = job_override_key("Processing: M", "MTools", "create_source")
        project.state.job_default_overrides[key] = {
            "--name": {"removed": "true"},
            "custom__1": {
                "custom": "true",
                "label": "Extra tolerance",
                "parameter": "--tolerance",
                "widget": "text",
                "default": "0.25",
            },
        }

        cleaned = get_project_job_default_overrides(project)
        field_keys = [
            field.key
            for field in effective_job_default_fields(project, "Processing: M", "MTools", "create_source")
        ]

        self.assertEqual(cleaned[key]["--name"], {"removed": "true"})
        self.assertNotIn("--name", field_keys)
        self.assertIn("custom__1", field_keys)
        self.assertEqual(
            command_parts_for_added_job_defaults(project, "Processing: M", "MTools", "create_source"),
            ["--tolerance 0.25"],
        )

    def test_removed_mtools_parameter_is_not_previewed(self) -> None:
        project = ProjectData()
        key = job_override_key("Processing: M", "Refinement", "m_refine")
        project.state.job_default_overrides[key] = {"--devicelist": {"removed": "true"}}
        text = _TextStub()
        tab = ProcessingMTab.__new__(ProcessingMTab)
        tab.app = SimpleNamespace(project=project)
        tab._suspend_command_preview_updates = False
        tab.current_job = SimpleNamespace(
            executable=MTOOLS_EXECUTABLE,
            command="m_refine",
            group="Refinement",
            flags=(SimpleNamespace(name="--devicelist", widget="text"),),
        )
        tab.parameter_vars = {"--devicelist": SimpleNamespace(get=lambda: "0 1")}
        tab.command_text = text
        tab._update_population_summary = lambda: None

        ProcessingMTab._update_command_preview(tab)

        self.assertEqual(text.value, "MTools m_refine")

    def test_catalog_flag_description_is_distinct_from_parameter_name(self) -> None:
        project = ProjectData()
        fields = {
            field.key: field
            for field in effective_job_default_fields(project, "Processing", "Tilt series", "ts_ctf")
        }

        self.assertIn("--device_list", fields)
        self.assertEqual(fields["--device_list"].parameter_name, "--device_list")
        self.assertNotEqual(fields["--device_list"].description, "--device_list")
        self.assertTrue(fields["--device_list"].description)

    def test_description_override_is_preserved_as_metadata(self) -> None:
        project = ProjectData()
        key = job_override_key("Processing", "Tilt series", "ts_ctf")
        project.state.job_default_overrides[key] = {
            "--device_list": {"description": "GPU list for this workstation"},
        }

        cleaned = get_project_job_default_overrides(project)
        fields = {
            field.key: field
            for field in effective_job_default_fields(project, "Processing", "Tilt series", "ts_ctf")
        }

        self.assertEqual(cleaned[key]["--device_list"], {"description": "GPU list for this workstation"})
        self.assertEqual(fields["--device_list"].description, "GPU list for this workstation")


if __name__ == "__main__":
    unittest.main()
