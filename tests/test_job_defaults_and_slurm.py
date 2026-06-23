from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cryoet_organizer.job_defaults import (
    export_settings_payload,
    get_project_job_default_overrides,
    set_project_job_default_overrides,
)
from cryoet_organizer.project import ProjectData
from cryoet_organizer.slurm import (
    SlurmProfile,
    get_project_slurm_profiles,
    render_sbatch_script,
    set_project_slurm_profiles,
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


if __name__ == "__main__":
    unittest.main()
