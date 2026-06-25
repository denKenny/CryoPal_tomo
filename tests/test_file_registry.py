from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cryoet_organizer.file_resolver import (
    FileRoleConfig,
    file_role_config,
    remove_custom_file_role,
    resolve_dataset_file,
    set_file_override,
    set_file_role_config,
)
from cryoet_organizer.project import DatasetRecord, ProjectData


class FileRegistryTests(unittest.TestCase):
    def test_resolver_prefers_manual_override_and_newest_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            recon = root / "reconstruction"
            recon.mkdir()
            older = recon / "TS_01_old.mrc"
            newer = recon / "TS_01_new.mrc"
            older.write_text("old", encoding="utf-8")
            newer.write_text("new", encoding="utf-8")
            newer.touch()

            dataset = DatasetRecord(
                dataset_name="DS",
                sample="S",
                pixel_size=1.0,
                exposure=1.0,
                tomogram_x=1,
                tomogram_y=1,
                tomogram_z=1,
                raw_frames_folder="",
                mdocs_folder="",
                tilt_series_processing_folder=str(root),
            )
            project = ProjectData(datasets=[dataset])

            auto = resolve_dataset_file(project, dataset, "TS_01", "tomogram")
            self.assertEqual(Path(auto.path).name, "TS_01_new.mrc")

            manual = root / "manual.mrc"
            manual.write_text("manual", encoding="utf-8")
            set_file_override(project, "DS", "tomogram", "TS_01", str(manual))
            overridden = resolve_dataset_file(project, dataset, "TS_01", "tomogram")
            self.assertEqual(overridden.path, str(manual))
            self.assertEqual(overridden.source, "manual override")

    def test_custom_role_is_stored_in_typed_state(self) -> None:
        project = ProjectData()
        config = FileRoleConfig(
            role="mask_file",
            title="Mask file",
            description="Custom mask",
            base_dir_template="{tilt_series_processing_folder}",
            filename_pattern="*.mrc",
        )
        set_file_role_config(project, config.role, config)
        self.assertIn("mask_file", project.state.file_registry_patterns)
        self.assertEqual(file_role_config(project, "mask_file").title, "Mask file")
        remove_custom_file_role(project, "mask_file")
        self.assertNotIn("mask_file", project.state.file_registry_patterns)

    def test_dataset_specific_tomogram_folder_overrides_default_reconstruction_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manual_tomograms = root / "manual_tomograms"
            manual_tomograms.mkdir()
            reconstruction = root / "reconstruction"
            reconstruction.mkdir()

            expected = manual_tomograms / "TS_01_manual.mrc"
            expected.write_text("manual tomogram", encoding="utf-8")
            fallback = reconstruction / "TS_01_reconstruction.mrc"
            fallback.write_text("fallback tomogram", encoding="utf-8")

            dataset = DatasetRecord(
                dataset_name="DS",
                sample="S",
                pixel_size=1.0,
                exposure=1.0,
                tomogram_x=1,
                tomogram_y=1,
                tomogram_z=1,
                raw_frames_folder="",
                mdocs_folder="",
                tilt_series_processing_folder=str(root),
                tomogram_folder=str(manual_tomograms),
            )
            project = ProjectData(datasets=[dataset])

            resolved = resolve_dataset_file(project, dataset, "TS_01", "tomogram")
            self.assertEqual(resolved.path, str(expected))
            self.assertEqual(resolved.source, "automatic")

    def test_unified_mdoc_role_uses_prepared_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "source_mdocs"
            source.mkdir()
            prepared = root / "new_mdoc"
            prepared.mkdir()

            (source / "Position_3.mdoc").write_text("src", encoding="utf-8")
            prepared_file = prepared / "Dataset_TS_001.mdoc"
            prepared_file.write_text("prepared", encoding="utf-8")

            dataset = DatasetRecord(
                dataset_name="Dataset",
                sample="S",
                pixel_size=1.0,
                exposure=1.0,
                tomogram_x=1,
                tomogram_y=1,
                tomogram_z=1,
                raw_frames_folder="",
                mdocs_folder=str(prepared),
                mdocs_source_folder=str(source),
                unified_mdoc_names=True,
                unified_mdocs_folder=str(prepared),
                prepared_mdoc_map={"Position_3": str(prepared_file)},
            )
            project = ProjectData(datasets=[dataset])

            resolved = resolve_dataset_file(project, dataset, "Position_3", "mdoc")
            self.assertEqual(resolved.path, str(prepared_file))
            self.assertEqual(resolved.source, "automatic")


if __name__ == "__main__":
    unittest.main()
