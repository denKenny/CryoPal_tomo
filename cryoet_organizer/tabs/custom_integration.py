"""Lazy embedding of the shared Custom job runtime in processing tabs."""
from tkinter import messagebox

from cryoet_organizer.custom_jobs import assigned_custom_jobs, get_project_custom_jobs


class CustomJobIntegration:
    def __init__(self, host):
        self.host = host
        self.runtime = None
        self.hidden = []
        self.jobs = {}
        self.signature = None
        self.variable = host.job_var if host.tab_id in {"processing", "processing_m"} else host.job_type_var
        self.combo = host.job_combo if host.tab_id in {"processing", "processing_m"} else host.job_type_combo
        self.combo.configure(postcommand=self.refresh)

    def refresh(self):
        host = self.host
        group = host.group_var.get() if host.tab_id == "processing" else host.job_group_var.get() if host.tab_id == "processing_m" else ""
        native = [value for value in self.combo.cget("values") if value not in self.jobs]
        jobs = assigned_custom_jobs(host.app.project, host.tab_id, group)
        previous = self.jobs
        self.jobs = {}
        for job in jobs:
            label = f"{job.name} [Custom]"
            if label in native or label in self.jobs:
                label = f"{label} ({job.job_id})"
            self.jobs[label] = job
        self.combo.configure(values=(*native, *self.jobs))
        if self.jobs:
            self.combo.configure(state="readonly")
        if self.variable.get() in previous and self.variable.get() not in self.jobs:
            self.variable.set("")
            self.hide()

    def hide(self):
        if self.runtime is not None:
            self.runtime.frame.grid_remove()
        for widget in self.hidden:
            widget.grid()
        self.hidden = []

    def select(self):
        self.refresh()
        job = self.jobs.get(self.variable.get())
        if job is None:
            self.hide()
            return False
        host = self.host
        if host.tab_id in {"processing", "processing_m"}:
            parent, row = host.parameters_pane_frame, 0
            host.current_job = None
            host.processing_pane.set_section_visible("parameters", True)
        elif host.tab_id == "tomograms":
            parent, row = host.workflow_bottom_frame, 0
        else:
            parent, row = host.content, 1
        if self.runtime is None:
            from cryoet_organizer.tabs.custom_runtime import EmbeddedCustomRuntime
            self.runtime = EmbeddedCustomRuntime(host, parent)
        for widget in parent.grid_slaves(row=row):
            if widget != self.runtime.frame and widget not in self.hidden:
                self.hidden.append(widget)
                widget.grid_remove()
        signature = (id(host.app.project), repr(job.to_dict()),
                     getattr(getattr(host, "current_dataset", None), "dataset_name", ""),
                     getattr(getattr(host, "current_population", None), "name", ""))
        if signature != self.signature:
            self.runtime.current_job = job
            self.runtime.environment_var.set(job.environment_title)
            self.runtime._refresh_slurm_profiles()
            self.runtime._build_runtime_form(job)
            self.signature = signature
        self.runtime.frame.grid(row=row, column=0, sticky="nsew")
        self.runtime._show_runtime()
        return True

    def copy_history(self, entry):
        job_id = entry.artifacts.get("custom_job_id")
        if not job_id:
            return False
        job = next((job for job in get_project_custom_jobs(self.host.app.project) if job.job_id == job_id), None)
        if job is None:
            messagebox.showinfo("Copy job parameters", "This custom job definition is no longer available.")
            return True
        from cryoet_organizer.custom_jobs import custom_job_groups
        group = custom_job_groups(job.target_tab).get(job.target_group, "")
        host = self.host
        if host.tab_id == "processing":
            host.group_var.set(group)
            host._on_group_selected()
        elif host.tab_id == "processing_m":
            host.job_group_var.set(group)
            host._on_group_selected()
        self.refresh()
        label = next((label for label, item in self.jobs.items() if item.job_id == job_id), None)
        if label is None:
            messagebox.showinfo("Copy job parameters", "This custom job has been reassigned. Open it in Processing: Custom jobs.")
            return True
        self.variable.set(label)
        self.select()
        self.runtime._suspend_custom_preview = True
        try:
            for parameter in job.parameters:
                for key, variable in self.runtime.runtime_state.get(parameter.key, {}).items():
                    value = str(entry.parameters.get(f"custom:{parameter.key}:{key}", entry.parameters.get(parameter.key, "")))
                    variable.set(value.lower() in {"true", "1", "yes", "on"} if parameter.widget == "bool" else value)
        finally:
            self.runtime._suspend_custom_preview = False
        self.runtime.environment_var.set(entry.environment_title or "None")
        if host.tab_id == "tomograms" and messagebox.askyesno("Update TS processing list?", "Update TS processing list?"):
            host.selected_entries = list(entry.artifacts.get("processed_ts", []))
            host._persist_selection()
            host._refresh_table()
        self.runtime._update_preview()
        return True
