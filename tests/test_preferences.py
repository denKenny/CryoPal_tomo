from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from cryoet_organizer.preferences import (
    display_profile_metrics,
    load_global_preferences,
    normalize_display_profile,
    project_preference,
    project_preference_enabled,
    project_preference_int,
    resolve_display_profile,
    save_global_preferences,
)
from cryoet_organizer.project import ProjectData
from cryoet_organizer.project import DatasetRecord
from cryoet_organizer.resizable_sections import clear_layout_preferences, load_layout_value, save_layout_value
from cryoet_organizer.thumbnail_cache import effective_thumbnail_source_folder, resolve_thumbnail_cache_dir


class PreferencesTests(unittest.TestCase):
    def test_defaults_are_returned_when_project_preference_missing(self) -> None:
        project = ProjectData()

        self.assertEqual(project_preference(project, "thumbnail_cache_location"), "dataset/thumbnail-cache")
        self.assertTrue(project_preference_enabled(project, "use_downscaled_thumbnails", default=False))
        self.assertEqual(project_preference_int(project, "thumbnail_cache_size", default=0), 256)
        self.assertEqual(project_preference_int(project, "gallery_page_size", default=0), 50)

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

        project.state.preferences["gallery_page_size"] = "2"
        self.assertEqual(
            project_preference_int(project, "gallery_page_size", default=50, minimum=8, maximum=500),
            8,
        )

        project.state.preferences["gallery_page_size"] = "not-a-number"
        self.assertEqual(
            project_preference_int(project, "gallery_page_size", default=50, minimum=8, maximum=500),
            50,
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

    def test_layout_preferences_are_saved_loaded_and_cleared(self) -> None:
        project = ProjectData()

        self.assertEqual(
            load_layout_value(project, "processing_warp", "history", default=200, minimum=120),
            200,
        )

        save_layout_value(project, "processing_warp", "history", 275)
        save_layout_value(project, "gallery", "details", 360, dimension="width")

        self.assertEqual(
            load_layout_value(project, "processing_warp", "history", default=200, minimum=120),
            275,
        )
        self.assertEqual(
            load_layout_value(project, "gallery", "details", dimension="width", default=320, minimum=240),
            360,
        )

        clear_layout_preferences(project, "processing_warp")
        self.assertEqual(
            load_layout_value(project, "processing_warp", "history", default=200, minimum=120),
            200,
        )
        self.assertEqual(
            load_layout_value(project, "gallery", "details", dimension="width", default=320, minimum=240),
            360,
        )

    def test_display_profile_normalization_and_auto_resolution(self) -> None:
        self.assertEqual(normalize_display_profile("large"), "large")
        self.assertEqual(normalize_display_profile("unknown"), "auto")
        self.assertEqual(
            resolve_display_profile("auto", screen_dpi=170, screen_width=1920, screen_height=1080),
            "large",
        )
        self.assertEqual(
            resolve_display_profile("auto", screen_dpi=210, screen_width=3840, screen_height=2160),
            "extra_large",
        )
        self.assertEqual(
            resolve_display_profile("auto", screen_dpi=96, screen_width=2560, screen_height=1440),
            "large",
        )
        self.assertEqual(resolve_display_profile("auto", screen_dpi=96, screen_width=1920, screen_height=1080), "normal")
        self.assertEqual(display_profile_metrics("large")["body_size"], 12)

    def test_global_preferences_are_loaded_and_saved(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "prefs.json"
            with patch("cryoet_organizer.preferences._GLOBAL_PREFERENCES_PATH", path):
                loaded = load_global_preferences()
                self.assertEqual(loaded["display_profile"], "auto")

                saved = save_global_preferences({"display_profile": "extra_large"})
                self.assertEqual(saved["display_profile"], "extra_large")

                reloaded = load_global_preferences()
                self.assertEqual(reloaded["display_profile"], "extra_large")


if __name__ == "__main__":
    unittest.main()
