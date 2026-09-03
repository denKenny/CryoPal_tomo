from __future__ import annotations

import tkinter as tk
from copy import deepcopy
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from cryoet_organizer.environments import environment_titles
from cryoet_organizer.job_queue import ScheduledJobRef
from cryoet_organizer.performance import TkDebouncer, perf_timer
from cryoet_organizer.workflows import (
    create_workflow_from_refs,
    normalize_workflow,
    project_workflows,
    save_workflow,
    schedule_workflow,
    update_command_from_parameter_changes,
    workflow_label,
)
from cryoet_organizer.workflow_catalog import (
    WorkflowCatalogJob,
    build_workflow_job_catalog,
    command_from_catalog_job,
    fields_for_workflow_job,
    owner_options_for_kind,
    rebuild_workflow_job_command,
    workflow_job_from_catalog,
)


class WorkflowEditorDialog:
    def __init__(
        self,
        app,
        parent: tk.Misc,
        *,
        initial_refs: list[ScheduledJobRef] | None = None,
        selected_refs_provider: Callable[[], list[ScheduledJobRef]] | None = None,
        host: tk.Misc | None = None,
    ) -> None:
        self.app = app
        self.parent = parent
        self.embedded = host is not None
        self.selected_refs_provider = selected_refs_provider
        self.workflows = [deepcopy(workflow) for workflow in project_workflows(app.project)]
        self._saved_snapshot = self._normalized_local_workflows()
        with perf_timer("workflow editor build catalog"):
            self.catalog_jobs = build_workflow_job_catalog(app.project)
        self.current_index = -1
        self.current_job_id: str | None = None
        self.name_var = tk.StringVar()
        self.detail_cache: dict[str, dict[str, Any]] = {}
        self.field_cache: dict[str, tuple[Any, ...]] = {}
        self.environment_title_options = environment_titles(app.project)
        self.active_detail: dict[str, Any] | None = None
        self.current_job_dirty = False
        self._loading_job_details = False
        self._suppress_job_selection = False
        self._pending_job_selection_after: str | None = None
        self._preload_after: str | None = None
        self._preload_generation = 0

        if initial_refs:
            self.workflows.append(create_workflow_from_refs(initial_refs, self._default_workflow_name()))
            self.current_index = len(self.workflows) - 1
        elif self.workflows:
            self.current_index = 0

        if host is None:
            self.window = tk.Toplevel(parent)
            self.window.title("Workflows")
            self.window.geometry("1180x740")
            self.window.minsize(980, 600)
            self.window.transient(parent.winfo_toplevel())
            self.window.grab_set()
            self.window.protocol("WM_DELETE_WINDOW", self._close_without_saving)
        else:
            self.window = host
        self.window.columnconfigure(0, weight=0)
        self.window.columnconfigure(1, weight=1)
        self.window.rowconfigure(0, weight=1)
        self.window.rowconfigure(1, weight=0)

        with perf_timer("workflow editor initial layout"):
            self._build()
            self._refresh_workflow_list()
            self._load_current_workflow()

    def show(self) -> None:
        if not self.embedded:
            self.window.wait_window()

    def _cancel_preload(self) -> None:
        if self._preload_after is None:
            return
        try:
            self.window.after_cancel(self._preload_after)
        except tk.TclError:
            pass
        self._preload_after = None

    def _cancel_pending_callbacks(self) -> None:
        self._cancel_preload()
        if self._pending_job_selection_after is not None:
            try:
                self.window.after_cancel(self._pending_job_selection_after)
            except tk.TclError:
                pass
            self._pending_job_selection_after = None

    def _close_without_saving(self) -> None:
        self._cancel_pending_callbacks()
        if not self.embedded:
            self.window.destroy()

    def _build(self) -> None:
        sidebar = ttk.Frame(self.window, padding=12)
        sidebar.grid(row=0, column=0, sticky="nsw")
        sidebar.rowconfigure(1, weight=1)

        ttk.Label(sidebar, text="Workflows").grid(row=0, column=0, sticky="w")
        self.workflow_list = tk.Listbox(sidebar, width=28, exportselection=False)
        self.workflow_list.grid(row=1, column=0, sticky="ns", pady=(8, 0))
        self.workflow_list.bind("<<ListboxSelect>>", self._on_workflow_selected)
        list_scroll = ttk.Scrollbar(sidebar, orient="vertical", command=self.workflow_list.yview)
        list_scroll.grid(row=1, column=1, sticky="ns", pady=(8, 0))
        self.workflow_list.configure(yscrollcommand=list_scroll.set)

        ttk.Button(sidebar, text="New empty workflow", command=self._new_empty_workflow).grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(sidebar, text="Delete workflow", command=self._delete_current_workflow).grid(row=3, column=0, sticky="ew", pady=(8, 0))

        main = ttk.Frame(self.window, padding=(0, 12, 12, 12))
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)

        name_row = ttk.Frame(main)
        name_row.grid(row=0, column=0, sticky="ew")
        name_row.columnconfigure(1, weight=1)
        ttk.Label(name_row, text="Workflow name").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(name_row, textvariable=self.name_var).grid(row=0, column=1, sticky="ew")

        tools = ttk.Frame(main)
        tools.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(tools, text="Add job", command=self._add_job_from_catalog).grid(row=0, column=0, sticky="w")
        ttk.Button(tools, text="Move job up", command=lambda: self._move_selected_job(-1)).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Button(tools, text="Move job down", command=lambda: self._move_selected_job(1)).grid(row=0, column=2, sticky="w", padx=(8, 0))
        ttk.Button(tools, text="Remove job", command=self._remove_selected_job).grid(row=0, column=3, sticky="w", padx=(8, 0))

        self.workflow_panes = ttk.PanedWindow(main, orient="vertical")
        self.workflow_panes.grid(row=2, column=0, sticky="nsew", pady=(8, 0))

        sequence_box = ttk.LabelFrame(self.workflow_panes, text="Workflow job sequence", padding=8)
        sequence_box.columnconfigure(0, weight=1)
        sequence_box.rowconfigure(0, weight=1)
        self.job_tree = ttk.Treeview(
            sequence_box,
            columns=("processing_tab", "job", "owner"),
            show="headings",
            height=7,
            style="Technical.Treeview",
        )
        self.job_tree.heading("processing_tab", text="Processing tab")
        self.job_tree.heading("job", text="Job")
        self.job_tree.heading("owner", text="Owner")
        self.job_tree.column("processing_tab", width=150, anchor="w")
        self.job_tree.column("job", width=210, anchor="w")
        self.job_tree.column("owner", width=150, anchor="w")
        self.job_tree.grid(row=0, column=0, sticky="nsew")
        self.job_tree.bind("<<TreeviewSelect>>", self._on_job_selected)
        job_scroll = ttk.Scrollbar(sequence_box, orient="vertical", command=self.job_tree.yview)
        job_scroll.grid(row=0, column=1, sticky="ns")
        self.job_tree.configure(yscrollcommand=job_scroll.set)
        self.workflow_panes.add(sequence_box, weight=1)

        details_outer = ttk.LabelFrame(self.workflow_panes, text="Selected job details", padding=8)
        details_outer.columnconfigure(0, weight=1)
        details_outer.rowconfigure(0, weight=1)
        self.details_canvas = tk.Canvas(details_outer, highlightthickness=0)
        self.details_canvas.grid(row=0, column=0, sticky="nsew")
        details_scroll = ttk.Scrollbar(details_outer, orient="vertical", command=self.details_canvas.yview)
        details_scroll.grid(row=0, column=1, sticky="ns")
        self.details_canvas.configure(yscrollcommand=details_scroll.set)
        self.details_box = ttk.Frame(self.details_canvas)
        self.details_box.columnconfigure(0, weight=1)
        self.details_window = self.details_canvas.create_window((0, 0), window=self.details_box, anchor="nw")
        self.details_box.bind("<Configure>", self._on_details_frame_configured)
        self.details_canvas.bind("<Configure>", self._on_details_canvas_configured)
        self.workflow_panes.add(details_outer, weight=4)
        self._build_empty_details()

        footer = ttk.Frame(self.window, padding=(12, 0, 12, 12))
        footer.grid(row=1, column=0, columnspan=2, sticky="ew")
        footer.columnconfigure(0, weight=1)
        if self.embedded:
            ttk.Button(footer, text="Revert section", command=self.revert_section).grid(row=0, column=1, padx=(8, 0))
            ttk.Button(footer, text="Save section", command=self.save_section).grid(row=0, column=2, padx=(8, 0))
            ttk.Button(footer, text="Schedule workflow", command=self._schedule_current_workflow).grid(row=0, column=3, padx=(8, 0))
        else:
            ttk.Button(footer, text="Cancel", command=self._close_without_saving).grid(row=0, column=1, padx=(8, 0))
            ttk.Button(footer, text="Save", command=self._save_and_close).grid(row=0, column=2, padx=(8, 0))
            ttk.Button(footer, text="Schedule workflow", command=self._schedule_current_workflow).grid(row=0, column=3, padx=(8, 0))

    def _default_workflow_name(self) -> str:
        return f"Workflow {len(project_workflows(self.app.project)) + 1}"

    def _normalized_local_workflows(self) -> list[dict[str, Any]]:
        return [normalize_workflow(workflow) for workflow in self.workflows if isinstance(workflow, dict)]

    def _on_details_frame_configured(self, _event=None) -> None:
        self.details_canvas.configure(scrollregion=self.details_canvas.bbox("all"))

    def _on_details_canvas_configured(self, event) -> None:
        self.details_canvas.itemconfigure(self.details_window, width=event.width)

    def _workflow_jobs(self) -> list[dict]:
        if not (0 <= self.current_index < len(self.workflows)):
            return []
        jobs = self.workflows[self.current_index].setdefault("jobs", [])
        return jobs if isinstance(jobs, list) else []

    def _workflow_job_id(self, job: dict) -> str:
        job_id = str(job.get("workflow_job_id", "")).strip()
        if not job_id:
            import uuid

            job_id = uuid.uuid4().hex
            job["workflow_job_id"] = job_id
        return job_id

    def _workflow_job_index(self, job_id: str | None) -> int | None:
        if not job_id:
            return None
        for index, job in enumerate(self._workflow_jobs()):
            if self._workflow_job_id(job) == job_id:
                return index
        return None

    def _workflow_job_by_id(self, job_id: str | None) -> dict | None:
        index = self._workflow_job_index(job_id)
        if index is None:
            return None
        return self._workflow_jobs()[index]

    def _refresh_workflow_list(self) -> None:
        self.workflow_list.delete(0, "end")
        for workflow in self.workflows:
            self.workflow_list.insert("end", workflow_label(workflow))
        if 0 <= self.current_index < len(self.workflows):
            self.workflow_list.selection_clear(0, "end")
            self.workflow_list.selection_set(self.current_index)
            self.workflow_list.see(self.current_index)

    def _on_workflow_selected(self, _event=None) -> None:
        selection = self.workflow_list.curselection()
        if not selection:
            return
        next_index = int(selection[0])
        if next_index == self.current_index:
            return
        self._capture_current_job()
        self._capture_current_workflow(refresh_list=False)
        self.current_index = next_index
        self.current_job_id = None
        self._refresh_workflow_list()
        self._load_current_workflow()

    def _load_current_workflow(self) -> None:
        self._cancel_pending_callbacks()
        self._preload_generation += 1
        self._loading_job_details = True
        self.job_tree.delete(*self.job_tree.get_children())
        self._clear_details(destroy_cache=True)
        self.current_job_id = None
        self.active_detail = None
        self.current_job_dirty = False
        if not (0 <= self.current_index < len(self.workflows)):
            self.name_var.set("")
            self._build_empty_details()
            self._loading_job_details = False
            return
        workflow = self.workflows[self.current_index]
        self.name_var.set(str(workflow.get("name", "")))
        self._refresh_job_tree()
        self._loading_job_details = False
        jobs = self._workflow_jobs()
        if jobs:
            self._select_tree_job(self._workflow_job_id(jobs[0]))
            self._schedule_detail_preload()
        else:
            self._build_empty_details()

    def _capture_current_workflow(self, *, refresh_list: bool = True) -> None:
        if not (0 <= self.current_index < len(self.workflows)):
            return
        workflow = deepcopy(self.workflows[self.current_index])
        workflow["name"] = self.name_var.get().strip() or "Untitled workflow"
        workflow["jobs"] = deepcopy(self._workflow_jobs())
        self.workflows[self.current_index] = workflow
        if refresh_list:
            self._refresh_workflow_list()

    def _capture_current_job(self) -> None:
        if not self.current_job_dirty or self.current_job_id is None:
            return
        index = self._workflow_job_index(self.current_job_id)
        detail = self.active_detail
        if index is None or detail is None:
            return
        jobs = self._workflow_jobs()
        job = jobs[index]
        entry = deepcopy(job.get("entry", {})) if isinstance(job.get("entry"), dict) else {}
        old_parameters = deepcopy(entry.get("parameters", {})) if isinstance(entry.get("parameters"), dict) else {}
        new_parameters = self._current_parameter_values(detail)
        command_text = detail.get("command_text")
        fallback_command = command_text.get("1.0", "end").strip() if command_text is not None else str(entry.get("command", ""))
        rebuilt = fallback_command
        if old_parameters != new_parameters:
            rebuilt = update_command_from_parameter_changes(fallback_command, old_parameters, new_parameters)
        entry["parameters"] = new_parameters
        entry["command"] = rebuilt
        entry["environment_title"] = new_parameters.get("execution_environment", "") if new_parameters.get("execution_environment", "") != "None" else ""
        owner_kind_var = detail.get("owner_kind_var")
        owner_name_var = detail.get("owner_name_var")
        owner_name = owner_name_var.get().strip() if owner_name_var is not None else ""
        entry["dataset_name"] = owner_name
        job["owner_kind"] = owner_kind_var.get().strip() if owner_kind_var is not None else "dataset"
        job["owner_kind"] = job["owner_kind"] or "dataset"
        job["owner_name"] = owner_name
        job["entry"] = entry
        jobs[index] = job
        self._update_job_tree_row(self.current_job_id)
        self.current_job_dirty = False

    def _current_parameter_values(self, detail: dict[str, Any] | None = None) -> dict[str, str]:
        detail = detail or self.active_detail or {}
        parameter_vars = detail.get("parameter_vars", {})
        values: dict[str, str] = {}
        for key, variable in parameter_vars.items():
            if isinstance(variable, tk.BooleanVar):
                values[key] = "true" if bool(variable.get()) else ""
            else:
                values[key] = str(variable.get()).strip()
        return values

    def _refresh_job_tree(self) -> None:
        self.job_tree.delete(*self.job_tree.get_children())
        for index, job in enumerate(self._workflow_jobs()):
            self._insert_job_tree_row(job, index)

    def _insert_job_tree_row(self, job: dict, index: int | str = "end") -> None:
        job_id = self._workflow_job_id(job)
        if self.job_tree.exists(job_id):
            self._update_job_tree_row(job_id)
            return
        self.job_tree.insert("", index, iid=job_id, values=self._job_tree_values(job))

    def _update_job_tree_row(self, job_id: str) -> None:
        job = self._workflow_job_by_id(job_id)
        if job is not None and self.job_tree.exists(job_id):
            self.job_tree.item(job_id, values=self._job_tree_values(job))

    def _job_tree_values(self, job: dict) -> tuple[str, str, str]:
        entry = job.get("entry", {}) if isinstance(job.get("entry"), dict) else {}
        owner = f"{job.get('owner_kind', 'dataset')}: {job.get('owner_name', '') or '-'}"
        return (
            str(entry.get("processing_tab", "-")),
            str(entry.get("job_name", "-")),
            owner,
        )

    def _select_tree_job(self, job_id: str | None) -> None:
        if not job_id or not self.job_tree.exists(job_id):
            return
        self._suppress_job_selection = True
        self.job_tree.selection_set(job_id)
        self.job_tree.see(job_id)
        self.window.after_idle(self._allow_job_selection_events)
        self._select_job_id(job_id)

    def _allow_job_selection_events(self) -> None:
        self._suppress_job_selection = False

    def _on_job_selected(self, _event=None) -> None:
        if self._loading_job_details or self._suppress_job_selection:
            return
        selection = self.job_tree.selection()
        if not selection:
            return
        next_job_id = str(selection[0])
        if next_job_id == self.current_job_id:
            return
        if self._pending_job_selection_after is not None:
            try:
                self.window.after_cancel(self._pending_job_selection_after)
            except tk.TclError:
                pass
        self._pending_job_selection_after = self.window.after_idle(lambda job_id=next_job_id: self._select_job_id(job_id))

    def _select_job_id(self, job_id: str) -> None:
        self._pending_job_selection_after = None
        if job_id == self.current_job_id:
            return
        self._capture_current_job()
        if self.active_detail is not None:
            frame = self.active_detail.get("frame")
            if frame is not None:
                frame.grid_remove()
        self.current_job_id = job_id
        self.current_job_dirty = False
        detail = self.detail_cache.get(job_id)
        if detail is None:
            job = self._workflow_job_by_id(job_id)
            if job is None:
                self._clear_details(destroy_cache=True)
                self._build_empty_details()
                return
            self._clear_empty_details()
            detail = self._ensure_detail_cached(job_id, job)
        self.active_detail = detail
        detail["frame"].grid(row=0, column=0, sticky="nsew")
        self.details_canvas.yview_moveto(0)
        self.details_canvas.after_idle(lambda: self.details_canvas.configure(scrollregion=self.details_canvas.bbox("all")))

    def _ensure_detail_cached(self, job_id: str, job: dict | None = None) -> dict[str, Any]:
        detail = self.detail_cache.get(job_id)
        if detail is not None:
            return detail
        job = job or self._workflow_job_by_id(job_id)
        if job is None:
            raise KeyError(job_id)
        detail = self._build_job_detail_frame(job_id, job)
        self.detail_cache[job_id] = detail
        return detail

    def _fields_for_job(self, job_id: str, job: dict) -> tuple[Any, ...]:
        fields = self.field_cache.get(job_id)
        if fields is None:
            fields = fields_for_workflow_job(self.app.project, job, self.catalog_jobs)
            self.field_cache[job_id] = fields
        return fields

    def _schedule_detail_preload(self) -> None:
        self._cancel_preload()
        job_ids: list[str] = []
        for job in self._workflow_jobs():
            job_id = self._workflow_job_id(job)
            if job_id != self.current_job_id:
                job_ids.append(job_id)
        if not job_ids:
            return
        generation = self._preload_generation

        def preload_next() -> None:
            self._preload_after = None
            if generation != self._preload_generation or not self.window.winfo_exists():
                return
            while job_ids and (job_ids[0] in self.detail_cache or job_ids[0] in self.field_cache):
                job_ids.pop(0)
            if not job_ids:
                return
            job_id = job_ids.pop(0)
            job = self._workflow_job_by_id(job_id)
            if job is not None and job_id not in self.field_cache:
                self._fields_for_job(job_id, job)
            if job_ids:
                self._preload_after = self.window.after(8, preload_next)

        self._preload_after = self.window.after_idle(preload_next)

    def _build_job_detail_frame(self, job_id: str, job: dict) -> dict[str, Any]:
        with perf_timer("workflow editor build job detail"):
            return self._build_job_detail_frame_uncached(job_id, job)

    def _build_job_detail_frame_uncached(self, job_id: str, job: dict) -> dict[str, Any]:
        frame = ttk.Frame(self.details_box)
        frame.columnconfigure(0, weight=1)
        entry = job.get("entry", {}) if isinstance(job.get("entry"), dict) else {}
        ttk.Label(
            frame,
            text=f"{entry.get('processing_tab', '-')} > {entry.get('group', '-')} > {entry.get('job_name', '-')}",
        ).grid(row=0, column=0, sticky="w")

        detail: dict[str, Any] = {
            "frame": frame,
            "job_id": job_id,
            "parameter_vars": {},
        }

        owner_row = ttk.Frame(frame)
        owner_row.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        owner_row.columnconfigure(3, weight=1)
        owner_kind_var = tk.StringVar(value=str(job.get("owner_kind", "dataset") or "dataset"))
        owner_name_var = tk.StringVar(value=str(job.get("owner_name", "")))
        detail["owner_kind_var"] = owner_kind_var
        detail["owner_name_var"] = owner_name_var
        ttk.Label(owner_row, text="Owner type").grid(row=0, column=0, sticky="w", padx=(0, 8))
        owner_kind_combo = ttk.Combobox(
            owner_row,
            textvariable=owner_kind_var,
            state="readonly",
            values=("dataset", "m_population"),
            width=16,
        )
        owner_kind_combo.grid(row=0, column=1, sticky="w", padx=(0, 12))
        ttk.Label(owner_row, text="Owner").grid(row=0, column=2, sticky="w", padx=(0, 8))
        owner_name_combo = ttk.Combobox(owner_row, textvariable=owner_name_var, state="readonly", width=30)
        owner_name_combo.grid(row=0, column=3, sticky="ew")
        detail["owner_name_combo"] = owner_name_combo
        owner_kind_combo.bind("<<ComboboxSelected>>", lambda _event, current=detail: self._refresh_owner_options(current))
        self._refresh_owner_options(detail, mark_dirty=False)
        self._trace_dirty_var(owner_kind_var)
        self._trace_dirty_var(owner_name_var)

        form = ttk.Frame(frame)
        form.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        form.columnconfigure(0, weight=0)
        form.columnconfigure(1, weight=1)
        form.columnconfigure(2, weight=1)

        parameters = entry.get("parameters", {}) if isinstance(entry.get("parameters"), dict) else {}
        fields = self._fields_for_job(job_id, job)
        for row, field in enumerate(fields):
            ttk.Label(form, text=field.label or field.key).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
            self._build_parameter_widget(form, row, field, str(parameters.get(field.key, field.default_value)), detail)
            if field.description:
                ttk.Label(form, text=field.description, wraplength=620).grid(row=row, column=2, sticky="ew", padx=(8, 0), pady=3)

        command_box = ttk.LabelFrame(frame, text="Command", padding=8)
        command_box.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        command_box.columnconfigure(0, weight=1)
        command_text = tk.Text(command_box, height=5, wrap="word", font="TkDefaultFont")
        command_text.grid(row=0, column=0, sticky="ew")
        command_text.insert("1.0", str(entry.get("command", "")))
        command_text.edit_modified(False)
        command_text.bind("<<Modified>>", self._on_command_text_modified)
        detail["command_text"] = command_text
        ttk.Button(command_box, text="Rebuild command from parameters", command=self._rebuild_current_command_preview).grid(
            row=1,
            column=0,
            sticky="w",
            pady=(8, 0),
        )
        return detail

    def _build_parameter_widget(self, parent: tk.Misc, row: int, field, value: str, detail: dict[str, Any]) -> None:
        widget = field.widget
        parameter_vars = detail["parameter_vars"]
        if widget == "bool":
            variable = tk.BooleanVar(value=value.casefold() in {"1", "true", "yes", "on"})
            parameter_vars[field.key] = variable
            self._trace_dirty_var(variable)
            ttk.Checkbutton(parent, variable=variable).grid(row=row, column=1, sticky="w", pady=3)
            return
        if widget == "choice":
            variable = tk.StringVar(value=value)
            parameter_vars[field.key] = variable
            self._trace_dirty_var(variable)
            ttk.Combobox(parent, textvariable=variable, values=field.options, state="readonly").grid(row=row, column=1, sticky="ew", pady=3)
            return
        if widget == "environment":
            variable = tk.StringVar(value=value or "None")
            parameter_vars[field.key] = variable
            self._trace_dirty_var(variable)
            ttk.Combobox(parent, textvariable=variable, values=self.environment_title_options, state="readonly").grid(row=row, column=1, sticky="ew", pady=3)
            return
        variable = tk.StringVar(value=value)
        parameter_vars[field.key] = variable
        self._trace_dirty_var(variable)
        field_frame = ttk.Frame(parent)
        field_frame.grid(row=row, column=1, sticky="ew", pady=3)
        field_frame.columnconfigure(0, weight=1)
        ttk.Entry(field_frame, textvariable=variable).grid(row=0, column=0, sticky="ew")
        if widget in {"path", "file"}:
            ttk.Button(field_frame, text="Browse", command=lambda current=variable: self._browse_path(current)).grid(row=0, column=1, padx=(6, 0))

    def _trace_dirty_var(self, variable: tk.Variable) -> None:
        variable.trace_add("write", lambda *_args: self._mark_current_job_dirty())

    def _mark_current_job_dirty(self) -> None:
        if not self._loading_job_details:
            self.current_job_dirty = True

    def _on_command_text_modified(self, event) -> None:
        widget = event.widget
        if widget.edit_modified():
            widget.edit_modified(False)
            self._mark_current_job_dirty()

    def _browse_path(self, variable: tk.StringVar) -> None:
        path = filedialog.askopenfilename(parent=self.window)
        if path:
            variable.set(path)

    def _refresh_owner_options(self, detail: dict[str, Any] | None = None, *, mark_dirty: bool = True) -> None:
        detail = detail or self.active_detail
        if detail is None:
            return
        owner_kind_var = detail.get("owner_kind_var")
        owner_name_var = detail.get("owner_name_var")
        owner_name_combo = detail.get("owner_name_combo")
        if owner_kind_var is None or owner_name_var is None or owner_name_combo is None:
            return
        owner_kind = owner_kind_var.get().strip() or "dataset"
        values = owner_options_for_kind(self.app.project, owner_kind)
        owner_name_combo.configure(values=values)
        if owner_name_var.get() not in values:
            owner_name_var.set(values[0] if values else "")
        if mark_dirty:
            self._mark_current_job_dirty()

    def _rebuild_current_command_preview(self) -> None:
        if self.current_job_id is None or self.active_detail is None:
            return
        job = self._workflow_job_by_id(self.current_job_id)
        if job is None:
            return
        command_text = self.active_detail.get("command_text")
        if command_text is None:
            return
        fallback = command_text.get("1.0", "end").strip()
        command = rebuild_workflow_job_command(self.app.project, job, self._current_parameter_values(), fallback, self.catalog_jobs)
        command_text.delete("1.0", "end")
        command_text.insert("1.0", command)
        command_text.edit_modified(False)
        self.current_job_dirty = True

    def _clear_details(self, *, destroy_cache: bool = False) -> None:
        for child in self.details_box.winfo_children():
            child.destroy()
        self.detail_cache = {} if destroy_cache else self.detail_cache
        self.field_cache = {} if destroy_cache else self.field_cache
        self.active_detail = None
        self.empty_detail_label = None
        self.details_canvas.yview_moveto(0)
        self.details_canvas.configure(scrollregion=self.details_canvas.bbox("all"))

    def _build_empty_details(self) -> None:
        self._clear_empty_details()
        self.empty_detail_label = ttk.Label(self.details_box, text="Select a workflow job to edit its parameters.")
        self.empty_detail_label.grid(row=0, column=0, sticky="w")

    def _clear_empty_details(self) -> None:
        label = getattr(self, "empty_detail_label", None)
        if label is not None:
            try:
                label.destroy()
            except tk.TclError:
                pass
        self.empty_detail_label = None

    def _add_job_from_catalog(self) -> None:
        self._ensure_workflow()
        picker = WorkflowJobPickerDialog(self.app, self.window, catalog_jobs=self.catalog_jobs)
        selected = picker.show()
        if selected is None:
            return
        self._capture_current_job()
        job = workflow_job_from_catalog(self.app.project, selected)
        jobs = self._workflow_jobs()
        jobs.append(job)
        job_id = self._workflow_job_id(job)
        self._insert_job_tree_row(job)
        self._select_tree_job(job_id)

    def _move_selected_job(self, direction: int) -> None:
        job_id = self.current_job_id
        index = self._workflow_job_index(job_id)
        jobs = self._workflow_jobs()
        if index is None or job_id is None:
            return
        self._capture_current_job()
        new_index = index + direction
        if not (0 <= new_index < len(jobs)):
            return
        jobs[index], jobs[new_index] = jobs[new_index], jobs[index]
        self.job_tree.move(job_id, "", new_index)
        self.job_tree.see(job_id)

    def _remove_selected_job(self) -> None:
        job_id = self.current_job_id
        index = self._workflow_job_index(job_id)
        jobs = self._workflow_jobs()
        if index is None or job_id is None or not (0 <= index < len(jobs)):
            return
        del jobs[index]
        if self.job_tree.exists(job_id):
            self.job_tree.delete(job_id)
        cached = self.detail_cache.pop(job_id, None)
        self.field_cache.pop(job_id, None)
        if cached is not None:
            frame = cached.get("frame")
            if frame is not None:
                frame.destroy()
        self.current_job_id = None
        self.active_detail = None
        self.current_job_dirty = False
        if jobs:
            next_index = min(index, len(jobs) - 1)
            self._select_tree_job(self._workflow_job_id(jobs[next_index]))
        else:
            self._clear_details(destroy_cache=True)
            self._build_empty_details()

    def _new_empty_workflow(self) -> None:
        self._capture_current_job()
        self._capture_current_workflow()
        self.workflows.append(create_workflow_from_refs([], self._default_workflow_name()))
        self.current_index = len(self.workflows) - 1
        self.current_job_id = None
        self._refresh_workflow_list()
        self._load_current_workflow()

    def _delete_current_workflow(self) -> None:
        if not (0 <= self.current_index < len(self.workflows)):
            return
        del self.workflows[self.current_index]
        self.current_index = min(self.current_index, len(self.workflows) - 1)
        self.current_job_id = None
        self._refresh_workflow_list()
        self._load_current_workflow()

    def _ensure_workflow(self) -> None:
        if 0 <= self.current_index < len(self.workflows):
            return
        self.workflows.append(create_workflow_from_refs([], self._default_workflow_name()))
        self.current_index = len(self.workflows) - 1
        self._refresh_workflow_list()
        self._load_current_workflow()

    def _save_all_local_workflows(self) -> None:
        self._capture_current_job()
        self._capture_current_workflow()
        self.app.project.state.workflows = []
        for workflow in self.workflows:
            save_workflow(self.app.project, workflow)
        self.workflows = [deepcopy(workflow) for workflow in project_workflows(self.app.project)]
        self._saved_snapshot = self._normalized_local_workflows()

    def _save_and_close(self) -> None:
        self._save_all_local_workflows()
        self.app.on_project_changed("job_queue")
        self.app.status_var.set("Saved workflows")
        self._cancel_pending_callbacks()
        if not self.embedded:
            self.window.destroy()

    def save_section(self, close_window: bool = False) -> bool:
        self._save_all_local_workflows()
        self.app.on_project_changed("job_queue")
        self.app.status_var.set("Saved workflows")
        if close_window and not self.embedded:
            self._cancel_pending_callbacks()
            self.window.destroy()
        return True

    def has_unsaved_changes(self) -> bool:
        self._capture_current_job()
        self._capture_current_workflow(refresh_list=False)
        return self._normalized_local_workflows() != self._saved_snapshot

    def revert_section(self) -> None:
        self._cancel_pending_callbacks()
        self.workflows = [deepcopy(workflow) for workflow in project_workflows(self.app.project)]
        self._saved_snapshot = self._normalized_local_workflows()
        self.current_index = 0 if self.workflows else -1
        self.current_job_id = None
        self._refresh_workflow_list()
        self._load_current_workflow()
        self.app.status_var.set("Reverted workflows")

    def _schedule_current_workflow(self) -> None:
        if not (0 <= self.current_index < len(self.workflows)):
            messagebox.showinfo("Schedule workflow", "Please select a workflow first.")
            return
        self._save_all_local_workflows()
        workflow = self.app.project.state.workflows[self.current_index]
        try:
            created = schedule_workflow(self.app.project, workflow)
        except Exception as exc:
            messagebox.showerror("Schedule workflow failed", str(exc))
            return
        self.app.on_project_changed("job_queue", "processing", "processing_m", "tomograms", "particles", "custom")
        self.app.status_var.set(f"Scheduled workflow '{workflow_label(workflow)}' with {len(created)} job(s)")
        self._cancel_pending_callbacks()
        if not self.embedded:
            self.window.destroy()


