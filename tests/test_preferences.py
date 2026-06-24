from __future__ import annotations

import unittest
from pathlib import Path

from cryoet_organizer.preferences import (
    project_preference,
    project_preference_enabled,
    project_preference_int,
)
from cryoet_organizer.project import ProjectData
from cryoet_organizer.project import DatasetRecord
from cryoet_organizer.thumbnail_cache import effective_thumbnail_source_folder, resolve_thumbnail_cache_dir


class PreferencesTests(unittest.TestCase):
    def test_defaults_are_returned_when_project_preference_missing(self) -> None:
        project = ProjectData()

        self.assertEqual(project_preference(project, "thumbnail_cache_location"), "dataset/thumbnail-cache")
        self.assertTrue(project_preference_enabled(project, "use_downscaled_thumbnails", default=False))
        self.assertEqual(project_preference_int(project, "thumbnail_cache_size", default=0), 256)

    def test_integer_preference_is_clamped_and_falls_back_when_invalid(self) -> None:
        project = ProjectData()
        project.state.preferences["thumbnail_cache_size"] = "12"
        self.assertEqual(
            project_preference_int(project, "thumbnail_cache_size", default=256, minimum=32, maximum=4096),
            32,
        )

        project.state.preferences["thumbnail_cache_size"] = "not-a-number"
        self.assertEqual(
            project_preference_int(project, "thumbnail_cache_size", default=256, minimum=32, maximum=4096),
            256,
        )

    def test_thumbnail_cache_dir_uses_tilt_series_processing_parent_for_imported_projects(self) -> None:
        project = ProjectData()
        dataset = DatasetRecord(
            dataset_name="ImportedDS",
            sample="Sample",
            pixel_size=1.0,
            exposure=1.0,
            tomogram_x=1,
            tomogram_y=1,
            tomogram_z=1,
            raw_frames_folder="",
            mdocs_folder="",
            processing_folder="/legacy/root",
            tilt_series_processing_folder="/real/project/ImportedDS/warp_tiltseries",
        )

        cache_dir = resolve_thumbnail_cache_dir(project, dataset)
        self.assertEqual(cache_dir, Path("/real/project/ImportedDS/thumbnail-cache"))

    def test_effective_thumbnail_source_folder_prefers_reconstruction_fallback(self) -> None:
        dataset = DatasetRecord(
            dataset_name="ImportedDS",
            sample="Sample",
            pixel_size=1.0,
            exposure=1.0,
            tomogram_x=1,
            tomogram_y=1,
            tomogram_z=1,
            raw_frames_folder="",
            mdocs_folder="",
            processing_folder="/real/project/ImportedDS",
            tilt_series_processing_folder="/real/project/ImportedDS/warp_tiltseries",
        )

        self.assertEqual(
            effective_thumbnail_source_folder(dataset),
            "/real/project/ImportedDS/warp_tiltseries/reconstructions",
        )


if __name__ == "__main__":
    unittest.main()
