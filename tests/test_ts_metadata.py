from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cryoet_organizer.project import DatasetRecord, ProjectData
from cryoet_organizer.ts_metadata import clear_ts_metadata_cache, collect_ts_metadata


class TsMetadataCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_ts_metadata_cache()

    def test_cache_reuses_entries_until_invalidated(self) -> None:
        dataset = DatasetRecord(
            dataset_name="DS",
            sample="S",
            pixel_size=1.5,
            exposure=2.0,
            tomogram_x=1,
            tomogram_y=1,
            tomogram_z=1,
            raw_frames_folder="",
            mdocs_folder="",
        )
        project = ProjectData(datasets=[dataset])

        first = collect_ts_metadata(project, dataset, "TS_01")
        second = collect_ts_metadata(project, dataset, "TS_01")
        self.assertIs(first, second)

        clear_ts_metadata_cache(dataset.dataset_name, "TS_01")
        third = collect_ts_metadata(project, dataset, "TS_01")
        self.assertIsNot(first, third)

    def test_xml_defocus_value_is_exposed_in_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            processing = root / "processing"
            processing.mkdir()
            xml_path = processing / "TS_01.xml"
            xml_path.write_text(
                (
                    "<TiltSeries>\n"
                    "  <Angles>\n"
                    "    -10\n"
                    "    10\n"
                    "  </Angles>\n"
                    "  <Dose>\n"
                    "    1.5\n"
                    "    3.0\n"
                    "  </Dose>\n"
                    "  <AxisAngle>86.6</AxisAngle>\n"
                    "  <CTF>\n"
                    '    <Param Name="CTFResolutionEstimate" Value="7.2" />\n'
                    '    <Param Name="Defocus" Value="2.35" />\n'
                    "  </CTF>\n"
                    "</TiltSeries>\n"
                ),
                encoding="utf-8",
            )

            dataset = DatasetRecord(
                dataset_name="DS",
                sample="S",
                pixel_size=1.5,
                exposure=2.0,
                tomogram_x=1,
                tomogram_y=1,
                tomogram_z=1,
                raw_frames_folder="",
                mdocs_folder="",
                tilt_series_processing_folder=str(processing),
            )
            project = ProjectData(datasets=[dataset])

            metadata = collect_ts_metadata(project, dataset, "TS_01")

            self.assertEqual(metadata.defocus_value, 2.35)
            self.assertEqual(metadata.defocus_min, 2.35)
            self.assertEqual(metadata.defocus_max, 2.35)
            self.assertEqual(metadata.ctf_resolution_estimate, 7.2)
            self.assertEqual(metadata.axis_angle, 86.6)


if __name__ == "__main__":
    unittest.main()