class WorkflowJobPickerDialog:
    def __init__(self, app, parent: tk.Misc, *, catalog_jobs: list[WorkflowCatalogJob] | None = None) -> None:
        self.app = app
        self.parent = parent
        self.jobs = list(catalog_jobs) if catalog_jobs is not None else build_workflow_job_catalog(app.project)
        self.filtered_jobs = list(self.jobs)
        self.search_var = tk.StringVar()
        self.result: WorkflowCatalogJob | None = None
        self.window = tk.Toplevel(parent)
        self.window.title("Add workflow job")
        self.window.geometry("780x520")
        self.window.minsize(620, 420)
        self.window.transient(parent.winfo_toplevel())
        self.window.grab_set()
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(1, weight=1)
        self._refresh_debouncer = TkDebouncer(self.window, self._refresh, delay_ms=100)
        self._build()
        self._refresh()

    def show(self) -> WorkflowCatalogJob | None:
        self.window.wait_window()
        return self.result

    def _build(self) -> None:
        top = ttk.Frame(self.window, padding=12)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Search").grid(row=0, column=0, sticky="w", padx=(0, 8))
        search = ttk.Entry(top, textvariable=self.search_var)
        search.grid(row=0, column=1, sticky="ew")
        self.search_var.trace_add("write", lambda *_args: self._refresh_debouncer.schedule())

        body = ttk.Frame(self.window, padding=(12, 0, 12, 12))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(body, columns=("processing_tab", "group", "job"), show="headings", style="Technical.Treeview")
        self.table.heading("processing_tab", text="Processing tab")
        self.table.heading("group", text="Group")
        self.table.heading("job", text="Job")
        self.table.column("processing_tab", width=170, anchor="w")
        self.table.column("group", width=220, anchor="w")
        self.table.column("job", width=260, anchor="w")
        self.table.grid(row=0, column=0, sticky="nsew")
        self.table.bind("<Double-1>", lambda _event: self._select())
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.table.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.table.configure(yscrollcommand=scroll.set)

        footer = ttk.Frame(self.window, padding=(12, 0, 12, 12))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Button(footer, text="Cancel", command=self.window.destroy).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(footer, text="Add", command=self._select).grid(row=0, column=2, padx=(8, 0))

    def _refresh(self) -> None:
        text = self.search_var.get().strip().casefold()
        self.filtered_jobs = [
            job
            for job in self.jobs
            if not text
            or text in " ".join([job.processing_tab, job.group, job.title, job.job_key]).casefold()
        ]
        self.table.delete(*self.table.get_children())
        for index, job in enumerate(self.filtered_jobs):
            self.table.insert("", "end", iid=str(index), values=(job.processing_tab, job.group, job.title))
        if self.filtered_jobs:
            self.table.selection_set("0")

    def _select(self) -> None:
        selection = self.table.selection()
        if not selection:
            messagebox.showinfo("Add workflow job", "Please select a job first.")
            return
        self.result = self.filtered_jobs[int(selection[0])]
        self.window.destroy()
