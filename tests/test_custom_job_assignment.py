import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cryoet_organizer.custom_jobs import (
    CustomJobDefinition, CustomJobParameter, assigned_custom_jobs, custom_job_assignment_error,
    export_custom_jobs, import_custom_jobs, get_project_custom_jobs, set_project_custom_jobs, merge_custom_jobs,
    render_custom_context,
)
from cryoet_organizer.project import ProjectData, MPopulationRecord
from cryoet_organizer.settings_bundle import apply_settings_import, build_settings_export_payload
from cryoet_organizer.tabs.custom import CustomTab
from cryoet_organizer.tabs.custom_integration import CustomJobIntegration
from cryoet_organizer.workflow_catalog import build_workflow_job_catalog, workflow_job_from_catalog, fields_for_workflow_job
from cryoet_organizer.job_queue import iter_scheduled_job_refs


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class CustomJobAssignmentTests(unittest.TestCase):
    def test_context_substitution_preserves_shell_variables_and_does_not_recurse(self):
        self.assertEqual(render_custom_context("echo ${ts_name} {ts_name}", {"ts_name": "A {dataset_name}"}),
                         "echo ${ts_name} 'A {dataset_name}'")

    def test_embedded_runtime_is_created_once_and_reused_until_definition_changes(self):
        project = ProjectData()
        job = CustomJobDefinition("Example", target_tab="particles")
        set_project_custom_jobs(project, [job])
        values = ["Native"]
        combo = Mock()
        combo.cget.side_effect = lambda _key: tuple(values)
        combo.configure.side_effect = lambda **kw: values.__setitem__(slice(None), kw["values"]) if "values" in kw else None
        native = Mock()
        parent = Mock()
        parent.grid_slaves.return_value = [native]
        host = SimpleNamespace(tab_id="particles", app=SimpleNamespace(project=project),
                               job_type_var=Value("Example [Custom]"), job_type_combo=combo, content=parent)
        integration = CustomJobIntegration(host)
        with patch("cryoet_organizer.tabs.custom_runtime.EmbeddedCustomRuntime") as factory:
            self.assertTrue(integration.select())
            self.assertTrue(integration.select())
            factory.assert_called_once()
            factory.return_value._build_runtime_form.assert_called_once()
            job.description = "Updated"
            set_project_custom_jobs(project, [job])
            integration.select()
            self.assertEqual(factory.return_value._build_runtime_form.call_count, 2)
            host.job_type_var.set("Native")
            self.assertFalse(integration.select())
            factory.return_value.frame.grid_remove.assert_called()
            native.grid.assert_called()

    def test_legacy_definitions_keep_stable_identity_and_remain_unassigned(self):
        first = CustomJobDefinition.from_dict({"name": "Legacy"})
        second = CustomJobDefinition.from_dict({"name": "Legacy"})
        self.assertEqual(first.job_id, second.job_id)
        self.assertEqual(first.target_tab, "")
        self.assertFalse(custom_job_assignment_error(first.target_tab, first.target_group))

    def test_group_validation_and_filtering(self):
        project = ProjectData()
        jobs = [CustomJobDefinition("Warp", target_tab="processing", target_group="frame_series"),
                CustomJobDefinition("M", target_tab="processing_m", target_group="mcore"),
                CustomJobDefinition("TS", target_tab="tomograms"),
                CustomJobDefinition("Particle", target_tab="particles"),
                CustomJobDefinition("Broken", target_tab="processing", target_group="missing")]
        set_project_custom_jobs(project, jobs)
        for tab, group, name in [("processing", "Frame series", "Warp"), ("processing_m", "MCore", "M"),
                                 ("tomograms", "", "TS"), ("particles", "", "Particle")]:
            self.assertEqual([job.name for job in assigned_custom_jobs(project, tab, group)], [name])
        self.assertTrue(custom_job_assignment_error("processing", ""))
        self.assertTrue(custom_job_assignment_error("particles", "mcore"))
        self.assertEqual(len(get_project_custom_jobs(project)), 5)

    def test_assignment_survives_file_and_settings_export_import(self):
        project = ProjectData()
        job = CustomJobDefinition("Example", target_tab="processing_m", target_group="mtools")
        set_project_custom_jobs(project, [job])
        with tempfile.TemporaryDirectory() as folder:
            path = export_custom_jobs(Path(folder) / "jobs", [job])
            self.assertEqual(import_custom_jobs(path), [job])
        payload = build_settings_export_payload(project, ["custom_job_types::Example"])
        restored = ProjectData()
        apply_settings_import(restored, payload, ["custom_job_types::Example"], overwrite_existing=True)
        self.assertEqual(get_project_custom_jobs(restored), [job])

    def test_imported_copy_has_distinct_id_and_same_assignment(self):
        job = CustomJobDefinition("Example", target_tab="particles")
        merged = merge_custom_jobs([job], [job])
        self.assertNotEqual(merged[0].job_id, merged[1].job_id)
        self.assertEqual(merged[1].target_tab, "particles")

    def runtime(self, job, host):
        tab = CustomTab.__new__(CustomTab)
        tab.current_job = job
        tab.execution_host = host
        tab.app = SimpleNamespace(project=ProjectData(), tabs={})
        tab.runtime_state = {}
        tab._dataset_map = lambda: {}
        return tab

    def test_m_population_context_and_history_owner(self):
        population = MPopulationRecord("Pop", "/tmp/my population", "/tmp/my population/p.population")
        job = CustomJobDefinition("M custom", command_template="tool --population {population_file}",
                                  target_tab="processing_m", target_group="mtools")
        tab = self.runtime(job, SimpleNamespace(current_population=population))
        tab.app.project.m_populations.append(population)
        commands, errors = tab._build_commands()
        self.assertFalse(errors)
        self.assertEqual(commands[0][2], "tool --population '/tmp/my population/p.population'")
        tab.execution_mode_var = Value("Run locally")
        tab.slurm_profile_var = Value("")
        tab.environment_var = Value("None")
        tab._current_slurm_overrides = lambda: {}
        entry = tab._record_history("", "", job.name, commands[0][2], {}, "scheduled")
        self.assertIs(population.job_history[0], entry)
        self.assertEqual(entry.processing_tab, "Processing: M")
        self.assertEqual(entry.group, "MTools")
        self.assertEqual(entry.artifacts["custom_job_id"], job.job_id)
        self.assertEqual(len(iter_scheduled_job_refs(tab.app.project)), 1)

    def test_missing_context_does_not_produce_executable_commands(self):
        job = CustomJobDefinition("M custom", command_template="tool", target_tab="processing_m", target_group="mcore")
        tab = self.runtime(job, SimpleNamespace(current_population=None))
        commands, errors = tab._build_commands()
        self.assertFalse(commands)
        self.assertTrue(errors)

    def test_ts_assignment_uses_selection_even_without_ts_file_parameter(self):
        job = CustomJobDefinition("TS custom", command_template="tool {ts_name}", target_tab="tomograms")
        tab = self.runtime(job, SimpleNamespace())
        tab._global_ts_entries = lambda: [{"dataset_name": "D", "ts_name": "TS_2"}, {"dataset_name": "D", "ts_name": "TS_1"}]
        tab._dataset_map = lambda: {"D": SimpleNamespace(processing_folder="/tmp/D")}
        commands, errors = tab._build_commands()
        self.assertFalse(errors)
        self.assertEqual([command for _, _, command in commands], ["tool TS_2", "tool TS_1"])

    def test_selector_refresh_preserves_builtins_and_does_not_duplicate(self):
        project = ProjectData()
        set_project_custom_jobs(project, [CustomJobDefinition("Example", target_tab="particles")])
        values = ["Built-in", "Job history"]
        combo = Mock()
        combo.cget.side_effect = lambda _key: tuple(values)
        def configure(**kwargs):
            if "values" in kwargs:
                values[:] = kwargs["values"]
        combo.configure.side_effect = configure
        host = SimpleNamespace(tab_id="particles", app=SimpleNamespace(project=project),
                               job_type_var=Value(""), job_type_combo=combo)
        integration = CustomJobIntegration(host)
        integration.refresh()
        integration.refresh()
        self.assertEqual(values, ["Built-in", "Job history", "Example [Custom]"])
        set_project_custom_jobs(project, [])
        integration.refresh()
        self.assertEqual(values, ["Built-in", "Job history"])

    def test_workflow_retains_custom_identity_after_rename(self):
        project = ProjectData()
        project.m_populations.append(MPopulationRecord("Pop"))
        job = CustomJobDefinition("Example", target_tab="processing_m", target_group="mcore",
                                  parameters=[CustomJobParameter("x", "Value", "--x")])
        set_project_custom_jobs(project, [job])
        catalog = next(item for item in build_workflow_job_catalog(project) if item.namespace == "Custom")
        workflow_job = workflow_job_from_catalog(project, catalog)
        self.assertEqual(workflow_job["owner_kind"], "m_population")
        job.name = "Renamed"
        set_project_custom_jobs(project, [job])
        self.assertEqual(fields_for_workflow_job(project, workflow_job)[1].key, "x")
