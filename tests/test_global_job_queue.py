from __future__ import annotations

import unittest

from cryoet_organizer.job_execution import create_history_entry
from cryoet_organizer.job_queue import (
    assign_queue_order,
    iter_job_history_refs,
    iter_scheduled_job_refs,
    remove_job_ref_from_project,
    remove_scheduled_ref,
)
from cryoet_organizer.project import DatasetRecord, MPopulationRecord, ProjectData


def _dataset(name: str) -> DatasetRecord:
    return DatasetRecord(
        dataset_name=name,
        sample="",
        pixel_size=0,
        exposure=0,
        tomogram_x=0,
        tomogram_y=0,
        tomogram_z=0,
        raw_frames_folder="",
        mdocs_folder="",
        processing_folder=f"/tmp/{name}",
    )


class GlobalJobQueueTests(unittest.TestCase):
    def test_iterates_all_history_entries_for_global_job_list(self) -> None:
        project = ProjectData()
        dataset = _dataset("ds1")
        dataset.job_history.extend(
            [
                create_history_entry(action="ran", group="", job_name="finished", command="true"),
                create_history_entry(action="scheduled", group="", job_name="queued", command="echo queued", scheduled=True),
            ]
        )
        project.datasets.append(dataset)

        refs = iter_job_history_refs(project)

        self.assertEqual([ref.entry.job_name for ref in refs], ["queued", "finished"])

    def test_default_global_order_places_recent_completed_jobs_first_after_scheduled(self) -> None:
        project = ProjectData()
        dataset = _dataset("ds1")
        older = create_history_entry(action="ran", group="", job_name="older", command="true")
        older.timestamp = "2026-08-03T10:00:00+00:00"
        newer = create_history_entry(action="ran", group="", job_name="newer", command="true")
        newer.timestamp = "2026-08-03T11:00:00+00:00"
        queued = create_history_entry(action="scheduled", group="", job_name="queued", command="echo queued", scheduled=True)
        queued.artifacts["queue_order"] = 1
        dataset.job_history.extend([older, newer, queued])
        project.datasets.append(dataset)

        refs = iter_job_history_refs(project)

        self.assertEqual([ref.entry.job_name for ref in refs], ["queued", "newer", "older"])

    def test_global_refs_dedupe_shared_grouped_history_entries(self) -> None:
        project = ProjectData()
        first = _dataset("ds1")
        second = _dataset("ds2")
        entry = create_history_entry(action="scheduled", group="", job_name="grouped", command="echo grouped", scheduled=True)
        first.job_history.append(entry)
        second.job_history.append(entry)
        project.datasets.extend([first, second])

        refs = iter_job_history_refs(project)
        remove_job_ref_from_project(project, refs[0])

        self.assertEqual([ref.entry.job_name for ref in refs], ["grouped"])
        self.assertEqual(iter_job_history_refs(project), [])

    def test_iterates_scheduled_dataset_and_m_population_jobs(self) -> None:
        project = ProjectData()
        dataset = _dataset("ds1")
        population = MPopulationRecord(name="pop1", directory="/tmp/pop1")
        dataset_entry = create_history_entry(
            action="scheduled",
            group="Frame series",
            job_name="fs_motion",
            command="WarpTools fs_motion",
            processing_tab="Processing: WARP",
            dataset_name="ds1",
            scheduled=True,
        )
        dataset_entry.artifacts["queue_order"] = 1
        dataset.job_history.append(dataset_entry)
        dataset.job_history.append(
            create_history_entry(action="ran", group="Frame series", job_name="done", command="true")
        )
        population_entry = create_history_entry(
            action="scheduled",
            group="MTools",
            job_name="create_source",
            command="MTools create_source",
            processing_tab="Processing: M",
            dataset_name="pop1",
            scheduled=True,
        )
        population_entry.artifacts["queue_order"] = 2
        population.job_history.append(population_entry)
        project.datasets.append(dataset)
        project.m_populations.append(population)

        refs = iter_scheduled_job_refs(project)

        self.assertEqual([ref.entry.job_name for ref in refs], ["fs_motion", "create_source"])
        self.assertEqual([ref.owner_kind for ref in refs], ["dataset", "m_population"])
        self.assertEqual([ref.cwd for ref in refs], ["/tmp/ds1", "/tmp/pop1"])

    def test_queue_order_metadata_controls_global_order(self) -> None:
        project = ProjectData()
        dataset = _dataset("ds1")
        first = create_history_entry(action="scheduled", group="", job_name="first", command="a", scheduled=True)
        second = create_history_entry(action="scheduled", group="", job_name="second", command="b", scheduled=True)
        first.artifacts["queue_order"] = 2
        second.artifacts["queue_order"] = 1
        dataset.job_history.extend([first, second])
        project.datasets.append(dataset)

        refs = iter_scheduled_job_refs(project)

        self.assertEqual([ref.entry.job_name for ref in refs], ["second", "first"])

    def test_assign_queue_order_and_remove_ref_update_backing_history(self) -> None:
        project = ProjectData()
        dataset = _dataset("ds1")
        dataset.job_history.extend(
            [
                create_history_entry(action="scheduled", group="", job_name="a", command="a", scheduled=True),
                create_history_entry(action="scheduled", group="", job_name="b", command="b", scheduled=True),
            ]
        )
        project.datasets.append(dataset)
        refs = iter_scheduled_job_refs(project)

        assign_queue_order(list(reversed(refs)))
        ordered = iter_scheduled_job_refs(project)
        removed_id = ordered[0].entry_id
        remove_scheduled_ref(ordered[0])

        remaining = iter_scheduled_job_refs(project)
        self.assertEqual(len(remaining), 1)
        self.assertNotEqual(remaining[0].entry_id, removed_id)


if __name__ == "__main__":
    unittest.main()
