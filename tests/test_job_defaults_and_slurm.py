from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryoet_organizer.job_defaults import (
    export_settings_payload,
    get_project_job_default_overrides,
    set_project_job_default_overrides,
)
from cryoet_organizer.job_execution import slurm_override_payload
from cryoet_organizer.project import ProjectData
from cryoet_organizer.slurm import (
    SlurmHeaderField,
    SlurmError,
    SlurmProfile,
    cancel_slurm_job,
    get_project_slurm_profiles,
    render_sbatch_script,
    set_project_slurm_profiles,
    submit_sbatch_script,
    wait_for_slurm_job,
    write_sbatch_script,
)


class JobDefaultsAndSlurmTests(unittest.TestCase):
    def test_job_default_overrides_use_typed_project_state(self) -> None:
        project = ProjectData()
        overrides = {
            "Particles/Export particles/ts_export_particles": {
                "box_size": {"enabled": "true", "value": "128"}
            }
        }
        set_project_job_default_overrides(project, overrides)

        self.assertEqual(get_project_job_default_overrides(project), overrides)
        self.assertIn("Particles/Export particles/ts_export_particles", project.state.job_default_overrides)

        payload = export_settings_payload(project, overrides)
        self.assertIn("job_default_overrides", payload)
        self.assertIn("file_registry_patterns", payload)

    def test_slurm_profiles_use_typed_project_state_and_render(self) -> None:
        project = ProjectData()
        profiles = [SlurmProfile(name="GPU", partition="gpu", gpus="1", time_limit="01:00:00")]
        set_project_slurm_profiles(project, profiles)

        restored = get_project_slurm_profiles(project)
        self.assertEqual(restored[0].name, "GPU")

        script = render_sbatch_script(
            command="echo hello",
            profile=restored[0],
            cwd="/tmp/work",
            dataset_name="DS",
            job_name="job",
        )
        self.assertIn("#SBATCH --partition=gpu", script)
        self.assertIn("#SBATCH --gres=gpu:1", script)
        self.assertIn("cd /tmp/work", script)

    def test_encoded_slurm_header_overrides_render_as_sbatch_values(self) -> None:
        profile = SlurmProfile(
            name="GPU",
            header_fields=[
                SlurmHeaderField(key="partition", flag="--partition", description="partition", value=""),
                SlurmHeaderField(key="gres", flag="--gres", description="GPU resources", value=""),
                SlurmHeaderField(key="job_name", flag="-J", description="job name", value="{job_name}"),
            ],
        )

        script = render_sbatch_script(
            command="echo hello",
            profile=profile,
            cwd=None,
            dataset_name="DS",
            job_name="my_job",
            overrides=slurm_override_payload(
                {
                    "slurm_header__partition": "gpu-long",
                    "slurm_header__gres": "gpu:2",
                }
            ),
        )

        self.assertIn("#SBATCH --partition=gpu-long", script)
        self.assertIn("#SBATCH --gres=gpu:2", script)
        self.assertIn("#SBATCH -J my_job", script)

    def test_slurm_header_rejects_newline_injection(self) -> None:
        profile = SlurmProfile(
            name="unsafe",
            header_fields=[SlurmHeaderField(key="partition", flag="--partition", value="gpu\n#SBATCH --qos=admin")],
        )
        with self.assertRaisesRegex(SlurmError, "single line"):
            render_sbatch_script("echo safe", profile, None, "DS", "job")

    def test_slurm_header_rejects_invalid_template_and_script_write_is_exclusive(self) -> None:
        profile = SlurmProfile(
            name="template",
            header_fields=[SlurmHeaderField(key="name", flag="--job-name", value="{unknown}")],
        )
        with self.assertRaisesRegex(SlurmError, "Invalid Slurm header template"):
            render_sbatch_script("echo safe", profile, None, "DS", "job")

        profile.header_fields[0].value = "{job_name}"
        with tempfile.TemporaryDirectory() as tmpdir:
            first = write_sbatch_script(tmpdir, "echo safe", profile, None, "DS", "job")
            second = write_sbatch_script(tmpdir, "echo safe", profile, None, "DS", "job")

            self.assertNotEqual(first, second)
            self.assertTrue(first.is_file())
            self.assertTrue(second.is_file())

    @patch("cryoet_organizer.slurm.subprocess.run")
    def test_submit_uses_parsable_job_id_and_rejects_missing_id(self, run) -> None:
        run.return_value.stdout = "12345;cluster\n"
        result = submit_sbatch_script("job.sbatch")
        self.assertEqual(result.job_id, "12345")
        self.assertEqual(run.call_args.args[0][:2], ["sbatch", "--parsable"])

        run.return_value.stdout = "submission accepted\n"
        with self.assertRaisesRegex(SlurmError, "valid job ID"):
            submit_sbatch_script("job.sbatch")

    def test_wait_rejects_empty_job_id_without_polling(self) -> None:
        with patch("cryoet_organizer.slurm.query_slurm_job_state") as query:
            self.assertEqual(wait_for_slurm_job(""), (False, "INVALID_JOB_ID"))
        query.assert_not_called()

    @patch("cryoet_organizer.slurm.subprocess.run")
    def test_cancel_slurm_job_validates_identifier(self, run) -> None:
        cancel_slurm_job("12345")
        run.assert_called_once_with(["scancel", "12345"], check=True, capture_output=True, text=True)

        with self.assertRaisesRegex(SlurmError, "Invalid Slurm job ID"):
            cancel_slurm_job("123; touch unsafe")

    def test_wait_has_overall_deadline(self) -> None:
        with (
            patch("cryoet_organizer.slurm.query_slurm_job_state", return_value="RUNNING"),
            patch("cryoet_organizer.slurm.time.monotonic", side_effect=[0.0, 2.0]),
        ):
            self.assertEqual(
                wait_for_slurm_job("123", poll_interval_seconds=0, timeout_seconds=1),
                (False, "WAIT_TIMEOUT"),
            )


if __name__ == "__main__":
    unittest.main()
