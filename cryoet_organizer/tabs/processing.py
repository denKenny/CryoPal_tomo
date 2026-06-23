from __future__ import annotations

from datetime import datetime, timezone
import shlex
import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from cryoet_organizer.dialogs import bind_scrollable_canvas, show_detail_dialog
from cryoet_organizer.environments import environment_titles
from cryoet_organizer.job_execution import (
    build_slurm_override_metadata,
    create_history_entry,
    display_history_timestamp,
    execute_scheduled_history_entries,
    is_scheduled_history_entry,
    slurm_override_payload,
)
from cryoet_organizer.job_defaults import resolve_job_default
from cryoet_organizer.project import DatasetRecord, JobHistoryEntry, ProjectData
from cryoet_organizer.scheduled_slurm_dialog import CollectiveSlurmSubmissionDialog, ask_scheduled_slurm_mode
from cryoet_organizer.slurm import SlurmSubmissionResult, find_slurm_profile, render_sbatch_script
from cryoet_organizer.slurm_override_ui import SlurmOverrideUI
from cryoet_organizer.tabs.base import SidebarTab
from cryoet_organizer.warptools_catalog import GROUPS, WarpToolFlag, jobs_by_group
from cryoet_organizer.warp_settings import parse_warp_settings


class ProcessingTab(SidebarTab):
    tab_id = "processing"
    title = "Processing: WARP"
    refresh_domains = ("processing", "datasets", "defaults", "slurm", "environments")

    def build(self) -> None:
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(0, weight=1)
        self.dataset_var = tk.StringVar()
        self.group_var = tk.StringVar(value=GROUPS[0])
        self.job_var = tk.StringVar()
        self.available_jobs: dict[str, tuple] = jobs_by_group()
        self.parameter_vars: dict[str, tk.Variable] = {}
        self.parameter_inputs: dict[str, object] = {}
        self.current_dataset: DatasetRecord | None = None
        self.current_job = None
        self.history_sort_column = "timestamp"
        self.history_sort_descending = True
        self.execution_mode_var = tk.StringVar(value="Run locally")
        self.environment_var = tk.StringVar(value="None")
        self.export_output_directory_var = tk.StringVar()
        self.export_output_name_var = tk.StringVar(value="Output.star")
        self.slurm_profile_var = tk.StringVar()
        self.slurm_partition_var = tk.StringVar()
        self.slurm_time_var = tk.StringVar()
        self.slurm_gpus_var = tk.StringVar()
        self.slurm_cpus_var = tk.StringVar()
        self.slurm_mem_var = tk.StringVar()
        self.slurm_mem_per_cpu_var = tk.StringVar()
        self.slurm_mem_mode_var = tk.StringVar(value="mem")
        self.slurm_overrides_ui = SlurmOverrideUI(self.app, self.slurm_profile_var)
        self.processing_advanced_visible = tk.BooleanVar(value=False)
        self._suspend_command_preview_updates = False
        self._required_param_rows: list[dict[str, object]] = []
        self._advanced_param_rows: list[dict[str, object]] = []
        self.history_entry_refs: dict[str, tuple[int, JobHistoryEntry]] = {}

        self.outer_canvas = tk.Canvas(self.frame, highlightthickness=0)
        self.outer_canvas.grid(row=0, column=0, sticky="nsew")
        self.outer_scrollbar = ttk.Scrollbar(
            self.frame,
            orient="vertical",
            command=self.outer_canvas.yview,
        )
        self.outer_scrollbar.grid(row=0, column=1, sticky="ns")
        self.outer_canvas.configure(yscrollcommand=self.outer_scrollbar.set)

        self.content = ttk.Frame(self.outer_canvas, padding=2)
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(5, weight=1)
        self.outer_window = self.outer_canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", self._on_outer_frame_configure)
        self.outer_canvas.bind("<Configure>", self._on_outer_canvas_configure)

        ttk.Label(
            self.content,
            text="Waehle zuerst ein geladenes Dataset aus. Danach kannst du einen Processing-Job auswaehlen.",
            wraplength=900,
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 12))

        dataset_box = ttk.LabelFrame(self.content, text="Dataset selection", padding=12)
        dataset_box.grid(row=1, column=0, sticky="ew")
        dataset_box.columnconfigure(0, weight=1)
        ttk.Label(dataset_box, text="Loaded dataset").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.dataset_combo = ttk.Combobox(
            dataset_box,
            textvariable=self.dataset_var,
            state="readonly",
        )
        self.dataset_combo.grid(row=1, column=0, sticky="ew")
        self.dataset_combo.bind("<<ComboboxSelected>>", self._on_dataset_selected)

        self.dataset_summary = ttk.Label(self.content, text="", wraplength=900, justify="left")
        self.dataset_summary.grid(row=3, column=0, sticky="w", pady=(16, 0))

        self.history_box = ttk.LabelFrame(self.content, text="Job history", padding=12)
        self.history_box.grid(row=2, column=0, sticky="nsew", pady=(12, 0))
        self.history_box.columnconfigure(0, weight=1)
        self.history_box.rowconfigure(0, weight=1)

        self.history_table = ttk.Treeview(
            self.history_box,
            columns=("job_name", "timestamp", "action"),
            show="headings",
            height=6,
        )
        self.history_table.heading("job_name", text="Job", command=lambda: self._sort_history("job_name"))
        self.history_table.column("job_name", width=240, anchor="w")
        self.history_table.heading("timestamp", text="Timestamp", command=lambda: self._sort_history("timestamp"))
        self.history_table.column("timestamp", width=180, anchor="w")
        self.history_table.heading("action", text="Action", command=lambda: self._sort_history("action"))
        self.history_table.column("action", width=110, anchor="w")
        self.history_table.grid(row=0, column=0, sticky="nsew")
        self.history_table.tag_configure("scheduled", background="#ececec")
        self.history_table.tag_configure("waiting", background="#dbeeff")
        self.history_table.tag_configure("running", background="#dff4d8")
        self.history_table.tag_configure("completed", background="#dde8ff")

        history_scrollbar = ttk.Scrollbar(
            self.history_box,
            orient="vertical",
            command=self.history_table.yview,
        )
        history_scrollbar.grid(row=0, column=1, sticky="ns")
        self.history_table.configure(yscrollcommand=history_scrollbar.set)

        history_actions = ttk.Frame(self.history_box)
        history_actions.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        history_actions.columnconfigure(5, weight=1)
        ttk.Button(
            history_actions,
            text="Show selected job details",
            command=self._show_selected_history_details,
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            history_actions,
            text="Copy job parameters",
            command=self._copy_history_job_parameters,
        ).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Button(
            history_actions,
            text="Remove selected job",
            command=self._remove_selected_history_entry,
        ).grid(row=0, column=2, sticky="w", padx=(8, 0))
        ttk.Button(
            history_actions,
            text="Run scheduled jobs",
            command=self._run_scheduled_jobs,
        ).grid(row=0, column=3, sticky="w", padx=(8, 0))
        ttk.Button(
            history_actions,
            text="Submit scheduled jobs to Slurm",
            command=self._submit_scheduled_jobs_to_slurm,
        ).grid(row=0, column=4, sticky="w", padx=(8, 0))
        abort_button = ttk.Button(
            history_actions,
            text="Abort",
            command=self.app.abort_running_commands,
            state="disabled",
        )
        abort_button.grid(row=0, column=6, sticky="e", padx=(8, 0))
        self.app.attach_abort_button(abort_button)
        self.history_table.bind("<Double-1>", self._show_selected_history_details)
        self.history_box.grid_remove()

        self.job_box = ttk.LabelFrame(self.content, text="Processing setup", padding=12)
        self.job_box.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        self.job_box.columnconfigure(0, weight=1)
        self.job_box.columnconfigure(1, weight=1)

        ttk.Label(self.job_box, text="Job group").grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(self.job_box, text="Processing job").grid(row=0, column=1, sticky="w", pady=(0, 4))
        self.group_combo = ttk.Combobox(
            self.job_box,
            textvariable=self.group_var,
            state="readonly",
            values=GROUPS,
        )
        self.group_combo.grid(row=1, column=0, sticky="ew", padx=(0, 10))
        self.group_combo.bind("<<ComboboxSelected>>", self._on_group_selected)
        self.job_combo = ttk.Combobox(
            self.job_box,
            textvariable=self.job_var,
            state="disabled",
            values=(),
        )
        self.job_combo.grid(row=1, column=1, sticky="ew")
        self.job_combo.bind("<<ComboboxSelected>>", self._on_job_selected)
        self.job_hint = ttk.Label(
            self.job_box,
            text="Die Jobs werden direkt aus der bereitgestellten WarpTools-Referenz geladen.",
            wraplength=900,
            justify="left",
        )
        self.job_hint.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.job_box.grid_remove()

        self.parameters_box = ttk.LabelFrame(self.content, text="Job parameters", padding=12)
        self.parameters_box.grid(row=5, column=0, sticky="nsew", pady=(12, 0))
        self.parameters_box.columnconfigure(0, weight=1)
        self.parameters_box.rowconfigure(2, weight=1)

        command_header = ttk.Frame(self.parameters_box)
        command_header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        command_header.columnconfigure(0, weight=1)
        ttk.Label(
            command_header,
            text="Command preview",
            style="Heading.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(command_header, text="Copy command", command=self._copy_command).grid(
            row=0, column=1, sticky="e", padx=(8, 0)
        )
        ttk.Button(command_header, text="Schedule command", command=self._schedule_command).grid(
            row=0, column=2, sticky="e", padx=(8, 0)
        )
        ttk.Button(command_header, text="Run command", command=self._run_command).grid(
            row=0, column=3, sticky="e", padx=(8, 0)
        )

        execution_row = ttk.Frame(self.parameters_box)
        execution_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        execution_row.columnconfigure(3, weight=1)
        ttk.Label(execution_row, text="Execution").grid(row=0, column=0, sticky="w", padx=(0, 8))
        execution_combo = ttk.Combobox(
            execution_row,
            textvariable=self.execution_mode_var,
            state="readonly",
            values=("Run locally", "Submit to Slurm"),
            width=16,
        )
        execution_combo.grid(row=0, column=1, sticky="w")
        execution_combo.bind("<<ComboboxSelected>>", lambda _e: self._toggle_slurm_controls())
        self.execution_target_label = ttk.Label(execution_row, text="Select environment")
        self.execution_target_label.grid(row=0, column=2, sticky="e", padx=(16, 8))
        self.environment_combo = ttk.Combobox(
            execution_row,
            textvariable=self.environment_var,
            state="readonly",
            width=22,
        )
        self.environment_combo.grid(row=0, column=3, sticky="w")
        self.slurm_profile_combo = ttk.Combobox(
            execution_row,
            textvariable=self.slurm_profile_var,
            state="readonly",
            width=22,
        )
        self.slurm_profile_combo.grid(row=0, column=3, sticky="w")
        self.slurm_profile_combo.bind("<<ComboboxSelected>>", lambda _e: self.slurm_overrides_ui.rebuild(preserve_existing=False))

        self.slurm_overrides_frame = ttk.Frame(self.parameters_box)
        self.slurm_overrides_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.slurm_overrides_ui.register_frame(self.slurm_overrides_frame)

        self.command_text = tk.Text(self.parameters_box, height=4, wrap="word", font="TkDefaultFont")
        self.command_text.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        self.command_text.insert("1.0", "WarpTools")

        self.parameter_canvas = tk.Canvas(self.parameters_box, highlightthickness=0)
        self.parameter_canvas.grid(row=4, column=0, sticky="nsew")
        self.parameter_scrollbar = ttk.Scrollbar(
            self.parameters_box,
            orient="vertical",
            command=self.parameter_canvas.yview,
        )
        self.parameter_scrollbar.grid(row=4, column=1, sticky="ns")
        self.parameter_xscrollbar = ttk.Scrollbar(
            self.parameters_box,
            orient="horizontal",
            command=self.parameter_canvas.xview,
        )
        self.parameter_xscrollbar.grid(row=5, column=0, sticky="ew")
        self.parameter_canvas.configure(
            yscrollcommand=self.parameter_scrollbar.set,
            xscrollcommand=self.parameter_xscrollbar.set,
        )

        self.parameter_frame = ttk.Frame(self.parameter_canvas)
        self.parameter_frame.columnconfigure(0, weight=1)
        self.parameter_canvas_window = self.parameter_canvas.create_window(
            (0, 0),
            window=self.parameter_frame,
            anchor="nw",
        )
        bind_scrollable_canvas(
            self.parameter_canvas,
            self.parameter_canvas_window,
            self.parameter_frame,
            allow_horizontal=True,
        )
        self.processing_advanced_button = ttk.Button(
            self.parameter_frame,
            text="Show advanced settings",
            command=lambda: (
                self.processing_advanced_visible.set(not self.processing_advanced_visible.get()),
                self._toggle_processing_advanced(),
            ),
        )
        self.processing_advanced_frame = ttk.LabelFrame(self.parameter_frame, text="Advanced settings", padding=12)
        self.processing_advanced_frame.columnconfigure(0, weight=1)
        self.processing_advanced_button.grid_remove()
        self.processing_advanced_frame.grid_remove()
        self.parameters_box.grid_remove()
        self._refresh_slurm_profiles()

    def _on_outer_frame_configure(self, _event=None) -> None:
        self.outer_canvas.configure(scrollregion=self.outer_canvas.bbox("all"))

    def _on_outer_canvas_configure(self, event) -> None:
        self.outer_canvas.itemconfigure(self.outer_window, width=event.width)

    def _on_parameter_frame_configure(self, _event=None) -> None:
        self.parameter_canvas.configure(scrollregion=self.parameter_canvas.bbox("all"))

    def _on_parameter_canvas_configure(self, event) -> None:
        self.parameter_canvas.itemconfigure(self.parameter_canvas_window, width=event.width)

    def _scroll_job_view_to_top(self) -> None:
        self.parameter_canvas.yview_moveto(0)
        self.parameter_canvas.xview_moveto(0)

    def _dataset_map(self, project: ProjectData) -> dict[str, DatasetRecord]:
        return {dataset.dataset_name: dataset for dataset in project.datasets}

    def _jobs_for_current_group(self) -> tuple:
        return self.available_jobs.get(self.group_var.get(), ())

    def _refresh_history(self) -> None:
        for item in self.history_table.get_children():
            self.history_table.delete(item)

        if self.current_dataset is None:
            self.history_entry_refs = {}
            self.history_box.grid_remove()
            return

        self.history_box.grid()
        entries = [
            (index, entry)
            for index, entry in enumerate(self.current_dataset.job_history)
            if (
                entry.processing_tab == "Processing: WARP"
                or (
                    not entry.processing_tab
                    and entry.group not in {"Tomograms", "Particles", "Project Overview"}
                )
            )
        ]
        if self.history_sort_column == "timestamp":
            scheduled_entries = [item for item in entries if is_scheduled_history_entry(item[1])]
            other_entries = [item for item in entries if not is_scheduled_history_entry(item[1])]
            scheduled_entries.sort(key=lambda item: item[1].timestamp, reverse=True)
            other_entries.sort(key=lambda item: item[1].timestamp, reverse=self.history_sort_descending)
            entries = scheduled_entries + other_entries
        else:
            entries.sort(
                key=lambda item: self._history_sort_value(item[1], self.history_sort_column),
                reverse=self.history_sort_descending,
            )
        self.history_entry_refs = {entry.entry_id: (index, entry) for index, entry in entries}
        for index, entry in entries:
            self.history_table.insert(
                "",
                "end",
                iid=entry.entry_id,
                values=(
                    entry.job_name,
                    display_history_timestamp(entry),
                    entry.action,
                ),
                tags=(self.app.history_entry_state_tag(entry),),
            )

    def _history_sort_value(self, entry: JobHistoryEntry, column: str):
        if column == "job_name":
            return entry.job_name.casefold()
        if column == "action":
            return entry.action.casefold()
        return entry.timestamp

    def _sort_history(self, column: str) -> None:
        if self.history_sort_column == column:
            self.history_sort_descending = not self.history_sort_descending
        else:
            self.history_sort_column = column
            self.history_sort_descending = True if column == "timestamp" else False
        self._refresh_history()

    def _show_selected_history_details(self, _event=None) -> None:
        selection = self.history_table.selection()
        if not selection:
            messagebox.showinfo("Job details", "Please select a job history entry first.")
            return
        selected = self.history_entry_refs.get(selection[0])
        if selected is None:
            return
        _index, entry = selected
        sections = [
            (
                "Overview",
                [
                    ("Job", entry.job_name),
                    ("Group", entry.group),
                    ("Action", entry.action),
                    ("Timestamp", display_history_timestamp(entry)),
                ],
            ),
            (
                "Parameters",
                [(key, value) for key, value in entry.parameters.items()] or [("Parameters", "-")],
            ),
        ]
        show_detail_dialog(self.frame, "Job details", sections, command=entry.command or "-")

    def _copy_history_job_parameters(self) -> None:
        selection = self.history_table.selection()
        if not selection:
            messagebox.showinfo("Copy job parameters", "Please select a job history entry first.")
            return
        selected = self.history_entry_refs.get(selection[0])
        if selected is None:
            return
        _index, entry = selected
        if entry.group not in GROUPS:
            messagebox.showinfo(
                "Copy job parameters",
                "The selected history entry is not a Processing job with editable parameters.",
            )
            return

        available_job = next(
            (job for job in self.available_jobs.get(entry.group, ()) if job.command == entry.job_name),
            None,
        )
        if available_job is None:
            messagebox.showinfo(
                "Copy job parameters",
                "The original job is not available in the current Processing job list.",
            )
            return

        self.group_var.set(entry.group)
        self._on_group_selected()
        self.job_var.set(entry.job_name)
        self._on_job_selected()
        self.execution_mode_var.set("Submit to Slurm" if entry.execution_mode == "slurm" else "Run locally")
        self.environment_var.set(entry.environment_title or entry.parameters.get("execution_environment", self._job_environment_default()))

        for flag_name, variable in self.parameter_vars.items():
            if flag_name not in entry.parameters:
                continue
            value = entry.parameters[flag_name]
            if self.current_job is not None and self.current_job.command == "ts_export_particles" and flag_name == "--output_star":
                path_value = Path(str(value).strip()) if str(value).strip() else Path("")
                if path_value.suffix:
                    variable.set(str(path_value.parent) if str(path_value.parent) != "." else "")
                    self.export_output_name_var.set(path_value.name)
                else:
                    variable.set(value)
                continue
            if isinstance(variable, tk.BooleanVar):
                variable.set(str(value).lower() in {"1", "true", "yes", "on"})
            else:
                variable.set(value)
        self.slurm_profile_var.set(entry.slurm_profile or self.slurm_profile_var.get())
        self.slurm_overrides_ui.rebuild(entry.parameters, preserve_existing=False)
        self._toggle_slurm_controls()
        self._update_command_preview()
        self.app.status_var.set(f"Copied parameters from history entry: {entry.job_name}")

    def _current_parameter_values(self) -> dict[str, str]:
        values: dict[str, str] = {}
        if self.current_job is None:
            return values

        for flag in self.current_job.flags:
            variable = self.parameter_vars.get(flag.name)
            if variable is None:
                continue
            if self.current_job.command == "ts_export_particles" and flag.name == "--output_star":
                combined = self._combined_export_output_path()
                if combined:
                    values[flag.name] = combined
                continue
            current = variable.get()
            if flag.widget == "bool":
                if current:
                    values[flag.name] = "true"
            elif str(current).strip():
                values[flag.name] = str(current).strip()
        return values

    def _job_environment_default(self) -> str:
        if self.current_job is None:
            return "None"
        value = resolve_job_default(
            self.app.project,
            "Processing",
            self.current_job.group,
            self.current_job.command,
            "execution_environment",
            "None",
        )
        available = set(environment_titles(self.app.project))
        return value if value in available else "None"

    def _record_history_entry(self, action: str, scheduled: bool = False) -> JobHistoryEntry | None:
        if self.current_dataset is None or self.current_job is None:
            return None

        entry = create_history_entry(
            scheduled=scheduled,
            action=action,
            group=self.current_job.group,
            job_name=self.current_job.command,
            command=self._current_command_text(),
            processing_tab="Processing: WARP",
            dataset_name=self.current_dataset.dataset_name,
            execution_mode="slurm" if self.execution_mode_var.get() == "Submit to Slurm" else "local",
            slurm_profile=self.slurm_profile_var.get().strip(),
            environment_title=self.environment_var.get().strip() if self.execution_mode_var.get() == "Run locally" else "",
            parameters=self._current_parameter_values(),
        )
        if self.execution_mode_var.get() == "Run locally" and self.environment_var.get().strip():
            entry.parameters["execution_environment"] = self.environment_var.get().strip()
        entry.parameters.update(self._current_slurm_overrides())
        self.current_dataset.job_history.append(entry)
        self._refresh_history()
        return entry

    def _toggle_slurm_controls(self) -> None:
        if self.execution_mode_var.get() == "Submit to Slurm":
            self.execution_target_label.configure(text="Slurm profile")
            self.environment_combo.grid_remove()
            self.slurm_profile_combo.grid()
            self.slurm_overrides_frame.grid()
            self.slurm_profile_combo.config(state="readonly")
            self.slurm_overrides_ui.rebuild()
        else:
            self.execution_target_label.configure(text="Select environment")
            self.slurm_profile_combo.grid_remove()
            self.environment_combo.grid()
            self.slurm_overrides_frame.grid_remove()
            self.slurm_profile_combo.config(state="disabled")

    def _refresh_slurm_profiles(self) -> None:
        self.slurm_overrides_ui.refresh_profile_choices([self.slurm_profile_combo])
        self.environment_combo.configure(values=environment_titles(self.app.project))
        if self.environment_var.get() not in set(environment_titles(self.app.project)):
            self.environment_var.set("None")
        self._toggle_slurm_controls()

    def _current_slurm_overrides(self) -> dict[str, str]:
        return self.slurm_overrides_ui.metadata()

    def _slurm_override_payload(self, parameters: dict[str, str]) -> dict[str, str]:
        return slurm_override_payload(parameters)

    def _combined_export_output_path(self) -> str:
        directory = self.export_output_directory_var.get().strip()
        name = self.export_output_name_var.get().strip()
        if not name:
            return directory
        if not name.endswith(".star"):
            name = f"{name}.star"
        return str(Path(directory) / name) if directory else name

    def _dataset_derived_default(self, flag: WarpToolFlag, dataset: DatasetRecord | None) -> str:
        if dataset is None:
            if flag.name == "--device_list":
                return "0"
            return flag.default_value

        is_create_settings = self.current_job is not None and self.current_job.command == "create_settings"
        is_frame_create_settings = (
            is_create_settings and self.current_job is not None and self.current_job.group == "Frame series"
        )
        is_tilt_create_settings = (
            is_create_settings and self.current_job is not None and self.current_job.group == "Tilt series"
        )

        frame_processing_folder = Path(
            dataset.frame_series_processing_folder or (Path(dataset.processing_folder) / "warp_frameseries")
        )
        tilt_processing_folder = Path(
            dataset.tilt_series_processing_folder or (Path(dataset.processing_folder) / "warp_tiltseries")
        )
        tomostar_folder = Path(
            dataset.tilt_series_data_folder or (Path(dataset.processing_folder) / "tomostar")
        )
        frame_output = Path(
            dataset.frame_series_settings_file or (Path(dataset.processing_folder) / "warp_frameseries.settings")
        )
        tilt_output = Path(
            dataset.tilt_series_settings_file or (Path(dataset.processing_folder) / "warp_tiltseries.settings")
        )
        default_output = dataset.processing_folder
        if is_frame_create_settings:
            default_output = str(frame_output)
        elif is_tilt_create_settings:
            default_output = str(tilt_output)

        settings_default = (
            str(frame_output)
            if self.current_job is not None and self.current_job.group == "Frame series"
            else str(tilt_output)
            if self.current_job is not None and self.current_job.group == "Tilt series"
            else flag.default_value
        )

        dataset_defaults = {
            "--settings": settings_default,
            "--folder_processing": (
                str(frame_processing_folder)
                if is_frame_create_settings
                else str(tilt_processing_folder)
                if is_tilt_create_settings
                else dataset.processing_folder
            ),
            "--folder_data": str(tomostar_folder) if is_tilt_create_settings else dataset.raw_frames_folder,
            "--gain_path": dataset.gain_file,
            "--gain_file": dataset.gain_file,
            "--mdocs": dataset.mdocs_folder,
            "--frameseries": str(frame_processing_folder),
            "--output_processing": "",
            "--input_processing": "",
            "--to": dataset.raw_frames_folder,
            "--angpix": str(dataset.pixel_size),
            "--tomo_angpix": str(dataset.pixel_size),
            "--output_angpix": str(dataset.pixel_size),
            "--coords_angpix": str(dataset.pixel_size),
            "--template_angpix": str(dataset.pixel_size),
            "--tilt_exposure": str(dataset.exposure),
            "--exposure": str(dataset.exposure),
            "--tomo_dimensions": f"{dataset.tomogram_x}x{dataset.tomogram_y}x{dataset.tomogram_z}",
            "--output": (
                str(tomostar_folder)
                if self.current_job is not None and self.current_job.command == "ts_import"
                else default_output
            ),
            "--device_list": "0",
        }
        if (
            flag.name == "--c_range_max"
            and self.current_job is not None
            and self.current_job.command in {"fs_motion_and_ctf", "fs_ctf", "ts_ctf"}
        ):
            dataset_defaults["--c_range_max"] = f"{dataset.pixel_size * 2.1:.4f}"
        if flag.name == "--range_high" and self.current_job is not None and self.current_job.command == "ts_ctf":
            dataset_defaults["--range_high"] = f"{dataset.pixel_size * 2.1:.4f}"
        base_default = dataset_defaults.get(flag.name, flag.default_value)
        if self.current_job is None:
            return base_default
        return resolve_job_default(
            self.app.project,
            "Processing",
            self.current_job.group,
            self.current_job.command,
            flag.name,
            base_default,
        )

    def sync_to_project(self, project: ProjectData) -> None:
        if self.current_dataset is None or self.current_job is None:
            return

        values = self._current_parameter_values()
        if self.current_job.command == "create_settings":
            output_path = values.get("--output", "")
            folder_processing = values.get("--folder_processing", "")
            if self.current_job.group == "Frame series":
                if output_path:
                    self.current_dataset.frame_series_settings_file = output_path
                if folder_processing:
                    self.current_dataset.frame_series_processing_folder = folder_processing
            elif self.current_job.group == "Tilt series":
                if output_path:
                    self.current_dataset.tilt_series_settings_file = output_path
                if folder_processing:
                    self.current_dataset.tilt_series_processing_folder = folder_processing
                folder_data = values.get("--folder_data", "")
                if folder_data:
                    self.current_dataset.tilt_series_data_folder = folder_data
        elif self.current_job.command == "ts_import" and self.current_job.group == "Tilt series":
            output_path = values.get("--output", "")
            if output_path:
                self.current_dataset.tilt_series_data_folder = output_path

    def _current_command_text(self) -> str:
        return self.command_text.get("1.0", "end").strip()

    def _set_command_text(self, command: str) -> None:
        self.command_text.delete("1.0", "end")
        self.command_text.insert("1.0", command)

    def _update_command_preview(self) -> None:
        if self._suspend_command_preview_updates:
            return
        if self.current_job is None:
            self._set_command_text("WarpTools")
            return

        parts = [f"WarpTools {self.current_job.command}"]
        for flag in self.current_job.flags:
            variable = self.parameter_vars.get(flag.name)
            if variable is None:
                continue
            if self.current_job.command == "ts_export_particles" and flag.name == "--output_star":
                value = self._combined_export_output_path()
                if value:
                    parts.append(f"{flag.name} {shlex.quote(value)}")
                continue
            value = variable.get()
            if flag.widget == "bool":
                if value:
                    parts.append(flag.name)
            elif str(value).strip():
                parts.append(f"{flag.name} {shlex.quote(str(value).strip())}")
        self._set_command_text(" ".join(parts))
        self.sync_to_project(self.app.project)

    def _copy_command(self) -> None:
        command = self._current_command_text()
        if not command:
            return
        self.frame.clipboard_clear()
        self.frame.clipboard_append(command)
        self._record_history_entry("copied")
        self.app.status_var.set("Command copied to clipboard")

    def _schedule_command(self) -> None:
        command = self._current_command_text()
        if not command:
            return
        self._record_history_entry("scheduled", scheduled=True)
        self.app.on_project_changed("processing")
        self.app.status_var.set("Command scheduled")

    def _run_command(self) -> None:
        command = self._current_command_text()
        if not command:
            messagebox.showerror("Command missing", "There is no command to run.")
            return

        working_directory = None
        if self.current_dataset is not None and self.current_dataset.processing_folder:
            working_directory = self.current_dataset.processing_folder

        if self.execution_mode_var.get() == "Submit to Slurm":
            profile_name = self.slurm_profile_var.get().strip()
            if not profile_name and not self.app.is_debug_mode_enabled():
                messagebox.showerror("Slurm profile missing", "Please select a Slurm profile first.")
                return
            try:
                result = self.app.submit_slurm_command(
                    command,
                    profile_name=profile_name,
                    cwd=working_directory,
                    dataset_name=self.current_dataset.dataset_name if self.current_dataset else "",
                    job_name=self.current_job.command if self.current_job is not None else "processing",
                    overrides=self._slurm_override_payload(self._current_slurm_overrides()),
                )
            except Exception as exc:
                messagebox.showerror("Slurm submission failed", str(exc))
                return
            self._record_history_entry("submitted")
            entry = self.current_dataset.job_history[-1]
            entry.slurm_job_id = result.job_id
            entry.slurm_script_path = result.script_path
            self.app.on_project_changed("processing")
            self.app.status_var.set(f"Submitted to Slurm: {result.job_id or 'job submitted'}")
            return

        job_title = self.current_job.command if self.current_job is not None else "command"
        parameter_snapshot = self._current_parameter_values()
        job_group = self.current_job.group if self.current_job is not None else ""
        entry = self._record_history_entry("ran")
        activation_command = self.app.resolve_environment_activation(self.environment_var.get())
        try:
            self.app.run_managed_process_with_log(
                command,
                cwd=working_directory,
                title=f"Output: {job_title}",
                activation_command=activation_command,
                on_finished=lambda return_code, current_group=job_group, job_name=job_title, current_parameters=parameter_snapshot, history_entry=entry: self._handle_completed_local_run(
                    current_group,
                    job_name,
                    current_parameters,
                    return_code,
                    working_directory,
                    history_entry,
                ),
            )
        except Exception as exc:
            if entry is not None and self.current_dataset is not None:
                match_index = next(
                    (
                        index
                        for index, existing in enumerate(self.current_dataset.job_history)
                        if existing is entry or existing.entry_id == entry.entry_id
                    ),
                    None,
                )
                if match_index is not None:
                    del self.current_dataset.job_history[match_index]
                    self._refresh_history()
            messagebox.showerror("Run failed", str(exc))
            return

        if entry is not None:
            self.app.mark_history_entries_running([entry.entry_id])
        self.app.status_var.set(f"Started command: {job_title}")

    def _remove_selected_history_entry(self) -> None:
        if self.current_dataset is None:
            return
        selection = self.history_table.selection()
        if not selection:
            messagebox.showinfo("Remove job", "Please select a job history entry first.")
            return
        selected = self.history_entry_refs.get(selection[0])
        if selected is None:
            return
        index, _entry = selected
        del self.current_dataset.job_history[index]
        self._refresh_history()
        self.app.on_project_changed("processing")
        self.app.status_var.set("Removed selected job from history")

    def _run_scheduled_jobs(self) -> None:
        self._execute_scheduled_jobs(force_slurm=False)

    def _submit_scheduled_jobs_to_slurm(self) -> None:
        self._execute_scheduled_jobs(force_slurm=True)

    def _collective_scheduled_script(self, entries: list[JobHistoryEntry], profile_name: str, overrides: dict[str, str]) -> str:
        if self.current_dataset is None:
            raise ValueError("No dataset selected.")
        profile = find_slurm_profile(self.app.project, profile_name)
        if profile is None:
            raise ValueError("Please select a valid Slurm profile.")
        command = "\n".join(entry.command for entry in entries if entry.command.strip())
        return render_sbatch_script(
            command,
            profile,
            self.current_dataset.processing_folder or None,
            self.current_dataset.dataset_name,
            "scheduled_processing_batch",
            slurm_override_payload(overrides),
        )

    def _execute_scheduled_jobs(self, force_slurm: bool) -> None:
        if self.current_dataset is None:
            return
        scheduled_indices = [
            index
            for index, entry in enumerate(self.current_dataset.job_history)
            if is_scheduled_history_entry(entry)
        ]
        if not scheduled_indices:
            messagebox.showinfo("Run scheduled jobs", "No scheduled jobs found for this dataset.")
            return

        dataset = self.current_dataset
        forced_profile = self.slurm_profile_var.get().strip()
        if force_slurm and not forced_profile and not self.app.is_debug_mode_enabled():
            mode = ask_scheduled_slurm_mode(self.frame)
            if mode is None:
                return
            if mode == "separate":
                messagebox.showerror("Slurm profile missing", "Please select a Slurm profile first.")
                return
        elif force_slurm:
            mode = ask_scheduled_slurm_mode(self.frame)
            if mode is None:
                return
            if mode == "collective":
                dialog = CollectiveSlurmSubmissionDialog(
                    self.app,
                    self.frame,
                    initial_profile=forced_profile,
                    initial_overrides=self._current_slurm_overrides(),
                    script_builder=lambda profile_name, overrides: self._collective_scheduled_script(
                        [dataset.job_history[index] for index in scheduled_indices],
                        profile_name,
                        overrides,
                    ),
                )
                collective = dialog.show()
                if collective is None:
                    return
                collective_profile, collective_overrides = collective
                try:
                    result = self.app.submit_slurm_command(
                        "\n".join(dataset.job_history[index].command for index in scheduled_indices if dataset.job_history[index].command.strip()),
                        profile_name=collective_profile,
                        cwd=dataset.processing_folder or None,
                        dataset_name=dataset.dataset_name,
                        job_name="scheduled_processing_batch",
                        overrides=self._slurm_override_payload(collective_overrides),
                    )
                except Exception as exc:
                    messagebox.showerror("Slurm submission failed", str(exc))
                    return
                submitted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                for index in scheduled_indices:
                    self._mark_scheduled_entry_submitted(
                        dataset.job_history[index],
                        submitted_at,
                        result,
                        collective_profile,
                    )
                self.app.status_var.set(f"Submitted {len(scheduled_indices)} scheduled job(s) collectively")
                return
            forced_profile = forced_profile

        entries_snapshot = [dataset.job_history[index] for index in scheduled_indices]

        running_entry_ids = [entry.entry_id for entry in entries_snapshot]

        def start_batch(on_queue_finished=None) -> None:
            self.app.mark_history_entries_running(running_entry_ids)
            execute_scheduled_history_entries(
                self.app,
                entries_snapshot,
                cwd=dataset.processing_folder or None,
                dataset_name=dataset.dataset_name,
                force_slurm=force_slurm,
                forced_profile=forced_profile,
                wait_for_slurm_completion=force_slurm,
                on_entry_started=self._mark_scheduled_entry_started,
                on_entry_submitted=self._mark_scheduled_entry_submitted,
                on_entry_completed=self._handle_completed_history_entry,
                on_finished=lambda scheduled_count, failures: self._finish_processing_queue_run(
                    scheduled_count,
                    failures,
                    running_entry_ids,
                    on_queue_finished,
                ),
            )
            self.app.status_var.set(
                ("Submitting sequentially" if force_slurm else "Running") + f" {len(entries_snapshot)} scheduled job(s)"
            )

        if force_slurm:
            start_batch(None)
            return

        self.app.request_scheduled_batch_start(
            self.frame,
            queue_key="processing-local",
            title=f"Queued scheduled jobs: {dataset.dataset_name}",
            entry_ids=[entry.entry_id for entry in entries_snapshot],
            start_batch=start_batch,
        )

    def _mark_scheduled_entry_started(self, entry: JobHistoryEntry, started_at: str) -> None:
        entry.timestamp = started_at
        entry.action = "ran"
        self._refresh_history()
        self.app.on_project_changed("processing")

    def _handle_completed_processing_job(
        self,
        job_group: str,
        job_name: str,
        parameters: dict[str, str],
        return_code: int,
        working_directory: str | None,
    ) -> None:
        if return_code != 0 or self.app.is_debug_mode_enabled():
            return
        if job_name == "move_data":
            self._apply_move_data_updates(job_group, parameters, working_directory)

    def _handle_completed_local_run(
        self,
        job_group: str,
        job_name: str,
        parameters: dict[str, str],
        return_code: int,
        working_directory: str | None,
        history_entry: JobHistoryEntry | None,
    ) -> None:
        if history_entry is not None:
            self.app.clear_history_entries_running([history_entry.entry_id])
        self._handle_completed_processing_job(job_group, job_name, parameters, return_code, working_directory)

    def _handle_completed_history_entry(self, entry: JobHistoryEntry) -> None:
        if self.app.is_debug_mode_enabled() or entry.job_name != "move_data":
            return
        dataset = self.current_dataset
        if dataset is None or dataset.dataset_name != entry.dataset_name:
            dataset = next(
                (item for item in self.app.project.datasets if item.dataset_name == entry.dataset_name),
                None,
            )
        if dataset is None:
            return
        self._apply_move_data_updates(
            entry.group,
            entry.parameters,
            dataset.processing_folder or None,
            dataset=dataset,
        )

    def _resolve_runtime_path(self, value: str, working_directory: str | None) -> str:
        cleaned = value.strip()
        if not cleaned:
            return ""
        candidate = Path(cleaned).expanduser()
        if candidate.is_absolute():
            return str(candidate.resolve())
        if working_directory:
            return str((Path(working_directory).expanduser().resolve() / candidate).resolve())
        return str(candidate.resolve())

    def _apply_move_data_updates(
        self,
        job_group: str,
        parameters: dict[str, str],
        working_directory: str | None,
        *,
        dataset: DatasetRecord | None = None,
    ) -> None:
        target_dataset = dataset or self.current_dataset
        if target_dataset is None:
            return
        new_settings = self._resolve_runtime_path(parameters.get("--new_settings", ""), working_directory)
        if not new_settings:
            return
        settings_path = Path(new_settings)
        if not settings_path.exists():
            return
        try:
            summary = parse_warp_settings(settings_path)
        except Exception:
            return

        target_dataset.processing_folder = str(settings_path.parent)
        if summary.pixel_size:
            target_dataset.pixel_size = summary.pixel_size
        if summary.exposure:
            target_dataset.exposure = summary.exposure
        if summary.tomo_x:
            target_dataset.tomogram_x = summary.tomo_x
        if summary.tomo_y:
            target_dataset.tomogram_y = summary.tomo_y
        if summary.tomo_z:
            target_dataset.tomogram_z = summary.tomo_z

        if job_group == "Frame series":
            target_dataset.frame_series_settings_file = str(settings_path)
            if summary.data_folder:
                target_dataset.raw_frames_folder = summary.data_folder
            if summary.processing_folder:
                target_dataset.frame_series_processing_folder = summary.processing_folder
        elif job_group == "Tilt series":
            target_dataset.tilt_series_settings_file = str(settings_path)
            if summary.data_folder:
                target_dataset.tilt_series_data_folder = summary.data_folder
            if summary.processing_folder:
                target_dataset.tilt_series_processing_folder = summary.processing_folder

        if target_dataset is self.current_dataset:
            self._refresh_history()
            if self.current_job is not None:
                self._on_job_selected()
            else:
                self._on_dataset_selected()
            self.app.status_var.set("Updated dataset paths after successful move_data run")
        self.app.on_project_changed("processing")

    def _finish_scheduled_jobs_run(self, scheduled_count: int, failures: list[str]) -> None:
        self.app.clear_abort_request()
        self._refresh_history()
        self.app.on_project_changed("processing")
        if failures:
            self.app.status_var.set("Scheduled jobs stopped: " + "; ".join(failures))
            return
        self.app.status_var.set(f"Finished {scheduled_count} scheduled job(s)")

    def _finish_processing_queue_run(
        self,
        scheduled_count: int,
        failures: list[str],
        running_entry_ids: list[str],
        on_queue_finished,
    ) -> None:
        self.app.clear_history_entries_running(running_entry_ids)
        self._finish_scheduled_jobs_run(scheduled_count, failures)
        if on_queue_finished is not None:
            on_queue_finished()

    def _mark_scheduled_entry_submitted(
        self,
        entry: JobHistoryEntry,
        submitted_at: str,
        result: SlurmSubmissionResult,
        profile_name: str,
    ) -> None:
        entry.timestamp = submitted_at
        entry.action = "submitted"
        entry.execution_mode = "slurm"
        entry.slurm_profile = profile_name
        entry.slurm_job_id = result.job_id
        entry.slurm_script_path = result.script_path
        self._refresh_history()
        self.app.on_project_changed("processing")

    def _add_bool_widget(self, parent: ttk.Frame, row: int, flag: WarpToolFlag, default_value: bool) -> None:
        variable = tk.BooleanVar(value=default_value)
        check = ttk.Checkbutton(
            parent,
            text=f"{flag.name}{' *' if flag.required else ''}",
            variable=variable,
            command=self._update_command_preview,
        )
        check.grid(row=row, column=0, sticky="w", pady=(0, 2))
        ttk.Label(
            parent,
            text=flag.description,
            wraplength=900,
            justify="left",
        ).grid(row=row + 1, column=0, sticky="w", pady=(0, 10))
        self.parameter_vars[flag.name] = variable
        self.parameter_inputs[flag.name] = check

    def _browse_parameter(self, flag: WarpToolFlag) -> None:
        if self.current_job is not None and self.current_job.command == "ts_export_particles" and flag.name == "--output_star":
            value = filedialog.askdirectory(title=f"Select output directory for {flag.name}")
        elif flag.browse_mode == "dir":
            value = filedialog.askdirectory(title=f"Select value for {flag.name}")
        else:
            value = filedialog.askopenfilename(title=f"Select value for {flag.name}")

        if value and flag.name in self.parameter_vars:
            self.parameter_vars[flag.name].set(value)
            self._update_command_preview()

    def _add_text_widget(self, parent: ttk.Frame, row: int, flag: WarpToolFlag, default_value: str) -> None:
        block = ttk.Frame(parent)
        block.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        block.columnconfigure(0, weight=1)

        ttk.Label(
            block,
            text=f"{flag.name}{' *' if flag.required else ''}",
        ).grid(row=0, column=0, sticky="w", pady=(0, 2))

        variable = tk.StringVar(value=default_value)
        entry = ttk.Entry(block, textvariable=variable)
        entry.grid(row=1, column=0, sticky="ew")
        variable.trace_add("write", lambda *_args: self._update_command_preview())

        if flag.widget == "path":
            ttk.Button(
                block,
                text="Browse...",
                command=lambda current=flag: self._browse_parameter(current),
            ).grid(row=1, column=1, sticky="ew", padx=(8, 0))

        ttk.Label(
            block,
            text=flag.description,
            wraplength=900,
            justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.parameter_vars[flag.name] = variable
        self.parameter_inputs[flag.name] = entry

    def _toggle_processing_advanced(self) -> None:
        if hasattr(self, "processing_advanced_frame"):
            if self.processing_advanced_visible.get():
                self.processing_advanced_frame.grid()
                self.processing_advanced_button.config(text="Hide advanced settings")
            else:
                self.processing_advanced_frame.grid_remove()
                self.processing_advanced_button.config(text="Show advanced settings")

    def _ensure_parameter_rows(
        self,
        rows: list[dict[str, object]],
        parent: ttk.Frame,
        count: int,
    ) -> None:
        while len(rows) < count:
            row_frame = ttk.Frame(parent)
            row_frame.columnconfigure(0, weight=1)
            label_widget = ttk.Label(row_frame)
            label_widget.grid(row=0, column=0, sticky="w", pady=(0, 2))
            control_frame = ttk.Frame(row_frame)
            control_frame.grid(row=1, column=0, sticky="ew")
            control_frame.columnconfigure(0, weight=1)
            description_widget = ttk.Label(row_frame, wraplength=900, justify="left")
            description_widget.grid(row=2, column=0, sticky="w", pady=(4, 0))
            rows.append(
                {
                    "frame": row_frame,
                    "label": label_widget,
                    "control": control_frame,
                    "description": description_widget,
                    "widget_kind": "",
                    "value_var": None,
                    "value_widget": None,
                    "browse_button": None,
                    "check_widget": None,
                }
            )

    def _configure_parameter_row(
        self,
        rows: list[dict[str, object]],
        index: int,
        parent: ttk.Frame,
        flag: WarpToolFlag,
        default_value: str,
    ) -> None:
        self._ensure_parameter_rows(rows, parent, index + 1)
        row = rows[index]
        row_frame = row["frame"]
        label_widget = row["label"]
        control_frame = row["control"]
        description_widget = row["description"]
        row_frame.grid(row=index, column=0, sticky="ew", pady=(0, 10))
        description_widget.config(text=flag.description)

        desired_kind = (
            "export_output"
            if self.current_job is not None and self.current_job.command == "ts_export_particles" and flag.name == "--output_star"
            else "bool" if flag.widget == "bool" else "path" if flag.widget == "path" else "text"
        )
        if row["widget_kind"] != desired_kind:
            for child in control_frame.winfo_children():
                child.destroy()
            row["browse_button"] = None
            row["check_widget"] = None
            if desired_kind == "bool":
                value_var: tk.Variable = tk.BooleanVar()
                check = ttk.Checkbutton(
                    control_frame,
                    variable=value_var,
                    command=self._update_command_preview,
                )
                check.grid(row=0, column=0, sticky="w")
                row["value_widget"] = check
                row["check_widget"] = check
            elif desired_kind == "export_output":
                value_var = self.export_output_directory_var
                name_var = self.export_output_name_var
                entry = ttk.Entry(control_frame, textvariable=value_var)
                entry.grid(row=0, column=0, sticky="ew")
                browse = ttk.Button(
                    control_frame,
                    text="Browse dir",
                    command=lambda current=flag: self._browse_parameter(current),
                )
                browse.grid(row=0, column=1, sticky="ew", padx=(8, 0))
                ttk.Label(control_frame, text="Name").grid(row=0, column=2, padx=(12, 4), sticky="w")
                ttk.Entry(control_frame, textvariable=name_var, width=24).grid(row=0, column=3, sticky="ew")
                value_var.trace_add("write", lambda *_args: self._update_command_preview())
                name_var.trace_add("write", lambda *_args: self._update_command_preview())
                row["value_widget"] = entry
                row["browse_button"] = browse
            else:
                value_var = tk.StringVar()
                entry = ttk.Entry(control_frame, textvariable=value_var)
                entry.grid(row=0, column=0, sticky="ew")
                value_var.trace_add("write", lambda *_args: self._update_command_preview())
                row["value_widget"] = entry
                if desired_kind == "path":
                    browse = ttk.Button(
                        control_frame,
                        text="Browse...",
                        command=lambda current=flag: self._browse_parameter(current),
                    )
                    browse.grid(row=0, column=1, sticky="ew", padx=(8, 0))
                    row["browse_button"] = browse
            row["value_var"] = value_var
            row["widget_kind"] = desired_kind
        else:
            value_var = row["value_var"]
            if desired_kind in {"path", "export_output"}:
                browse = row["browse_button"]
                if browse is not None:
                    browse.configure(command=lambda current=flag: self._browse_parameter(current))

        if desired_kind == "bool":
            label_widget.grid_remove()
            check_widget = row["check_widget"]
            if check_widget is not None:
                check_widget.configure(
                    text=f"{flag.name}{' *' if flag.required else ''}",
                )
            assert isinstance(value_var, tk.BooleanVar)
            value_var.set(default_value.lower() in {"1", "true", "yes", "on"})
        elif desired_kind == "export_output":
            label_widget.grid()
            label_widget.config(text=f"{flag.name}{' *' if flag.required else ''}")
            assert isinstance(value_var, tk.StringVar)
            path_value = Path(default_value.strip()) if default_value.strip() else Path("")
            if path_value.suffix:
                value_var.set(str(path_value.parent) if str(path_value.parent) != "." else "")
                self.export_output_name_var.set(path_value.name)
            else:
                value_var.set(default_value)
                if not self.export_output_name_var.get().strip():
                    self.export_output_name_var.set("Output.star")
        else:
            label_widget.grid()
            label_widget.config(text=f"{flag.name}{' *' if flag.required else ''}")
            assert isinstance(value_var, tk.StringVar)
            value_var.set(default_value)

        self.parameter_vars[flag.name] = value_var
        self.parameter_inputs[flag.name] = row["value_widget"]

    def _hide_unused_parameter_rows(self, rows: list[dict[str, object]], used_count: int) -> None:
        for row in rows[used_count:]:
            frame = row["frame"]
            if isinstance(frame, ttk.Frame):
                frame.grid_remove()

    def _build_parameter_form(self) -> None:
        self._suspend_command_preview_updates = True
        self.parameter_vars.clear()
        self.parameter_inputs.clear()

        if self.current_job is None:
            self.parameters_box.grid_remove()
            self._suspend_command_preview_updates = False
            self._update_command_preview()
            return

        self.parameters_box.grid()
        required_flags: list[WarpToolFlag] = []
        advanced_flags: list[WarpToolFlag] = []
        for flag in self.current_job.flags:
            if (
                self.current_job.command == "create_settings"
                and self.current_job.group == "Frame series"
                and flag.name == "--tomo_dimensions"
            ):
                continue
            if flag.required:
                required_flags.append(flag)
            else:
                advanced_flags.append(flag)

        for row, flag in enumerate(required_flags):
            default_value = self._dataset_derived_default(flag, self.current_dataset)
            self._configure_parameter_row(self._required_param_rows, row, self.parameter_frame, flag, default_value)
        self._hide_unused_parameter_rows(self._required_param_rows, len(required_flags))

        if advanced_flags:
            advanced_button_row = len(required_flags)
            self.processing_advanced_button.grid(row=advanced_button_row, column=0, sticky="w", pady=(8, 8))
            self.processing_advanced_frame.grid(
                row=advanced_button_row + 1,
                column=0,
                sticky="ew",
                pady=(0, 8),
            )
            for advanced_row, flag in enumerate(advanced_flags):
                default_value = self._dataset_derived_default(flag, self.current_dataset)
                self._configure_parameter_row(
                    self._advanced_param_rows,
                    advanced_row,
                    self.processing_advanced_frame,
                    flag,
                    default_value,
                )
            self._hide_unused_parameter_rows(self._advanced_param_rows, len(advanced_flags))
            self._toggle_processing_advanced()
        else:
            self.processing_advanced_button.grid_remove()
            self.processing_advanced_frame.grid_remove()
            self._hide_unused_parameter_rows(self._advanced_param_rows, 0)

        self._suspend_command_preview_updates = False
        self._update_command_preview()

    def _on_group_selected(self, _event=None) -> None:
        jobs = self._jobs_for_current_group()
        self.job_combo.configure(
            state="readonly" if jobs else "disabled",
            values=[job.command for job in jobs],
        )
        self.job_var.set("")
        self.current_job = None
        self._build_parameter_form()
        self._scroll_job_view_to_top()
        if self.current_dataset is not None:
            self.dataset_summary.config(
                text=(
                    f"Selected dataset: {self.current_dataset.dataset_name}\n"
                    f"Job group: {self.group_var.get()} ({len(jobs)} jobs)"
                )
            )

    def _on_dataset_selected(self, _event=None) -> None:
        dataset_name = self.dataset_var.get()
        if dataset_name:
            self.current_dataset = self._dataset_map(self.app.project).get(dataset_name)
            self.job_box.grid()
            self._refresh_history()
            self._on_group_selected()
            self.dataset_summary.config(text=f"Selected dataset: {dataset_name}")
        else:
            self.current_dataset = None
            self.job_box.grid_remove()
            self.history_box.grid_remove()
            self.parameters_box.grid_remove()
            self._set_command_text("WarpTools")
            self.dataset_summary.config(text="")

    def _on_job_selected(self, _event=None) -> None:
        selected_command = self.job_var.get()
        self.current_job = next(
            (job for job in self._jobs_for_current_group() if job.command == selected_command),
            None,
        )
        self.environment_var.set(self._job_environment_default())
        self._build_parameter_form()
        self._scroll_job_view_to_top()
        if self.current_dataset is not None and self.current_job is not None:
            self.dataset_summary.config(
                text=(
                    f"Selected dataset: {self.current_dataset.dataset_name}\n"
                    f"Selected job: {self.current_job.command} ({self.group_var.get()})"
                )
            )

    def on_project_loaded(self, project: ProjectData) -> None:
        dataset_names = [dataset.dataset_name for dataset in project.datasets]
        self.dataset_combo.configure(values=dataset_names)
        self.available_jobs = jobs_by_group()
        self._refresh_slurm_profiles()

        current = self.dataset_var.get()
        if current not in dataset_names:
            self.dataset_var.set("")
            self.job_var.set("")
            self.current_dataset = None
            self.current_job = None
            self.job_box.grid_remove()
            self.history_box.grid_remove()
            self.parameters_box.grid_remove()
            if dataset_names:
                self.dataset_summary.config(
                    text="Datasets loaded. Choose one above to continue."
                )
            else:
                self.dataset_summary.config(
                    text="No datasets available yet. Add datasets in Project Overview first."
                )
        else:
            self._on_dataset_selected()
