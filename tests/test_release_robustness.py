from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cryoet_organizer.project import DatasetRecord, prepare_unified_mdocs_directory
from cryoet_organizer.viewer_defaults import (
    ViewerDefaultsConfig,
    ViewerException,
    load_global_viewer_defaults,
    save_global_viewer_defaults,
)
import cryoet_organizer.viewer_defaults as viewer_defaults_module


class PreparedMdocDirectoryTests(unittest.TestCase):
    def _dataset(self, source_dir: Path, processing_dir: Path) -> DatasetRecord:
        return DatasetRecord(
            dataset_name="DatasetA",
            sample="Sample",
            pixel_size=1.0,
            exposure=1.0,
            tomogram_x=1,
            tomogram_y=1,
            tomogram_z=1,
            raw_frames_folder=str(source_dir),
            mdocs_folder=str(source_dir),
            mdocs_source_folder=str(source_dir),
            processing_folder=str(processing_dir),
            processing_root_folder=str(processing_dir.parent),
            unified_mdoc_names=True,
        )

    def test_prepare_unified_mdocs_directory_replaces_target_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_dir = root / "mdocs"
            source_dir.mkdir()
            (source_dir / "TS_01.mdoc").write_text("one", encoding="utf-8")
            (source_dir / "TS_02.mdoc").write_text("two", encoding="utf-8")

            processing_dir = root / "processing" / "DatasetA"
            target_dir = processing_dir / "new_mdoc"
            target_dir.mkdir(parents=True)
            (target_dir / "stale.mdoc").write_text("stale", encoding="utf-8")

            folder, count, prepared_map = prepare_unified_mdocs_directory(
                self._dataset(source_dir, processing_dir)
            )

            self.assertEqual(Path(folder), target_dir)
            self.assertEqual(count, 2)
            self.assertFalse((target_dir / "stale.mdoc").exists())
            self.assertEqual(
                sorted(path.name for path in target_dir.glob("*.mdoc")),
                ["DatasetA_TS_001.mdoc", "DatasetA_TS_002.mdoc"],
            )
            self.assertEqual(
                prepared_map["TS_01"],
                str(target_dir / "DatasetA_TS_001.mdoc"),
            )

    def test_prepare_unified_mdocs_directory_leaves_previous_target_on_copy_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_dir = root / "mdocs"
            source_dir.mkdir()
            (source_dir / "TS_01.mdoc").write_text("one", encoding="utf-8")

            processing_dir = root / "processing" / "DatasetA"
            target_dir = processing_dir / "new_mdoc"
            target_dir.mkdir(parents=True)
            existing = target_dir / "keep.mdoc"
            existing.write_text("keep", encoding="utf-8")

            with mock.patch("cryoet_organizer.project.shutil.copy2", side_effect=OSError("copy failed")):
                with self.assertRaisesRegex(OSError, "copy failed"):
                    prepare_unified_mdocs_directory(self._dataset(source_dir, processing_dir))

            self.assertTrue(existing.exists())
            self.assertEqual(existing.read_text(encoding="utf-8"), "keep")


class ViewerDefaultsPersistenceTests(unittest.TestCase):
    def test_save_global_viewer_defaults_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "viewers.json"
            config = ViewerDefaultsConfig(
                exceptions=[ViewerException(command="napari", extensions=[".mrc"])]
            )

            with mock.patch.object(viewer_defaults_module, "_GLOBAL_VIEWER_DEFAULTS_PATH", target):
                save_global_viewer_defaults(config)
                loaded = load_global_viewer_defaults()

            self.assertEqual(loaded, config)

    def test_save_global_viewer_defaults_raises_when_replace_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "viewers.json"
            config = ViewerDefaultsConfig(
                exceptions=[ViewerException(command="napari", extensions=[".mrc"])]
            )

            with mock.patch.object(viewer_defaults_module, "_GLOBAL_VIEWER_DEFAULTS_PATH", target):
                with mock.patch("cryoet_organizer.viewer_defaults.os.replace", side_effect=PermissionError("denied")):
                    with self.assertRaisesRegex(OSError, "Failed to save the global viewer defaults file"):
                        save_global_viewer_defaults(config)


if __name__ == "__main__":
    unittest.main()
