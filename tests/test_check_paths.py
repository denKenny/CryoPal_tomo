from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cryoet_organizer.check_paths import collect_project_path_report
from cryoet_organizer.project import DatasetRecord, ProjectData, ThumbnailRecord


class CheckPathsTests(unittest.TestCase):
    def test_report_distinguishes_all_missing_vs_partial_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            raw = root / "raw"
            raw.mkdir()
            processing = root / "processing"
            processing.mkdir()
            tilt_processing = processing / "warp_tiltseries"
            tilt_processing.mkdir()
            tiltstack = tilt_processing / "tiltstack"
            tiltstack.mkdir()
            tomostar = processing / "tomostar"
            tomostar.mkdir()
            (tomostar / "TS_01.tomostar").write_text("one", encoding="utf-8")
            (tomostar / "TS_02.tomostar").write_text("two", encoding="utf-8")
            (processing / "warp_frameseries.settings").write_text("frame", encoding="utf-8")

            ts1_stack_dir = tiltstack / "TS_01"
            ts1_stack_dir.mkdir()
            (ts1_stack_dir / "TS_01_ali.mrc").write_text("ali", encoding="utf-8")

            thumbnail = root / "thumb.png"
            thumbnail.write_text("img", encoding="utf-8")

            dataset = DatasetRecord(
                dataset_name="DS",
                sample="S",
                pixel_size=1.0,
                exposure=1.0,
                tomogram_x=1,
                tomogram_y=1,
                tomogram_z=1,
                raw_frames_folder=str(raw),
                mdocs_folder="",
                processing_root_folder=str(root),
                processing_folder=str(processing),
                frame_series_settings_file=str(processing / "warp_frameseries.settings"),
                tilt_series_processing_folder=str(tilt_processing),
                tilt_series_data_folder=str(tomostar),
                thumbnails=[
                    ThumbnailRecord(image_path=str(thumbnail), ts_name="TS_01", mrc_path=""),
                ],
            )
            project = ProjectData(datasets=[dataset])

            report = collect_project_path_report(project)

            self.assertFalse(report.all_found)
            summary_labels = {(entry.dataset_name, entry.label) for entry in report.summary_missing}
            self.assertIn(("DS", "Angle files"), summary_labels)
            self.assertIn(("DS", "Aligned Stacks / TS_02"), summary_labels)
            self.assertIn(("DS", "Gallery associated .mrc files"), summary_labels)


if __name__ == "__main__":
    unittest.main()
