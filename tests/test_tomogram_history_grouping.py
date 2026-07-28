from __future__ import annotations

import unittest
from types import SimpleNamespace

from cryoet_organizer.project import DatasetRecord, JobHistoryEntry, ProjectData
from cryoet_organizer.tabs.tomograms import TomogramsTab


def _dataset(name: str) -> DatasetRecord:
    return DatasetRecord(
        dataset_name=name,
        sample="Sample",
        pixel_size=1.0,
        exposure=1.0,
        tomogram_x=1,
        tomogram_y=1,
        tomogram_z=1,
        raw_frames_folder="",
        mdocs_folder="",
        processing_folder=f"/processing/{name}",
    )


def _tab(project: ProjectData) -> TomogramsTab:
    tab = TomogramsTab.__new__(TomogramsTab)
    tab.app = SimpleNamespace(project=project)
    tab.execution_mode_var = SimpleNamespace(get=lambda: "Run locally")
    tab.slurm_profile_var = SimpleNamespace(get=lambda: "")
    tab.environment_var = SimpleNamespace(get=lambda: "None")
    tab._current_slurm_overrides = lambda: {}
    return tab


class TomogramHistoryGroupingTests(unittest.TestCase):
    def test_grouped_history_entry_records_all_processed_ts(self) -> None:
        project = ProjectData()
        first = _dataset("DatasetA")
        second = _dataset("DatasetB")
        project.datasets = [first, second]
        tab = _tab(project)

        entry = tab._record_grouped_job_history(
            [
                (first, {"job_name": "PyTom: Template matching", "ts_name": "TS_01", "tomogram": "a.mrc"}, "cmd a"),
                (first, {"job_name": "PyTom: Template matching", "ts_name": "TS_02", "tomogram": "b.mrc"}, "cmd b"),
                (second, {"job_name": "PyTom: Template matching", "ts_name": "TS_03", "tomogram": "c.mrc"}, "cmd c"),
            ],
            "ran",
        )

        self.assertIsNotNone(entry)
        self.assertEqual(len(first.job_history), 1)
        self.assertEqual(len(second.job_history), 1)
        self.assertIs(first.job_history[0], second.job_history[0])
        self.assertEqual(entry.dataset_name, "Multiple datasets")
        self.assertEqual(entry.parameters["ts_name"], "3 TS selected")
        self.assertEqual(entry.parameters["tomogram"], "3 values")
        self.assertEqual(len(tab._history_entries()), 1)
        self.assertEqual(
            entry.artifacts["processed_ts"],
            [
                {"dataset_name": "DatasetA", "ts_name": "TS_01"},
                {"dataset_name": "DatasetA", "ts_name": "TS_02"},
                {"dataset_name": "DatasetB", "ts_name": "TS_03"},
            ],
        )
        self.assertTrue(tab._entry_matches_dataset_filter(entry, "DatasetB"))

    def test_loaded_grouped_history_duplicates_are_deduplicated_and_synced(self) -> None:
        project = ProjectData()
        first = _dataset("DatasetA")
        second = _dataset("DatasetB")
        entry_a = JobHistoryEntry(
            timestamp="2026-01-01T00:00:00+00:00",
            action="scheduled",
            group="Tomograms",
            job_name="PyTom: Template matching",
            command="cmd",
            dataset_name="Multiple datasets",
            parameters={"ts_name": "2 TS selected"},
            artifacts={
                "processed_ts": [
                    {"dataset_name": "DatasetA", "ts_name": "TS_01"},
                    {"dataset_name": "DatasetB", "ts_name": "TS_02"},
                ]
            },
            entry_id="shared-entry",
        )
        entry_b = JobHistoryEntry.from_dict(entry_a.to_dict())
        first.job_history.append(entry_a)
        second.job_history.append(entry_b)
        project.datasets = [first, second]
        tab = _tab(project)

        self.assertEqual(len(tab._history_entries()), 1)

        entry_a.action = "submitted"
        entry_a.slurm_job_id = "12345"
        tab._sync_history_entry_copies(entry_a)

        self.assertEqual(second.job_history[0].action, "submitted")
        self.assertEqual(second.job_history[0].slurm_job_id, "12345")

    def test_processed_ts_detail_rows_fall_back_for_legacy_entries(self) -> None:
        tab = _tab(ProjectData())
        legacy = JobHistoryEntry(
            timestamp="2026-01-01T00:00:00+00:00",
            action="ran",
            group="Tomograms",
            job_name="PyTom: Template matching",
            command="cmd",
            dataset_name="DatasetA",
            parameters={"ts_name": "TS_01"},
        )

        self.assertEqual(tab._processed_ts_detail_rows(legacy), [("DatasetA", "TS_01")])


if __name__ == "__main__":
    unittest.main()
