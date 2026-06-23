from __future__ import annotations

import shlex
import tkinter as tk
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from cryoet_organizer.custom_jobs import (
    CustomJobDefinition,
    CustomJobParameter,
    get_project_custom_jobs,
    set_project_custom_jobs,
)
from cryoet_organizer.dialogs import bind_scrollable_canvas
from cryoet_organizer.environments import environment_titles
from cryoet_organizer.file_resolver import resolve_dataset_file
from cryoet_organizer.file_resolver import file_role_order, role_title
from cryoet_organizer.job_execution import (
    build_slurm_override_metadata,
    execute_command_sequence,
    slurm_override_payload,
)
from cryoet_organizer.project import (
    JobHistoryEntry,
    dataset_ts_names,
)
from cryoet_organizer.slurm import SlurmSubmissionResult
from cryoet_organizer.slurm_override_ui import SlurmOverrideUI
from cryoet_organizer.tabs.base import SidebarTab


BASE_INPUT_TYPE_OPTIONS = (
    "text",
    "path",
    "file",
    "bool",
    "All files that: [*.mrc]",
    "All files that: [*.st]",
    "All files that: [*.star]",
    "All files that: [*.json]",
    "All files that: custom pattern",
)

INPUT_TYPE_MAP = {
    "All files that: [*.mrc]": "all_files_mrc",
    "All files that: [*.st]": "all_files_st",
    "All files that: [*.star]": "all_files_star",
    "All files that: [*.json]": "all_files_json",
    "All files that: custom pattern": "all_files_custom_pattern",
}


def runtime_input_type_options(project) -> tuple[str, ...]:
    options = list(BASE_INPUT_TYPE_OPTIONS[:4])
    for role in file_role_order(project):
        options.append(f"TS selection: {role_title(project, role)}")
    options.extend(BASE_INPUT_TYPE_OPTIONS[4:])
    return tuple(options)


def display_input_type(project, stored: str) -> str:
    if stored.startswith("ts_role:"):
        role = stored.split(":", 1)[1]
        return f"TS selection: {role_title(project, role)}"
    if stored in {"ts_aligned_stack", "ts_angle_file", "ts_tomogram"}:
        legacy_map = {
            "ts_aligned_stack": "aligned_stack",
            "ts_angle_file": "angle_file",
            "ts_tomogram": "tomogram",
        }
        role = legacy_map[stored]
        return f"TS selection: {role_title(project, role)}"
    return {value: key for key, value in INPUT_TYPE_MAP.items()}.get(stored, stored or "text")


def stored_input_type(project, displayed: str) -> str:
    if displayed.startswith("TS selection: "):
        label = displayed.removeprefix("TS selection: ").strip()
        for role in file_role_order(project):
            if role_title(project, role) == label:
                return f"ts_role:{role}"
    return INPUT_TYPE_MAP.get(displayed, displayed or "text")


