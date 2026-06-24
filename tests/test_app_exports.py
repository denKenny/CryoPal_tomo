from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from cryoet_organizer.app import _export_ts_annotations_csv
from cryoet_organizer.project import DatasetRecord, ProjectData, ThumbnailRecord
from cryoet_organizer.ts_metadata import clear_ts_metadata_cache


class TsAnnotationExportTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_ts_metadata_cache()

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


if __name__ == "__main__":
    unittest.main()
