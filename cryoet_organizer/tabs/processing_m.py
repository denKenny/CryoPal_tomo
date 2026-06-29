from __future__ import annotations

from datetime import datetime, timezone
import shlex
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from cryoet_organizer.dialogs import bind_scrollable_canvas, fit_outer_canvas_to_viewport, show_detail_dialog
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
from cryoet_organizer.m_population import parse_population_file
from cryoet_organizer.mtools_catalog import MToolCommand, MToolFlag, M_GROUPS, m_jobs_by_group
from cryoet_organizer.project import JobHistoryEntry, MPopulationRecord, ProjectData
from cryoet_organizer.resizable_sections import ResizableSectionStack, VerticalSplitPane
from cryoet_organizer.scheduled_slurm_dialog import CollectiveSlurmSubmissionDialog, ask_scheduled_slurm_mode
from cryoet_organizer.slurm import SlurmSubmissionResult, find_slurm_profile, render_sbatch_script
from cryoet_organizer.slurm_override_ui import SlurmOverrideUI
from cryoet_organizer.tabs.base import SidebarTab


class ProcessingMTab(SidebarTab):
    tab_id = "processing_m"
    title = "Processing: M"
    refresh_domains = ("processing_m", "m_populations", "defaults", "slurm", "environments")

    def build(self) -> None:
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(0, weight=1)

        self.population_var = tk.StringVar()
        self.job_group_var = tk.StringVar(value=M_GROUPS[0])
        self.job_var = tk.StringVar()
        self.history_sort_column = "timestamp"
        self.history_sort_descending = True
        self.current_population: MPopulationRecord | None = None
        self.current_job: MToolCommand | None = None
        self.available_jobs: dict[str, tuple[MToolCommand, ...]] = m_jobs_by_group()
        self.parameter_vars: dict[str, tk.Variable] = {}
        self.parameter_inputs: dict[str, object] = {}
        self.parameter_choice_vars: dict[str, tk.StringVar] = {}

        self.creator_visible = False
        self.create_directory_var = tk.StringVar()
        self.create_name_var = tk.StringVar()
        self.create_environment_var = tk.StringVar(value="None")

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
        self.processing_advanced_visible = tk.BooleanVar(value=False)
        self._suspend_command_preview_updates = False
        self._required_param_rows: list[dict[str, object]] = []
        self._advanced_param_rows: list[dict[str, object]] = []
        self.history_entry_refs: dict[str, JobHistoryEntry] = {}
        self._layout_project_id: int | None = None
        self._history_pane_default_height = self.app._scale_pixels(200)
        self._job_pane_default_height = self.app._scale_pixels(170)
        self._parameters_pane_default_height = self.app._scale_pixels(580)
        self._history_pane_minsize = self.app._scale_pixels(180)
        self._job_pane_minsize = self.app._scale_pixels(150)
        self._parameters_pane_minsize = self.app._scale_pixels(460)
        self._command_preview_default_height = self.app._scale_pixels(220)
        self._parameter_fields_default_height = self.app._scale_pixels(320)
        self._command_preview_minsize = self.app._scale_pixels(180)
        self._parameter_fields_minsize = self.app._scale_pixels(260)

        self.outer_canvas = tk.Canvas(self.frame, highlightthickness=0)
        self.outer_canvas.grid(row=0, column=0, sticky="nsew")
        self.outer_scrollbar = ttk.Scrollbar(self.frame, orient="vertical", command=self.outer_canvas.yview)
        self.outer_scrollbar.grid(row=0, column=1, sticky="ns")
        self.outer_xscrollbar = ttk.Scrollbar(self.frame, orient="horizontal", command=self.outer_canvas.xview)
        self.outer_xscrollbar.grid(row=1, column=0, sticky="ew")
        self.outer_canvas.configure(yscrollcommand=self.outer_scrollbar.set, xscrollcommand=self.outer_xscrollbar.set)

        self.content = ttk.Frame(self.outer_canvas, padding=2)
        self.content.columnconfigure(0, weight=1)
        self.outer_window = self.outer_canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", self._on_outer_frame_configure)
        self.outer_canvas.bind("<Configure>", self._on_outer_canvas_configure)

        ttk.Label(
            self.content,
            text=(
                "Select an M population or create a new one. Once selected, the job history and "
                "MTools/MCore processing workflow for that population are available here."
            ),
            wraplength=900,
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 12))

        selector_box = ttk.LabelFrame(self.content, text="M population selection", padding=12)
        selector_box.grid(row=1, column=0, sticky="ew")
        selector_box.columnconfigure(0, weight=1)
        ttk.Label(selector_box, text="Select M population").grid(row=0, column=0, sticky="w", pady=(0, 4))
        selector_row = ttk.Frame(selector_box)
        selector_row.grid(row=1, column=0, sticky="ew")
        selector_row.columnconfigure(0, weight=1)
        self.population_combo = ttk.Combobox(selector_row, textvariable=self.population_var, state="readonly")
        self.population_combo.grid(row=0, column=0, sticky="ew")
        self.population_combo.bind("<<ComboboxSelected>>", self._on_population_selected)
        ttk.Button(selector_row, text="Create new M population", command=self._toggle_creator).grid(
            row=0, column=1, padx=(8, 0)
        )
        ttk.Button(selector_row, text="Import existing M population", command=self._import_population).grid(
            row=0, column=2, padx=(8, 0)
        )

        self.population_summary = ttk.Label(self.content, text="", wraplength=900, justify="left")
        self.population_summary.grid(row=2, column=0, sticky="w", pady=(16, 0))

        self.creator_box = ttk.LabelFrame(self.content, text="Create new M population", padding=12)
        self.creator_box.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        self.creator_box.columnconfigure(1, weight=1)
        self.creator_box.grid_remove()
        ttk.Label(self.creator_box, text="Directory").grid(row=0, column=0, sticky="w", pady=(0, 4))
        directory_row = ttk.Frame(self.creator_box)
        directory_row.grid(row=0, column=1, sticky="ew", pady=(0, 8))
        directory_row.columnconfigure(0, weight=1)
        ttk.Entry(directory_row, textvariable=self.create_directory_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(directory_row, text="Browse...", command=self._browse_create_directory).grid(
            row=0, column=1, padx=(8, 0)
        )
        ttk.Label(self.creator_box, text="Population name").grid(row=1, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(self.creator_box, textvariable=self.create_name_var).grid(row=1, column=1, sticky="ew", pady=(0, 8))
        ttk.Label(self.creator_box, text="Select environment").grid(row=2, column=0, sticky="w", pady=(0, 4))
        self.create_environment_combo = ttk.Combobox(
            self.creator_box,
            textvariable=self.create_environment_var,
            state="readonly",
            values=environment_titles(self.app.project),
            width=22,
        )
        self.create_environment_combo.grid(row=2, column=1, sticky="w", pady=(0, 8))

        command_box = ttk.LabelFrame(self.creator_box, text="Command preview", padding=12)
        command_box.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        command_box.columnconfigure(0, weight=1)
        self.create_command_text = tk.Text(command_box, height=3, wrap="word", font="TkDefaultFont")
        self.create_command_text.grid(row=0, column=0, sticky="ew")
        self.create_command_text.configure(state="disabled")

        creator_actions = ttk.Frame(self.creator_box)
        creator_actions.grid(row=4, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(creator_actions, text="Cancel", command=self._hide_creator).grid(row=0, column=0, padx=(0, 8))
        self.create_population_button = ttk.Button(creator_actions, text="Create", command=self._create_population)
        self.create_population_button.grid(row=0, column=1)

        self.processing_pane = ResizableSectionStack(
            self.content,
            app=self.app,
            preference_namespace="processing_m",
            bottom_spacing=self.app._scale_pixels(140),
            on_layout_changed=self._schedule_outer_layout_refresh,
        )
        self.processing_pane.grid(row=4, column=0, sticky="nsew", pady=(12, 0))
        self.history_pane_frame = self.processing_pane.add_section(
            "history",
            default_height=self._history_pane_default_height,
            min_height=self._history_pane_minsize,
        )
        self.history_pane_frame.columnconfigure(0, weight=1)
        self.history_pane_frame.rowconfigure(0, weight=1)
        self.job_pane_frame = self.processing_pane.add_section(
            "setup",
            default_height=self._job_pane_default_height,
            min_height=self._job_pane_minsize,
        )
        self.job_pane_frame.columnconfigure(0, weight=1)
        self.job_pane_frame.rowconfigure(0, weight=1)
        self.parameters_pane_frame = self.processing_pane.add_section(
            "parameters",
            default_height=self._parameters_pane_default_height,
            min_height=self._parameters_pane_minsize,
        )
        self.parameters_pane_frame.columnconfigure(0, weight=1)
        self.parameters_pane_frame.rowconfigure(0, weight=1)

        self.history_box = ttk.LabelFrame(self.history_pane_frame, text="Job history", padding=12)
        self.history_box.grid(row=0, column=0, sticky="nsew")
        self.history_box.columnconfigure(0, weight=1)
        self.history_box.rowconfigure(0, weight=1)

        self.history_table = ttk.Treeview(
            self.history_box,
            columns=("job_name", "timestamp", "action"),
            show="headings",
            height=6,
            style="Technical.Treeview",
        )
        self.history_table.heading("job_name", text="Job", command=lambda: self._sort_history("job_name"))
        self.history_table.column("job_name", width=260, anchor="w")
        self.history_table.heading("timestamp", text="Timestamp", command=lambda: self._sort_history("timestamp"))
        self.history_table.column("timestamp", width=180, anchor="w")
        self.history_table.heading("action", text="Action", command=lambda: self._sort_history("action"))
        self.history_table.column("action", width=110, anchor="w")
        self.history_table.grid(row=0, column=0, sticky="nsew")
        self.history_table.tag_configure("scheduled", background="#ececec")
        self.history_table.tag_configure("waiting", background="#dbeeff")
        self.history_table.tag_configure("running", background="#dff4d8")
        self.history_table.tag_configure("completed", background="#dde8ff")

        history_scrollbar = ttk.Scrollbar(self.history_box, orient="vertical", command=self.history_table.yview)
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
            command=self._copy_selected_history_parameters,
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
        history_abort = ttk.Button(
            history_actions,
            text="Abort",
            command=self.app.abort_running_commands,
            state="disabled",
        )
        history_abort.grid(row=0, column=6, sticky="e", padx=(8, 0))
        self.app.attach_abort_button(history_abort)
        self.history_table.bind("<Double-1>", self._show_selected_history_details)

        self.processing_box = ttk.LabelFrame(self.job_pane_frame, text="Processing setup", padding=12)
        self.processing_box.grid(row=0, column=0, sticky="nsew")
        self.processing_box.columnconfigure(0, weight=1)

        selection_grid = ttk.Frame(self.processing_box)
        selection_grid.grid(row=0, column=0, sticky="ew")
        selection_grid.columnconfigure(1, weight=1)
        ttk.Label(selection_grid, text="Job group").grid(row=0, column=0, sticky="w", pady=(0, 4), padx=(0, 12))
        self.job_group_combo = ttk.Combobox(
            selection_grid,
            textvariable=self.job_group_var,
            state="disabled",
            values=M_GROUPS,
        )
        self.job_group_combo.grid(row=0, column=1, sticky="ew", pady=(0, 8))
        self.job_group_combo.bind("<<ComboboxSelected>>", self._on_group_selected)
        ttk.Label(selection_grid, text="Processing job").grid(row=1, column=0, sticky="w", pady=(0, 4), padx=(0, 12))
        self.job_combo = ttk.Combobox(selection_grid, textvariable=self.job_var, state="disabled", values=())
        self.job_combo.grid(row=1, column=1, sticky="ew")
        self.job_combo.bind("<<ComboboxSelected>>", self._on_job_selected)

        self.job_hint = ttk.Label(
            self.processing_box,
            text="Select a job group and processing job to configure and run an MTools or MCore command.",
            wraplength=900,
            justify="left",
        )
        self.job_hint.grid(row=1, column=0, sticky="w", pady=(10, 0))

        self.parameters_box = ttk.LabelFrame(self.parameters_pane_frame, text="Job parameters", padding=12)
        self.parameters_box.grid(row=0, column=0, sticky="nsew")
        self.parameters_box.columnconfigure(0, weight=1)
        self.parameters_box.rowconfigure(0, weight=1)

        self.command_parameter_pane = VerticalSplitPane(
            self.parameters_box,
            app=self.app,
            preference_namespace="processing_m",
            default_top_height=self._command_preview_default_height,
            min_top_height=self._command_preview_minsize,
            min_bottom_height=self._parameter_fields_minsize,
            resize_parent_by=lambda delta: self.processing_pane.resize_section("parameters", delta),
            on_layout_changed=self._schedule_outer_layout_refresh,
        )
        self.command_parameter_pane.grid(row=0, column=0, sticky="nsew")
        self.command_pane_frame = self.command_parameter_pane.top_frame()
        self.command_pane_frame.columnconfigure(0, weight=1)
        self.command_pane_frame.rowconfigure(0, weight=1)
        self.parameter_fields_frame = self.command_parameter_pane.bottom_frame()
        self.parameter_fields_frame.columnconfigure(0, weight=1)
        self.parameter_fields_frame.rowconfigure(0, weight=1)

        command_box = ttk.LabelFrame(self.command_pane_frame, text="Command preview", padding=12)
        command_box.grid(row=0, column=0, sticky="nsew")
        command_box.columnconfigure(0, weight=1)
        command_box.rowconfigure(3, weight=1)

        command_header = ttk.Frame(command_box)
        command_header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        command_header.columnconfigure(0, weight=1)
        ttk.Button(command_header, text="Copy command", command=self._copy_command).grid(
            row=0, column=1, sticky="e", padx=(8, 0)
        )
        ttk.Button(command_header, text="Schedule command", command=self._schedule_command).grid(
            row=0, column=2, sticky="e", padx=(8, 0)
        )
        ttk.Button(command_header, text="Run command", command=self._run_command).grid(
            row=0, column=3, sticky="e", padx=(8, 0)
        )
        run_abort = ttk.Button(
            command_header,
            text="Abort",
            command=self.app.abort_running_commands,
            state="disabled",
        )
        run_abort.grid(row=0, column=4, sticky="e", padx=(8, 0))
        self.app.attach_abort_button(run_abort)

        execution_row = ttk.Frame(command_box)
        execution_row.grid(row=1, column=0, sticky="ew", pady=(0, 8))
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

        self.slurm_overrides_frame = ttk.Frame(command_box)
        self.slurm_overrides_frame.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self.slurm_overrides_ui.register_frame(self.slurm_overrides_frame)

        self.command_text = tk.Text(command_box, height=6, wrap="word", font="TkDefaultFont")
        self.command_text.grid(row=3, column=0, sticky="nsew")
        self.command_text.insert("1.0", "MTools")

        parameter_box = ttk.LabelFrame(self.parameter_fields_frame, text="Parameters", padding=12)
        parameter_box.grid(row=0, column=0, sticky="nsew")
        parameter_box.columnconfigure(0, weight=1)
        parameter_box.rowconfigure(0, weight=1)

        self.parameter_canvas = tk.Canvas(parameter_box, highlightthickness=0)
        self.parameter_canvas.grid(row=0, column=0, sticky="nsew")
        self.parameter_scrollbar = ttk.Scrollbar(
            parameter_box,
            orient="vertical",
            command=self.parameter_canvas.yview,
        )
        self.parameter_scrollbar.grid(row=0, column=1, sticky="ns")
        self.parameter_xscrollbar = ttk.Scrollbar(
            parameter_box,
            orient="horizontal",
            command=self.parameter_canvas.xview,
        )
        self.parameter_xscrollbar.grid(row=1, column=0, sticky="ew")
        self.parameter_canvas.configure(
            yscrollcommand=self.parameter_scrollbar.set,
            xscrollcommand=self.parameter_xscrollbar.set,
        )

        self.parameter_frame = ttk.Frame(self.parameter_canvas)
        self.parameter_frame.columnconfigure(0, weight=1)
        self.parameter_canvas_window = self.parameter_canvas.create_window((0, 0), window=self.parameter_frame, anchor="nw")
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
        self.processing_pane.grid_remove()

        for variable in (self.create_directory_var, self.create_name_var):
            variable.trace_add("write", lambda *_args: self._update_create_command_preview())
        self._refresh_population_choices(self.app.project)
        self._refresh_history()
        self._refresh_slurm_profiles()
        self._update_create_command_preview()
        self._update_population_ui()
        self.processing_pane.grid_remove()

    def _on_outer_frame_configure(self, _event=None) -> None:
        self.outer_canvas.configure(scrollregion=self.outer_canvas.bbox("all"))

    def _on_outer_canvas_configure(self, event) -> None:
        fit_outer_canvas_to_viewport(self.outer_canvas, self.outer_window, self.content, event)

    def _schedule_outer_layout_refresh(self) -> None:
        self.outer_canvas.after_idle(self._on_outer_frame_configure)

    def _on_parameter_frame_configure(self, _event=None) -> None:
        self.parameter_canvas.configure(scrollregion=self.parameter_canvas.bbox("all"))

    def _on_parameter_canvas_configure(self, event) -> None:
        self.parameter_canvas.itemconfigure(self.parameter_canvas_window, width=event.width)

    def _scroll_job_view_to_top(self) -> None:
        self.parameter_canvas.yview_moveto(0)
        self.parameter_canvas.xview_moveto(0)

    def on_tab_shown(self) -> None:
        self.frame.after_idle(self._on_outer_frame_configure)

    def _toggle_creator(self) -> None:
        if self.creator_visible:
            self._hide_creator()
            return
        self._refresh_creator_environments()
        self.create_environment_var.set(self._create_population_environment_default())
        self.creator_box.grid()
        self.creator_visible = True
        self._update_create_command_preview()

    def _hide_creator(self) -> None:
        self.creator_box.grid_remove()
        self.creator_visible = False
        self.create_directory_var.set("")
        self.create_name_var.set("")
        self.create_environment_var.set(self._create_population_environment_default())
        self._update_create_command_preview()

    def _browse_create_directory(self) -> None:
        path = filedialog.askdirectory(title="Select M population directory")
        if path:
            self.create_directory_var.set(path)

    def _create_population_command(self) -> str:
        directory = self.create_directory_var.get().strip()
        name = self.create_name_var.get().strip()
        parts = ["MTools", "create_population"]
        if directory:
            parts.extend(["--directory", directory])
        if name:
            parts.extend(["--name", name])
        return shlex.join(parts)

    def _update_create_command_preview(self) -> None:
        command = self._create_population_command()
        self.create_command_text.configure(state="normal")
        self.create_command_text.delete("1.0", "end")
        self.create_command_text.insert("1.0", command)
        self.create_command_text.configure(state="disabled")

    def _create_population_environment_default(self) -> str:
        available = set(environment_titles(self.app.project))
        current = self.create_environment_var.get().strip() or "None"
        return current if current in available else "None"

    def _refresh_creator_environments(self) -> None:
        values = environment_titles(self.app.project)
        self.create_environment_combo.configure(values=values)
        if self.create_environment_var.get() not in set(values):
            self.create_environment_var.set("None")

    def _selected_population(self) -> MPopulationRecord | None:
        selected_name = self.population_var.get().strip()
        if not selected_name:
            return None
        population = next((item for item in self.app.project.m_populations if item.name == selected_name), None)
        if population is not None:
            self._sync_population_file_metadata(population)
        return population

    def _population_file_path(self, population: MPopulationRecord | None) -> str:
        if population is None or not population.directory:
            return ""
        file_name = population.population_file.strip() or (f"{population.name}.population" if population.name else "")
        if not file_name:
            return ""
        return str(Path(population.directory) / file_name)

    def _population_record_from_file(self, population_path: str | Path) -> MPopulationRecord:
        parsed = parse_population_file(population_path)
        return MPopulationRecord(
            name=parsed.name,
            directory=parsed.directory,
            population_file=parsed.population_file,
            species=[item.to_dict() for item in parsed.species],
            sources=[item.to_dict() for item in parsed.sources],
        )

    def _sync_population_file_metadata(self, population: MPopulationRecord) -> None:
        population_path = self._population_file_path(population)
        if not population_path:
            return
        path = Path(population_path)
        if not path.exists():
            return
        try:
            refreshed = self._population_record_from_file(path)
        except Exception:
            return
        population.population_file = refreshed.population_file
        population.species = refreshed.species
        population.sources = refreshed.sources
        if not population.directory:
            population.directory = refreshed.directory

    def _population_item_resolved_path(self, population: MPopulationRecord | None, relative_or_absolute: str) -> str:
        raw = str(relative_or_absolute).strip()
        if not raw:
            return ""
        path = Path(raw)
        if path.is_absolute() or population is None or not population.directory:
            return str(path)
        return str((Path(population.directory) / path).resolve())

    def _population_choice_options(
        self,
        population: MPopulationRecord | None,
        field_name: str,
    ) -> list[tuple[str, str]]:
        if population is None:
            return []
        items = population.species if field_name == "--species" else population.sources if field_name == "--source" else []
        options: list[tuple[str, str]] = []
        for item in items:
            raw_path = item.get("path", "")
            resolved_path = self._population_item_resolved_path(population, raw_path)
            display_name = item.get("name", "").strip() or Path(raw_path).stem or Path(resolved_path).name or resolved_path
            if resolved_path:
                options.append((display_name, resolved_path))
        options.sort(key=lambda pair: pair[0].casefold())
        return options

    def _refresh_population_after_metadata_job(self, population_name: str) -> None:
        if self.current_job is None or self.current_job.command not in {"create_species", "create_source"}:
            return
        population = next(
            (item for item in self.app.project.m_populations if item.name == population_name),
            None,
        )
        if population is None:
            return
        self._sync_population_file_metadata(population)
        if self.population_var.get().strip() == population_name:
            self.current_population = population
            self._build_parameter_form()
            self._update_population_summary()
        self.app.on_project_changed("processing_m")
        self.app.status_var.set(
            f"Reloaded population metadata after {self.current_job.command}: {population_name}"
        )

    def _refresh_population_choices(self, project: ProjectData) -> None:
        values = [
            item.name
            for item in sorted(project.m_populations, key=lambda item: (item.name.casefold(), item.created_at))
        ]
        self.population_combo.configure(values=values, state="readonly" if values else "disabled")
        if self.population_var.get() not in values:
            self.population_var.set("")
        self.current_population = self._selected_population()
        self._update_population_ui()

    def _update_population_summary(self) -> None:
        population = self._selected_population()
        if population is None:
            self.population_summary.config(
                text="Select an M population to access its job history and processing jobs."
            )
            return
        lines = [
            f"Selected M population: {population.name}",
            f"Directory: {population.directory or '-'}",
            f"Population file: {self._population_file_path(population) or '-'}",
        ]
        if population.species:
            lines.append(
                "Species: "
                + ", ".join(
                    item.get("name") or Path(item.get("path", "")).stem or "-"
                    for item in population.species
                )
            )
        else:
            lines.append("Species: -")
        if population.sources:
            lines.append(
                "Sources: "
                + ", ".join(
                    item.get("name") or Path(item.get("path", "")).stem or "-"
                    for item in population.sources
                )
            )
        else:
            lines.append("Sources: -")
        if self.current_job is not None:
            lines.append(f"Selected job: {self.current_job.command} ({self.current_job.group})")
        self.population_summary.config(text="\n".join(lines))

    def _on_population_selected(self, _event=None) -> None:
        self.current_population = self._selected_population()
        self._refresh_history()
        self._refresh_processing_selection()
        self._update_population_ui()

    def _update_population_ui(self) -> None:
        population = self._selected_population()
        if population is None:
            self._update_population_summary()
            self.processing_pane.grid_remove()
            self.processing_pane.set_section_visible("history", True)
            self.processing_pane.set_section_visible("setup", True)
            self.processing_pane.set_section_visible("parameters", False)
            self.history_box.grid_remove()
            self.processing_box.grid_remove()
            return
        self.processing_pane.grid()
        self.history_box.grid()
        self.processing_box.grid()
        self.processing_pane.set_section_visible("history", True)
        self.processing_pane.set_section_visible("setup", True)
        self._update_population_summary()

    def _refresh_processing_selection(self) -> None:
        population = self._selected_population()
        if population is None:
            self.current_job = None
            self.job_group_combo.configure(state="disabled", values=M_GROUPS)
            self.job_combo.configure(state="disabled", values=())
            self.job_var.set("")
            self._build_parameter_form()
            return

        self.job_group_combo.configure(state="readonly", values=M_GROUPS)
        if self.job_group_var.get() not in M_GROUPS:
            self.job_group_var.set(M_GROUPS[0])
        self._on_group_selected()

    def _jobs_for_current_group(self) -> tuple[MToolCommand, ...]:
        return self.available_jobs.get(self.job_group_var.get(), ())

    def _create_population(self) -> None:
        directory = self.create_directory_var.get().strip()
        name = self.create_name_var.get().strip()
        if not directory or not name:
            messagebox.showinfo("Create new M population", "Please provide both a directory and a population name.")
            return
        if any(item.name.casefold() == name.casefold() for item in self.app.project.m_populations):
            messagebox.showerror("Create new M population", "A population with this name already exists.")
            return

        command = self._create_population_command()
        environment_title = self._create_population_environment_default()
        activation_command = self.app.resolve_environment_activation(environment_title)
        working_directory = str(Path(directory).expanduser().resolve().parent)
        self.create_population_button.config(state="disabled")
        self.app.status_var.set(f"Creating M population: {name}")
        try:
            self.app.run_managed_process_with_log(
                command,
                cwd=working_directory,
                title="Output: create_population",
                activation_command=activation_command,
                on_finished=lambda return_code, population_name=name, population_directory=directory, current_command=command, current_environment=environment_title: self._finish_create_population(
                    population_name,
                    population_directory,
                    current_command,
                    current_environment,
                    return_code == 0,
                ),
            )
        except Exception as exc:
            self.create_population_button.config(state="normal")
            messagebox.showerror("Create new M population failed", str(exc))
            self.app.status_var.set("Creating M population failed")

    def _import_population(self) -> None:
        path = filedialog.askopenfilename(
            title="Import existing M population",
            filetypes=[("M population", "*.population"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            population = self._population_record_from_file(path)
        except Exception as exc:
            messagebox.showerror("Import existing M population failed", str(exc))
            self.app.status_var.set("Importing M population failed")
            return
        if any(item.name.casefold() == population.name.casefold() for item in self.app.project.m_populations):
            messagebox.showerror(
                "Import existing M population",
                f"A population named '{population.name}' already exists.",
            )
            return
        population.job_history.append(
            JobHistoryEntry(
                timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                action="imported",
                group="Population setup",
                job_name="import_population",
                command=str(Path(path).expanduser().resolve()),
                processing_tab="Processing: M",
                dataset_name=population.name,
                parameters={
                    "population_file": str(Path(path).expanduser().resolve()),
                    "directory": population.directory,
                    "name": population.name,
                    "species_count": str(len(population.species)),
                    "source_count": str(len(population.sources)),
                },
            )
        )
        self.app.project.m_populations.append(population)
        self.population_var.set(population.name)
        self.current_population = population
        self.app.on_project_changed("processing_m")
        self._refresh_population_choices(self.app.project)
        self._refresh_history()
        self._refresh_processing_selection()
        self.app.status_var.set(f"Imported M population: {population.name}")

    def _finish_create_population(
        self,
        name: str,
        directory: str,
        command: str,
        environment_title: str,
        success: bool,
    ) -> None:
        self.create_population_button.config(state="normal")
        if not success:
            messagebox.showerror(
                "Create new M population failed",
                "MTools create_population returned a non-zero exit code. Please check the output window for details.",
            )
            self.app.status_var.set("Creating M population failed")
            return

        population = MPopulationRecord(name=name, directory=directory)
        population_path = Path(directory) / f"{name}.population"
        if population_path.exists():
            try:
                imported = self._population_record_from_file(population_path)
                population.population_file = imported.population_file
                population.species = imported.species
                population.sources = imported.sources
            except Exception:
                population.population_file = population_path.name
        else:
            population.population_file = f"{name}.population"
        population.job_history.append(
            JobHistoryEntry(
                timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                action="ran",
                group="Population setup",
                job_name="create_population",
                command=command,
                processing_tab="Processing: M",
                dataset_name=name,
                environment_title=environment_title if environment_title != "None" else "",
                parameters={
                    "directory": directory,
                    "name": name,
                    "population_file": str(population_path),
                    "species_count": str(len(population.species)),
                    "source_count": str(len(population.sources)),
                    **(
                        {"execution_environment": environment_title}
                        if environment_title and environment_title != "None"
                        else {}
                    ),
                },
            )
        )
        self.app.project.m_populations.append(population)
        self.population_var.set(name)
        self.current_population = population
        self.app.on_project_changed("processing_m")
        self._refresh_population_choices(self.app.project)
        self._refresh_history()
        self._refresh_processing_selection()
        self._hide_creator()
        self.app.status_var.set(f"Created M population: {name}")

    def _history_entries(self) -> list[JobHistoryEntry]:
        population = self._selected_population()
        if population is None:
            return []
        return population.job_history

    def _history_sort_value(self, entry: JobHistoryEntry, column: str):
        if column == "job_name":
            return entry.job_name.casefold()
        if column == "action":
            return entry.action.casefold()
        return entry.timestamp

    def _refresh_history(self) -> None:
        for item in self.history_table.get_children():
            self.history_table.delete(item)
        population = self._selected_population()
        if population is None:
            self.history_entry_refs = {}
            return
        entries = list(enumerate(population.job_history))
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
        self.history_entry_refs = {entry.entry_id: entry for _index, entry in entries}
        for index, entry in entries:
            self.history_table.insert(
                "",
                "end",
                iid=entry.entry_id,
                values=(entry.job_name, display_history_timestamp(entry), entry.action),
                tags=(self.app.history_entry_state_tag(entry),),
            )

    def _sort_history(self, column: str) -> None:
        if self.history_sort_column == column:
            self.history_sort_descending = not self.history_sort_descending
        else:
            self.history_sort_column = column
            self.history_sort_descending = True if column == "timestamp" else False
        self._refresh_history()

    def _selected_history_entry(self) -> JobHistoryEntry | None:
        selection = self.history_table.selection()
        if not selection:
            return None
        return self.history_entry_refs.get(selection[0])

    def _show_selected_history_details(self, _event=None) -> None:
        entry = self._selected_history_entry()
        if entry is None:
            messagebox.showinfo("Job details", "Please select a job history entry first.")
            return
        sections = [
            (
                "General",
                [
                    ("Job", entry.job_name),
                    ("Group", entry.group or "-"),
                    ("Timestamp", display_history_timestamp(entry)),
                    ("Action", entry.action),
                    ("Execution mode", entry.execution_mode),
                    ("Slurm profile", entry.slurm_profile or "-"),
                    ("Slurm job ID", entry.slurm_job_id or "-"),
                ],
            ),
            (
                "Parameters",
                [(key, value) for key, value in sorted(entry.parameters.items())] or [("Parameters", "-")],
            ),
        ]
        show_detail_dialog(self.frame, "Job details", sections, command=entry.command or "-")

    def _copy_selected_history_parameters(self) -> None:
        entry = self._selected_history_entry()
        if entry is None:
            messagebox.showinfo("Copy job parameters", "Please select a job history entry first.")
            return
        if entry.job_name == "create_population":
            self.create_directory_var.set(entry.parameters.get("directory", ""))
            self.create_name_var.set(entry.parameters.get("name", ""))
            self._refresh_creator_environments()
            self.create_environment_var.set(
                entry.environment_title
                or entry.parameters.get("execution_environment", self._create_population_environment_default())
            )
            self.creator_box.grid()
            self.creator_visible = True
            self._update_create_command_preview()
            self.app.status_var.set("Copied population creation parameters into the creator form")
            return
        if entry.group not in M_GROUPS:
            messagebox.showinfo(
                "Copy job parameters",
                "The selected history entry is not an MTools or MCore processing job.",
            )
            return
        available_job = next(
            (job for job in self.available_jobs.get(entry.group, ()) if job.command == entry.job_name),
            None,
        )
        if available_job is None:
            messagebox.showinfo(
                "Copy job parameters",
                "The original job is not available in the current M processing job list.",
            )
            return

        self.job_group_var.set(entry.group)
        self._on_group_selected()
        self.job_var.set(entry.job_name)
        self._on_job_selected()
        self.execution_mode_var.set("Submit to Slurm" if entry.execution_mode == "slurm" else "Run locally")
        self.environment_var.set(entry.environment_title or entry.parameters.get("execution_environment", self._job_environment_default()))
        self.slurm_profile_var.set(entry.slurm_profile or self.slurm_profile_var.get())
        self.slurm_partition_var.set(entry.parameters.get("slurm_partition", ""))
        self.slurm_time_var.set(entry.parameters.get("slurm_time", ""))
        self.slurm_gpus_var.set(entry.parameters.get("slurm_gpus", ""))
        self.slurm_cpus_var.set(entry.parameters.get("slurm_cpus_per_task", ""))
        self.slurm_mem_var.set(entry.parameters.get("slurm_mem", ""))
        self.slurm_mem_per_cpu_var.set(entry.parameters.get("slurm_mem_per_cpu", ""))
        self.slurm_mem_mode_var.set(entry.parameters.get("slurm_mem_mode", self.slurm_mem_mode_var.get() or "mem"))
        self.slurm_overrides_ui.rebuild(entry.parameters, preserve_existing=False)
        for flag_name, variable in self.parameter_vars.items():
            if flag_name not in entry.parameters:
                continue
            value = entry.parameters[flag_name]
            if isinstance(variable, tk.BooleanVar):
                variable.set(str(value).lower() in {"1", "true", "yes", "on"})
            else:
                variable.set(value)
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
            "Processing: M",
            self.current_job.group,
            self.current_job.command,
            "execution_environment",
            "None",
        )
        available = set(environment_titles(self.app.project))
        return value if value in available else "None"

    def _record_history_entry(self, action: str, scheduled: bool = False) -> JobHistoryEntry | None:
        population = self._selected_population()
        if population is None or self.current_job is None:
            return None
        entry = create_history_entry(
            scheduled=scheduled,
            action=action,
            group=self.current_job.group,
            job_name=self.current_job.command,
            command=self._current_command_text(),
            processing_tab="Processing: M",
            dataset_name=population.name,
            execution_mode="slurm" if self.execution_mode_var.get() == "Submit to Slurm" else "local",
            slurm_profile=self.slurm_profile_var.get().strip(),
            environment_title=self.environment_var.get().strip() if self.execution_mode_var.get() == "Run locally" else "",
            parameters=self._current_parameter_values(),
        )
        if self.execution_mode_var.get() == "Run locally" and self.environment_var.get().strip():
            entry.parameters["execution_environment"] = self.environment_var.get().strip()
        entry.parameters.update(self._current_slurm_overrides())
        population.job_history.append(entry)
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
        self._refresh_creator_environments()
        self._toggle_slurm_controls()

    def _current_slurm_overrides(self) -> dict[str, str]:
        return self.slurm_overrides_ui.metadata()

    def _slurm_override_payload(self, parameters: dict[str, str]) -> dict[str, str]:
        return slurm_override_payload(parameters)

    def _population_derived_default(self, flag: MToolFlag, population: MPopulationRecord | None) -> str:
        if flag.name in {"--population", "-p"}:
            base_default = self._population_file_path(population)
        elif flag.name in {"--species", "-s"}:
            options = self._population_choice_options(population, "--species")
            base_default = options[0][1] if options else flag.default_value
        elif flag.name in {"--source", "-s"}:
            options = self._population_choice_options(population, "--source")
            base_default = options[0][1] if options else flag.default_value
        else:
            base_default = flag.default_value
        if self.current_job is None:
            return base_default
        return resolve_job_default(
            self.app.project,
            "Processing: M",
            self.current_job.group,
            self.current_job.command,
            flag.name,
            base_default,
        )

    def _current_command_text(self) -> str:
        return self.command_text.get("1.0", "end").strip()

    def _set_command_text(self, command: str) -> None:
        self.command_text.delete("1.0", "end")
        self.command_text.insert("1.0", command)

    def _update_command_preview(self) -> None:
        if self._suspend_command_preview_updates:
            return
        if self.current_job is None:
            self._set_command_text("MTools")
            return

        parts = [self.current_job.executable]
        if self.current_job.command != self.current_job.executable:
            parts.append(self.current_job.command)
        for flag in self.current_job.flags:
            variable = self.parameter_vars.get(flag.name)
            if variable is None:
                continue
            value = variable.get()
            if flag.widget == "bool":
                if value:
                    parts.append(flag.name)
            elif str(value).strip():
                parts.append(flag.name)
                parts.append(str(value).strip())
        self._set_command_text(shlex.join(parts))
        self._update_population_summary()

    def _copy_command(self) -> None:
        command = self._current_command_text()
        if not command:
            return
        self.frame.clipboard_clear()
        self.frame.clipboard_append(command)
        self._record_history_entry("copied")
        self.app.on_project_changed("processing_m")
        self.app.status_var.set("Command copied to clipboard")

    def _schedule_command(self) -> None:
        command = self._current_command_text()
        if not command or self.current_job is None:
            return
        self._record_history_entry("scheduled", scheduled=True)
        self.app.on_project_changed("processing_m")
        self.app.status_var.set("Command scheduled")

    def _run_command(self) -> None:
        command = self._current_command_text()
        population = self._selected_population()
        if not command or self.current_job is None or population is None:
            messagebox.showerror("Command missing", "There is no command to run.")
            return

        working_directory = population.directory or None
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
                    dataset_name=population.name,
                    job_name=self.current_job.command,
                    overrides=self._slurm_override_payload(self._current_slurm_overrides()),
                )
            except Exception as exc:
                messagebox.showerror("Slurm submission failed", str(exc))
                return
            entry = self._record_history_entry("submitted")
            if entry is not None:
                entry.slurm_job_id = result.job_id
                entry.slurm_script_path = result.script_path
            self.app.on_project_changed("processing_m")
            self.app.status_var.set(f"Submitted to Slurm: {result.job_id or 'job submitted'}")
            return

        try:
            entry = self._record_history_entry("ran")
            activation_command = self.app.resolve_environment_activation(self.environment_var.get())
            self.app.run_managed_process_with_log(
                command,
                cwd=working_directory,
                title=f"Output: {self.current_job.command}",
                activation_command=activation_command,
                on_finished=(
                    (
                        lambda return_code, population_name=population.name, history_entry=entry:
                        self._handle_completed_local_run(population_name, return_code, history_entry)
                    )
                    if self.current_job.command in {"create_species", "create_source"}
                    else (lambda return_code, history_entry=entry: self._handle_completed_local_run(None, return_code, history_entry))
                ),
            )
        except Exception as exc:
            if entry is not None and population is not None:
                match_index = next(
                    (
                        index
                        for index, existing in enumerate(population.job_history)
                        if existing is entry or existing.entry_id == entry.entry_id
                    ),
                    None,
                )
                if match_index is not None:
                    del population.job_history[match_index]
                    self._refresh_history()
            messagebox.showerror("Run failed", str(exc))
            return

        if entry is not None:
            self.app.mark_history_entries_running([entry.entry_id])
        self.app.on_project_changed("processing_m")
        self.app.status_var.set(f"Started command: {self.current_job.command}")

    def _remove_selected_history_entry(self) -> None:
        entry = self._selected_history_entry()
        population = self._selected_population()
        if entry is None or population is None:
            messagebox.showinfo("Remove job", "Please select a job history entry first.")
            return
        population.job_history = [item for item in population.job_history if item is not entry]
        self._refresh_history()
        self.app.on_project_changed("processing_m")
        self.app.status_var.set("Removed selected job from history")

    def _run_scheduled_jobs(self) -> None:
        self._execute_scheduled_jobs(force_slurm=False)

    def _submit_scheduled_jobs_to_slurm(self) -> None:
        self._execute_scheduled_jobs(force_slurm=True)

    def _collective_scheduled_script(self, entries: list[JobHistoryEntry], profile_name: str, overrides: dict[str, str]) -> str:
        population = self._selected_population()
        if population is None:
            raise ValueError("No M population selected.")
        profile = find_slurm_profile(self.app.project, profile_name)
        if profile is None:
            raise ValueError("Please select a valid Slurm profile.")
        command = "\n".join(entry.command for entry in entries if entry.command.strip())
        return render_sbatch_script(
            command,
            profile,
            population.directory or None,
            population.name,
            "scheduled_m_batch",
            slurm_override_payload(overrides),
        )

    def _execute_scheduled_jobs(self, force_slurm: bool) -> None:
        population = self._selected_population()
        if population is None:
            return
        scheduled_entries = [entry for entry in population.job_history if is_scheduled_history_entry(entry)]
        if not scheduled_entries:
            messagebox.showinfo(
                "Run scheduled jobs",
                "No scheduled jobs found for this M population.",
            )
            return

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
                        scheduled_entries,
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
                        "\n".join(entry.command for entry in scheduled_entries if entry.command.strip()),
                        profile_name=collective_profile,
                        cwd=population.directory or None,
                        dataset_name=population.name,
                        job_name="scheduled_m_batch",
                        overrides=self._slurm_override_payload(collective_overrides),
                    )
                except Exception as exc:
                    messagebox.showerror("Slurm submission failed", str(exc))
                    return
                submitted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                for entry in scheduled_entries:
                    self._mark_scheduled_entry_submitted(entry, submitted_at, result, collective_profile)
                self.app.status_var.set(f"Submitted {len(scheduled_entries)} scheduled job(s) collectively")
                return

        running_entry_ids = [entry.entry_id for entry in scheduled_entries]

        def start_batch(on_queue_finished=None) -> None:
            self.app.mark_history_entries_running(running_entry_ids)
            execute_scheduled_history_entries(
                self.app,
                scheduled_entries,
                cwd=population.directory or None,
                dataset_name=population.name,
                force_slurm=force_slurm,
                forced_profile=forced_profile,
                wait_for_slurm_completion=force_slurm,
                on_entry_started=self._mark_scheduled_entry_started,
                on_entry_submitted=self._mark_scheduled_entry_submitted,
                on_entry_completed=None,
                on_finished=lambda scheduled_count, failures: self._finish_processing_queue_run(
                    scheduled_count,
                    failures,
                    running_entry_ids,
                    on_queue_finished,
                ),
            )
            self.app.status_var.set(
                ("Submitting sequentially" if force_slurm else "Running") + f" {len(scheduled_entries)} scheduled job(s)"
            )

        if force_slurm:
            start_batch(None)
            return

        self.app.request_scheduled_batch_start(
            self.frame,
            queue_key="processing-m-local",
            title=f"Queued scheduled jobs: {population.name}",
            entry_ids=[entry.entry_id for entry in scheduled_entries],
            start_batch=start_batch,
        )

    def _mark_scheduled_entry_started(self, entry: JobHistoryEntry, started_at: str) -> None:
        entry.timestamp = started_at
        entry.action = "ran"
        self._refresh_history()
        self.app.on_project_changed("processing_m")

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
        self.app.on_project_changed("processing_m")

    def _finish_scheduled_jobs_run(self, scheduled_count: int, failures: list[str]) -> None:
        self.app.clear_abort_request()
        self._refresh_history()
        self.app.on_project_changed("processing_m")
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

    def _handle_completed_local_run(
        self,
        population_name: str | None,
        return_code: int,
        history_entry: JobHistoryEntry | None,
    ) -> None:
        if history_entry is not None:
            self.app.clear_history_entries_running([history_entry.entry_id])
        if population_name and return_code == 0:
            self._refresh_population_after_metadata_job(population_name)

    def _browse_parameter(self, flag: MToolFlag) -> None:
        if flag.browse_mode == "dir":
            value = filedialog.askdirectory(title=f"Select value for {flag.name}")
        else:
            value = filedialog.askopenfilename(title=f"Select value for {flag.name}")
        if value and flag.name in self.parameter_vars:
            self.parameter_vars[flag.name].set(value)
            self._update_command_preview()

    def _add_bool_widget(self, parent: ttk.Frame, row: int, flag: MToolFlag, default_value: bool) -> None:
        variable = tk.BooleanVar(value=default_value)
        check = ttk.Checkbutton(
            parent,
            text=f"{flag.name}{' *' if flag.required else ''}",
            variable=variable,
            command=self._update_command_preview,
        )
        check.grid(row=row, column=0, sticky="w", pady=(0, 2))
        ttk.Label(parent, text=flag.description, wraplength=900, justify="left").grid(
            row=row + 1,
            column=0,
            sticky="w",
            pady=(0, 10),
        )
        self.parameter_vars[flag.name] = variable
        self.parameter_inputs[flag.name] = check

    def _add_text_widget(self, parent: ttk.Frame, row: int, flag: MToolFlag, default_value: str) -> None:
        block = ttk.Frame(parent)
        block.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        block.columnconfigure(0, weight=1)

        ttk.Label(block, text=f"{flag.name}{' *' if flag.required else ''}").grid(
            row=0,
            column=0,
            sticky="w",
            pady=(0, 2),
        )

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

        ttk.Label(block, text=flag.description, wraplength=900, justify="left").grid(
            row=2,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(4, 0),
        )
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
        flag: MToolFlag,
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

        desired_kind = "bool" if flag.widget == "bool" else "path" if flag.widget == "path" else "text"
        if row["widget_kind"] != desired_kind:
            for child in control_frame.winfo_children():
                child.destroy()
            row["browse_button"] = None
            row["check_widget"] = None
            if desired_kind == "bool":
                value_var: tk.Variable = tk.BooleanVar()
                check = ttk.Checkbutton(control_frame, variable=value_var, command=self._update_command_preview)
                check.grid(row=0, column=0, sticky="w")
                row["value_widget"] = check
                row["check_widget"] = check
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
            if desired_kind == "path":
                browse = row["browse_button"]
                if browse is not None:
                    browse.configure(command=lambda current=flag: self._browse_parameter(current))

        if desired_kind == "bool":
            label_widget.grid_remove()
            check_widget = row["check_widget"]
            if check_widget is not None:
                check_widget.configure(text=f"{flag.name}{' *' if flag.required else ''}")
            assert isinstance(value_var, tk.BooleanVar)
            value_var.set(default_value.lower() in {"1", "true", "yes", "on"})
        else:
            label_widget.grid()
            label_widget.config(text=f"{flag.name}{' *' if flag.required else ''}")
            assert isinstance(value_var, tk.StringVar)
            value_var.set(default_value)

        self.parameter_vars[flag.name] = value_var
        self.parameter_inputs[flag.name] = row["value_widget"]

    def _configure_population_choice_row(
        self,
        rows: list[dict[str, object]],
        index: int,
        parent: ttk.Frame,
        flag: MToolFlag,
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

        desired_kind = "population_choice"
        if row["widget_kind"] != desired_kind:
            for child in control_frame.winfo_children():
                child.destroy()
            value_var: tk.Variable = tk.StringVar()
            display_var = tk.StringVar()
            combo = ttk.Combobox(control_frame, textvariable=display_var, state="readonly")
            combo.grid(row=0, column=0, sticky="ew")
            row["value_widget"] = combo
            row["value_var"] = value_var
            row["display_var"] = display_var
            row["widget_kind"] = desired_kind
        else:
            value_var = row["value_var"]
            display_var = row["display_var"]
            combo = row["value_widget"]

        assert isinstance(value_var, tk.StringVar)
        assert isinstance(display_var, tk.StringVar)
        assert isinstance(combo, ttk.Combobox)

        label_widget.grid()
        label_widget.config(text=f"{flag.name}{' *' if flag.required else ''}")

        field_name = "--species" if "species" in flag.name else "--source"
        options = self._population_choice_options(self.current_population, field_name)
        options_by_name = {name: path for name, path in options}
        combo.configure(values=[name for name, _path in options], state="readonly" if options else "disabled")

        current_path = default_value.strip()
        current_display = ""
        for name, path in options:
            if path == current_path:
                current_display = name
                break
        if not current_display and options:
            current_display, current_path = options[0]
        elif not current_display and current_path:
            current_display = Path(current_path).name

        value_var.set(current_path)
        display_var.set(current_display)

        def on_selected(_event=None, *, values=options_by_name, display=display_var, actual=value_var) -> None:
            actual.set(values.get(display.get(), ""))
            self._update_command_preview()

        combo.bind("<<ComboboxSelected>>", on_selected)

        self.parameter_vars[flag.name] = value_var
        self.parameter_choice_vars[flag.name] = display_var
        self.parameter_inputs[flag.name] = combo

    def _hide_unused_parameter_rows(self, rows: list[dict[str, object]], used_count: int) -> None:
        for row in rows[used_count:]:
            frame = row["frame"]
            if isinstance(frame, ttk.Frame):
                frame.grid_remove()

    def _build_parameter_form(self) -> None:
        self._suspend_command_preview_updates = True
        self.parameter_vars.clear()
        self.parameter_inputs.clear()
        self.parameter_choice_vars.clear()

        if self.current_job is None:
            self.parameters_box.grid_remove()
            self.processing_pane.set_section_visible("parameters", False)
            self._suspend_command_preview_updates = False
            self._set_command_text("MTools")
            self.processing_advanced_button.grid_remove()
            self.processing_advanced_frame.grid_remove()
            self._hide_unused_parameter_rows(self._required_param_rows, 0)
            self._hide_unused_parameter_rows(self._advanced_param_rows, 0)
            return

        self.parameters_box.grid()
        self.processing_pane.set_section_visible("parameters", True)
        required_flags = [flag for flag in self.current_job.flags if flag.required]
        advanced_flags = [flag for flag in self.current_job.flags if not flag.required]

        for row, flag in enumerate(required_flags):
            default_value = self._population_derived_default(flag, self.current_population)
            if flag.name in {"--species", "--source", "-s"}:
                self._configure_population_choice_row(self._required_param_rows, row, self.parameter_frame, flag, default_value)
            else:
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
                default_value = self._population_derived_default(flag, self.current_population)
                if flag.name in {"--species", "--source", "-s"}:
                    self._configure_population_choice_row(
                        self._advanced_param_rows,
                        advanced_row,
                        self.processing_advanced_frame,
                        flag,
                        default_value,
                    )
                else:
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
        job_names = [job.command for job in jobs]
        current_job_name = self.job_var.get()
        self.job_combo.configure(state="readonly" if jobs else "disabled", values=job_names)
        if current_job_name not in job_names:
            current_job_name = job_names[0] if job_names else ""
        self.job_var.set(current_job_name)
        self.current_job = next((job for job in jobs if job.command == current_job_name), None)
        self._build_parameter_form()
        self._scroll_job_view_to_top()
        self._update_population_summary()

    def _on_job_selected(self, _event=None) -> None:
        selected_command = self.job_var.get()
        self.current_job = next(
            (job for job in self._jobs_for_current_group() if job.command == selected_command),
            None,
        )
        self.environment_var.set(self._job_environment_default())
        self._build_parameter_form()
        self._scroll_job_view_to_top()
        self._update_population_summary()

    def on_project_loaded(self, project: ProjectData) -> None:
        project_id = id(project)
        if self._layout_project_id != project_id:
            self._layout_project_id = project_id
            self.processing_pane.restore_from_project(project)
            self.command_parameter_pane.restore_from_project(project)
        self.available_jobs = m_jobs_by_group()
        self._refresh_population_choices(project)
        self._refresh_history()
        self._refresh_slurm_profiles()
        self._refresh_processing_selection()
        self._update_population_ui()
        self.create_environment_var.set(self._create_population_environment_default())

    def sync_to_project(self, project: ProjectData) -> None:
        self.processing_pane.write_to_project(project)
        self.command_parameter_pane.write_to_project(project)

    def reset_window_sizes(self) -> None:
        self.processing_pane.reset_to_defaults()
        self.command_parameter_pane.reset_to_defaults()
        self._schedule_outer_layout_refresh()
