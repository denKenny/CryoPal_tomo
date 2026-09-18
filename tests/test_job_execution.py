from __future__ import annotations

import unittest
from unittest.mock import patch

from cryoet_organizer.job_execution import (
    complete_history_entry,
    create_history_entry,
    execute_scheduled_history_entries,
    execution_environment_fingerprint,
)
from cryoet_organizer.slurm import SlurmSubmissionResult


class JobExecutionProvenanceTests(unittest.TestCase):
    def test_local_job_lifecycle_records_success_and_failure(self) -> None:
        succeeded = create_history_entry(
            action="ran",
            group="Processing",
            job_name="tool",
            command="tool --input data",
            working_directory="/data/project",
        )

        self.assertEqual(succeeded.status, "running")
        self.assertTrue(succeeded.started_at)
        self.assertEqual(succeeded.working_directory, "/data/project")
        self.assertTrue(succeeded.host)
        self.assertTrue(succeeded.app_version)

        complete_history_entry(succeeded, 0)
        self.assertEqual(succeeded.status, "succeeded")
        self.assertEqual(succeeded.exit_code, 0)
        self.assertTrue(succeeded.finished_at)

        failed = create_history_entry(action="ran", group="Processing", job_name="tool", command="false")
        complete_history_entry(failed, 7)
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.failure_reason, "Process exited with code 7")

        launch_failed = create_history_entry(action="ran", group="Processing", job_name="tool", command="missing")
        complete_history_entry(launch_failed, None, failure_reason="executable not found")
        self.assertEqual(launch_failed.status, "failed")
        self.assertIsNone(launch_failed.exit_code)
        self.assertEqual(launch_failed.failure_reason, "executable not found")

    def test_submitted_job_records_submission_time(self) -> None:
        entry = create_history_entry(action="submitted", group="Processing", job_name="tool", command="tool")

        self.assertEqual(entry.status, "submitted")
        self.assertTrue(entry.submitted_at)

    def test_environment_fingerprint_is_stable_and_does_not_embed_command(self) -> None:
        first = execution_environment_fingerprint("GPU", "source /env/activate")
        second = execution_environment_fingerprint("GPU", "source /env/activate")

        self.assertEqual(first, second)
        self.assertTrue(first.startswith("sha256:"))
        self.assertNotIn("activate", first)
        self.assertNotEqual(first, execution_environment_fingerprint("CPU", "source /env/activate"))

    def test_slurm_submission_does_not_claim_the_cluster_job_started(self) -> None:
        class Root:
            @staticmethod
            def after(_delay, callback) -> None:
                callback()

        class App:
            root = Root()

            @staticmethod
            def submit_slurm_command(*_args, **_kwargs) -> SlurmSubmissionResult:
                return SlurmSubmissionResult(job_id="42", script_path="job.sbatch", stdout="42")

            @staticmethod
            def abort_requested() -> bool:
                return False

            @staticmethod
            def is_debug_mode_enabled() -> bool:
                return False

        entry = create_history_entry(
            action="scheduled",
            group="Processing",
            job_name="tool",
            command="tool",
            scheduled=True,
            execution_mode="slurm",
        )
        submitted: list[str] = []

        with patch("cryoet_organizer.job_execution.threading.Thread") as thread_class:
            execute_scheduled_history_entries(
                App(),
                [entry],
                cwd="/data",
                dataset_name="DS",
                force_slurm=False,
                forced_profile="cluster",
                wait_for_slurm_completion=False,
                on_entry_started=lambda *_args: self.fail("Slurm job was marked as locally started"),
                on_entry_submitted=lambda _entry, timestamp, _result, _profile: submitted.append(timestamp),
                on_finished=lambda *_args: None,
            )
            thread_class.call_args.kwargs["target"]()

        self.assertEqual(entry.status, "submitted")
        self.assertEqual(entry.slurm_job_id, "42")
        self.assertTrue(entry.submitted_at)
        self.assertEqual(entry.started_at, "")
        self.assertEqual(submitted, [entry.submitted_at])

    def test_command_sequence_preserves_a_failed_history_entry(self) -> None:
        from cryoet_organizer.job_execution import execute_command_sequence

        class Root:
            @staticmethod
            def after(_delay, callback) -> None:
                callback()

        class App:
            root = Root()

            @staticmethod
            def start_managed_process_for_output(*_args, **_kwargs) -> object:
                return object()

            @staticmethod
            def wait_managed_process(_process) -> int:
                return 9

            @staticmethod
            def abort_requested() -> bool:
                return False

        entry = create_history_entry(
            action="scheduled",
            group="Custom",
            job_name="tool",
            command="tool",
            scheduled=True,
        )
        finished: list[tuple[int, list[str]]] = []

        with (
            patch("cryoet_organizer.job_execution.BatchCommandOutputWindow"),
            patch("cryoet_organizer.job_execution.threading.Thread") as thread_class,
        ):
            execute_command_sequence(
                App(),
                [{"command": "tool", "job_name": "tool", "history_entry": entry}],
                use_slurm=False,
                profile_name="",
                overrides=None,
                on_submitted=None,
                on_completed=None,
                on_finished=lambda count, failures: finished.append((count, failures)),
            )
            thread_class.call_args.kwargs["target"]()

        self.assertEqual(entry.action, "ran")
        self.assertEqual(entry.status, "failed")
        self.assertEqual(entry.exit_code, 9)
        self.assertTrue(entry.started_at)
        self.assertTrue(entry.finished_at)
        self.assertEqual(entry.failure_reason, "Process exited with code 9")
        self.assertEqual(finished, [(1, ["tool: exit code 9"])])


if __name__ == "__main__":
    unittest.main()
