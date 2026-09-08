from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from cryoet_organizer.app import CryoETOrganizerApp, _export_history_csv, _export_history_html, _export_ts_annotations_csv
from cryoet_organizer.project import DatasetRecord, JobHistoryEntry, ProjectData, ThumbnailRecord
from cryoet_organizer.ts_metadata import clear_ts_metadata_cache


class TsAnnotationExportTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_ts_metadata_cache()

    def test_history_state_tags_surface_failures_and_cancellations(self) -> None:
        app = SimpleNamespace(_running_history_entry_ids=set(), _waiting_history_entry_ids=set())
        self.assertEqual(
            CryoETOrganizerApp.history_entry_state_tag(app, JobHistoryEntry("", "", "", "", "", status="failed")),
            "failed",
        )
        self.assertEqual(
            CryoETOrganizerApp.history_entry_state_tag(app, JobHistoryEntry("", "", "", "", "", status="cancelled")),
            "cancelled",
        )

    def test_export_ts_annotations_csv_writes_expected_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            processing = root / "processing"
            processing.mkdir()
            xml_path = processing / "TS_01.xml"
            xml_path.write_text(
                (
                    '<TiltSeries CTFResolutionEstimate="6.8" Defocus="2.10">\n'
                    "  <Angles>\n"
                    "    -20\n"
                    "    0\n"
                    "    20\n"
                    "  </Angles>\n"
                    "  <Dose>\n"
                    "    1.0\n"
                    "    2.0\n"
                    "    3.0\n"
                    "  </Dose>\n"
                    "</TiltSeries>\n"
                ),
                encoding="utf-8",
            )
            thumbnail_path = root / "thumb.png"
            thumbnail_path.write_text("img", encoding="utf-8")

            dataset = DatasetRecord(
                dataset_name="DatasetA",
                sample="Sample 42",
                pixel_size=2.31,
                exposure=1.0,
                tomogram_x=1,
                tomogram_y=1,
                tomogram_z=1,
                raw_frames_folder="",
                mdocs_folder="",
                tilt_series_processing_folder=str(processing),
                thumbnails=[
                    ThumbnailRecord(
                        image_path=str(thumbnail_path),
                        ts_name="TS_01",
                        rating=4,
                        tags=["good", "membrane"],
                    )
                ],
            )
            project = ProjectData(datasets=[dataset])
            out_path = root / "annotations.csv"

            _export_ts_annotations_csv(str(out_path), project)

            with out_path.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))

            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["TS Name"], "TS_01")
            self.assertEqual(row["Dataset"], "DatasetA")
            self.assertEqual(row["Sample information"], "Sample 42")
            self.assertEqual(row["Pixel size"], "2.3100")
            self.assertEqual(row["CTF resolution estimate"], "6.80")
            self.assertEqual(row["Defocus value"], "2.10")
            self.assertEqual(row["Total dose"], "3.00")
            self.assertEqual(row["Rating"], "4")
            self.assertEqual(row["Tags"], "good, membrane")

    def test_history_exports_escape_active_content_and_include_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            entry = JobHistoryEntry(
                timestamp="2026-01-01T00:00:00+00:00",
                action="ran",
                group="Particles",
                job_name="<unsafe>",
                command="=HYPERLINK(\"bad\") <script>alert(1)</script>",
                status="failed",
                exit_code=2,
                app_version="1.0.0",
            )
            dataset = DatasetRecord(
                dataset_name="Dataset<script>",
                sample="Sample",
                pixel_size=1.0,
                exposure=1.0,
                tomogram_x=1,
                tomogram_y=1,
                tomogram_z=1,
                raw_frames_folder="",
                mdocs_folder="",
                job_history=[entry],
            )
            project = ProjectData(name="<Project>", datasets=[dataset])
            csv_path = root / "history.csv"
            html_path = root / "history.html"

            _export_history_csv(str(csv_path), project)
            _export_history_html(str(html_path), project)

            with csv_path.open(newline="", encoding="utf-8") as handle:
                row = next(csv.DictReader(handle))
            self.assertTrue(row["command"].startswith("'="))
            self.assertEqual(row["status"], "failed")
            self.assertEqual(row["exit_code"], "2")
            html_text = html_path.read_text(encoding="utf-8")
            self.assertNotIn("<script>", html_text)
            self.assertIn("&lt;Project&gt;", html_text)
            self.assertIn("<th>Status</th>", html_text)
            self.assertIn("1.0.0", html_text)


if __name__ == "__main__":
    unittest.main()
