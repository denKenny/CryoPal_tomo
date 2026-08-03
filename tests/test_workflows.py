from __future__ import annotations

import unittest

from cryoet_organizer.job_execution import create_history_entry
from cryoet_organizer.job_queue import ScheduledJobRef, iter_scheduled_job_refs
from cryoet_organizer.project import DatasetRecord, ProjectData
from cryoet_organizer.workflow_catalog import build_workflow_job_catalog, workflow_job_from_catalog
from cryoet_organizer.settings_bundle import apply_settings_import, build_settings_export_payload, importable_settings_groups
from cryoet_organizer.workflows import (
    create_workflow_from_refs,
    save_workflow,
    schedule_workflow,
    update_command_from_parameter_changes,
)


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


def _ref(dataset: DatasetRecord, job_name: str, command: str) -> ScheduledJobRef:
    entry = create_history_entry(
        action="scheduled",
        group="Frame series",
        job_name=job_name,
        command=command,
        processing_tab="Processing: WARP",
        dataset_name=dataset.dataset_name,
        parameters={"--input": "old.star", "--angpix": "10"},
        scheduled=True,
    )
    entry.slurm_job_id = "12345"
    entry.slurm_script_path = "/tmp/old.sbatch"
    entry.artifacts["queue_order"] = 42
    dataset.job_history.append(entry)
    return ScheduledJobRef(entry, dataset, "dataset", dataset.dataset_name, dataset.processing_folder)


class WorkflowTests(unittest.TestCase):
    def test_catalog_add_job_builds_builtin_workflow_job_with_defaults(self) -> None:
        project = ProjectData()
        project.datasets.append(_dataset("ds1"))
        catalog_job = next(
            job
            for job in build_workflow_job_catalog(project)
            if job.processing_tab == "Processing: WARP" and job.job_key == "create_settings"
        )

        workflow_job = workflow_job_from_catalog(project, catalog_job)

        self.assertEqual(workflow_job["owner_kind"], "dataset")
        self.assertEqual(workflow_job["owner_name"], "ds1")
        self.assertIn("WarpTools create_settings", workflow_job["entry"]["command"])
        self.assertEqual(workflow_job["entry"]["artifacts"]["workflow_catalog_job_key"], "create_settings")

    def test_catalog_includes_custom_jobs(self) -> None:
        project = ProjectData()
        project.datasets.append(_dataset("ds1"))
        project.state.custom_job_types = [
            {
                "name": "my_custom",
                "command_template": "python custom.py",
                "parameters": [
                    {"key": "input", "label": "Input", "flag": "--input", "widget": "path", "default": "in.star"}
                ],
            }
        ]

        catalog_job = next(job for job in build_workflow_job_catalog(project) if job.title == "my_custom")
        workflow_job = workflow_job_from_catalog(project, catalog_job)

        self.assertEqual(workflow_job["entry"]["processing_tab"], "Processing: Custom jobs")
        self.assertEqual(workflow_job["entry"]["parameters"]["input"], "in.star")
        self.assertEqual(workflow_job["entry"]["command"], "python custom.py --input in.star")

    def test_workflow_round_trips_through_project_state(self) -> None:
        project = ProjectData()
        dataset = _dataset("ds1")
        ref = _ref(dataset, "fs_motion", "WarpTools fs_motion --input old.star --angpix 10")

        workflow = save_workflow(project, create_workflow_from_refs([ref], "Motion workflow"))
        restored = ProjectData.from_dict(project.to_dict())

        self.assertEqual(restored.state.workflows[0]["name"], "Motion workflow")
        payload = restored.state.workflows[0]["jobs"][0]["entry"]
        self.assertEqual(payload["slurm_job_id"], "")
        self.assertNotIn("queue_order", payload["artifacts"])
        self.assertEqual(workflow["workflow_id"], restored.state.workflows[0]["workflow_id"])

    def test_schedule_workflow_creates_fresh_scheduled_entries_in_order(self) -> None:
        project = ProjectData()
        dataset = _dataset("ds1")
        project.datasets.append(dataset)
        first = _ref(dataset, "first", "echo first")
        second = _ref(dataset, "second", "echo second")
        workflow = create_workflow_from_refs([first, second], "Two step")
        dataset.job_history = []

        created = schedule_workflow(project, workflow)
        scheduled = iter_scheduled_job_refs(project)

        self.assertEqual([entry.job_name for entry in created], ["first", "second"])
        self.assertEqual([ref.entry.job_name for ref in scheduled], ["first", "second"])
        self.assertNotEqual(created[0].entry_id, first.entry.entry_id)
        self.assertEqual(created[0].action, "scheduled")
        self.assertEqual(created[0].slurm_job_id, "")

    def test_schedule_workflow_is_atomic_when_owner_is_missing(self) -> None:
        project = ProjectData()
        dataset = _dataset("ds1")
        ref = _ref(dataset, "first", "echo first")
        workflow = create_workflow_from_refs([ref], "Missing owner")

        with self.assertRaises(ValueError):
            schedule_workflow(project, workflow)

        self.assertEqual(project.datasets, [])

    def test_parameter_changes_update_flag_values_in_command(self) -> None:
        command = "WarpTools fs_motion --input old.star --angpix 10"
        updated = update_command_from_parameter_changes(
            command,
            {"--input": "old.star", "--angpix": "10"},
            {"--input": "new.star", "--angpix": "12"},
        )

        self.assertEqual(updated, "WarpTools fs_motion --input new.star --angpix 12")

    def test_settings_bundle_exports_and_imports_workflows(self) -> None:
        source = ProjectData()
        dataset = _dataset("ds1")
        ref = _ref(dataset, "fs_motion", "WarpTools fs_motion --input old.star")
        workflow = save_workflow(source, create_workflow_from_refs([ref], "Motion workflow"))
        workflow_key = f"workflows::{workflow['workflow_id']}"

        payload = build_settings_export_payload(source, [workflow_key])
        groups = importable_settings_groups(payload)

        self.assertIn("workflows", payload["categories"])
        self.assertTrue(any(group.key == "workflows" for group in groups))

        target = ProjectData()
        applied, skipped = apply_settings_import(
            target,
            payload,
            [workflow_key],
            overwrite_existing=True,
        )

        self.assertEqual(applied, [workflow_key])
        self.assertEqual(skipped, [])
        self.assertEqual(target.state.workflows[0]["name"], "Motion workflow")
        self.assertEqual(target.state.workflows[0]["jobs"][0]["entry"]["job_name"], "fs_motion")

    def test_settings_bundle_skips_existing_workflow_without_overwrite(self) -> None:
        source = ProjectData()
        target = ProjectData()
        dataset = _dataset("ds1")
        ref = _ref(dataset, "fs_motion", "WarpTools fs_motion --input old.star")
        workflow = save_workflow(source, create_workflow_from_refs([ref], "Motion workflow"))
        save_workflow(target, workflow)
        workflow_key = f"workflows::{workflow['workflow_id']}"
        payload = build_settings_export_payload(source, [workflow_key])

        applied, skipped = apply_settings_import(
            target,
            payload,
            [workflow_key],
            overwrite_existing=False,
        )

        self.assertEqual(applied, [])
        self.assertEqual(skipped, [workflow_key])
        self.assertEqual(len(target.state.workflows), 1)


if __name__ == "__main__":
    unittest.main()