class CustomTab(SidebarTab):
    tab_id = "custom"
    title = "Processing: Custom jobs"
    refresh_domains = ("custom", "datasets", "defaults", "file_registry", "ts_selection", "environments")

    def build(self) -> None:
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(2, weight=1)
        self.jobs: list[CustomJobDefinition] = []
        self.current_job: CustomJobDefinition | None = None
        self.job_type_var = tk.StringVar(value="Build custom job type...")
        self.builder_name_var = tk.StringVar()
        self.builder_validation_var = tk.StringVar()
        self.builder_environment_var = tk.StringVar(value="None")
        self.execution_mode_var = tk.StringVar(value="Run locally")
        self.environment_var = tk.StringVar(value="None")
        self.slurm_profile_var = tk.StringVar()
        self.slurm_partition_var = tk.StringVar()
        self.slurm_time_var = tk.StringVar()
        self.slurm_gpus_var = tk.StringVar()
        self.slurm_cpus_var = tk.StringVar()
        self.slurm_mem_var = tk.StringVar()
        self.slurm_mem_per_cpu_var = tk.StringVar()
        self.slurm_mem_mode_var = tk.StringVar(value="mem")
        self.slurm_overrides_ui = SlurmOverrideUI(self.app, self.slurm_profile_var)
        self.runtime_state: dict[str, dict[str, tk.Variable]] = {}
        self.parameter_rows: list[dict[str, tk.Variable]] = []

        intro = ttk.Label(
            self.frame,
            text=(
                "This section is for custom job types in CryoPal_tomo. "
                "To create a new one, select 'Build custom job type...'."
            ),
            wraplength=940,
            justify="left",
        )
        intro.grid(row=0, column=0, sticky="ew")

        selector = ttk.LabelFrame(self.frame, text="Job type", padding=12)
        selector.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        selector.columnconfigure(1, weight=1)
        ttk.Label(selector, text="Select job type").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.job_combo = ttk.Combobox(selector, textvariable=self.job_type_var, state="readonly")
        self.job_combo.grid(row=0, column=1, sticky="ew")
        self.job_combo.bind("<<ComboboxSelected>>", self._on_job_selection_changed)

        self.builder_frame = ttk.Frame(self.frame)
        self.builder_frame.grid(row=2, column=0, sticky="nsew", pady=(12, 0))
        self.builder_frame.columnconfigure(0, weight=1)
        self.builder_frame.rowconfigure(1, weight=1)

        self.runtime_frame = ttk.Frame(self.frame)
        self.runtime_frame.grid(row=2, column=0, sticky="nsew", pady=(12, 0))
        self.runtime_frame.columnconfigure(0, weight=1)
        self.runtime_frame.rowconfigure(2, weight=1)

        self._build_builder_ui()
        self._build_runtime_ui()
        self._refresh_job_options()
        self._show_builder()

    def _build_builder_ui(self) -> None:
        builder_meta = ttk.LabelFrame(self.builder_frame, text="Build custom job type", padding=12)
        builder_meta.grid(row=0, column=0, sticky="ew")
        builder_meta.columnconfigure(1, weight=1)

        ttk.Label(builder_meta, text="Job name").grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(builder_meta, textvariable=self.builder_name_var).grid(row=0, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(builder_meta, text="Default local environment").grid(row=1, column=0, sticky="w", pady=(0, 4))
        self.builder_environment_combo = ttk.Combobox(
            builder_meta,
            textvariable=self.builder_environment_var,
            state="readonly",
            values=environment_titles(self.app.project),
        )
        self.builder_environment_combo.grid(row=1, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(builder_meta, text="Description").grid(row=2, column=0, sticky="nw", pady=(0, 4))
        self.builder_description_text = tk.Text(builder_meta, height=5, wrap="word")
        self.builder_description_text.grid(row=2, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(builder_meta, text="Command template").grid(row=3, column=0, sticky="nw", pady=(0, 4))
        self.builder_command_text = tk.Text(builder_meta, height=3, wrap="word")
        self.builder_command_text.grid(row=3, column=1, sticky="ew")

        ttk.Label(
            builder_meta,
            text="The command template is the fixed base command. Flags and values from the table below are appended automatically.",
            wraplength=720,
            justify="left",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))

        ttk.Label(
            builder_meta,
            textvariable=self.builder_validation_var,
            style="Error.TLabel",
            wraplength=720,
            justify="left",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))

        params_box = ttk.LabelFrame(self.builder_frame, text="Custom parameters", padding=12)
        params_box.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        params_box.columnconfigure(0, weight=1)
        params_box.rowconfigure(0, weight=1)

        self.params_canvas = tk.Canvas(params_box, highlightthickness=0)
        self.params_canvas.grid(row=0, column=0, sticky="nsew")
        params_scroll = ttk.Scrollbar(params_box, orient="vertical", command=self.params_canvas.yview)
        params_scroll.grid(row=0, column=1, sticky="ns")
        params_xscroll = ttk.Scrollbar(params_box, orient="horizontal", command=self.params_canvas.xview)
        params_xscroll.grid(row=1, column=0, sticky="ew")
        self.params_canvas.configure(yscrollcommand=params_scroll.set)
        self.params_canvas.configure(xscrollcommand=params_xscroll.set)
        self.params_rows_frame = ttk.Frame(self.params_canvas)
        for column in range(4):
            self.params_rows_frame.columnconfigure(column, weight=1 if column in {0, 1, 3} else 0)
        self.params_window = self.params_canvas.create_window((0, 0), window=self.params_rows_frame, anchor="nw")
        bind_scrollable_canvas(self.params_canvas, self.params_window, self.params_rows_frame, allow_horizontal=True)

        builder_actions = ttk.Frame(self.builder_frame)
        builder_actions.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        builder_actions.columnconfigure(0, weight=1)
        ttk.Button(builder_actions, text="Add parameter row", command=self._add_builder_row).grid(row=0, column=0, sticky="w")
        ttk.Button(builder_actions, text="Save custom job type", command=self._save_custom_job).grid(row=0, column=1, padx=(8, 0))

        self._add_builder_row()

    def _build_runtime_ui(self) -> None:
        self.runtime_command_text = self._build_command_section(self.runtime_frame, 0)

        self.ts_list_frame = ttk.LabelFrame(self.runtime_frame, text="TS processing list", padding=12)
        self.ts_list_frame.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self.ts_list_frame.columnconfigure(0, weight=1)
        self.ts_table = ttk.Treeview(self.ts_list_frame, columns=("dataset_name", "ts_name"), show="headings", height=5)
        self.ts_table.heading("dataset_name", text="Dataset")
        self.ts_table.heading("ts_name", text="TS")
        self.ts_table.column("dataset_name", width=220, anchor="w")
        self.ts_table.column("ts_name", width=260, anchor="w")
        self.ts_table.grid(row=0, column=0, sticky="ew")
        ts_scroll = ttk.Scrollbar(self.ts_list_frame, orient="vertical", command=self.ts_table.yview)
        ts_scroll.grid(row=0, column=1, sticky="ns")
        self.ts_table.configure(yscrollcommand=ts_scroll.set)
        self.ts_summary_var = tk.StringVar(value="0 TS in global processing list")
        ttk.Label(self.ts_list_frame, textvariable=self.ts_summary_var).grid(row=1, column=0, sticky="w", pady=(8, 0))

        self.runtime_params_box = ttk.LabelFrame(self.runtime_frame, text="Custom job parameters", padding=12)
        self.runtime_params_box.grid(row=2, column=0, sticky="nsew", pady=(12, 0))
        self.runtime_params_box.columnconfigure(0, weight=1)
        self.runtime_params_box.rowconfigure(0, weight=1)
        self.runtime_canvas = tk.Canvas(self.runtime_params_box, highlightthickness=0)
        self.runtime_canvas.grid(row=0, column=0, sticky="nsew")
        runtime_scroll = ttk.Scrollbar(self.runtime_params_box, orient="vertical", command=self.runtime_canvas.yview)
        runtime_scroll.grid(row=0, column=1, sticky="ns")
        runtime_xscroll = ttk.Scrollbar(self.runtime_params_box, orient="horizontal", command=self.runtime_canvas.xview)
        runtime_xscroll.grid(row=1, column=0, sticky="ew")
        self.runtime_canvas.configure(yscrollcommand=runtime_scroll.set)
        self.runtime_canvas.configure(xscrollcommand=runtime_xscroll.set)
        self.runtime_params_frame = ttk.Frame(self.runtime_canvas)
        self.runtime_params_frame.columnconfigure(1, weight=1)
        self.runtime_window = self.runtime_canvas.create_window((0, 0), window=self.runtime_params_frame, anchor="nw")
        bind_scrollable_canvas(
            self.runtime_canvas,
            self.runtime_window,
            self.runtime_params_frame,
            allow_horizontal=True,
        )

    def _build_command_section(self, parent, row: int):
        box = ttk.LabelFrame(parent, text="Command preview", padding=12)
        box.grid(row=row, column=0, sticky="ew")
        box.columnconfigure(0, weight=1)
        actions = ttk.Frame(box)
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        actions.columnconfigure(0, weight=1)
        ttk.Label(actions, text="Execution").grid(row=0, column=1, sticky="e", padx=(0, 8))
        self.execution_mode_combo = ttk.Combobox(
            actions,
            textvariable=self.execution_mode_var,
            state="readonly",
            values=("Run locally", "Submit to Slurm"),
            width=18,
        )
        self.execution_mode_combo.grid(row=0, column=2, sticky="e")
        self.execution_mode_combo.bind("<<ComboboxSelected>>", lambda _event: self._toggle_slurm_controls())
        self.execution_target_label = ttk.Label(actions, text="Select environment")
        self.execution_target_label.grid(row=0, column=3, sticky="e", padx=(12, 8))
        self.environment_combo = ttk.Combobox(
            actions,
            textvariable=self.environment_var,
            state="readonly",
            width=18,
        )
        self.environment_combo.grid(row=0, column=4, sticky="e")
        self.slurm_profile_combo = ttk.Combobox(
            actions,
            textvariable=self.slurm_profile_var,
            state="disabled",
            width=18,
        )
        self.slurm_profile_combo.grid(row=0, column=4, sticky="e")
        self.slurm_profile_combo.bind("<<ComboboxSelected>>", lambda _event: self.slurm_overrides_ui.rebuild(preserve_existing=False))
        ttk.Button(actions, text="Copy command", command=self._copy_commands).grid(row=0, column=5, padx=(8, 0))
        ttk.Button(actions, text="Run command", command=self._run_commands).grid(row=0, column=6, padx=(8, 0))
        abort_button = ttk.Button(actions, text="Abort", command=self.app.abort_running_commands, state="disabled")
        abort_button.grid(row=0, column=7, padx=(8, 0))
        self.app.attach_abort_button(abort_button)
        self.slurm_overrides_frame = ttk.Frame(box)
        self.slurm_overrides_frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.slurm_overrides_ui.register_frame(self.slurm_overrides_frame)
        text = tk.Text(box, height=10, wrap="word", font="TkDefaultFont")
        text.grid(row=2, column=0, sticky="ew")
        self._toggle_slurm_controls()
        return text

    def _new_parameter_row(self) -> dict[str, tk.Variable]:
        return {
            "label": tk.StringVar(),
            "flag": tk.StringVar(),
            "widget": tk.StringVar(value="text"),
            "default_text": tk.StringVar(),
            "default_bool": tk.BooleanVar(value=False),
            "pattern_text": tk.StringVar(),
        }

    def _display_input_type(self, stored: str) -> str:
        return display_input_type(self.app.project, stored)

    def _stored_input_type(self, displayed: str) -> str:
        return stored_input_type(self.app.project, displayed)

    def _browse_builder_default(self, variable: tk.Variable, mode: str) -> None:
        if mode == "path" or mode.startswith("all_files_"):
            path = filedialog.askdirectory(title="Select directory")
        else:
            path = filedialog.askopenfilename(title="Select file")
        if path:
            variable.set(path)

    def _rebuild_builder_rows(self) -> None:
        for child in self.params_rows_frame.winfo_children():
            child.destroy()
        headings = ("Description", "Flag", "Input type", "Default", "")
        for column, heading in enumerate(headings):
            ttk.Label(self.params_rows_frame, text=heading).grid(row=0, column=column, sticky="w", padx=(0, 8))
        for row_index, row in enumerate(self.parameter_rows, start=1):
            ttk.Entry(self.params_rows_frame, textvariable=row["label"]).grid(row=row_index, column=0, sticky="ew", padx=(0, 8), pady=4)
            ttk.Entry(self.params_rows_frame, textvariable=row["flag"]).grid(row=row_index, column=1, sticky="ew", padx=(0, 8), pady=4)
            combo = ttk.Combobox(
                self.params_rows_frame,
                textvariable=row["widget"],
                state="readonly",
                values=runtime_input_type_options(self.app.project),
                width=34,
            )
            combo.grid(row=row_index, column=2, sticky="ew", padx=(0, 8), pady=4)
            combo.bind("<<ComboboxSelected>>", lambda _event, current=row: self._on_builder_type_changed(current))

            default_cell = ttk.Frame(self.params_rows_frame)
            default_cell.grid(row=row_index, column=3, sticky="ew", padx=(0, 8), pady=4)
            default_cell.columnconfigure(0, weight=1)
            widget = self._stored_input_type(str(row["widget"].get()))
            if widget == "text":
                ttk.Entry(default_cell, textvariable=row["default_text"]).grid(row=0, column=0, sticky="ew")
            elif widget == "path":
                ttk.Entry(default_cell, textvariable=row["default_text"]).grid(row=0, column=0, sticky="ew")
                ttk.Button(
                    default_cell,
                    text="Browse dir",
                    command=lambda current=row["default_text"]: self._browse_builder_default(current, "path"),
                ).grid(row=0, column=1, padx=(8, 0))
            elif widget == "file":
                ttk.Entry(default_cell, textvariable=row["default_text"]).grid(row=0, column=0, sticky="ew")
                ttk.Button(
                    default_cell,
                    text="Browse file",
                    command=lambda current=row["default_text"]: self._browse_builder_default(current, "file"),
                ).grid(row=0, column=1, padx=(8, 0))
            elif widget == "bool":
                ttk.Checkbutton(default_cell, variable=row["default_bool"]).grid(row=0, column=0, sticky="w")
            elif widget.startswith("ts_"):
                ttk.Label(default_cell, text="From TS processing list").grid(row=0, column=0, sticky="w")
            elif widget == "all_files_custom_pattern":
                ttk.Entry(default_cell, textvariable=row["default_text"]).grid(row=0, column=0, sticky="ew")
                ttk.Button(
                    default_cell,
                    text="Browse dir",
                    command=lambda current=row["default_text"]: self._browse_builder_default(current, "all_files"),
                ).grid(row=0, column=1, padx=(8, 0))
                ttk.Label(default_cell, text="Pattern").grid(row=0, column=2, padx=(8, 4), sticky="w")
                ttk.Entry(default_cell, textvariable=row["pattern_text"], width=18).grid(row=0, column=3, padx=(0, 0))
            else:
                ttk.Entry(default_cell, textvariable=row["default_text"]).grid(row=0, column=0, sticky="ew")
                ttk.Button(
                    default_cell,
                    text="Browse dir",
                    command=lambda current=row["default_text"]: self._browse_builder_default(current, "all_files"),
                ).grid(row=0, column=1, padx=(8, 0))
            ttk.Button(self.params_rows_frame, text="Remove", command=lambda current=row: self._remove_builder_row(current)).grid(row=row_index, column=4, sticky="w", pady=4)
        self._update_builder_validation()

    def _on_builder_type_changed(self, _row: dict[str, tk.Variable]) -> None:
        self._rebuild_builder_rows()

    def _add_builder_row(self) -> None:
        self.parameter_rows.append(self._new_parameter_row())
        self._rebuild_builder_rows()

    def _remove_builder_row(self, row: dict[str, tk.Variable]) -> None:
        self.parameter_rows = [item for item in self.parameter_rows if item is not row]
        self._rebuild_builder_rows()

    def _builder_parameter_widgets(self) -> list[str]:
        widgets: list[str] = []
        for row in self.parameter_rows:
            label = str(row["label"].get()).strip()
            flag = str(row["flag"].get()).strip()
            widget = self._stored_input_type(str(row["widget"].get()).strip() or "text")
            default_text = str(row["default_text"].get()).strip()
            default_bool = bool(row["default_bool"].get())
            pattern_text = str(row["pattern_text"].get()).strip()
            if not label and not flag and not default_text and not default_bool and not pattern_text and widget == "text":
                continue
            widgets.append(widget)
        return widgets

    def _builder_validation_message(self) -> str:
        widgets = self._builder_parameter_widgets()
        ts_count = sum(widget.startswith("ts_") for widget in widgets)
        all_files_count = sum(widget.startswith("all_files_") for widget in widgets)
        if ts_count and all_files_count:
            return "TS selection parameters cannot be combined with 'All files that...' parameters in the same custom job type."
        if all_files_count > 1:
            return "Only one 'All files that...' parameter is currently supported in a custom job type."
        return ""

    def _update_builder_validation(self) -> None:
        self.builder_validation_var.set(self._builder_validation_message())

    def _refresh_job_options(self) -> None:
        self.jobs = get_project_custom_jobs(self.app.project)
        values = ["Build custom job type..."] + [job.name for job in self.jobs]
        self.job_combo.configure(values=values)
        environment_values = environment_titles(self.app.project)
        available_environments = set(environment_values)
        self.builder_environment_combo.configure(values=environment_values)
        if self.builder_environment_var.get() not in available_environments:
            self.builder_environment_var.set("None")
        if self.job_type_var.get() not in values:
            self.job_type_var.set("Build custom job type...")
        if self.job_type_var.get() == "Build custom job type...":
            self._rebuild_builder_rows()

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

    def _show_builder(self) -> None:
        self.runtime_frame.grid_remove()
        self.builder_frame.grid()

    def _show_runtime(self) -> None:
        self.builder_frame.grid_remove()
        self.runtime_frame.grid()

    def _scroll_active_view_to_top(self, *, builder: bool = False) -> None:
        if builder:
            self.params_canvas.yview_moveto(0)
            self.params_canvas.xview_moveto(0)
            return
        self.runtime_canvas.yview_moveto(0)
        self.runtime_canvas.xview_moveto(0)

    def _global_ts_entries(self) -> list[dict[str, str]]:
        stored = self.app.project.state.tomograms_selection
        return [
            {"dataset_name": str(item.get("dataset_name", "")), "ts_name": str(item.get("ts_name", ""))}
            for item in stored
            if isinstance(item, dict)
        ]

    def _refresh_ts_table(self) -> None:
        self.ts_table.delete(*self.ts_table.get_children())
        entries = self._global_ts_entries()
        for index, entry in enumerate(entries):
            self.ts_table.insert("", "end", iid=str(index), values=(entry["dataset_name"], entry["ts_name"]))
        dataset_count = len({entry["dataset_name"] for entry in entries})
        self.ts_summary_var.set(f"{len(entries)} TS in global processing list across {dataset_count} dataset(s)")

    def _on_job_selection_changed(self, _event=None) -> None:
        selected = self.job_type_var.get()
        if selected == "Build custom job type...":
            self.current_job = None
            self._show_builder()
            self._scroll_active_view_to_top(builder=True)
            return
        job = next((item for item in self.jobs if item.name == selected), None)
        if job is None:
            self.current_job = None
            self._show_builder()
            self._scroll_active_view_to_top(builder=True)
            return
        self.current_job = job
        available_environments = set(environment_titles(self.app.project))
        self.environment_var.set(job.environment_title if job.environment_title in available_environments else "None")
        self._build_runtime_form(job)
        self._show_runtime()
        self._scroll_active_view_to_top()

    def _build_runtime_form(self, job: CustomJobDefinition) -> None:
        for child in self.runtime_params_frame.winfo_children():
            child.destroy()
        self.runtime_state.clear()
        self._refresh_ts_table()
        row = 0
        has_ts_inputs = any(parameter.widget.startswith("ts_") for parameter in job.parameters)
        if has_ts_inputs:
            self.ts_list_frame.grid()
        else:
            self.ts_list_frame.grid_remove()

        for parameter in job.parameters:
            state: dict[str, tk.Variable] = {}
            input_type = parameter.widget
            if input_type == "bool":
                value_var: tk.Variable = tk.BooleanVar(value=parameter.default.lower() in {"1", "true", "yes", "on"})
                ttk.Checkbutton(
                    self.runtime_params_frame,
                    text=parameter.label or parameter.key,
                    variable=value_var,
                    command=self._update_preview,
                ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 8))
                state["value"] = value_var
            elif input_type in {"text", "path", "file"}:
                value_var = tk.StringVar(value=parameter.default)
                ttk.Label(self.runtime_params_frame, text=parameter.label or parameter.key).grid(row=row, column=0, sticky="w", pady=(0, 4))
                editor = ttk.Frame(self.runtime_params_frame)
                editor.grid(row=row, column=1, sticky="ew", pady=(0, 8))
                editor.columnconfigure(0, weight=1)
                ttk.Entry(editor, textvariable=value_var).grid(row=0, column=0, sticky="ew")
                if input_type == "path":
                    ttk.Button(
                        editor,
                        text="Browse dir",
                        command=lambda current=value_var: self._browse_runtime_input(current, "path"),
                    ).grid(row=0, column=1, padx=(8, 0))
                elif input_type == "file":
                    ttk.Button(
                        editor,
                        text="Browse file",
                        command=lambda current=value_var: self._browse_runtime_input(current, "file"),
                    ).grid(row=0, column=1, padx=(8, 0))
                state["value"] = value_var
            elif input_type.startswith("ts_"):
                ttk.Label(
                    self.runtime_params_frame,
                    text=f"{parameter.label or parameter.key}: resolved from the global TS processing list",
                ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 8))
            else:
                directory_var = tk.StringVar(value=parameter.default)
                ttk.Label(self.runtime_params_frame, text=parameter.label or parameter.key).grid(row=row, column=0, sticky="w", pady=(0, 4))
                editor = ttk.Frame(self.runtime_params_frame)
                editor.grid(row=row, column=1, sticky="ew", pady=(0, 8))
                editor.columnconfigure(0, weight=1)
                ttk.Entry(editor, textvariable=directory_var).grid(row=0, column=0, sticky="ew")
                ttk.Button(
                    editor,
                    text="Browse dir",
                    command=lambda current=directory_var: self._browse_runtime_input(current, "all_files"),
                ).grid(row=0, column=1, padx=(8, 0))
                state["directory"] = directory_var
                if input_type == "all_files_custom_pattern":
                    pattern_var = tk.StringVar(value=parameter.extra.get("pattern", ""))
                    ttk.Label(editor, text="Pattern").grid(row=0, column=2, padx=(8, 4), sticky="w")
                    ttk.Entry(editor, textvariable=pattern_var, width=18).grid(row=0, column=3, padx=(0, 0))
                    state["pattern"] = pattern_var
            for variable in state.values():
                variable.trace_add("write", lambda *_args: self._update_preview())
            self.runtime_state[parameter.key] = state
            row += 1
        self._update_preview()

    def _browse_runtime_input(self, variable: tk.Variable, input_type: str) -> None:
        if input_type in {"path", "all_files"}:
            path = filedialog.askdirectory(title="Select directory")
        else:
            path = filedialog.askopenfilename(title="Select file")
        if path:
            variable.set(path)

    def _save_custom_job(self) -> None:
        name = self.builder_name_var.get().strip()
        if not name:
            messagebox.showinfo("Save custom job type", "Please provide a job name first.")
            return
        command_template = self.builder_command_text.get("1.0", "end").strip()
        if not command_template:
            messagebox.showinfo("Save custom job type", "Please provide a command template first.")
            return
        validation_message = self._builder_validation_message()
        if validation_message:
            self.builder_validation_var.set(validation_message)
            return
        description = self.builder_description_text.get("1.0", "end").strip()
        parameters: list[CustomJobParameter] = []
        for index, row in enumerate(self.parameter_rows, start=1):
            label = str(row["label"].get()).strip()
            flag = str(row["flag"].get()).strip()
            widget = self._stored_input_type(str(row["widget"].get()).strip() or "text")
            default_text = str(row["default_text"].get()).strip()
            default_bool = bool(row["default_bool"].get())
            pattern_text = str(row["pattern_text"].get()).strip()
            if not label and not flag and not default_text and not default_bool and not pattern_text and widget == "text":
                continue
            default_value = ""
            extra: dict[str, str] = {}
            if widget == "bool":
                default_value = "true" if default_bool else ""
            elif widget == "all_files_custom_pattern":
                default_value = default_text
                if pattern_text:
                    extra["pattern"] = pattern_text
            elif widget.startswith("ts_"):
                default_value = ""
            else:
                default_value = default_text
            parameters.append(
                CustomJobParameter(
                    key=f"param_{index}",
                    label=label,
                    flag=flag,
                    widget=widget,
                    default=default_value,
                    extra=extra,
                )
            )
        definition = CustomJobDefinition(
            name=name,
            description=description,
            command_template=command_template,
            environment_title=self.builder_environment_var.get().strip() or "None",
            parameters=parameters,
        )
        jobs = [job for job in get_project_custom_jobs(self.app.project) if job.name != definition.name]
        jobs.append(definition)
        set_project_custom_jobs(self.app.project, jobs)
        self.app.on_project_changed("custom")
        self.job_type_var.set(definition.name)
        self._refresh_job_options()
        self._on_job_selection_changed()
        self.app.status_var.set(f"Saved custom job type: {definition.name}")

    def _dataset_map(self) -> dict[str, object]:
        return {dataset.dataset_name: dataset for dataset in self.app.project.datasets}

    def _dataset_has_ts_name(self, dataset, ts_name: str) -> bool:
        return any(name.casefold() == ts_name.casefold() for name in dataset_ts_names(dataset))

    def _resolve_role_path(self, dataset, ts_name: str, role: str) -> Path | None:
        resolved = resolve_dataset_file(self.app.project, dataset, ts_name, role)
        return Path(resolved.path) if resolved.path else None

    def _all_files_for_parameter(self, parameter: CustomJobParameter) -> tuple[list[Path], list[str]]:
        state = self.runtime_state.get(parameter.key, {})
        directory = str(state.get("directory").get()).strip() if "directory" in state else ""
        if not directory:
            return [], [f"{parameter.label or parameter.key}: directory is missing."]
        folder = Path(directory)
        if not folder.exists():
            return [], [f"{parameter.label or parameter.key}: directory not found."]
        if parameter.widget == "all_files_custom_pattern":
            pattern = str(state.get("pattern").get()).strip() if "pattern" in state else ""
            if not pattern:
                return [], [f"{parameter.label or parameter.key}: custom pattern is missing."]
            files = sorted(
                [item for item in folder.iterdir() if item.is_file() and pattern.casefold() in item.name.casefold()],
                key=lambda item: item.name.casefold(),
            )
        else:
            suffix = "." + parameter.widget.removeprefix("all_files_")
            files = sorted(
                [item for item in folder.iterdir() if item.is_file() and item.suffix.lower() == suffix],
                key=lambda item: item.name.casefold(),
            )
        if not files:
            return [], [f"{parameter.label or parameter.key}: no matching files found."]
        return files, []

    def _append_flag(self, parts: list[str], flag: str, value: str | None = None, is_bool: bool = False) -> None:
        if is_bool:
            if value and flag:
                parts.append(flag)
            return
        if not value:
            return
        if flag:
            parts.append(flag)
        parts.append(shlex.quote(value))

    def _build_commands(self) -> tuple[list[tuple[str, str, str]], list[str]]:
        if self.current_job is None:
            return [], ["No custom job type selected."]
        job = self.current_job
        errors: list[str] = []
        commands: list[tuple[str, str, str]] = []
        ts_parameters = [item for item in job.parameters if item.widget.startswith("ts_")]
        all_file_parameters = [item for item in job.parameters if item.widget.startswith("all_files_")]
        if ts_parameters and all_file_parameters:
            return [], ["Custom jobs currently support either TS selection inputs or all-files inputs, not both together."]
        if len(all_file_parameters) > 1:
            return [], ["Only one 'All files that...' parameter is currently supported per custom job type."]

        dataset_map = self._dataset_map()
        if ts_parameters:
            entries = self._global_ts_entries()
            if not entries:
                return [], ["No TS present in the global TS processing list."]
            for entry in entries:
                dataset = dataset_map.get(entry["dataset_name"])
                if dataset is None:
                    errors.append(f"{entry['dataset_name']} | {entry['ts_name']}: dataset not found")
                    continue
                context = {
                    "dataset_name": entry["dataset_name"],
                    "ts_name": entry["ts_name"],
                    "input_stem": entry["ts_name"],
                }
                for parameter in ts_parameters:
                    resolved: Path | None
                    if parameter.widget.startswith("ts_role:"):
                        resolved = self._resolve_role_path(dataset, entry["ts_name"], parameter.widget.split(":", 1)[1])
                    elif parameter.widget == "ts_aligned_stack":
                        resolved = self._resolve_role_path(dataset, entry["ts_name"], "aligned_stack")
                    elif parameter.widget == "ts_angle_file":
                        resolved = self._resolve_role_path(dataset, entry["ts_name"], "angle_file")
                    else:
                        resolved = self._resolve_role_path(dataset, entry["ts_name"], "tomogram")
                    if resolved is None:
                        errors.append(f"{entry['dataset_name']} | {entry['ts_name']}: missing input for {parameter.label or parameter.key}")
                        context = {}
                        break
                    context[parameter.key] = str(resolved)
                if context:
                    commands.append((entry["dataset_name"], entry["ts_name"], self._command_for_context(job, context)))
        elif all_file_parameters:
            parameter = all_file_parameters[0]
            files, file_errors = self._all_files_for_parameter(parameter)
            if file_errors:
                return [], file_errors
            for item in files:
                context = {
                    "dataset_name": "",
                    "ts_name": item.stem,
                    "input_stem": item.stem,
                    parameter.key: str(item),
                }
                commands.append(("", item.stem, self._command_for_context(job, context)))
        else:
            commands.append(("", "", self._command_for_context(job, {"dataset_name": "", "ts_name": "", "input_stem": ""})))
        return commands, errors

    def _command_for_context(self, job: CustomJobDefinition, context: dict[str, str]) -> str:
        parts = [job.command_template.strip()]
        for parameter in job.parameters:
            state = self.runtime_state.get(parameter.key, {})
            if parameter.widget == "bool":
                enabled = bool(state.get("value").get()) if "value" in state else False
                self._append_flag(parts, parameter.flag, "true" if enabled else "", is_bool=True)
            elif parameter.widget in {"text", "path", "file"}:
                value = str(state.get("value").get()).strip() if "value" in state else ""
                self._append_flag(parts, parameter.flag, value)
            else:
                self._append_flag(parts, parameter.flag, context.get(parameter.key, ""))
        return " ".join(part for part in parts if part)

    def _update_preview(self) -> None:
        commands, errors = self._build_commands()
        lines = [command for _dataset, _ts, command in commands]
        lines.extend(f"# {error}" for error in errors)
        self.runtime_command_text.delete("1.0", "end")
        self.runtime_command_text.insert("1.0", "\n".join(lines))

    def _record_history(
        self,
        dataset_name: str,
        ts_name: str,
        job_name: str,
        command: str,
        parameters: dict[str, str],
        action: str,
        slurm_result: SlurmSubmissionResult | None = None,
    ) -> None:
        dataset = next((item for item in self.app.project.datasets if item.dataset_name == dataset_name), None)
        if dataset is None:
            return
        entry = JobHistoryEntry(
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            action=action,
            group="Tomograms",
            job_name=job_name,
            command=command,
            processing_tab="Processing: Custom jobs",
            dataset_name=dataset_name,
            execution_mode="slurm" if self.execution_mode_var.get() == "Submit to Slurm" else "local",
            slurm_profile=self.slurm_profile_var.get().strip(),
            environment_title=self.environment_var.get().strip() if self.execution_mode_var.get() == "Run locally" else "",
            parameters={key: value for key, value in parameters.items() if value},
        )
        entry.parameters.update(self._current_slurm_overrides())
        if slurm_result is not None:
            entry.slurm_job_id = slurm_result.job_id
            entry.slurm_script_path = slurm_result.script_path
        dataset.job_history.append(entry)

    def _current_runtime_parameters(self) -> dict[str, str]:
        payload: dict[str, str] = {}
        for parameter in self.current_job.parameters if self.current_job else []:
            state = self.runtime_state.get(parameter.key, {})
            if parameter.widget == "bool":
                payload[parameter.label or parameter.key] = "true" if bool(state.get("value").get()) else ""
            elif parameter.widget in {"text", "path", "file"}:
                payload[parameter.label or parameter.key] = str(state.get("value").get()).strip() if "value" in state else ""
            elif parameter.widget.startswith("all_files_"):
                payload[f"{parameter.label or parameter.key} directory"] = str(state.get("directory").get()).strip() if "directory" in state else ""
                if parameter.widget == "all_files_custom_pattern":
                    payload[f"{parameter.label or parameter.key} pattern"] = str(state.get("pattern").get()).strip() if "pattern" in state else ""
            else:
                payload[parameter.label or parameter.key] = "global TS selection"
        return payload

    def _copy_commands(self) -> None:
        commands, errors = self._build_commands()
        if errors:
            messagebox.showerror("Cannot copy commands", "\n".join(errors))
            return
        if not commands:
            messagebox.showinfo("No commands", "No commands available for the selected custom job.")
            return
        self.frame.clipboard_clear()
        self.frame.clipboard_append("\n".join(command for _dataset, _ts, command in commands))
        self.app.status_var.set("Custom commands copied to clipboard")

    def _run_commands(self) -> None:
        commands, errors = self._build_commands()
        if errors:
            if self.app.is_debug_mode_enabled():
                self.app.debug_log(
                    "WARN",
                    "Ignoring custom-job resolution errors in Debug mode: " + "; ".join(errors),
                )
            else:
                messagebox.showerror("Cannot run commands", "\n".join(errors))
                return
        if not commands:
            if self.app.is_debug_mode_enabled():
                preview = self.runtime_command_text.get("1.0", "end").strip()
                preview_commands = [
                    line.strip()
                    for line in preview.splitlines()
                    if line.strip() and not line.lstrip().startswith("#")
                ]
                if preview_commands:
                    self.app.debug_log(
                        "WARN",
                        "No concrete custom commands resolved; simulating the current command preview instead.",
                    )
                    commands = [("", "", line) for line in preview_commands]
                else:
                    messagebox.showinfo("No commands", "No commands available for the selected custom job.")
                    return
            else:
                messagebox.showinfo("No commands", "No commands available for the selected custom job.")
                return
        runtime_parameters = self._current_runtime_parameters()
        job_name = self.current_job.name if self.current_job is not None else "Custom job"
        use_slurm = self.execution_mode_var.get() == "Submit to Slurm"
        profile_name = self.slurm_profile_var.get().strip()
        if use_slurm and not profile_name and not self.app.is_debug_mode_enabled():
            messagebox.showerror("Slurm profile missing", "Please select a Slurm profile first.")
            return

        items = [
            {
                "command": command,
                "dataset_name": dataset_name,
                "job_name": job_name,
                "cwd": "",
                "error_label": f"{dataset_name}/{ts_name}" if dataset_name else (ts_name or job_name),
                "ts_name": ts_name,
                "activation_command": self.app.resolve_environment_activation(self.environment_var.get()),
            }
            for dataset_name, ts_name, command in commands
        ]
        execute_command_sequence(
            self.app,
            items,
            use_slurm=use_slurm,
            profile_name=profile_name,
            overrides=self._slurm_override_payload(self._current_slurm_overrides()),
            on_submitted=lambda item, result: self._record_history(
                str(item.get("dataset_name", "")),
                str(item.get("ts_name", "")),
                job_name,
                str(item.get("command", "")),
                {**runtime_parameters, "ts_name": str(item.get("ts_name", ""))},
                "submitted",
                result,
            )
            if item.get("dataset_name")
            else None,
            on_completed=lambda item: self._record_history(
                str(item.get("dataset_name", "")),
                str(item.get("ts_name", "")),
                job_name,
                str(item.get("command", "")),
                {**runtime_parameters, "ts_name": str(item.get("ts_name", ""))},
                "ran",
            )
            if item.get("dataset_name")
            else None,
            on_finished=self._finish_run_commands,
        )
        self.app.status_var.set(
            f"{'Submitting' if use_slurm else 'Started'} custom job with {len(commands)} command(s)"
        )

    def _finish_run_commands(self, command_count: int, failures: list[str]) -> None:
        self.app.clear_abort_request()
        self.app.on_project_changed("custom", "tomograms")
        if failures:
            self.app.status_var.set("Custom job stopped: " + "; ".join(failures))
            return
        mode = "Submitted" if self.execution_mode_var.get() == "Submit to Slurm" else "Finished"
        self.app.status_var.set(f"{mode} custom job for {command_count} command(s)")

    def on_project_loaded(self, _project) -> None:
        self._refresh_job_options()
        self._refresh_slurm_profiles()
        self._refresh_ts_table()
        self._refresh_slurm_profiles()
        if self.job_type_var.get() == "Build custom job type...":
            self._rebuild_builder_rows()
            self._update_builder_validation()
            self._scroll_active_view_to_top(builder=True)
        elif self.current_job is not None:
            selected_name = self.current_job.name
            refreshed = next((job for job in self.jobs if job.name == selected_name), None)
            if refreshed is not None:
                self.current_job = refreshed
                self._build_runtime_form(refreshed)
                self._scroll_active_view_to_top()
