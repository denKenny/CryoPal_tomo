from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from cryoet_organizer.executables import (
    MCORE_EXECUTABLE,
    MTOOLS_EXECUTABLE,
    PYTOM_MATCH_EXECUTABLE,
    WARPTOOLS_EXECUTABLE,
    build_executable_usage_registry,
    get_project_executable_overrides,
    resolve_executable_command,
    set_project_executable_overrides,
)
from cryoet_organizer.project import PROJECT_SCHEMA_VERSION, ProjectData
from cryoet_organizer.settings_bundle import (
    apply_settings_import,
    build_settings_export_payload,
    importable_settings_groups,
    load_settings_bundle,
    selected_executable_settings,
)
from cryoet_organizer.tabs.processing_m import ProcessingMTab


class ExecutableOverrideTests(unittest.TestCase):
    def test_registry_contains_deduplicated_executables_with_usage(self) -> None:
        registry = build_executable_usage_registry()
        executables = [item.executable for item in registry]

        self.assertEqual(len(executables), len(set(executables)))
        self.assertIn(WARPTOOLS_EXECUTABLE, executables)
        self.assertIn(MTOOLS_EXECUTABLE, executables)
        self.assertIn(MCORE_EXECUTABLE, executables)
        self.assertIn(PYTOM_MATCH_EXECUTABLE, executables)
        warp_usage = next(item.applications for item in registry if item.executable == WARPTOOLS_EXECUTABLE)
        mtools_usage = next(item.applications for item in registry if item.executable == MTOOLS_EXECUTABLE)
        self.assertTrue(any("create_settings" in item for item in warp_usage))
        self.assertTrue(any("ts_export_particles" in item for item in warp_usage))
        self.assertIn("MTools > create_population", mtools_usage)

    def test_resolve_executable_command_uses_override_or_default(self) -> None:
        project = ProjectData()

        self.assertEqual(resolve_executable_command(project, WARPTOOLS_EXECUTABLE), WARPTOOLS_EXECUTABLE)
        set_project_executable_overrides(
            project,
            {
                WARPTOOLS_EXECUTABLE: "/opt/wrapper WarpTools",
                "UnknownTool": "/tmp/unknown",
                MTOOLS_EXECUTABLE: MTOOLS_EXECUTABLE,
            },
        )

        self.assertEqual(resolve_executable_command(project, WARPTOOLS_EXECUTABLE), "/opt/wrapper WarpTools")
        self.assertEqual(resolve_executable_command(project, MTOOLS_EXECUTABLE), MTOOLS_EXECUTABLE)
        self.assertNotIn("UnknownTool", get_project_executable_overrides(project))

    def test_project_migration_adds_executable_override_state(self) -> None:
        project = ProjectData.from_dict(
            {
                "name": "Old project",
                "schema_version": 5,
                "datasets": [],
                "state": {},
            }
        )

        self.assertEqual(project.schema_version, PROJECT_SCHEMA_VERSION)
        self.assertEqual(project.state.executable_overrides, {})

    def test_settings_bundle_exports_and_imports_executable_overrides(self) -> None:
        source = ProjectData()
        set_project_executable_overrides(source, {WARPTOOLS_EXECUTABLE: "/opt/bin/WarpTools"})
        payload = build_settings_export_payload(source, ["executables::executables"])

        self.assertEqual(payload["categories"]["executables"][WARPTOOLS_EXECUTABLE], "/opt/bin/WarpTools")
        groups = importable_settings_groups(payload)
        self.assertTrue(any(group.key == "executables" for group in groups))

        target = ProjectData()
        applied, skipped = apply_settings_import(
            target,
            payload,
            ["executables::executables"],
            overwrite_existing=True,
        )

        self.assertEqual(applied, ["executables::executables"])
        self.assertEqual(skipped, [])
        self.assertEqual(resolve_executable_command(target, WARPTOOLS_EXECUTABLE), "/opt/bin/WarpTools")

    def test_settings_bundle_rejects_future_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "future.cryopal.settings"
            path.write_text(json.dumps({"version": 999, "categories": {}}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "No settings were imported"):
                load_settings_bundle(path)

    def test_settings_bundle_rejects_malformed_root_and_nonstandard_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            malformed = root / "malformed.cryopal.settings"
            malformed.write_text('{"version": 3, "categories": []}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "categories.*JSON object"):
                load_settings_bundle(malformed)

            nonstandard = root / "nonstandard.cryopal.settings"
            nonstandard.write_text('{"categories": {"appearance": {"scale": NaN}}}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "non-standard numeric value"):
                load_settings_bundle(nonstandard)

    def test_command_bearing_settings_are_identified_for_trust_prompt(self) -> None:
        selected = selected_executable_settings(
            ["appearance::appearance", "executables::executables", "workflows::abc", "shortcuts::__empty__"]
        )

        self.assertEqual(selected, ["executables::executables", "workflows::abc"])

    def test_mtools_create_population_keeps_multi_token_override_as_command_prefix(self) -> None:
        project = ProjectData()
        set_project_executable_overrides(project, {MTOOLS_EXECUTABLE: "apptainer exec mtools.sif MTools"})
        tab = SimpleNamespace(
            app=SimpleNamespace(project=project),
            create_directory_var=SimpleNamespace(get=lambda: "/tmp/populations"),
            create_name_var=SimpleNamespace(get=lambda: "Population A"),
        )

        command = ProcessingMTab._create_population_command(tab)

        self.assertTrue(command.startswith("apptainer exec mtools.sif MTools create_population "))
        self.assertIn("--directory /tmp/populations", command)
        self.assertIn("--name Population A", command)


if __name__ == "__main__":
    unittest.main()
