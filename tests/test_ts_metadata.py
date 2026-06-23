from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
