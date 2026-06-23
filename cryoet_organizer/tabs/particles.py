from __future__ import annotations
import re
import shlex
import subprocess
import threading
import tkinter as tk
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from cryoet_organizer.dialogs import bind_scrollable_canvas, show_detail_dialog
from cryoet_organizer.environments import environment_titles
from cryoet_organizer.job_execution import (
    build_slurm_override_metadata,
    create_history_entry,
    display_history_timestamp,
    execute_command_sequence,
    is_scheduled_history_entry,
    slurm_override_payload,
)
from cryoet_organizer.job_defaults import resolve_job_default
from cryoet_organizer.particles_catalog import (
    export_particles_warp_job,
    particle_job_titles,
    particle_jobs_by_title,
)
from cryoet_organizer.preferences import project_preference_enabled
from cryoet_organizer.project import DatasetRecord, JobHistoryEntry, ProjectData
from cryoet_organizer.slurm import SlurmSubmissionResult
from cryoet_organizer.slurm_override_ui import SlurmOverrideUI
from cryoet_organizer.star_merge import (
    ClassificationIteration,
    MergeResult,
    OperationAborted,
    ParticleAbundancePlot,
    ParticleClassificationConvergencePlot,
    SplitResult,
    StarMergeError,
    detect_particle_star_mode,
    distance_clean_particles,
    intersect_particle_stars,
    merge_particle_exports,
    merge_particle_star_files,
    particle_abundance_plot_data,
    particle_classification_convergence_data,
    particle_star_pixel_size,
    split_particle_star_file,
)
from cryoet_organizer.tabs.base import SidebarTab
from cryoet_organizer.warptools_catalog import WarpToolFlag


def _ensure_star_name_local(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return "Output.star"
    return stripped if stripped.endswith(".star") else f"{stripped}.star"


class _ParticleBusyDialog:
    def __init__(self, parent: tk.Misc, message: str, on_abort=None) -> None:
        self.window = tk.Toplevel(parent)
        self.window.title("Working...")
        self.window.transient(parent.winfo_toplevel())
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", lambda: None)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)

        body = ttk.Frame(self.window, padding=16)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        ttk.Label(
            body,
            text=message,
            wraplength=360,
            justify="left",
        ).grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(body, orient="horizontal", mode="indeterminate", length=320)
        self.progress.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self.progress.start(10)
        self.abort_button = ttk.Button(body, text="Abort process", command=lambda: self._abort(on_abort))
        self.abort_button.grid(row=2, column=0, sticky="e", pady=(12, 0))

        self.window.update_idletasks()
        self.window.update()
        self.window.grab_set()
        self.window.focus_set()

    def _abort(self, callback) -> None:
        if callable(callback):
            try:
                callback()
            except Exception:
                pass
        self.close()

    def close(self) -> None:
        try:
            self.progress.stop()
        except tk.TclError:
            pass
        try:
            self.window.grab_release()
        except tk.TclError:
            pass
        try:
            self.window.destroy()
        except tk.TclError:
            pass


class ParticlesTab(SidebarTab):
    tab_id = "particles"
    title = "Processing: Particle jobs"
    refresh_domains = ("particles", "datasets", "defaults", "file_registry", "preferences", "environments")

    def build(self) -> None:
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(0, weight=1)
        self.job_type_var = tk.StringVar(value="Select job type")
        self.dataset_picker_var = tk.StringVar()
        self.distance_dataset_picker_var = tk.StringVar()
        self.intersect_dataset_picker_var = tk.StringVar()
        self.history_dataset_var = tk.StringVar(value="All datasets")
        self.parameter_vars: dict[str, tk.Variable] = {}
        self.selected_export_datasets: list[str] = []
        self.selected_distance_datasets: list[str] = []
        self.selected_intersect_datasets: list[str] = []
        self.abundance_star_paths: list[str] = []
        self.intersect_star_paths: list[str] = []
        self.merge_split_star_paths: list[str] = []
        self.selected_abundance_star_path: str = ""
        self.selected_intersect_star_path: str = ""
        self.selected_merge_split_star_path: str = ""
        self.convergence_directory_var = tk.StringVar()
        self.convergence_mode_var = tk.StringVar(value="-")
        self.convergence_pixel_size_var = tk.StringVar(value="-")
        self.convergence_iteration_count_var = tk.StringVar(value="-")
        self.convergence_iteration_span_var = tk.StringVar(value="-")
        self.current_abundance_plots: list[ParticleAbundancePlot] = []
        self.current_abundance_summary = ""
        self.current_convergence_plot: ParticleClassificationConvergencePlot | None = None
        self.current_convergence_summary = ""
        self._abundance_resize_after_id: str | None = None
        self._convergence_resize_after_id: str | None = None
        self.merge_split_mode_var = tk.StringVar(value="Merge .star files")
        self.merge_split_output_dir_var = tk.StringVar()
        self.merge_split_output_name_var = tk.StringVar(value="Output.star")
        self.export_output_directory_var = tk.StringVar()
        self.export_output_name_var = tk.StringVar(value="Output.star")
        self.bound_project_id: int | None = None
        self.distance_updating = False
        self.distance_pixel_size = 0.0
        self.intersect_updating = False
        self.intersect_pixel_size = 0.0
        self.intersect_radius_ang_canonical: float | None = None
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
        self.history_sort_column = "timestamp"
        self.history_sort_descending = True
        self.job_catalog = particle_jobs_by_title()
        self.current_job = export_particles_warp_job()
        self._export_param_rows: list[dict[str, object]] = []

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
        self.content.rowconfigure(1, weight=1)
        self.outer_window = self.outer_canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", self._on_outer_frame_configure)
        self.outer_canvas.bind("<Configure>", self._on_outer_canvas_configure)

        header = ttk.LabelFrame(self.content, text="Particles", padding=12)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        ttk.Label(header, text="Job type").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.job_type_combo = ttk.Combobox(
            header,
            textvariable=self.job_type_var,
            state="readonly",
            values=(
                "Select job type",
                *particle_job_titles(),
                "Job history",
            ),
        )
        self.job_type_combo.grid(row=0, column=1, sticky="ew")
        self.job_type_combo.bind("<<ComboboxSelected>>", self._on_job_type_changed)

        self.export_frame = ttk.Frame(self.content)
        self.export_frame.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        self.export_frame.columnconfigure(0, weight=1)
        self.export_frame.rowconfigure(2, weight=0)
        self.export_frame.rowconfigure(3, weight=1)

        dataset_box = ttk.LabelFrame(self.export_frame, text="Datasets", padding=12)
        dataset_box.grid(row=0, column=0, sticky="ew")
        dataset_box.columnconfigure(1, weight=1)

        ttk.Label(dataset_box, text="Add dataset").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.dataset_picker = ttk.Combobox(
            dataset_box,
            textvariable=self.dataset_picker_var,
            state="readonly",
        )
        self.dataset_picker.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(dataset_box, text="Add", command=self._add_selected_dataset).grid(row=0, column=2)
        ttk.Button(dataset_box, text="Add all", command=self._add_all_datasets).grid(
            row=0, column=3, padx=(8, 0)
        )
        ttk.Button(dataset_box, text="Remove selected", command=self._remove_selected_dataset).grid(
            row=0, column=4, padx=(8, 0)
        )

        self.selected_dataset_list = tk.Listbox(dataset_box, height=5)
        self.selected_dataset_list.grid(row=1, column=0, columnspan=5, sticky="ew", pady=(10, 0))

        command_box = ttk.LabelFrame(self.export_frame, text="Command preview", padding=12)
        command_box.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        command_box.columnconfigure(0, weight=1)

        action_row = ttk.Frame(command_box)
        action_row.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        action_row.columnconfigure(0, weight=1)
        ttk.Label(action_row, text="Execution").grid(row=0, column=1, sticky="e", padx=(0, 8))
        self.execution_mode_combo = ttk.Combobox(
            action_row,
            textvariable=self.execution_mode_var,
            state="readonly",
            values=("Run locally", "Submit to Slurm"),
            width=18,
        )
        self.execution_mode_combo.grid(row=0, column=2, sticky="e")
        self.execution_mode_combo.bind("<<ComboboxSelected>>", lambda _event: self._toggle_slurm_controls())
        self.execution_target_label = ttk.Label(action_row, text="Select environment")
        self.execution_target_label.grid(row=0, column=3, sticky="e", padx=(12, 8))
        self.slurm_profile_combo = ttk.Combobox(
            action_row,
            textvariable=self.slurm_profile_var,
            state="disabled",
            width=18,
        )
        self.slurm_profile_combo.grid(row=0, column=4, sticky="e")
        self.slurm_profile_combo.bind("<<ComboboxSelected>>", lambda _event: self.slurm_overrides_ui.rebuild(preserve_existing=False))
        self.environment_combo = ttk.Combobox(
            action_row,
            textvariable=self.environment_var,
            state="readonly",
            width=18,
            values=environment_titles(self.app.project),
        )
        self.environment_combo.grid(row=0, column=4, sticky="e")
        ttk.Button(action_row, text="Copy command", command=self._copy_commands).grid(
            row=0, column=5, padx=(8, 0)
        )
        ttk.Button(action_row, text="Run command", command=self._run_commands).grid(
            row=0, column=6, padx=(8, 0)
        )
        export_abort = ttk.Button(
            action_row,
            text="Abort",
            command=self.app.abort_running_commands,
            state="disabled",
        )
        export_abort.grid(row=0, column=7, padx=(8, 0))
        self.app.attach_abort_button(export_abort)
        self.slurm_overrides_frame = ttk.Frame(command_box)
        self.slurm_overrides_frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.slurm_overrides_ui.register_frame(self.slurm_overrides_frame)
        self._toggle_slurm_controls()

        self.command_text = tk.Text(command_box, height=14, wrap="word", font="TkDefaultFont")
        self.command_text.grid(row=2, column=0, sticky="nsew")

        parameter_box = ttk.LabelFrame(self.export_frame, text="ts_export_particles parameters", padding=12)
        parameter_box.grid(row=2, column=0, sticky="nsew", pady=(12, 0))
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

        self.parameter_container = ttk.Frame(self.parameter_canvas)
        self.parameter_container.columnconfigure(0, weight=1)
        self.parameter_window = self.parameter_canvas.create_window(
            (0, 0),
            window=self.parameter_container,
            anchor="nw",
        )
        bind_scrollable_canvas(
            self.parameter_canvas,
            self.parameter_window,
            self.parameter_container,
            allow_horizontal=True,
        )

        self.distance_frame = ttk.Frame(self.content)
        self.distance_frame.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        self.distance_frame.columnconfigure(0, weight=1)

        distance_dataset_box = ttk.LabelFrame(self.distance_frame, text="Datasets", padding=12)
        distance_dataset_box.grid(row=0, column=0, sticky="ew")
        distance_dataset_box.columnconfigure(1, weight=1)
        ttk.Label(distance_dataset_box, text="Add dataset").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.distance_dataset_picker = ttk.Combobox(
            distance_dataset_box,
            textvariable=self.distance_dataset_picker_var,
            state="readonly",
        )
        self.distance_dataset_picker.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(
            distance_dataset_box,
            text="Add",
            command=self._add_selected_distance_dataset,
        ).grid(row=0, column=2)
        ttk.Button(
            distance_dataset_box,
            text="Add all",
            command=self._add_all_distance_datasets,
        ).grid(row=0, column=3, padx=(8, 0))
        ttk.Button(
            distance_dataset_box,
            text="Remove selected",
            command=self._remove_selected_distance_dataset,
        ).grid(row=0, column=4, padx=(8, 0))

        self.distance_selected_dataset_list = tk.Listbox(distance_dataset_box, height=5)
        self.distance_selected_dataset_list.grid(
            row=1, column=0, columnspan=5, sticky="ew", pady=(10, 0)
        )

        distance_command_box = ttk.LabelFrame(self.distance_frame, text="Log window", padding=12)
        distance_command_box.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        distance_command_box.columnconfigure(0, weight=1)

        distance_action_row = ttk.Frame(distance_command_box)
        distance_action_row.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        distance_action_row.columnconfigure(0, weight=1)
        ttk.Button(
            distance_action_row,
            text="Copy log",
            command=self._copy_distance_clean_preview,
        ).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(
            distance_action_row,
            text="Run command",
            command=self._run_distance_clean,
        ).grid(row=0, column=2, padx=(8, 0))
        distance_abort = ttk.Button(
            distance_action_row,
            text="Abort",
            command=self.app.abort_running_commands,
            state="disabled",
        )
        distance_abort.grid(row=0, column=3, padx=(8, 0))
        self.app.attach_abort_button(distance_abort)

        self.distance_command_text = tk.Text(distance_command_box, height=12, wrap="word")
        self.distance_command_text.grid(row=1, column=0, sticky="nsew")

        distance_parameters = ttk.LabelFrame(self.distance_frame, text="Distance clean parameters", padding=12)
        distance_parameters.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        distance_parameters.columnconfigure(1, weight=1)

        self.distance_input_star_var = tk.StringVar()
        self.distance_radius_px_var = tk.StringVar()
        self.distance_radius_ang_var = tk.StringVar()
        self.distance_output_name_var = tk.StringVar(value="Output.star")
        self.distance_cleaned_var = tk.BooleanVar(value=True)
        self.distance_duplicates_var = tk.BooleanVar(value=False)
        self.distance_mode_var = tk.StringVar(value="-")
        self.distance_pixel_size_var = tk.StringVar(value="-")

        ttk.Label(distance_parameters, text="Particles STAR").grid(row=0, column=0, sticky="w", pady=(0, 4))
        input_star_row = ttk.Frame(distance_parameters)
        input_star_row.grid(row=0, column=1, sticky="ew", pady=(0, 8))
        input_star_row.columnconfigure(0, weight=1)
        ttk.Entry(input_star_row, textvariable=self.distance_input_star_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(input_star_row, text="Browse...", command=self._browse_distance_input_star).grid(
            row=0, column=1, padx=(8, 0)
        )

        ttk.Label(distance_parameters, text="Detected mode").grid(row=1, column=0, sticky="w", pady=(0, 4))
        ttk.Label(distance_parameters, textvariable=self.distance_mode_var).grid(row=1, column=1, sticky="w", pady=(0, 8))

        ttk.Label(distance_parameters, text="Image pixel size").grid(row=2, column=0, sticky="w", pady=(0, 4))
        ttk.Label(distance_parameters, textvariable=self.distance_pixel_size_var).grid(
            row=2, column=1, sticky="w", pady=(0, 8)
        )

        ttk.Label(distance_parameters, text="Clearing radius in px").grid(row=3, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(distance_parameters, textvariable=self.distance_radius_px_var).grid(
            row=3, column=1, sticky="ew", pady=(0, 8)
        )
        ttk.Label(distance_parameters, text="Clearing radius in A").grid(row=4, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(distance_parameters, textvariable=self.distance_radius_ang_var).grid(
            row=4, column=1, sticky="ew", pady=(0, 8)
        )

        ttk.Label(distance_parameters, text="Output modes").grid(row=5, column=0, sticky="nw", pady=(0, 4))
        output_modes = ttk.Frame(distance_parameters)
        output_modes.grid(row=5, column=1, sticky="w", pady=(0, 8))
        ttk.Checkbutton(
            output_modes,
            text="Distance cleaned coordinates",
            variable=self.distance_cleaned_var,
            command=self._update_distance_clean_preview,
        ).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(
            output_modes,
            text="Dublicate coordinates",
            variable=self.distance_duplicates_var,
            command=self._update_distance_clean_preview,
        ).grid(row=1, column=0, sticky="w")

        ttk.Label(distance_parameters, text="Output star").grid(row=6, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(distance_parameters, textvariable=self.distance_output_name_var).grid(
            row=6, column=1, sticky="ew"
        )

        self.distance_input_star_var.trace_add("write", lambda *_args: self._on_distance_input_changed())
        self.distance_radius_px_var.trace_add("write", lambda *_args: self._on_distance_radius_px_changed())
        self.distance_radius_ang_var.trace_add("write", lambda *_args: self._on_distance_radius_ang_changed())
        self.distance_output_name_var.trace_add("write", lambda *_args: self._update_distance_clean_preview())

        self.intersect_frame = ttk.Frame(self.content)
        self.intersect_frame.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        self.intersect_frame.columnconfigure(0, weight=1)

        intersect_dataset_box = ttk.LabelFrame(self.intersect_frame, text="Datasets", padding=12)
        intersect_dataset_box.grid(row=0, column=0, sticky="ew")
        intersect_dataset_box.columnconfigure(1, weight=1)
        ttk.Label(intersect_dataset_box, text="Add dataset").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.intersect_dataset_picker = ttk.Combobox(
            intersect_dataset_box,
            textvariable=self.intersect_dataset_picker_var,
            state="readonly",
        )
        self.intersect_dataset_picker.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(intersect_dataset_box, text="Add", command=self._add_selected_intersect_dataset).grid(
            row=0, column=2
        )
        ttk.Button(intersect_dataset_box, text="Add all", command=self._add_all_intersect_datasets).grid(
            row=0, column=3, padx=(8, 0)
        )
        ttk.Button(
            intersect_dataset_box,
            text="Remove selected",
            command=self._remove_selected_intersect_dataset,
        ).grid(row=0, column=4, padx=(8, 0))
        self.intersect_selected_dataset_list = tk.Listbox(intersect_dataset_box, height=5)
        self.intersect_selected_dataset_list.grid(row=1, column=0, columnspan=5, sticky="ew", pady=(10, 0))

        intersect_star_box = ttk.LabelFrame(self.intersect_frame, text="Input STAR files", padding=12)
        intersect_star_box.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        intersect_star_box.columnconfigure(0, weight=1)
        star_actions = ttk.Frame(intersect_star_box)
        star_actions.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        star_actions.columnconfigure(0, weight=1)
        ttk.Button(star_actions, text="Add .star-files", command=self._browse_intersect_input_stars).grid(
            row=0, column=1, padx=(8, 0)
        )
        ttk.Button(star_actions, text="Remove selected", command=self._remove_selected_intersect_star).grid(
            row=0, column=2, padx=(8, 0)
        )
        self.intersect_star_list = tk.Listbox(intersect_star_box, height=5)
        self.intersect_star_list.grid(row=1, column=0, sticky="ew")
        self.intersect_star_list.bind("<<ListboxSelect>>", self._on_intersect_star_selected)

        intersect_log_box = ttk.LabelFrame(self.intersect_frame, text="Log window", padding=12)
        intersect_log_box.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        intersect_log_box.columnconfigure(0, weight=1)
        intersect_action_row = ttk.Frame(intersect_log_box)
        intersect_action_row.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        intersect_action_row.columnconfigure(0, weight=1)
        ttk.Button(intersect_action_row, text="Copy log", command=self._copy_intersect_preview).grid(
            row=0, column=1, padx=(8, 0)
        )
        ttk.Button(intersect_action_row, text="Run command", command=self._run_intersect).grid(
            row=0, column=2, padx=(8, 0)
        )
        intersect_abort = ttk.Button(
            intersect_action_row,
            text="Abort",
            command=self.app.abort_running_commands,
            state="disabled",
        )
        intersect_abort.grid(row=0, column=3, padx=(8, 0))
        self.app.attach_abort_button(intersect_abort)
        self.intersect_log_text = tk.Text(intersect_log_box, height=12, wrap="word")
        self.intersect_log_text.grid(row=1, column=0, sticky="nsew")

        intersect_parameters = ttk.LabelFrame(self.intersect_frame, text="Intersect parameters", padding=12)
        intersect_parameters.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        intersect_parameters.columnconfigure(1, weight=1)

        self.intersect_mode_var = tk.StringVar(value="-")
        self.intersect_pixel_size_var = tk.StringVar(value="-")
        self.intersect_identification_mode_var = tk.StringVar(value="By distance")
        self.intersect_radius_px_var = tk.StringVar()
        self.intersect_radius_ang_var = tk.StringVar()
        self.intersect_output_name_var = tk.StringVar(value="Output.star")
        self.intersect_common_var = tk.BooleanVar(value=True)
        self.intersect_unique_var = tk.BooleanVar(value=False)

        ttk.Label(intersect_parameters, text="Detected mode").grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(intersect_parameters, textvariable=self.intersect_mode_var).grid(
            row=0, column=1, sticky="w", pady=(0, 8)
        )
        ttk.Label(intersect_parameters, text="Image pixel size").grid(row=1, column=0, sticky="w", pady=(0, 4))
        ttk.Label(intersect_parameters, textvariable=self.intersect_pixel_size_var).grid(
            row=1, column=1, sticky="w", pady=(0, 8)
        )
        ttk.Label(intersect_parameters, text="Identification mode").grid(row=2, column=0, sticky="w", pady=(0, 4))
        self.intersect_identification_combo = ttk.Combobox(
            intersect_parameters,
            textvariable=self.intersect_identification_mode_var,
            state="readonly",
            values=("By distance", "By name"),
        )
        self.intersect_identification_combo.grid(row=2, column=1, sticky="ew", pady=(0, 8))
        self.intersect_identification_combo.bind("<<ComboboxSelected>>", self._on_intersect_identification_changed)
        ttk.Label(intersect_parameters, text="Distance in px").grid(row=3, column=0, sticky="w", pady=(0, 4))
        self.intersect_radius_px_entry = ttk.Entry(intersect_parameters, textvariable=self.intersect_radius_px_var)
        self.intersect_radius_px_entry.grid(row=3, column=1, sticky="ew", pady=(0, 8))
        ttk.Label(intersect_parameters, text="Distance in A").grid(row=4, column=0, sticky="w", pady=(0, 4))
        self.intersect_radius_ang_entry = ttk.Entry(intersect_parameters, textvariable=self.intersect_radius_ang_var)
        self.intersect_radius_ang_entry.grid(row=4, column=1, sticky="ew", pady=(0, 8))
        ttk.Label(intersect_parameters, text="Output modes").grid(row=5, column=0, sticky="nw", pady=(0, 4))
        intersect_output_modes = ttk.Frame(intersect_parameters)
        intersect_output_modes.grid(row=5, column=1, sticky="w", pady=(0, 8))
        ttk.Checkbutton(
            intersect_output_modes,
            text="Only common coordinates",
            variable=self.intersect_common_var,
            command=self._update_intersect_preview,
        ).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(
            intersect_output_modes,
            text="Only unique coordinates",
            variable=self.intersect_unique_var,
            command=self._update_intersect_preview,
        ).grid(row=1, column=0, sticky="w")
        ttk.Label(intersect_parameters, text="Output star").grid(row=6, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(intersect_parameters, textvariable=self.intersect_output_name_var).grid(
            row=6, column=1, sticky="ew"
        )

        self.intersect_radius_px_var.trace_add("write", lambda *_args: self._on_intersect_radius_px_changed())
        self.intersect_radius_ang_var.trace_add("write", lambda *_args: self._on_intersect_radius_ang_changed())
        self.intersect_output_name_var.trace_add("write", lambda *_args: self._update_intersect_preview())

        self.merge_split_frame = ttk.Frame(self.content)
        self.merge_split_frame.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        self.merge_split_frame.columnconfigure(0, weight=1)

        merge_split_mode_box = ttk.LabelFrame(self.merge_split_frame, text="Mode", padding=12)
        merge_split_mode_box.grid(row=0, column=0, sticky="ew")
        ttk.Radiobutton(
            merge_split_mode_box,
            text="Merge .star files",
            value="Merge .star files",
            variable=self.merge_split_mode_var,
            command=self._on_merge_split_mode_changed,
        ).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(
            merge_split_mode_box,
            text="Split .star file",
            value="Split .star file",
            variable=self.merge_split_mode_var,
            command=self._on_merge_split_mode_changed,
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))

        merge_split_input_box = ttk.LabelFrame(self.merge_split_frame, text="Input STAR file(s)", padding=12)
        merge_split_input_box.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        merge_split_input_box.columnconfigure(0, weight=1)
        merge_split_actions = ttk.Frame(merge_split_input_box)
        merge_split_actions.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        merge_split_actions.columnconfigure(0, weight=1)
        ttk.Button(
            merge_split_actions,
            text="Add .star-file(s)",
            command=self._browse_merge_split_input_stars,
        ).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(
            merge_split_actions,
            text="Add directory",
            command=self._browse_merge_split_input_directory,
        ).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(
            merge_split_actions,
            text="Remove selected",
            command=self._remove_selected_merge_split_star,
        ).grid(row=0, column=3, padx=(8, 0))
        self.merge_split_star_list = tk.Listbox(merge_split_input_box, height=5)
        self.merge_split_star_list.grid(row=1, column=0, sticky="ew")
        self.merge_split_star_list.bind("<<ListboxSelect>>", self._on_merge_split_star_selected)

        merge_split_log_box = ttk.LabelFrame(self.merge_split_frame, text="Log window", padding=12)
        merge_split_log_box.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        merge_split_log_box.columnconfigure(0, weight=1)
        merge_split_action_row = ttk.Frame(merge_split_log_box)
        merge_split_action_row.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        merge_split_action_row.columnconfigure(0, weight=1)
        ttk.Button(
            merge_split_action_row,
            text="Copy log",
            command=self._copy_merge_split_preview,
        ).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(
            merge_split_action_row,
            text="Run command",
            command=self._run_merge_split,
        ).grid(row=0, column=2, padx=(8, 0))
        merge_split_abort = ttk.Button(
            merge_split_action_row,
            text="Abort",
            command=self.app.abort_running_commands,
            state="disabled",
        )
        merge_split_abort.grid(row=0, column=3, padx=(8, 0))
        self.app.attach_abort_button(merge_split_abort)
        self.merge_split_log_text = tk.Text(merge_split_log_box, height=12, wrap="word")
        self.merge_split_log_text.grid(row=1, column=0, sticky="nsew")

        merge_split_parameters = ttk.LabelFrame(self.merge_split_frame, text="Parameters", padding=12)
        merge_split_parameters.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        merge_split_parameters.columnconfigure(1, weight=1)
        ttk.Label(merge_split_parameters, text="Output directory").grid(row=0, column=0, sticky="w", pady=(0, 4))
        merge_split_outdir_row = ttk.Frame(merge_split_parameters)
        merge_split_outdir_row.grid(row=0, column=1, sticky="ew", pady=(0, 8))
        merge_split_outdir_row.columnconfigure(0, weight=1)
        ttk.Entry(merge_split_outdir_row, textvariable=self.merge_split_output_dir_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(
            merge_split_outdir_row,
            text="Browse...",
            command=self._browse_merge_split_output_directory,
        ).grid(row=0, column=1, padx=(8, 0))
        ttk.Label(merge_split_parameters, text="Output name").grid(row=1, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(merge_split_parameters, textvariable=self.merge_split_output_name_var).grid(
            row=1, column=1, sticky="ew"
        )
        self.merge_split_output_dir_var.trace_add("write", lambda *_args: self._update_merge_split_preview())
        self.merge_split_output_name_var.trace_add("write", lambda *_args: self._update_merge_split_preview())

        self.abundance_frame = ttk.Frame(self.content)
        self.abundance_frame.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        self.abundance_frame.columnconfigure(0, weight=1)

        abundance_star_box = ttk.LabelFrame(self.abundance_frame, text="Input STAR files", padding=12)
        abundance_star_box.grid(row=0, column=0, sticky="ew")
        abundance_star_box.columnconfigure(0, weight=1)
        abundance_actions = ttk.Frame(abundance_star_box)
        abundance_actions.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        abundance_actions.columnconfigure(0, weight=1)
        ttk.Button(
            abundance_actions,
            text="Add .star-files",
            command=self._browse_abundance_input_stars,
        ).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(
            abundance_actions,
            text="Remove selected",
            command=self._remove_selected_abundance_star,
        ).grid(row=0, column=2, padx=(8, 0))
        self.abundance_star_list = tk.Listbox(abundance_star_box, height=5)
        self.abundance_star_list.grid(row=1, column=0, sticky="ew")
        self.abundance_star_list.bind("<<ListboxSelect>>", self._on_abundance_star_selected)

        abundance_parameters = ttk.LabelFrame(self.abundance_frame, text="Plot parameters", padding=12)
        abundance_parameters.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        abundance_parameters.columnconfigure(1, weight=1)
        self.abundance_compare_samples_var = tk.BooleanVar(value=False)
        self.abundance_measure_var = tk.StringVar(value="Plot total particle numbers")
        self.abundance_rescale_var = tk.BooleanVar(value=False)
        self.abundance_mode_var = tk.StringVar(value="-")
        self.abundance_pixel_size_var = tk.StringVar(value="-")

        ttk.Label(abundance_parameters, text="Selected STAR mode").grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(abundance_parameters, textvariable=self.abundance_mode_var).grid(
            row=0,
            column=1,
            sticky="w",
            pady=(0, 8),
        )
        ttk.Label(abundance_parameters, text="Image pixel size").grid(row=1, column=0, sticky="w", pady=(0, 4))
        ttk.Label(abundance_parameters, textvariable=self.abundance_pixel_size_var).grid(
            row=1,
            column=1,
            sticky="w",
            pady=(0, 8),
        )
        ttk.Checkbutton(
            abundance_parameters,
            text="Compare Samples",
            variable=self.abundance_compare_samples_var,
            command=self._mark_abundance_dirty,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(abundance_parameters, text="Plot mode").grid(row=3, column=0, sticky="w", pady=(0, 4))
        self.abundance_measure_combo = ttk.Combobox(
            abundance_parameters,
            textvariable=self.abundance_measure_var,
            state="readonly",
            values=("Plot total particle numbers", "Plot particle density"),
        )
        self.abundance_measure_combo.grid(row=3, column=1, sticky="ew", pady=(0, 8))
        self.abundance_measure_combo.bind("<<ComboboxSelected>>", self._mark_abundance_dirty)
        ttk.Checkbutton(
            abundance_parameters,
            text="Rescale plot to window-size",
            variable=self.abundance_rescale_var,
            command=self._on_abundance_rescale_changed,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Button(
            abundance_parameters,
            text="Render plot",
            command=self._render_abundance_plots,
        ).grid(row=5, column=0, sticky="w", pady=(6, 0))

        abundance_plot_box = ttk.LabelFrame(self.abundance_frame, text="Particle abundance plots", padding=12)
        abundance_plot_box.grid(row=2, column=0, sticky="nsew", pady=(12, 0))
        abundance_plot_box.columnconfigure(0, weight=1)
        abundance_plot_box.rowconfigure(1, weight=1)
        self.abundance_plot_summary = ttk.Label(
            abundance_plot_box,
            text="Add one or more particle STAR files to render abundance plots.",
            wraplength=920,
            justify="left",
        )
        self.abundance_plot_summary.grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.abundance_plot_canvas = tk.Canvas(abundance_plot_box, highlightthickness=0)
        self.abundance_plot_canvas.grid(row=1, column=0, sticky="nsew")
        self.abundance_plot_yscroll = ttk.Scrollbar(
            abundance_plot_box, orient="vertical", command=self.abundance_plot_canvas.yview
        )
        self.abundance_plot_yscroll.grid(row=1, column=1, sticky="ns")
        self.abundance_plot_xscroll = ttk.Scrollbar(
            abundance_plot_box, orient="horizontal", command=self.abundance_plot_canvas.xview
        )
        self.abundance_plot_xscroll.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        self.abundance_plot_canvas.configure(
            xscrollcommand=self.abundance_plot_xscroll.set,
            yscrollcommand=self.abundance_plot_yscroll.set,
            height=460,
        )
        self.abundance_plot_container = ttk.Frame(self.abundance_plot_canvas)
        self.abundance_plot_container.columnconfigure(0, weight=1)
        self.abundance_plot_window = self.abundance_plot_canvas.create_window((0, 0), window=self.abundance_plot_container, anchor="nw")
        bind_scrollable_canvas(
            self.abundance_plot_canvas,
            self.abundance_plot_window,
            self.abundance_plot_container,
            allow_horizontal=True,
        )
        self.abundance_plot_canvas.bind("<Configure>", self._on_abundance_plot_canvas_configure, add="+")

        self.convergence_frame = ttk.Frame(self.content)
        self.convergence_frame.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        self.convergence_frame.columnconfigure(0, weight=1)

        convergence_input_box = ttk.LabelFrame(self.convergence_frame, text="Classification directory", padding=12)
        convergence_input_box.grid(row=0, column=0, sticky="ew")
        convergence_input_box.columnconfigure(1, weight=1)
        ttk.Label(convergence_input_box, text="Directory").grid(row=0, column=0, sticky="w", pady=(0, 4))
        convergence_row = ttk.Frame(convergence_input_box)
        convergence_row.grid(row=0, column=1, sticky="ew", pady=(0, 8))
        convergence_row.columnconfigure(0, weight=1)
        ttk.Entry(convergence_row, textvariable=self.convergence_directory_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(
            convergence_row,
            text="Browse...",
            command=self._browse_convergence_directory,
        ).grid(row=0, column=1, padx=(8, 0))

        convergence_parameters = ttk.LabelFrame(self.convergence_frame, text="Plot parameters", padding=12)
        convergence_parameters.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        convergence_parameters.columnconfigure(1, weight=1)
        ttk.Label(convergence_parameters, text="Detected mode").grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(convergence_parameters, textvariable=self.convergence_mode_var).grid(
            row=0, column=1, sticky="w", pady=(0, 8)
        )
        ttk.Label(convergence_parameters, text="Image pixel size").grid(row=1, column=0, sticky="w", pady=(0, 4))
        ttk.Label(convergence_parameters, textvariable=self.convergence_pixel_size_var).grid(
            row=1, column=1, sticky="w", pady=(0, 8)
        )
        ttk.Label(convergence_parameters, text="Iterations recognized").grid(row=2, column=0, sticky="w", pady=(0, 4))
        ttk.Label(convergence_parameters, textvariable=self.convergence_iteration_count_var).grid(
            row=2, column=1, sticky="w", pady=(0, 8)
        )
        ttk.Label(convergence_parameters, text="Iteration span").grid(row=3, column=0, sticky="w", pady=(0, 4))
        ttk.Label(convergence_parameters, textvariable=self.convergence_iteration_span_var).grid(
            row=3, column=1, sticky="w", pady=(0, 8)
        )
        self.convergence_rescale_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            convergence_parameters,
            text="Rescale plot to window-size",
            variable=self.convergence_rescale_var,
            command=self._on_convergence_rescale_changed,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Button(
            convergence_parameters,
            text="Render plot",
            command=self._render_convergence_plots,
        ).grid(row=5, column=0, sticky="w", pady=(6, 0))

        convergence_plot_box = ttk.LabelFrame(self.convergence_frame, text="Classification convergence plots", padding=12)
        convergence_plot_box.grid(row=2, column=0, sticky="nsew", pady=(12, 0))
        convergence_plot_box.columnconfigure(0, weight=1)
        convergence_plot_box.rowconfigure(1, weight=1)
        self.convergence_plot_summary = ttk.Label(
            convergence_plot_box,
            text="Select a classification directory to render convergence plots.",
            wraplength=920,
            justify="left",
        )
        self.convergence_plot_summary.grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.convergence_plot_canvas = tk.Canvas(convergence_plot_box, highlightthickness=0)
        self.convergence_plot_canvas.grid(row=1, column=0, sticky="nsew")
        self.convergence_plot_yscroll = ttk.Scrollbar(
            convergence_plot_box, orient="vertical", command=self.convergence_plot_canvas.yview
        )
        self.convergence_plot_yscroll.grid(row=1, column=1, sticky="ns")
        self.convergence_plot_xscroll = ttk.Scrollbar(
            convergence_plot_box, orient="horizontal", command=self.convergence_plot_canvas.xview
        )
        self.convergence_plot_xscroll.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        self.convergence_plot_canvas.configure(
            xscrollcommand=self.convergence_plot_xscroll.set,
            yscrollcommand=self.convergence_plot_yscroll.set,
            height=460,
        )
        self.convergence_plot_container = ttk.Frame(self.convergence_plot_canvas)
        self.convergence_plot_container.columnconfigure(0, weight=1)
        self.convergence_plot_window = self.convergence_plot_canvas.create_window((0, 0), window=self.convergence_plot_container, anchor="nw")
        bind_scrollable_canvas(
            self.convergence_plot_canvas,
            self.convergence_plot_window,
            self.convergence_plot_container,
            allow_horizontal=True,
        )
        self.convergence_plot_canvas.bind("<Configure>", self._on_convergence_plot_canvas_configure, add="+")

        self.history_frame = ttk.Frame(self.content)
        self.history_frame.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        self.history_frame.columnconfigure(0, weight=1)
        self.history_frame.rowconfigure(1, weight=1)

        history_filter = ttk.LabelFrame(self.history_frame, text="History filter", padding=12)
        history_filter.grid(row=0, column=0, sticky="ew")
        history_filter.columnconfigure(1, weight=1)
        ttk.Label(history_filter, text="Dataset").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.history_dataset_combo = ttk.Combobox(
            history_filter,
            textvariable=self.history_dataset_var,
            state="readonly",
        )
        self.history_dataset_combo.grid(row=0, column=1, sticky="ew")
        self.history_dataset_combo.bind("<<ComboboxSelected>>", self._refresh_history)

        history_box = ttk.LabelFrame(self.history_frame, text="Job history", padding=12)
        history_box.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        history_box.columnconfigure(0, weight=1)
        history_box.rowconfigure(0, weight=1)

        self.history_table = ttk.Treeview(
            history_box,
            columns=("job_name", "dataset_name", "timestamp", "action"),
            show="headings",
            height=14,
        )
        self.history_table.heading("job_name", text="Job", command=lambda: self._sort_history("job_name"))
        self.history_table.heading("dataset_name", text="Dataset", command=lambda: self._sort_history("dataset_name"))
        self.history_table.heading("timestamp", text="Timestamp", command=lambda: self._sort_history("timestamp"))
        self.history_table.heading("action", text="Action", command=lambda: self._sort_history("action"))
        self.history_table.column("job_name", width=180, anchor="w")
        self.history_table.column("dataset_name", width=160, anchor="w")
        self.history_table.column("timestamp", width=180, anchor="w")
        self.history_table.column("action", width=90, anchor="w")
        self.history_table.grid(row=0, column=0, sticky="nsew")
        self.history_table.tag_configure("scheduled", background="#ececec")
        self.history_table.tag_configure("waiting", background="#dbeeff")
        self.history_table.tag_configure("running", background="#dff4d8")
        self.history_table.tag_configure("completed", background="#dde8ff")
        history_scrollbar = ttk.Scrollbar(history_box, orient="vertical", command=self.history_table.yview)
        history_scrollbar.grid(row=0, column=1, sticky="ns")
        self.history_table.configure(yscrollcommand=history_scrollbar.set)

        history_actions = ttk.Frame(history_box)
        history_actions.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        history_actions.columnconfigure(2, weight=1)
        ttk.Button(
            history_actions,
            text="Show selected job details",
            command=self._show_selected_history_details,
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            history_actions,
            text="Remove selected job",
            command=self._remove_selected_history_entry,
        ).grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.history_table.bind("<Double-1>", self._show_selected_history_details)

        self._build_parameter_form()
        self._on_intersect_identification_changed()
        self._on_job_type_changed()

    def _on_outer_frame_configure(self, _event=None) -> None:
        self.outer_canvas.configure(scrollregion=self.outer_canvas.bbox("all"))

    def _on_outer_canvas_configure(self, event) -> None:
        self.outer_canvas.itemconfigure(self.outer_window, width=event.width)

    def _on_parameter_frame_configure(self, _event=None) -> None:
        self.parameter_canvas.configure(scrollregion=self.parameter_canvas.bbox("all"))

    def _on_parameter_canvas_configure(self, event) -> None:
        self.parameter_canvas.itemconfigure(self.parameter_window, width=event.width)

    def _scroll_job_view_to_top(self) -> None:
        self.parameter_canvas.yview_moveto(0)
        self.parameter_canvas.xview_moveto(0)

    def _dataset_map(self) -> dict[str, DatasetRecord]:
        return {dataset.dataset_name: dataset for dataset in self.app.project.datasets}

    def _is_particle_history_entry(self, entry: JobHistoryEntry) -> bool:
        if entry.processing_tab == "Processing: Particle jobs":
            return True
        return entry.job_name in {
            "ts_export_particles",
            "distance_clean",
            "intersect_star_files",
            "plot_particle_abundance",
            "plot_classification_convergence",
        }

    def _history_entries(self) -> list[JobHistoryEntry]:
        entries: list[JobHistoryEntry] = []
        for dataset in self.app.project.datasets:
            for entry in dataset.job_history:
                if not self._is_particle_history_entry(entry):
                    continue
                if entry.dataset_name:
                    entries.append(entry)
                else:
                    entries.append(
                        JobHistoryEntry(
                            timestamp=entry.timestamp,
                            action=entry.action,
                            group=entry.group,
                            job_name=entry.job_name,
                            command=entry.command,
                            processing_tab=entry.processing_tab,
                            dataset_name=dataset.dataset_name,
                            execution_mode=entry.execution_mode,
                            slurm_profile=entry.slurm_profile,
                            slurm_job_id=entry.slurm_job_id,
                            slurm_script_path=entry.slurm_script_path,
                            parameters=entry.parameters,
                            artifacts=entry.artifacts,
                            entry_id=entry.entry_id,
                        )
                    )
        return entries

    def _dataset_options(self) -> list[str]:
        return [dataset.dataset_name for dataset in self.app.project.datasets]

    def _ensure_export_parameter_rows(self, count: int) -> None:
        while len(self._export_param_rows) < count:
            row_frame = ttk.Frame(self.parameter_container)
            row_frame.columnconfigure(0, weight=1)
            label_widget = ttk.Label(row_frame)
            label_widget.grid(row=0, column=0, sticky="w", pady=(0, 2))
            control_frame = ttk.Frame(row_frame)
            control_frame.grid(row=1, column=0, sticky="ew")
            control_frame.columnconfigure(0, weight=1)
            description_widget = ttk.Label(row_frame, wraplength=900, justify="left")
            description_widget.grid(row=2, column=0, sticky="w", pady=(4, 0))
            self._export_param_rows.append(
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

    def _configure_export_parameter_row(self, index: int, flag: WarpToolFlag, default_value: str) -> None:
        self._ensure_export_parameter_rows(index + 1)
        row = self._export_param_rows[index]
        row_frame = row["frame"]
        label_widget = row["label"]
        control_frame = row["control"]
        description_widget = row["description"]
        row_frame.grid(row=index, column=0, sticky="ew", pady=(0, 10))
        description_widget.config(text=flag.description)

        desired_kind = "export_output" if flag.name == "--output_star" else (
            "bool" if flag.widget == "bool" else "path" if flag.widget == "path" else "text"
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
                directory_entry = ttk.Entry(control_frame, textvariable=value_var)
                directory_entry.grid(row=0, column=0, sticky="ew")
                browse = ttk.Button(
                    control_frame,
                    text="Browse dir",
                    command=lambda current=flag: self._browse_parameter(current),
                )
                browse.grid(row=0, column=1, padx=(8, 0))
                ttk.Label(control_frame, text="Name").grid(row=0, column=2, padx=(12, 4), sticky="w")
                name_entry = ttk.Entry(control_frame, textvariable=name_var, width=24)
                name_entry.grid(row=0, column=3, sticky="ew")
                value_var.trace_add("write", lambda *_args: self._update_command_preview())
                name_var.trace_add("write", lambda *_args: self._update_command_preview())
                row["value_widget"] = directory_entry
                row["browse_button"] = browse
                row["name_widget"] = name_entry
                row["name_var"] = name_var
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
                    browse.grid(row=0, column=1, padx=(8, 0))
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
                check_widget.configure(text=f"{flag.name}{' *' if flag.required else ''}")
            assert isinstance(value_var, tk.BooleanVar)
            value_var.set(False)
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

    def _hide_unused_export_parameter_rows(self, used_count: int) -> None:
        for row in self._export_param_rows[used_count:]:
            frame = row["frame"]
            if isinstance(frame, ttk.Frame):
                frame.grid_remove()

    def _build_parameter_form(self) -> None:
        self.parameter_vars.clear()

        row = 0
        for flag in self.current_job.flags:
            if flag.name == "--settings":
                continue
            default_value = resolve_job_default(
                self.app.project,
                "Particles",
                "Export particles",
                "ts_export_particles",
                flag.name,
                flag.default_value,
            )
            if flag.name == "--device_list":
                default_value = "0"
            if flag.name == "--output_star":
                path_value = Path(default_value.strip()) if default_value.strip() else Path("")
                if path_value.suffix:
                    self.export_output_directory_var.set(str(path_value.parent) if str(path_value.parent) != "." else "")
                    self.export_output_name_var.set(path_value.name)
                    default_value = str(path_value.parent) if str(path_value.parent) != "." else ""
                else:
                    self.export_output_directory_var.set(default_value)
                    self.export_output_name_var.set("Output.star")
            self._configure_export_parameter_row(row, flag, default_value)
            row += 1

        self._hide_unused_export_parameter_rows(row)
        self._update_command_preview()

    def _particle_environment_default(self) -> str:
        default_value = resolve_job_default(
            self.app.project,
            "Particles",
            "Export particles",
            "ts_export_particles",
            "execution_environment",
            "None",
        ).strip() or "None"
        available = set(environment_titles(self.app.project))
        if default_value in available and default_value != "None":
            return default_value
        warp_default = resolve_job_default(
            self.app.project,
            "Processing",
            "Tilt series",
            "ts_export_particles",
            "execution_environment",
            "None",
        ).strip() or "None"
        return warp_default if warp_default in available else "None"

    def _effective_particle_environment(self) -> str:
        chosen = self.environment_var.get().strip() or "None"
        if chosen != "None" and self.app.resolve_environment_activation(chosen).strip():
            return chosen
        particle_default = self._particle_environment_default()
        if particle_default != "None" and self.app.resolve_environment_activation(particle_default).strip():
            return particle_default
        return "None"

    def _apply_particle_custom_defaults(self) -> None:
        self.distance_radius_px_var.set(
            resolve_job_default(
                self.app.project,
                "Particles",
                "Distance clean",
                "distance_clean",
                "radius_px",
                "",
            )
        )
        self.distance_radius_ang_var.set(
            resolve_job_default(
                self.app.project,
                "Particles",
                "Distance clean",
                "distance_clean",
                "radius_angstrom",
                "",
            )
        )
        self.distance_output_name_var.set(
            resolve_job_default(
                self.app.project,
                "Particles",
                "Distance clean",
                "distance_clean",
                "output_star",
                "Output.star",
            )
        )
        self.distance_cleaned_var.set(
            resolve_job_default(
                self.app.project,
                "Particles",
                "Distance clean",
                "distance_clean",
                "write_cleaned",
                "true",
            ).lower()
            in {"1", "true", "yes", "on"}
        )
        self.distance_duplicates_var.set(
            resolve_job_default(
                self.app.project,
                "Particles",
                "Distance clean",
                "distance_clean",
                "write_dublicates",
                "",
            ).lower()
            in {"1", "true", "yes", "on"}
        )

        self.intersect_identification_mode_var.set(
            resolve_job_default(
                self.app.project,
                "Particles",
                "Intersect .star-files",
                "intersect_star_files",
                "identification_mode",
                "By distance",
            )
        )
        self.intersect_radius_px_var.set(
            resolve_job_default(
                self.app.project,
                "Particles",
                "Intersect .star-files",
                "intersect_star_files",
                "radius_px",
                "",
            )
        )
        self.intersect_radius_ang_var.set(
            resolve_job_default(
                self.app.project,
                "Particles",
                "Intersect .star-files",
                "intersect_star_files",
                "radius_angstrom",
                "",
            )
        )
        self.intersect_output_name_var.set(
            resolve_job_default(
                self.app.project,
                "Particles",
                "Intersect .star-files",
                "intersect_star_files",
                "output_star",
                "Output.star",
            )
        )
        self.intersect_common_var.set(
            resolve_job_default(
                self.app.project,
                "Particles",
                "Intersect .star-files",
                "intersect_star_files",
                "write_common",
                "true",
            ).lower()
            in {"1", "true", "yes", "on"}
        )
        self.intersect_unique_var.set(
            resolve_job_default(
                self.app.project,
                "Particles",
                "Intersect .star-files",
                "intersect_star_files",
                "write_unique",
                "",
            ).lower()
            in {"1", "true", "yes", "on"}
        )

        self.abundance_compare_samples_var.set(
            resolve_job_default(
                self.app.project,
                "Particles",
                "Plot particle abundance",
                "plot_particle_abundance",
                "compare_samples",
                "",
            ).lower()
            in {"1", "true", "yes", "on"}
        )
        self.abundance_measure_var.set(
            resolve_job_default(
                self.app.project,
                "Particles",
                "Plot particle abundance",
                "plot_particle_abundance",
                "plot_mode",
                "Plot total particle numbers",
            )
        )
        self.convergence_directory_var.set(
            resolve_job_default(
                self.app.project,
                "Particles",
                "Plot classification convergence",
                "plot_classification_convergence",
                "input_directory",
                "",
            )
        )
        self.merge_split_mode_var.set(
            resolve_job_default(
                self.app.project,
                "Particles",
                "Merge/Split .star-files",
                "merge_split_star_files",
                "mode",
                "Merge .star files",
            )
        )
        self.merge_split_output_dir_var.set(
            resolve_job_default(
                self.app.project,
                "Particles",
                "Merge/Split .star-files",
                "merge_split_star_files",
                "output_directory",
                "",
            )
        )
        self.merge_split_output_name_var.set(
            resolve_job_default(
                self.app.project,
                "Particles",
                "Merge/Split .star-files",
                "merge_split_star_files",
                "output_name",
                "Output.star",
            )
        )

    def _browse_parameter(self, flag: WarpToolFlag) -> None:
        if flag.name == "--output_star":
            value = filedialog.askdirectory(title=f"Select output directory for {flag.name}")
        elif flag.browse_mode == "dir":
            value = filedialog.askdirectory(title=f"Select value for {flag.name}")
        else:
            value = filedialog.askopenfilename(title=f"Select value for {flag.name}")
        if value:
            self.parameter_vars[flag.name].set(value)
            self._update_command_preview()

    def _selected_job_key(self) -> str:
        definition = self.job_catalog.get(self.job_type_var.get())
        return definition.job_key if definition is not None else ""

    def _hide_all_job_views(self) -> None:
        self.export_frame.grid_remove()
        self.distance_frame.grid_remove()
        self.intersect_frame.grid_remove()
        self.merge_split_frame.grid_remove()
        self.abundance_frame.grid_remove()
        self.convergence_frame.grid_remove()
        self.history_frame.grid_remove()

    def _on_job_type_changed(self, _event=None) -> None:
        self._scroll_job_view_to_top()
        job_key = self._selected_job_key()
        self._hide_all_job_views()
        if self.job_type_var.get() == "Job history":
            self.history_frame.grid()
            self._refresh_history()
        elif job_key == "ts_export_particles":
            available_envs = set(environment_titles(self.app.project))
            current_env = self.environment_var.get().strip()
            if current_env not in available_envs:
                self.environment_var.set(self._particle_environment_default())
            self.export_frame.grid()
            self._update_command_preview()
        elif job_key == "distance_clean":
            self.distance_frame.grid()
            self._update_distance_clean_preview()
        elif job_key == "intersect_star_files":
            self.intersect_frame.grid()
            self._update_intersect_preview()
        elif job_key == "merge_split_star_files":
            self.merge_split_frame.grid()
            self._update_merge_split_preview()
        elif job_key == "plot_particle_abundance":
            self.abundance_frame.grid()
        elif job_key == "plot_classification_convergence":
            self.convergence_frame.grid()
        else:
            self.command_text.delete("1.0", "end")
            self.distance_command_text.delete("1.0", "end")
            self.intersect_log_text.delete("1.0", "end")

    def _add_selected_dataset(self) -> None:
        dataset_name = self.dataset_picker_var.get()
        if dataset_name and dataset_name not in self.selected_export_datasets:
            self.selected_export_datasets.append(dataset_name)
            self._refresh_selected_dataset_list()

    def _add_all_datasets(self) -> None:
        for dataset_name in self._dataset_options():
            if dataset_name not in self.selected_export_datasets:
                self.selected_export_datasets.append(dataset_name)
        self._refresh_selected_dataset_list()

    def _remove_selected_dataset(self) -> None:
        selection = self.selected_dataset_list.curselection()
        if not selection:
            return
        index = selection[0]
        del self.selected_export_datasets[index]
        self._refresh_selected_dataset_list()

    def _refresh_selected_dataset_list(self) -> None:
        self.selected_dataset_list.delete(0, "end")
        for dataset_name in self.selected_export_datasets:
            self.selected_dataset_list.insert("end", dataset_name)
        self._update_command_preview()

    def _add_selected_distance_dataset(self) -> None:
        dataset_name = self.distance_dataset_picker_var.get()
        if dataset_name and dataset_name not in self.selected_distance_datasets:
            self.selected_distance_datasets.append(dataset_name)
            self._refresh_distance_selected_dataset_list()

    def _add_all_distance_datasets(self) -> None:
        for dataset_name in self._dataset_options():
            if dataset_name not in self.selected_distance_datasets:
                self.selected_distance_datasets.append(dataset_name)
        self._refresh_distance_selected_dataset_list()

    def _remove_selected_distance_dataset(self) -> None:
        selection = self.distance_selected_dataset_list.curselection()
        if not selection:
            return
        index = selection[0]
        del self.selected_distance_datasets[index]
        self._refresh_distance_selected_dataset_list()

    def _refresh_distance_selected_dataset_list(self) -> None:
        self.distance_selected_dataset_list.delete(0, "end")
        for dataset_name in self.selected_distance_datasets:
            self.distance_selected_dataset_list.insert("end", dataset_name)
        self._update_distance_clean_preview()

    def _add_selected_intersect_dataset(self) -> None:
        dataset_name = self.intersect_dataset_picker_var.get()
        if dataset_name and dataset_name not in self.selected_intersect_datasets:
            self.selected_intersect_datasets.append(dataset_name)
            self._refresh_intersect_selected_dataset_list()

    def _add_all_intersect_datasets(self) -> None:
        for dataset_name in self._dataset_options():
            if dataset_name not in self.selected_intersect_datasets:
                self.selected_intersect_datasets.append(dataset_name)
        self._refresh_intersect_selected_dataset_list()

    def _remove_selected_intersect_dataset(self) -> None:
        selection = self.intersect_selected_dataset_list.curselection()
        if not selection:
            return
        index = selection[0]
        del self.selected_intersect_datasets[index]
        self._refresh_intersect_selected_dataset_list()

    def _refresh_intersect_selected_dataset_list(self) -> None:
        self.intersect_selected_dataset_list.delete(0, "end")
        for dataset_name in self.selected_intersect_datasets:
            self.intersect_selected_dataset_list.insert("end", dataset_name)
        self._update_intersect_preview()

    def _quote(self, value: str) -> str:
        return shlex.quote(value)

    def _resolved_export_settings_path(self, dataset: DatasetRecord, *, require_exists: bool = False) -> str:
        candidates: list[Path] = []
        if dataset.tilt_series_settings_file.strip():
            candidates.append(Path(dataset.tilt_series_settings_file.strip()))
        if dataset.processing_folder.strip():
            candidates.append(Path(dataset.processing_folder.strip()) / "warp_tiltseries.settings")
        if dataset.tilt_series_processing_folder.strip():
            tilt_processing = Path(dataset.tilt_series_processing_folder.strip())
            candidates.append(tilt_processing.parent / "warp_tiltseries.settings")

        seen: set[str] = set()
        unique_candidates: list[Path] = []
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            unique_candidates.append(candidate)

        if require_exists:
            for candidate in unique_candidates:
                if candidate.exists():
                    return str(candidate)
            return ""

        if unique_candidates:
            return str(unique_candidates[0])
        return ""

    def _validate_export_datasets(self, commands: list[tuple[DatasetRecord, str]]) -> list[str]:
        problems: list[str] = []
        for dataset, _command in commands:
            settings_path = self._resolved_export_settings_path(dataset, require_exists=True)
            if not settings_path:
                problems.append(
                    f"{dataset.dataset_name}: no Warp tilt-series settings file could be resolved."
                )
        return problems

    def _dataset_command_values(self, dataset: DatasetRecord) -> dict[str, str]:
        output_directory = self.parameter_vars["--output_star"].get().strip() if "--output_star" in self.parameter_vars else ""
        output_name = _ensure_star_name_local(self.export_output_name_var.get().strip())
        output_star = str(Path(output_directory) / output_name) if output_directory else output_name
        output_path = Path(output_star) if output_star else Path("")
        if output_star and output_path.suffix:
            suffixed_output = output_path.with_name(
                f"{output_path.stem}_{dataset.dataset_name}{output_path.suffix}"
            )
        elif output_star:
            suffixed_output = Path(f"{output_star}_{dataset.dataset_name}")
        else:
            suffixed_output = Path("")

        return {
            "--settings": self._resolved_export_settings_path(dataset, require_exists=False),
            "--output_star": str(suffixed_output) if output_star else "",
        }

    def _build_command_for_dataset(self, dataset: DatasetRecord) -> str:
        parts = [f"WarpTools {self.current_job.command}"]
        dataset_specific = self._dataset_command_values(dataset)
        for flag in self.current_job.flags:
            variable = self.parameter_vars.get(flag.name)
            if flag.widget == "bool":
                if variable is not None and variable.get():
                    parts.append(flag.name)
                continue

            value = dataset_specific.get(flag.name, "")
            if variable is not None:
                current_value = str(variable.get()).strip()
                if flag.name not in dataset_specific:
                    value = current_value
                elif not value:
                    value = current_value
            if value:
                parts.append(f"{flag.name} {self._quote(value)}")
        return " ".join(parts)

    def _commands(self) -> list[tuple[DatasetRecord, str]]:
        dataset_map = self._dataset_map()
        commands: list[tuple[DatasetRecord, str]] = []
        for dataset_name in self.selected_export_datasets:
            dataset = dataset_map.get(dataset_name)
            if dataset is None:
                continue
            commands.append((dataset, self._build_command_for_dataset(dataset)))
        return commands

    def _update_command_preview(self, *_args) -> None:
        commands = [command for _dataset, command in self._commands()]
        self.command_text.delete("1.0", "end")
        self.command_text.insert("1.0", "\n".join(commands) if commands else "")

    def _raw_output_star(self) -> str:
        variable = self.parameter_vars.get("--output_star")
        directory = str(variable.get()).strip() if variable is not None else ""
        name = _ensure_star_name_local(self.export_output_name_var.get().strip())
        if directory:
            return str(Path(directory) / name)
        return name

    def _merged_output_path(self) -> Path | None:
        output_star = self._raw_output_star()
        if not output_star:
            return None
        output_path = Path(output_star)
        if output_path.suffix:
            return output_path.with_name(f"{output_path.stem}_merged{output_path.suffix}")
        return Path(f"{output_star}_merged")

    def _is_2d_export(self) -> bool:
        variable = self.parameter_vars.get("--2d")
        return bool(variable and variable.get())

    def _current_preview_text(self) -> str:
        return self.command_text.get("1.0", "end").strip()

    def _browse_distance_input_star(self) -> None:
        value = filedialog.askopenfilename(
            title="Select particles STAR",
            filetypes=[("STAR files", "*.star"), ("All files", "*.*")],
        )
        if value:
            busy = self._show_particle_busy("Reading in .star-file metadata. Please wait.")
            try:
                self.distance_input_star_var.set(value)
            finally:
                self._close_particle_busy(busy)

    def _on_distance_input_changed(self) -> None:
        input_path = self.distance_input_star_var.get().strip()
        if not input_path:
            self.distance_mode_var.set("-")
            self.distance_pixel_size = 0.0
            self.distance_pixel_size_var.set("-")
            self._update_distance_clean_preview()
            return
        try:
            self.distance_mode_var.set(detect_particle_star_mode(input_path))
            self.distance_pixel_size = particle_star_pixel_size(input_path)
            self.distance_pixel_size_var.set(f"{self.distance_pixel_size:.4f} A")
            self._sync_distance_fields(source="px")
        except (FileNotFoundError, StarMergeError, ValueError):
            self.distance_mode_var.set("unrecognized")
            self.distance_pixel_size = 0.0
            self.distance_pixel_size_var.set("-")
        self._update_distance_clean_preview()

    def _on_distance_radius_px_changed(self) -> None:
        if self.distance_updating:
            return
        self._sync_distance_fields(source="px")
        self._update_distance_clean_preview()

    def _on_distance_radius_ang_changed(self) -> None:
        if self.distance_updating:
            return
        self._sync_distance_fields(source="ang")
        self._update_distance_clean_preview()

    def _sync_distance_fields(self, source: str) -> None:
        if self.distance_pixel_size <= 0:
            return
        try:
            self.distance_updating = True
            if source == "px":
                radius_px = float(self.distance_radius_px_var.get().strip())
                self.distance_radius_ang_var.set(f"{radius_px * self.distance_pixel_size:.4f}")
            else:
                radius_ang = float(self.distance_radius_ang_var.get().strip())
                self.distance_radius_px_var.set(f"{radius_ang / self.distance_pixel_size:.4f}")
        except ValueError:
            pass
        finally:
            self.distance_updating = False

    def _distance_preview_text(self) -> str:
        input_path = self.distance_input_star_var.get().strip()
        if not input_path:
            return ""
        dataset_list = ", ".join(self.selected_distance_datasets) or "(no datasets selected)"
        output_name = self.distance_output_name_var.get().strip() or "(no output name)"
        modes: list[str] = []
        if self.distance_cleaned_var.get():
            modes.append("cleaned")
        if self.distance_duplicates_var.get():
            modes.append("dublicates")
        mode_text = ", ".join(modes) if modes else "(no output mode selected)"
        return "\n".join(
            [
                "Distance clean",
                f"Input STAR: {input_path}",
                f"Detected mode: {self.distance_mode_var.get()}",
                f"Datasets: {dataset_list}",
                f"Clearing radius (px): {self.distance_radius_px_var.get().strip() or '-'}",
                f"Clearing radius (A): {self.distance_radius_ang_var.get().strip() or '-'}",
                f"Output name: {output_name}",
                f"Output modes: {mode_text}",
            ]
        )

    def _set_distance_log(self, text: str) -> None:
        self.distance_command_text.delete("1.0", "end")
        self.distance_command_text.insert("1.0", text)

    def _append_distance_log(self, line: str) -> None:
        current = self.distance_command_text.get("1.0", "end").strip()
        updated = f"{current}\n{line}".strip() if current else line
        self._set_distance_log(updated)

    def _update_distance_clean_preview(self) -> None:
        self._set_distance_log(self._distance_preview_text())

    def _distance_output_parameters(self) -> dict[str, str]:
        values = {
            "input_star": self.distance_input_star_var.get().strip(),
            "radius_px": self.distance_radius_px_var.get().strip(),
            "radius_angstrom": self.distance_radius_ang_var.get().strip(),
            "output_star": self.distance_output_name_var.get().strip(),
        }
        if self.distance_cleaned_var.get():
            values["write_cleaned"] = "true"
        if self.distance_duplicates_var.get():
            values["write_dublicates"] = "true"
        return {key: value for key, value in values.items() if value}

    def _copy_distance_clean_preview(self) -> None:
        preview = self._distance_preview_text()
        if not preview:
            return
        self.frame.clipboard_clear()
        self.frame.clipboard_append(preview)
        for dataset_name in self.selected_distance_datasets:
            dataset = self._dataset_map().get(dataset_name)
            if dataset is None:
                continue
            dataset.job_history.append(
                JobHistoryEntry(
                    timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    action="copied",
                    group="Particles",
                    job_name="distance_clean",
                    command=preview,
                    processing_tab="Processing: Particle jobs",
                    dataset_name=dataset.dataset_name,
                    parameters=self._distance_output_parameters(),
                )
            )
        self.app.on_project_changed("particles")
        self.app.status_var.set("Distance clean log copied to clipboard")

    def _run_distance_clean(self) -> None:
        input_star = self.distance_input_star_var.get().strip()
        if not input_star:
            messagebox.showinfo("Missing STAR file", "Please select a particles STAR file first.")
            return
        if not self.selected_distance_datasets:
            messagebox.showinfo("No datasets selected", "Please add at least one dataset.")
            return
        try:
            radius_px = float(self.distance_radius_px_var.get().strip())
        except ValueError:
            messagebox.showinfo("Invalid radius", "Please provide a numerical clearing radius in px.")
            return

        preview = self._distance_preview_text()
        cancel_event = threading.Event()
        busy = self._show_particle_busy(
            "CryoPal is calculating the distance clean outputs. Please wait.",
            on_abort=cancel_event.set,
        )

        def worker() -> None:
            try:
                self.app.root.after(0, lambda: self._set_distance_log(""))
                outputs = distance_clean_particles(
                    input_star_path=input_star,
                    dataset_names=self.selected_distance_datasets,
                    radius_px=radius_px,
                    output_name=self.distance_output_name_var.get().strip(),
                    write_cleaned=self.distance_cleaned_var.get(),
                    write_duplicates=self.distance_duplicates_var.get(),
                    log_callback=lambda line: self.app.root.after(0, lambda message=line: self._append_distance_log(message)),
                    cancel_event=cancel_event,
                )
            except OperationAborted:
                self.app.root.after(
                    0,
                    lambda: (
                        self._close_particle_busy(busy),
                        self._set_distance_log(preview),
                        self.app.status_var.set("Distance clean aborted"),
                    ),
                )
                return
            except Exception as exc:
                self.app.root.after(
                    0,
                    lambda: (
                        self._close_particle_busy(busy),
                        self.app.status_var.set(f"Distance clean failed: {exc}"),
                    ),
                )
                return

            def update_status() -> None:
                self._close_particle_busy(busy)
                for dataset_name in self.selected_distance_datasets:
                    dataset = self._dataset_map().get(dataset_name)
                    if dataset is None:
                        continue
                    dataset.job_history.append(
                        JobHistoryEntry(
                            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            action="ran",
                            group="Particles",
                            job_name="distance_clean",
                            command=preview,
                            processing_tab="Processing: Particle jobs",
                            dataset_name=dataset.dataset_name,
                            parameters=self._distance_output_parameters(),
                        )
                    )
                self.app.on_project_changed("particles")
                written: list[str] = []
                if outputs.cleaned_path is not None:
                    written.append(outputs.cleaned_path.name)
                if outputs.duplicates_path is not None:
                    written.append(outputs.duplicates_path.name)
                self.app.status_var.set(
                    "Distance clean finished: " + ", ".join(written or ["no outputs"])
                )

            self.app.root.after(0, update_status)

        threading.Thread(target=worker, daemon=True).start()
        self.app.status_var.set("Started distance clean")

    def _browse_intersect_input_stars(self) -> None:
        values = filedialog.askopenfilenames(
            title="Select particle STAR files",
            filetypes=[("STAR files", "*.star"), ("All files", "*.*")],
        )
        for value in values:
            if value not in self.intersect_star_paths:
                self.intersect_star_paths.append(value)
        self._refresh_intersect_star_list(show_busy=True)

    def _remove_selected_intersect_star(self) -> None:
        selection = list(self.intersect_star_list.curselection())
        if not selection:
            return
        for index in reversed(selection):
            del self.intersect_star_paths[index]
        self._refresh_intersect_star_list(show_busy=True)

    def _refresh_intersect_star_list(self, *, show_busy: bool = False) -> None:
        self.intersect_star_list.delete(0, "end")
        for path in self.intersect_star_paths:
            self.intersect_star_list.insert("end", path)
        if self.intersect_star_paths:
            if self.selected_intersect_star_path not in self.intersect_star_paths:
                self.selected_intersect_star_path = self.intersect_star_paths[0]
            selected_index = self.intersect_star_paths.index(self.selected_intersect_star_path)
            self.intersect_star_list.selection_clear(0, "end")
            self.intersect_star_list.selection_set(selected_index)
            self.intersect_star_list.activate(selected_index)
        else:
            self.selected_intersect_star_path = ""
        busy = self._show_particle_busy("Reading in .star-file metadata. Please wait.") if show_busy else None
        try:
            self._on_intersect_inputs_changed()
        finally:
            self._close_particle_busy(busy)

    def _on_intersect_inputs_changed(self) -> None:
        if not self.intersect_star_paths:
            self.intersect_mode_var.set("-")
            self.intersect_pixel_size = 0.0
            self.intersect_pixel_size_var.set("-")
            self._update_intersect_preview()
            return
        try:
            if not self.selected_intersect_star_path or self.selected_intersect_star_path not in self.intersect_star_paths:
                self.selected_intersect_star_path = self.intersect_star_paths[0]
            self._refresh_selected_intersect_star_metadata()
        except (FileNotFoundError, StarMergeError, ValueError):
            self.intersect_mode_var.set("unrecognized")
            self.intersect_pixel_size = 0.0
            self.intersect_pixel_size_var.set("-")
        self._update_intersect_preview()

    def _on_intersect_star_selected(self, _event=None) -> None:
        selection = self.intersect_star_list.curselection()
        if not selection:
            return
        self.selected_intersect_star_path = self.intersect_star_paths[selection[0]]
        busy = self._show_particle_busy("Reading in .star-file metadata. Please wait.")
        try:
            self._refresh_selected_intersect_star_metadata()
            self._update_intersect_preview()
        finally:
            self._close_particle_busy(busy)

    def _browse_abundance_input_stars(self) -> None:
        values = filedialog.askopenfilenames(
            title="Select particle STAR files",
            filetypes=[("STAR files", "*.star"), ("All files", "*.*")],
        )
        for value in values:
            if value not in self.abundance_star_paths:
                self.abundance_star_paths.append(value)
        self._refresh_abundance_star_list(show_busy=True)

    def _remove_selected_abundance_star(self) -> None:
        selection = list(self.abundance_star_list.curselection())
        if not selection:
            return
        for index in reversed(selection):
            del self.abundance_star_paths[index]
        self._refresh_abundance_star_list(show_busy=True)

    def _refresh_abundance_star_list(self, *, show_busy: bool = False) -> None:
        self.abundance_star_list.delete(0, "end")
        for path in self.abundance_star_paths:
            self.abundance_star_list.insert("end", path)
        if self.abundance_star_paths:
            if self.selected_abundance_star_path not in self.abundance_star_paths:
                self.selected_abundance_star_path = self.abundance_star_paths[0]
            selected_index = self.abundance_star_paths.index(self.selected_abundance_star_path)
            self.abundance_star_list.selection_clear(0, "end")
            self.abundance_star_list.selection_set(selected_index)
            self.abundance_star_list.activate(selected_index)
        else:
            self.selected_abundance_star_path = ""
        busy = self._show_particle_busy("Reading in .star-file metadata. Please wait.") if show_busy else None
        try:
            self._refresh_abundance_selected_star_metadata()
        finally:
            self._close_particle_busy(busy)
        self._mark_abundance_dirty()

    def _browse_merge_split_input_stars(self) -> None:
        values = filedialog.askopenfilenames(
            title="Select particle STAR files",
            filetypes=[("STAR files", "*.star"), ("All files", "*.*")],
        )
        if self.merge_split_mode_var.get() == "Split .star file":
            self.merge_split_star_paths = [values[0]] if values else []
        else:
            for value in values:
                if value not in self.merge_split_star_paths:
                    self.merge_split_star_paths.append(value)
        self._refresh_merge_split_star_list()

    def _browse_merge_split_input_directory(self) -> None:
        directory = filedialog.askdirectory(title="Select directory containing .star files")
        if not directory:
            return
        paths = sorted(Path(directory).glob("*.star"), key=lambda path: path.name.casefold())
        if self.merge_split_mode_var.get() == "Split .star file":
            self.merge_split_star_paths = [str(paths[0])] if paths else []
        else:
            for path in paths:
                value = str(path)
                if value not in self.merge_split_star_paths:
                    self.merge_split_star_paths.append(value)
        self._refresh_merge_split_star_list()

    def _remove_selected_merge_split_star(self) -> None:
        selection = list(self.merge_split_star_list.curselection())
        if not selection:
            return
        for index in reversed(selection):
            del self.merge_split_star_paths[index]
        self._refresh_merge_split_star_list()

    def _refresh_merge_split_star_list(self) -> None:
        self.merge_split_star_list.delete(0, "end")
        for path in self.merge_split_star_paths:
            self.merge_split_star_list.insert("end", path)
        if self.merge_split_star_paths:
            if self.selected_merge_split_star_path not in self.merge_split_star_paths:
                self.selected_merge_split_star_path = self.merge_split_star_paths[0]
            selected_index = self.merge_split_star_paths.index(self.selected_merge_split_star_path)
            self.merge_split_star_list.selection_clear(0, "end")
            self.merge_split_star_list.selection_set(selected_index)
            self.merge_split_star_list.activate(selected_index)
        else:
            self.selected_merge_split_star_path = ""
        self._update_merge_split_preview()

    def _on_merge_split_star_selected(self, _event=None) -> None:
        selection = self.merge_split_star_list.curselection()
        if not selection:
            return
        self.selected_merge_split_star_path = self.merge_split_star_paths[selection[0]]
        self._update_merge_split_preview()

    def _browse_merge_split_output_directory(self) -> None:
        value = filedialog.askdirectory(title="Select output directory")
        if value:
            self.merge_split_output_dir_var.set(value)

    def _on_merge_split_mode_changed(self) -> None:
        if self.merge_split_mode_var.get() == "Split .star file" and len(self.merge_split_star_paths) > 1:
            self.merge_split_star_paths = self.merge_split_star_paths[:1]
        self._refresh_merge_split_star_list()

    def _merge_split_preview_text(self) -> str:
        mode = self.merge_split_mode_var.get()
        lines = [mode]
        if self.merge_split_star_paths:
            lines.append(
                "Input STAR file(s): " + ", ".join(Path(path).name for path in self.merge_split_star_paths)
            )
        else:
            lines.append("Input STAR file(s): (none selected)")
        lines.append(f"Output directory: {self.merge_split_output_dir_var.get().strip() or '(none selected)'}")
        lines.append(f"Output name: {self.merge_split_output_name_var.get().strip() or '(none provided)'}")
        return "\n".join(lines)

    def _set_merge_split_log(self, text: str) -> None:
        self.merge_split_log_text.delete("1.0", "end")
        self.merge_split_log_text.insert("1.0", text)

    def _append_merge_split_log(self, line: str) -> None:
        current = self.merge_split_log_text.get("1.0", "end").strip()
        updated = f"{current}\n{line}".strip() if current else line
        self._set_merge_split_log(updated)

    def _update_merge_split_preview(self) -> None:
        self._set_merge_split_log(self._merge_split_preview_text())

    def _copy_merge_split_preview(self) -> None:
        preview = self._merge_split_preview_text()
        if not preview:
            return
        self.frame.clipboard_clear()
        self.frame.clipboard_append(preview)
        self.app.status_var.set("Merge/Split log copied to clipboard")

    def _merge_split_parameters(self) -> dict[str, str]:
        return {
            "mode": self.merge_split_mode_var.get(),
            "input_stars": ", ".join(self.merge_split_star_paths),
            "output_directory": self.merge_split_output_dir_var.get().strip(),
            "output_name": self.merge_split_output_name_var.get().strip(),
        }

    def _run_merge_split(self) -> None:
        mode = self.merge_split_mode_var.get()
        star_paths = [path for path in self.merge_split_star_paths if path.strip()]
        if mode == "Merge .star files":
            if len(star_paths) < 2:
                messagebox.showinfo("Missing STAR files", "Please add at least two STAR files to merge.")
                return
        else:
            if len(star_paths) != 1:
                messagebox.showinfo("Missing STAR file", "Please select exactly one STAR file to split.")
                return
        output_dir = self.merge_split_output_dir_var.get().strip()
        if not output_dir:
            messagebox.showinfo("Missing output directory", "Please select an output directory first.")
            return
        output_name = self.merge_split_output_name_var.get().strip()
        if not output_name:
            messagebox.showinfo("Missing output name", "Please provide an output name first.")
            return

        preview = self._merge_split_preview_text()
        cancel_event = threading.Event()
        busy = self._show_particle_busy(
            "CryoPal is processing the STAR file operation. Please wait.",
            on_abort=cancel_event.set,
        )

        def worker() -> None:
            try:
                self.app.root.after(0, lambda: self._set_merge_split_log(""))
                if mode == "Merge .star files":
                    output_path = Path(output_dir) / _ensure_star_name_local(output_name)
                    result = merge_particle_star_files(
                        star_paths,
                        output_path,
                        log_callback=lambda line: self.app.root.after(0, lambda message=line: self._append_merge_split_log(message)),
                        cancel_event=cancel_event,
                    )
                else:
                    result = split_particle_star_file(
                        star_paths[0],
                        output_dir,
                        output_name,
                        log_callback=lambda line: self.app.root.after(0, lambda message=line: self._append_merge_split_log(message)),
                        cancel_event=cancel_event,
                    )
            except OperationAborted:
                self.app.root.after(
                    0,
                    lambda: (
                        self._close_particle_busy(busy),
                        self._set_merge_split_log(preview),
                        self.app.status_var.set("Merge/Split aborted"),
                    ),
                )
                return
            except Exception as exc:
                self.app.root.after(
                    0,
                    lambda: (
                        self._close_particle_busy(busy),
                        self._append_merge_split_log(f"Error: {exc}"),
                        self.app.status_var.set(f"Merge/Split failed: {exc}"),
                    ),
                )
                return

            def update_status() -> None:
                self._close_particle_busy(busy)
                self.app.clear_abort_request()
                if isinstance(result, MergeResult):
                    status = f"Merged STAR files into {result.merged_particles_path.name}"
                else:
                    status = f"Split STAR file into {len(result.output_paths)} TS-specific files"
                self._record_project_scope_history_entry(
                    "merge_split_star_files",
                    parameters=self._merge_split_parameters(),
                    command=preview,
                )
                self.app.status_var.set(status)

            self.app.root.after(0, update_status)

        threading.Thread(target=worker, daemon=True).start()
        self.app.status_var.set("Started STAR merge/split")

    def _on_abundance_star_selected(self, _event=None) -> None:
        selection = self.abundance_star_list.curselection()
        if not selection:
            return
        self.selected_abundance_star_path = self.abundance_star_paths[selection[0]]
        busy = self._show_particle_busy("Reading in .star-file metadata. Please wait.")
        try:
            self._refresh_abundance_selected_star_metadata()
        finally:
            self._close_particle_busy(busy)
        self._mark_abundance_dirty()

    def _refresh_abundance_selected_star_metadata(self) -> None:
        if not self.selected_abundance_star_path:
            self.abundance_mode_var.set("-")
            self.abundance_pixel_size_var.set("-")
            return
        try:
            self.abundance_mode_var.set(detect_particle_star_mode(self.selected_abundance_star_path))
            self.abundance_pixel_size_var.set(f"{particle_star_pixel_size(self.selected_abundance_star_path):.4f} A")
        except (FileNotFoundError, StarMergeError, ValueError):
            self.abundance_mode_var.set("unrecognized")
            self.abundance_pixel_size_var.set("-")

    def _abundance_measure_key(self) -> str:
        return "density" if self.abundance_measure_var.get() == "Plot particle density" else "total"

    def _abundance_dataset_to_sample(self) -> dict[str, str]:
        return {
            dataset.dataset_name: dataset.sample or dataset.dataset_name
            for dataset in self.app.project.datasets
        }

    def _mark_abundance_dirty(self, _event=None) -> None:
        self.current_abundance_plots = []
        self.current_abundance_summary = ""
        self._reset_abundance_plot_display(
            "Plot settings changed. Click 'Render plot' to update the particle abundance plots."
        )

    def _reset_abundance_plot_display(self, message: str) -> None:
        for child in self.abundance_plot_container.winfo_children():
            child.destroy()
        self.abundance_plot_summary.config(text=message)

    def _on_abundance_rescale_changed(self) -> None:
        if self.current_abundance_plots:
            self._display_abundance_plots(self.current_abundance_plots, self.current_abundance_summary)
        else:
            self._mark_abundance_dirty()

    def _on_abundance_plot_canvas_configure(self, _event=None) -> None:
        if not self.abundance_rescale_var.get() or not self.current_abundance_plots:
            return
        if self._abundance_resize_after_id is not None:
            try:
                self.frame.after_cancel(self._abundance_resize_after_id)
            except tk.TclError:
                pass
        self._abundance_resize_after_id = self.frame.after(120, self._rerender_abundance_rescaled)

    def _rerender_abundance_rescaled(self) -> None:
        self._abundance_resize_after_id = None
        if self.abundance_rescale_var.get() and self.current_abundance_plots:
            self._display_abundance_plots(self.current_abundance_plots, self.current_abundance_summary)

    def _show_particle_busy(self, message: str, on_abort=None) -> _ParticleBusyDialog:
        return _ParticleBusyDialog(self.frame, message, on_abort=on_abort)

    def _close_particle_busy(self, dialog: _ParticleBusyDialog | None) -> None:
        if dialog is not None:
            dialog.close()

    def _display_abundance_plots(self, plots: list[ParticleAbundancePlot], summary: str) -> None:
        for child in self.abundance_plot_container.winfo_children():
            child.destroy()
        self.abundance_plot_summary.config(text=summary)
        for plot in plots:
            block = ttk.Frame(self.abundance_plot_container)
            block.grid(sticky="ew", pady=(0, 14))
            block.columnconfigure(0, weight=1)
            self._draw_abundance_plot(
                block,
                plot,
                rescale_to_window=self.abundance_rescale_var.get(),
                available_width=max(self.abundance_plot_canvas.winfo_width() - 24, 320),
            )

    def _draw_abundance_plot(
        self,
        parent: ttk.Frame,
        plot: ParticleAbundancePlot,
        *,
        rescale_to_window: bool = False,
        available_width: int | None = None,
    ) -> None:
        title = ttk.Label(
            parent,
            text=(
                f"{plot.star_path.name} | mode: {plot.mode} | "
                f"{'samples' if plot.compare_samples else 'datasets'} | "
                f"{'total particle numbers' if plot.measure == 'total' else 'particle density'}"
            ),
            style="Heading.TLabel",
        )
        title.grid(sticky="w", pady=(0, 6))

        conditions = [plot.all_condition] + plot.conditions if plot.all_condition is not None else plot.conditions
        if plot.measure == "total":
            all_values = [float(condition.pooled_total) for condition in conditions]
        else:
            all_values = [value for condition in conditions for value in condition.values]
        if not all_values:
            ttk.Label(parent, text="No matching particle counts found for the current project datasets.").grid(
                sticky="w",
                pady=(0, 8),
            )
            return

        default_width = max(880, 160 * len(conditions))
        canvas_width = max(560, available_width or default_width) if rescale_to_window else default_width
        canvas_height = 360
        canvas = tk.Canvas(parent, width=canvas_width, height=canvas_height, highlightthickness=1, highlightbackground="#d0d7de")
        canvas.grid(sticky="ew" if rescale_to_window else "w", pady=(0, 10))

        left = 70
        right = canvas_width - 30
        top = 30
        bottom = canvas_height - 70
        low = 0.0
        high = max(all_values)
        if high <= low:
            high = low + 1.0

        def y_from_value(value: float) -> float:
            ratio = (value - low) / (high - low)
            return bottom - ratio * (bottom - top)

        canvas.create_line(left, top, left, bottom, fill="#2f3b46", width=1)
        canvas.create_line(left, bottom, right, bottom, fill="#2f3b46", width=1)
        ticks = 5
        for step in range(ticks + 1):
            value = low + (high - low) * step / ticks
            y = y_from_value(value)
            canvas.create_line(left - 5, y, left, y, fill="#2f3b46")
            canvas.create_text(left - 10, y, text=f"{value:.1f}", anchor="e", fill="#2f3b46")

        slot_width = (right - left) / max(len(conditions), 1)
        colors = ["#9ecae1", "#fdae6b", "#a1d99b", "#c7a9e6", "#fdd0a2", "#bcbddc"]
        for index, condition in enumerate(conditions):
            x_center = left + slot_width * (index + 0.5)
            color = colors[index % len(colors)]
            values = condition.values
            if values:
                if plot.measure == "total":
                    total_value = float(condition.pooled_total)
                    bar_top = y_from_value(total_value)
                    canvas.create_rectangle(
                        x_center - slot_width * 0.2,
                        bar_top,
                        x_center + slot_width * 0.2,
                        bottom,
                        fill=color,
                        outline="#2f3b46",
                    )
                    canvas.create_text(
                        x_center,
                        bar_top - 10,
                        text=f"{total_value:.1f}",
                        anchor="s",
                        fill="#2f3b46",
                    )
                else:
                    mean_value = sum(values) / len(values)
                    bar_top = y_from_value(mean_value)
                    canvas.create_rectangle(
                        x_center - slot_width * 0.2,
                        bar_top,
                        x_center + slot_width * 0.2,
                        bottom,
                        fill=color,
                        outline="#2f3b46",
                    )
                    for point_index, value in enumerate(sorted(values)):
                        offsets = (-0.22, -0.11, 0.0, 0.11, 0.22)
                        x_offset = offsets[point_index % len(offsets)] * slot_width
                        y_value = y_from_value(value)
                        canvas.create_oval(
                            x_center + x_offset - 3,
                            y_value - 3,
                            x_center + x_offset + 3,
                            y_value + 3,
                            fill="#ffffff",
                            outline="#111111",
                        )
                    canvas.create_text(
                        x_center,
                        bar_top - 10,
                        text=f"mean {mean_value:.1f}",
                        anchor="s",
                        fill="#2f3b46",
                    )
            else:
                canvas.create_text(x_center, 160, text="no data", fill="#666666")
            canvas.create_text(x_center, bottom + 18, text=condition.label, anchor="n", width=slot_width - 12)
            canvas.create_text(
                x_center,
                bottom + 44,
                text=f"Particles={condition.pooled_total} | N={condition.dataset_count} | TS={condition.tomogram_count}",
                anchor="n",
                width=slot_width - 12,
                fill="#4f5d6b",
            )

    def _convergence_iteration_files(self, directory: str | Path) -> list[tuple[int, Path]]:
        base = Path(directory)
        if not base.is_dir():
            return []
        results: list[tuple[int, Path]] = []
        for path in base.glob("run_it*_data.star"):
            match = re.fullmatch(r"run_it(\d{3})_data\.star", path.name)
            if match is None:
                continue
            iteration = int(match.group(1))
            if iteration <= 0:
                continue
            results.append((iteration, path))
        return sorted(results, key=lambda item: item[0])

    def _browse_convergence_directory(self) -> None:
        value = filedialog.askdirectory(title="Select classification directory")
        if value:
            self.convergence_directory_var.set(value)
            busy = self._show_particle_busy("Reading in .star-file metadata. Please wait.")
            try:
                self._refresh_convergence_directory_metadata()
            finally:
                self._close_particle_busy(busy)
            self._reset_convergence_plot_display(
                "Directory changed. Click 'Render plot' to update the classification convergence plots."
            )

    def _refresh_convergence_directory_metadata(self) -> None:
        directory = self.convergence_directory_var.get().strip()
        if not directory:
            self.convergence_mode_var.set("-")
            self.convergence_pixel_size_var.set("-")
            self.convergence_iteration_count_var.set("-")
            self.convergence_iteration_span_var.set("-")
            return

        files = self._convergence_iteration_files(directory)
        if not files:
            self.convergence_mode_var.set("unrecognized")
            self.convergence_pixel_size_var.set("-")
            self.convergence_iteration_count_var.set("0")
            self.convergence_iteration_span_var.set("-")
            return

        try:
            first_path = files[0][1]
            self.convergence_mode_var.set(detect_particle_star_mode(first_path))
            self.convergence_pixel_size_var.set(f"{particle_star_pixel_size(first_path):.4f} A")
        except (FileNotFoundError, StarMergeError, ValueError):
            self.convergence_mode_var.set("unrecognized")
            self.convergence_pixel_size_var.set("-")
        self.convergence_iteration_count_var.set(str(len(files)))
        self.convergence_iteration_span_var.set(f"{files[0][0]} - {files[-1][0]}")

    def _reset_convergence_plot_display(self, message: str) -> None:
        self.current_convergence_plot = None
        self.current_convergence_summary = ""
        for child in self.convergence_plot_container.winfo_children():
            child.destroy()
        self.convergence_plot_summary.config(text=message)

    def _on_convergence_rescale_changed(self) -> None:
        if self.current_convergence_plot is not None:
            self._display_convergence_plot(self.current_convergence_plot, self.current_convergence_summary)
        else:
            self._reset_convergence_plot_display(
                "Plot settings changed. Click 'Render plot' to update the classification convergence plots."
            )

    def _on_convergence_plot_canvas_configure(self, _event=None) -> None:
        if not self.convergence_rescale_var.get() or self.current_convergence_plot is None:
            return
        if self._convergence_resize_after_id is not None:
            try:
                self.frame.after_cancel(self._convergence_resize_after_id)
            except tk.TclError:
                pass
        self._convergence_resize_after_id = self.frame.after(120, self._rerender_convergence_rescaled)

    def _rerender_convergence_rescaled(self) -> None:
        self._convergence_resize_after_id = None
        if self.convergence_rescale_var.get() and self.current_convergence_plot is not None:
            self._display_convergence_plot(self.current_convergence_plot, self.current_convergence_summary)

    def _draw_line_plot(
        self,
        parent: ttk.Frame,
        *,
        title_text: str,
        x_values: list[int],
        series: list[tuple[str, list[int]]],
        summary_text: str,
        show_legend: bool,
        rescale_to_window: bool = False,
        available_width: int | None = None,
    ) -> None:
        ttk.Label(parent, text=title_text, style="Heading.TLabel").grid(sticky="w", pady=(0, 6))
        if not x_values or not series:
            ttk.Label(parent, text="No data available for this plot.").grid(sticky="w", pady=(0, 8))
            return

        all_y_values = [value for _label, values in series for value in values]
        high = max(all_y_values) if all_y_values else 1
        if high <= 0:
            high = 1

        default_width = max(900, 110 * max(len(x_values), 2))
        canvas_width = max(560, available_width or default_width) if rescale_to_window else default_width
        canvas_height = 360
        canvas = tk.Canvas(parent, width=canvas_width, height=canvas_height, highlightthickness=1, highlightbackground="#d0d7de")
        canvas.grid(sticky="ew" if rescale_to_window else "w", pady=(0, 8))

        left = 80
        right = canvas_width - 30
        top = 30
        bottom = canvas_height - 70

        def y_from_value(value: float) -> float:
            return bottom - (value / high) * (bottom - top)

        def x_from_index(index: int) -> float:
            if len(x_values) == 1:
                return (left + right) / 2
            return left + ((right - left) / (len(x_values) - 1)) * index

        canvas.create_line(left, top, left, bottom, fill="#2f3b46", width=1)
        canvas.create_line(left, bottom, right, bottom, fill="#2f3b46", width=1)
        canvas.create_text((left + right) / 2, canvas_height - 18, text="iteration", fill="#2f3b46")
        canvas.create_text(18, top, text="Particle number", anchor="nw", fill="#2f3b46")

        ticks = 5
        for step in range(ticks + 1):
            value = high * step / ticks
            y = y_from_value(value)
            canvas.create_line(left - 5, y, left, y, fill="#2f3b46")
            canvas.create_text(left - 10, y, text=f"{value:.0f}", anchor="e", fill="#2f3b46")

        for index, iteration in enumerate(x_values):
            x = x_from_index(index)
            canvas.create_line(x, bottom, x, bottom + 5, fill="#2f3b46")
            canvas.create_text(x, bottom + 20, text=str(iteration), anchor="n", fill="#2f3b46")

        colors = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf", "#8c564b", "#e377c2"]
        for series_index, (_label, values) in enumerate(series):
            if not values:
                continue
            color = colors[series_index % len(colors)]
            points: list[float] = []
            for point_index, value in enumerate(values):
                x = x_from_index(point_index)
                y = y_from_value(value)
                points.extend((x, y))
                canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color, outline=color)
            if len(points) >= 4:
                canvas.create_line(*points, fill=color, width=2)

        ttk.Label(parent, text=summary_text, wraplength=920, justify="left").grid(sticky="w", pady=(0, 8))
        if show_legend:
            legend = ttk.Frame(parent)
            legend.grid(sticky="w", pady=(0, 8))
            for legend_index, (label, _values) in enumerate(series):
                swatch = tk.Label(legend, width=2, background=colors[legend_index % len(colors)])
                swatch.grid(row=legend_index // 4, column=(legend_index % 4) * 2, sticky="w", padx=(0, 4), pady=2)
                ttk.Label(legend, text=f"Class {label}").grid(
                    row=legend_index // 4,
                    column=(legend_index % 4) * 2 + 1,
                    sticky="w",
                    padx=(0, 12),
                    pady=2,
                )

    def _display_convergence_plot(
        self,
        plot: ParticleClassificationConvergencePlot,
        summary: str,
    ) -> None:
        for child in self.convergence_plot_container.winfo_children():
            child.destroy()
        self.convergence_plot_summary.config(text=summary)
        block = ttk.Frame(self.convergence_plot_container)
        block.grid(sticky="ew")
        block.columnconfigure(0, weight=1)
        self._draw_convergence_plots(
            block,
            plot,
            rescale_to_window=self.convergence_rescale_var.get(),
            available_width=max(self.convergence_plot_canvas.winfo_width() - 24, 320),
        )

    def _draw_convergence_plots(
        self,
        parent: ttk.Frame,
        plot: ParticleClassificationConvergencePlot,
        *,
        rescale_to_window: bool = False,
        available_width: int | None = None,
    ) -> None:
        ttk.Label(
            parent,
            text=(
                f"{plot.directory.name} | mode: {plot.mode} | pixel size: {plot.pixel_size:.4f} A | "
                f"iterations: {len(plot.iterations)}"
            ),
            style="Heading.TLabel",
        ).grid(sticky="w", pady=(0, 8))
        iterations = [item.iteration for item in plot.iterations]
        occupancy_series = [
            (class_label, [item.class_counts.get(class_label, 0) for item in plot.iterations])
            for class_label in plot.class_labels
        ]
        convergence_series = [("Changed assignments", [item.changed_count for item in plot.iterations])]
        summary_text = f"Particles={plot.particle_count} | N={plot.dataset_count} | TS={plot.tomogram_count}"

        occupancy_box = ttk.Frame(parent)
        occupancy_box.grid(sticky="ew", pady=(0, 12))
        self._draw_line_plot(
            occupancy_box,
            title_text="Class occupancy",
            x_values=iterations,
            series=occupancy_series,
            summary_text=summary_text,
            show_legend=True,
            rescale_to_window=rescale_to_window,
            available_width=available_width,
        )

        convergence_box = ttk.Frame(parent)
        convergence_box.grid(sticky="ew")
        self._draw_line_plot(
            convergence_box,
            title_text="Convergence",
            x_values=iterations,
            series=convergence_series,
            summary_text=summary_text,
            show_legend=False,
            rescale_to_window=rescale_to_window,
            available_width=available_width,
        )

    def _serialize_abundance_plot(self, plot: ParticleAbundancePlot) -> dict[str, object]:
        def serialize_condition(condition):
            return asdict(condition) if condition is not None else None

        return {
            "star_path": str(plot.star_path),
            "mode": plot.mode,
            "measure": plot.measure,
            "compare_samples": plot.compare_samples,
            "conditions": [serialize_condition(condition) for condition in plot.conditions],
            "all_condition": serialize_condition(plot.all_condition),
        }

    def _deserialize_abundance_plot(self, payload: dict[str, object]) -> ParticleAbundancePlot:
        def read_condition(raw):
            if not isinstance(raw, dict):
                return None
            return type("Cond", (), raw)

        from cryoet_organizer.star_merge import AbundanceCondition

        conditions = [AbundanceCondition(**item) for item in payload.get("conditions", []) if isinstance(item, dict)]
        all_raw = payload.get("all_condition")
        all_condition = AbundanceCondition(**all_raw) if isinstance(all_raw, dict) else None
        return ParticleAbundancePlot(
            star_path=Path(str(payload.get("star_path", ""))),
            mode=str(payload.get("mode", "")),
            measure=str(payload.get("measure", "")),
            compare_samples=bool(payload.get("compare_samples", False)),
            conditions=conditions,
            all_condition=all_condition,
        )

    def _serialize_convergence_plot(self, plot: ParticleClassificationConvergencePlot) -> dict[str, object]:
        return {
            "directory": str(plot.directory),
            "mode": plot.mode,
            "pixel_size": plot.pixel_size,
            "iterations": [asdict(item) for item in plot.iterations],
            "class_labels": list(plot.class_labels),
            "particle_count": plot.particle_count,
            "dataset_count": plot.dataset_count,
            "tomogram_count": plot.tomogram_count,
        }

    def _deserialize_convergence_plot(self, payload: dict[str, object]) -> ParticleClassificationConvergencePlot:
        iterations = [
            ClassificationIteration(**item)
            for item in payload.get("iterations", [])
            if isinstance(item, dict)
        ]
        return ParticleClassificationConvergencePlot(
            directory=Path(str(payload.get("directory", ""))),
            mode=str(payload.get("mode", "")),
            pixel_size=float(payload.get("pixel_size", 0.0) or 0.0),
            iterations=iterations,
            class_labels=[str(item) for item in payload.get("class_labels", [])],
            particle_count=int(payload.get("particle_count", 0) or 0),
            dataset_count=int(payload.get("dataset_count", 0) or 0),
            tomogram_count=int(payload.get("tomogram_count", 0) or 0),
        )

    def _save_particle_plots_enabled(self) -> bool:
        return project_preference_enabled(self.app.project, "save_particle_plots", default=False)

    def _record_project_scope_history_entry(
        self,
        job_name: str,
        *,
        parameters: dict[str, str],
        command: str = "",
        artifacts: dict[str, object] | None = None,
    ) -> None:
        datasets = list(self.app.project.datasets)
        if not datasets:
            return
        involved = sorted({name for name in parameters.get("datasets", "").split(", ") if name})
        dataset_label = "Multiple datasets" if len(involved) > 1 else (involved[0] if involved else datasets[0].dataset_name)
        entry = create_history_entry(
            action="ran",
            group="Particles",
            job_name=job_name,
            command=command,
            processing_tab="Processing: Particle jobs",
            dataset_name=dataset_label,
            parameters=parameters,
        )
        if artifacts:
            entry.artifacts = artifacts
        datasets[0].job_history.append(entry)
        self.app._modified = True
        self.app._update_title()
        self._refresh_history()

    def _render_abundance_plots(self, _event=None) -> None:
        if not self.abundance_star_paths:
            self._reset_abundance_plot_display("Add one or more particle STAR files to render abundance plots.")
            return

        dataset_to_sample = self._abundance_dataset_to_sample()
        compare_samples = self.abundance_compare_samples_var.get()
        measure = self._abundance_measure_key()
        star_paths = list(self.abundance_star_paths)
        cancel_event = threading.Event()
        busy = self._show_particle_busy(
            "CryoPal is calculating the particle abundance plots. Please wait.",
            on_abort=cancel_event.set,
        )

        def worker() -> None:
            plots: list[ParticleAbundancePlot] = []
            errors: list[str] = []
            for path in star_paths:
                try:
                    plots.append(
                        particle_abundance_plot_data(
                            input_star_path=path,
                            dataset_to_sample=dataset_to_sample,
                            compare_samples=compare_samples,
                            measure=measure,
                            cancel_event=cancel_event,
                        )
                    )
                except OperationAborted:
                    self.app.root.after(
                        0,
                        lambda: (
                            self._close_particle_busy(busy),
                            self.app.status_var.set("Particle abundance plotting aborted"),
                        ),
                    )
                    return
                except Exception as exc:
                    errors.append(f"{Path(path).name}: {exc}")

            def finish() -> None:
                self._close_particle_busy(busy)
                if errors and not plots:
                    self._reset_abundance_plot_display("Could not render plots:\n" + "\n".join(errors))
                    return

                summary = (
                    f"Showing {len(plots)} plot(s) | "
                    f"grouped by {'sample' if compare_samples else 'dataset'} | "
                    f"mode: {measure}"
                )
                if errors:
                    summary += "\nSkipped:\n" + "\n".join(errors)
                self.current_abundance_plots = plots
                self.current_abundance_summary = summary
                self._display_abundance_plots(plots, summary)

                artifacts = None
                if self._save_particle_plots_enabled():
                    artifacts = {
                        "kind": "particle_abundance",
                        "plots": [self._serialize_abundance_plot(plot) for plot in plots],
                        "summary": summary,
                        "rescale_to_window": self.abundance_rescale_var.get(),
                    }
                self._record_project_scope_history_entry(
                    "plot_particle_abundance",
                    parameters={
                        "input_stars": ", ".join(star_paths),
                        "compare_samples": "true" if compare_samples else "false",
                        "plot_mode": self.abundance_measure_var.get(),
                        "datasets": ", ".join(self._dataset_options()),
                    },
                    command=summary,
                    artifacts=artifacts,
                )

            self.app.root.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _render_convergence_plots(self) -> None:
        directory = self.convergence_directory_var.get().strip()
        if not directory:
            messagebox.showinfo("Missing directory", "Please select a classification directory first.")
            return
        cancel_event = threading.Event()
        busy = self._show_particle_busy(
            "CryoPal is calculating the classification convergence plots. Please wait.",
            on_abort=cancel_event.set,
        )
        dataset_names = self._dataset_options()

        def worker() -> None:
            try:
                plot = particle_classification_convergence_data(directory, dataset_names, cancel_event=cancel_event)
            except OperationAborted:
                self.app.root.after(
                    0,
                    lambda: (
                        self._close_particle_busy(busy),
                        self.app.status_var.set("Classification convergence plotting aborted"),
                    ),
                )
                return
            except Exception as exc:
                self.app.root.after(
                    0,
                    lambda: (
                        self._close_particle_busy(busy),
                        self._reset_convergence_plot_display(f"Could not render plots:\n{exc}"),
                    ),
                )
                return

            def finish() -> None:
                self._close_particle_busy(busy)
                summary = (
                    f"Showing classification convergence for {plot.directory.name} | "
                    f"mode: {plot.mode} | iterations: {len(plot.iterations)}"
                )
                self.current_convergence_plot = plot
                self.current_convergence_summary = summary
                self.convergence_mode_var.set(plot.mode)
                self.convergence_pixel_size_var.set(f"{plot.pixel_size:.4f} A")
                self.convergence_iteration_count_var.set(str(len(plot.iterations)))
                if plot.iterations:
                    self.convergence_iteration_span_var.set(
                        f"{plot.iterations[0].iteration} - {plot.iterations[-1].iteration}"
                    )
                self._display_convergence_plot(plot, summary)
                artifacts = None
                if self._save_particle_plots_enabled():
                    artifacts = {
                        "kind": "particle_classification_convergence",
                        "plot": self._serialize_convergence_plot(plot),
                        "summary": summary,
                        "rescale_to_window": self.convergence_rescale_var.get(),
                    }
                self._record_project_scope_history_entry(
                    "plot_classification_convergence",
                    parameters={
                        "input_directory": directory,
                        "datasets": ", ".join(dataset_names),
                        "mode": plot.mode,
                        "iteration_count": str(len(plot.iterations)),
                    },
                    command=summary,
                    artifacts=artifacts,
                )

            self.app.root.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_selected_intersect_star_metadata(self) -> None:
        if not self.selected_intersect_star_path:
            self.intersect_mode_var.set("-")
            self.intersect_pixel_size = 0.0
            self.intersect_pixel_size_var.set("-")
            return
        self.intersect_mode_var.set(detect_particle_star_mode(self.selected_intersect_star_path))
        self.intersect_pixel_size = particle_star_pixel_size(self.selected_intersect_star_path)
        self.intersect_pixel_size_var.set(f"{self.intersect_pixel_size:.4f} A")
        self._apply_intersect_canonical_distance()

    def _on_intersect_identification_changed(self, _event=None) -> None:
        enabled = self.intersect_identification_mode_var.get() == "By distance"
        self.intersect_radius_px_entry.configure(state="normal" if enabled else "disabled")
        self.intersect_radius_ang_entry.configure(state="normal" if enabled else "disabled")
        self._update_intersect_preview()

    def _on_intersect_radius_px_changed(self) -> None:
        if self.intersect_updating:
            return
        self._sync_intersect_fields(source="px")
        self._update_intersect_preview()

    def _on_intersect_radius_ang_changed(self) -> None:
        if self.intersect_updating:
            return
        self._sync_intersect_fields(source="ang")
        self._update_intersect_preview()

    def _sync_intersect_fields(self, source: str) -> None:
        if self.intersect_pixel_size <= 0:
            return
        try:
            self.intersect_updating = True
            if source == "px":
                radius_px = float(self.intersect_radius_px_var.get().strip())
                self.intersect_radius_ang_canonical = radius_px * self.intersect_pixel_size
                self.intersect_radius_ang_var.set(f"{self.intersect_radius_ang_canonical:.4f}")
            else:
                radius_ang = float(self.intersect_radius_ang_var.get().strip())
                self.intersect_radius_ang_canonical = radius_ang
                self.intersect_radius_px_var.set(f"{radius_ang / self.intersect_pixel_size:.4f}")
        except ValueError:
            pass
        finally:
            self.intersect_updating = False

    def _apply_intersect_canonical_distance(self) -> None:
        if self.intersect_pixel_size <= 0 or self.intersect_radius_ang_canonical is None:
            return
        try:
            self.intersect_updating = True
            self.intersect_radius_ang_var.set(f"{self.intersect_radius_ang_canonical:.4f}")
            self.intersect_radius_px_var.set(f"{self.intersect_radius_ang_canonical / self.intersect_pixel_size:.4f}")
        finally:
            self.intersect_updating = False

    def _intersect_preview_text(self) -> str:
        if not self.intersect_star_paths:
            return ""
        dataset_list = ", ".join(self.selected_intersect_datasets) or "(no datasets selected)"
        star_list = ", ".join(Path(path).name for path in self.intersect_star_paths)
        output_modes: list[str] = []
        if self.intersect_common_var.get():
            output_modes.append("common")
        if self.intersect_unique_var.get():
            output_modes.append("unique")
        lines = [
            "Intersect .star-files",
            f"Input STAR files: {star_list}",
            f"Selected STAR: {Path(self.selected_intersect_star_path).name if self.selected_intersect_star_path else '-'}",
            f"Detected mode: {self.intersect_mode_var.get()}",
            f"Datasets: {dataset_list}",
            f"Identification mode: {self.intersect_identification_mode_var.get()}",
        ]
        if self.intersect_identification_mode_var.get() == "By distance":
            lines.extend(
                [
                    f"Distance (px): {self.intersect_radius_px_var.get().strip() or '-'}",
                    f"Distance (A): {self.intersect_radius_ang_var.get().strip() or '-'}",
                ]
            )
        lines.extend(
            [
                f"Output name: {self.intersect_output_name_var.get().strip() or '(no output name)'}",
                f"Output modes: {', '.join(output_modes) if output_modes else '(no output mode selected)'}",
            ]
        )
        return "\n".join(lines)

    def _set_intersect_log(self, text: str) -> None:
        self.intersect_log_text.delete("1.0", "end")
        self.intersect_log_text.insert("1.0", text)

    def _append_intersect_log(self, line: str) -> None:
        current = self.intersect_log_text.get("1.0", "end").strip()
        updated = f"{current}\n{line}".strip() if current else line
        self._set_intersect_log(updated)

    def _update_intersect_preview(self) -> None:
        self._set_intersect_log(self._intersect_preview_text())

    def _intersect_output_parameters(self) -> dict[str, str]:
        values = {
            "input_stars": ", ".join(self.intersect_star_paths),
            "output_star": self.intersect_output_name_var.get().strip(),
            "identification_mode": self.intersect_identification_mode_var.get(),
        }
        if self.intersect_identification_mode_var.get() == "By distance":
            values["radius_px"] = self.intersect_radius_px_var.get().strip()
            values["radius_angstrom"] = self.intersect_radius_ang_var.get().strip()
        if self.intersect_common_var.get():
            values["write_common"] = "true"
        if self.intersect_unique_var.get():
            values["write_unique"] = "true"
        return {key: value for key, value in values.items() if value}

    def _copy_intersect_preview(self) -> None:
        preview = self._intersect_preview_text()
        if not preview:
            return
        self.frame.clipboard_clear()
        self.frame.clipboard_append(preview)
        for dataset_name in self.selected_intersect_datasets:
            dataset = self._dataset_map().get(dataset_name)
            if dataset is None:
                continue
            dataset.job_history.append(
                JobHistoryEntry(
                    timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    action="copied",
                    group="Particles",
                    job_name="intersect_star_files",
                    command=preview,
                    processing_tab="Processing: Particle jobs",
                    dataset_name=dataset.dataset_name,
                    parameters=self._intersect_output_parameters(),
                )
            )
        self.app.on_project_changed("particles")
        self.app.status_var.set("Intersect log copied to clipboard")

    def _run_intersect(self) -> None:
        if len(self.intersect_star_paths) < 2:
            messagebox.showinfo("Missing STAR files", "Please add at least two STAR files.")
            return
        if not self.selected_intersect_datasets:
            messagebox.showinfo("No datasets selected", "Please add at least one dataset.")
            return
        identification_mode = self.intersect_identification_mode_var.get()
        radius_px = 0.0
        if identification_mode == "By distance":
            try:
                radius_ang = float(self.intersect_radius_ang_var.get().strip())
            except ValueError:
                messagebox.showinfo("Invalid distance", "Please provide a numerical distance in A or px.")
                return
        else:
            radius_ang = 0.0

        preview = self._intersect_preview_text()
        cancel_event = threading.Event()
        busy = self._show_particle_busy(
            "CryoPal is calculating the STAR intersection outputs. Please wait.",
            on_abort=cancel_event.set,
        )

        def worker() -> None:
            try:
                self.app.root.after(0, lambda: self._set_intersect_log(""))
                outputs = intersect_particle_stars(
                    input_star_paths=self.intersect_star_paths,
                    dataset_names=self.selected_intersect_datasets,
                    output_name=self.intersect_output_name_var.get().strip(),
                    write_common=self.intersect_common_var.get(),
                    write_unique=self.intersect_unique_var.get(),
                    identification_mode="distance" if identification_mode == "By distance" else "name",
                    radius_ang=radius_ang,
                    log_callback=lambda line: self.app.root.after(
                        0, lambda message=line: self._append_intersect_log(message)
                    ),
                    cancel_event=cancel_event,
                )
            except OperationAborted:
                self.app.root.after(
                    0,
                    lambda: (
                        self._close_particle_busy(busy),
                        self._set_intersect_log(preview),
                        self.app.status_var.set("STAR intersection aborted"),
                    ),
                )
                return
            except Exception as exc:
                self.app.root.after(
                    0,
                    lambda: (
                        self._close_particle_busy(busy),
                        self._append_intersect_log(f"Error: {exc}"),
                        self.app.status_var.set(f"Intersect failed: {exc}"),
                    ),
                )
                return

            def update_status() -> None:
                self._close_particle_busy(busy)
                self.app.clear_abort_request()
                for dataset_name in self.selected_intersect_datasets:
                    dataset = self._dataset_map().get(dataset_name)
                    if dataset is None:
                        continue
                    dataset.job_history.append(
                        JobHistoryEntry(
                            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            action="ran",
                            group="Particles",
                            job_name="intersect_star_files",
                            command=preview,
                            processing_tab="Processing: Particle jobs",
                            dataset_name=dataset.dataset_name,
                            parameters=self._intersect_output_parameters(),
                        )
                    )
                self.app.on_project_changed("particles")
                written: list[str] = []
                if outputs.common_path is not None:
                    written.append(outputs.common_path.name)
                written.extend(path.name for path in outputs.unique_paths)
                self.app.status_var.set("Intersect finished: " + ", ".join(written or ["no outputs"]))

            self.app.root.after(0, update_status)

        threading.Thread(target=worker, daemon=True).start()
        self.app.status_var.set("Started STAR intersection")

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

    def _record_history_entry(
        self,
        dataset: DatasetRecord,
        action: str,
        command: str,
        slurm_result: SlurmSubmissionResult | None = None,
    ) -> JobHistoryEntry:
        effective_environment = self._effective_particle_environment()
        values = {}
        for flag in self.current_job.flags:
            variable = self.parameter_vars.get(flag.name)
            if variable is None:
                continue
            if flag.widget == "bool":
                if variable.get():
                    values[flag.name] = "true"
            else:
                value = str(variable.get()).strip()
                if value:
                    values[flag.name] = value
        entry = create_history_entry(
            action=action,
            group="Particles",
            job_name=self.current_job.command,
            command=command,
            processing_tab="Processing: Particle jobs",
            dataset_name=dataset.dataset_name,
            execution_mode="slurm" if self.execution_mode_var.get() == "Submit to Slurm" else "local",
            slurm_profile=self.slurm_profile_var.get().strip(),
            environment_title=effective_environment if self.execution_mode_var.get() == "Run locally" else "",
            parameters=values,
        )
        if self.execution_mode_var.get() == "Run locally" and effective_environment:
            entry.parameters["execution_environment"] = effective_environment
        entry.parameters.update(self._current_slurm_overrides())
        if slurm_result is not None:
            entry.slurm_job_id = slurm_result.job_id
            entry.slurm_script_path = slurm_result.script_path
        dataset.job_history.append(entry)
        return entry

    def _copy_commands(self) -> None:
        preview = self._current_preview_text()
        if not preview:
            return
        self.frame.clipboard_clear()
        self.frame.clipboard_append(preview)
        for dataset, command in self._commands():
            self._record_history_entry(dataset, "copied", command)
        self.app.on_project_changed("particles")
        self.app.status_var.set("Particle export commands copied to clipboard")

    def _run_commands(self) -> None:
        commands = self._commands()
        if not commands:
            messagebox.showinfo("No datasets selected", "Please add at least one dataset.")
            return
        dataset_problems = self._validate_export_datasets(commands)
        if dataset_problems:
            messagebox.showerror(
                "Export particles",
                "The selected datasets are not ready for particle export:\n\n" + "\n".join(dataset_problems),
            )
            return
        use_slurm = self.execution_mode_var.get() == "Submit to Slurm"
        profile_name = self.slurm_profile_var.get().strip()
        if use_slurm and not profile_name and not self.app.is_debug_mode_enabled():
            messagebox.showerror("Slurm profile missing", "Please select a Slurm profile first.")
            return
        output_paths = [
            Path(values["--output_star"])
            for dataset, _command in commands
            for values in [self._dataset_command_values(dataset)]
            if values.get("--output_star")
        ]
        merged_output_path = self._merged_output_path()
        is_2d_export = self._is_2d_export()
        effective_environment = self._effective_particle_environment()
        activation_command = self.app.resolve_environment_activation(effective_environment)

        history_entries = [
            self._record_history_entry(dataset, "submitted" if use_slurm else "ran", command)
            for dataset, command in commands
        ]
        if not use_slurm:
            self.app.mark_history_entries_running([entry.entry_id for entry in history_entries])
        self.app.on_project_changed("particles")

        items = [
            {
                "command": command,
                "dataset_name": dataset.dataset_name,
                "job_name": self.current_job.command,
                "cwd": dataset.processing_folder or "",
                "error_label": dataset.dataset_name,
                "dataset": dataset,
                "activation_command": activation_command,
            }
            for dataset, command in commands
        ]
        execute_command_sequence(
            self.app,
            items,
            use_slurm=use_slurm,
            profile_name=profile_name,
            overrides=self._slurm_override_payload(self._current_slurm_overrides()),
            on_submitted=lambda item, result: self._record_slurm_submission_metadata(
                item["dataset"],
                str(item.get("command", "")),
                result,
                profile_name,
            ),
            on_completed=None,
            on_finished=lambda count, failures: self._finish_export_run(
                count,
                failures,
                use_slurm,
                output_paths,
                merged_output_path,
                is_2d_export,
                [entry.entry_id for entry in history_entries],
            ),
        )
        self.app.status_var.set(
            "Submitting particle export for selected datasets" if use_slurm else "Started particle export for selected datasets"
        )

    def _finish_export_run(
        self,
        command_count: int,
        failures: list[str],
        use_slurm: bool,
        output_paths: list[Path],
        merged_output_path: Path | None,
        is_2d_export: bool,
        running_entry_ids: list[str],
    ) -> None:
        self.app.clear_history_entries_running(running_entry_ids)
        self.app.clear_abort_request()
        merge_result: MergeResult | None = None
        if (
            not self.app.is_debug_mode_enabled()
            and not use_slurm
            and not failures
            and merged_output_path is not None
            and output_paths
        ):
            try:
                merge_result = merge_particle_exports(
                    output_paths=output_paths,
                    merged_output_path=merged_output_path,
                    is_2d=is_2d_export,
                )
            except (FileNotFoundError, StarMergeError) as exc:
                failures.append(str(exc))
        if failures:
            self.app.status_var.set("Particle export stopped with failure: " + "; ".join(failures))
            return
        merged_suffix = ""
        if merge_result is not None:
            merged_suffix = f"; merged output: {merge_result.merged_particles_path.name}"
        self.app.status_var.set(
            ("Particle export submitted for all selected datasets" if use_slurm else "Particle export finished for all selected datasets")
            + merged_suffix
        )

    def _record_slurm_submission_metadata(
        self,
        dataset: DatasetRecord,
        command: str,
        result: SlurmSubmissionResult,
        profile_name: str,
    ) -> None:
        for entry in reversed(dataset.job_history):
            if entry.group == "Particles" and entry.command == command:
                entry.execution_mode = "slurm"
                entry.slurm_profile = profile_name
                entry.slurm_job_id = result.job_id
                entry.slurm_script_path = result.script_path
                break
        self.app.on_project_changed("particles")

    def _entry_matches_dataset_filter(self, entry: JobHistoryEntry, dataset_filter: str) -> bool:
        if dataset_filter in ("", "All datasets"):
            return True
        if entry.dataset_name == dataset_filter:
            return True
        involved = [name for name in entry.parameters.get("datasets", "").split(", ") if name]
        return dataset_filter in involved

    def _refresh_history(self, _event=None) -> None:
        self.history_table.delete(*self.history_table.get_children())
        dataset_filter = self.history_dataset_var.get()
        entries = list(enumerate(self._history_entries()))
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
            if not self._entry_matches_dataset_filter(entry, dataset_filter):
                continue
            self.history_table.insert(
                "",
                "end",
                iid=entry.entry_id,
                values=(
                    entry.job_name,
                    entry.dataset_name or "-",
                    display_history_timestamp(entry),
                    entry.action,
                ),
                tags=(self.app.history_entry_state_tag(entry),),
            )

    def _history_sort_value(self, entry: JobHistoryEntry, column: str):
        if column == "job_name":
            return entry.job_name.casefold()
        if column == "dataset_name":
            return (entry.dataset_name or "").casefold()
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
        window = tk.Toplevel(self.frame)
        window.title("Job details")
        window.geometry("1080x760")
        window.minsize(820, 560)
        window.transient(self.frame.winfo_toplevel())
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)

        container = ttk.Frame(window, padding=12)
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)
        container.rowconfigure(1, weight=0)
        container.rowconfigure(2, weight=0)

        has_saved_plots = entry.artifacts.get("kind") in {
            "particle_abundance",
            "particle_classification_convergence",
        }

        if has_saved_plots:
            paned = ttk.Panedwindow(container, orient="vertical")
            paned.grid(row=0, column=0, columnspan=2, sticky="nsew")

            top_panel = ttk.Frame(paned)
            top_panel.columnconfigure(0, weight=1)
            top_panel.rowconfigure(0, weight=1)
            bottom_panel = ttk.Frame(paned)
            bottom_panel.columnconfigure(0, weight=1)
            bottom_panel.rowconfigure(0, weight=1)
            paned.add(top_panel, weight=1)
            paned.add(bottom_panel, weight=1)
            tree_parent = top_panel
            plots_parent = bottom_panel
        else:
            tree_parent = container
            plots_parent = None

        tree = ttk.Treeview(tree_parent, columns=("field", "value"), show="tree headings")
        tree.heading("#0", text="Section")
        tree.heading("field", text="Field")
        tree.heading("value", text="Value")
        tree.column("#0", width=180, anchor="w")
        tree.column("field", width=240, anchor="w")
        tree.column("value", width=560, anchor="w")
        tree.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(tree_parent, orient="vertical", command=tree.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll = ttk.Scrollbar(tree_parent, orient="horizontal", command=tree.xview)
        xscroll.grid(row=1, column=0, sticky="ew")
        tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        sections = [
            (
                "Overview",
                [
                    ("Job", entry.job_name),
                    ("Dataset", entry.dataset_name or "-"),
                    ("Group", entry.group),
                    ("Action", entry.action),
                    ("Timestamp", display_history_timestamp(entry)),
                    ("Execution mode", entry.execution_mode or "-"),
                    ("Slurm profile", entry.slurm_profile or "-"),
                    ("Slurm job ID", entry.slurm_job_id or "-"),
                    ("Slurm script", entry.slurm_script_path or "-"),
                ],
            ),
        ]
        input_output_rows = [
            (key, value)
            for key, value in entry.parameters.items()
            if any(token in key.lower() for token in ("input", "output", "path", "directory", "star"))
        ]
        other_rows = [
            (key, value)
            for key, value in entry.parameters.items()
            if (key, value) not in input_output_rows
        ]
        if input_output_rows:
            sections.append(("Input / output", input_output_rows))
        sections.append(("Parameters", other_rows or [("Parameters", "-")]))

        for section_title, rows in sections:
            section_id = tree.insert("", "end", text=section_title, open=True, values=("", ""))
            for field, value in rows:
                tree.insert(section_id, "end", text="", values=(field, value))

        next_row = 1
        if entry.command:
            command_box = ttk.LabelFrame(container, text="Command", padding=12)
            command_box.grid(row=next_row, column=0, columnspan=2, sticky="nsew", pady=(12, 0))
            command_box.columnconfigure(0, weight=1)
            command_text = tk.Text(command_box, height=6, wrap="word", font="TkDefaultFont")
            command_text.grid(row=0, column=0, sticky="nsew")
            command_text.insert("1.0", entry.command)
            command_scroll = ttk.Scrollbar(command_box, orient="vertical", command=command_text.yview)
            command_scroll.grid(row=0, column=1, sticky="ns")
            command_text.configure(yscrollcommand=command_scroll.set)
            next_row += 1

        if entry.artifacts.get("kind") == "particle_abundance" and plots_parent is not None:
            plots_box = ttk.LabelFrame(plots_parent, text="Saved plots", padding=12)
            plots_box.grid(row=0, column=0, sticky="nsew")
            plots_box.columnconfigure(0, weight=1)
            plots_box.rowconfigure(0, weight=1)
            plots_canvas = tk.Canvas(plots_box, highlightthickness=0, height=320)
            plots_canvas.grid(row=0, column=0, sticky="nsew")
            plots_yscroll = ttk.Scrollbar(plots_box, orient="vertical", command=plots_canvas.yview)
            plots_yscroll.grid(row=0, column=1, sticky="ns")
            plots_xscroll = ttk.Scrollbar(plots_box, orient="horizontal", command=plots_canvas.xview)
            plots_xscroll.grid(row=1, column=0, sticky="ew")
            plots_canvas.configure(yscrollcommand=plots_yscroll.set, xscrollcommand=plots_xscroll.set)
            plots_inner = ttk.Frame(plots_canvas)
            plots_inner.columnconfigure(0, weight=1)
            plots_window = plots_canvas.create_window((0, 0), window=plots_inner, anchor="nw")
            bind_scrollable_canvas(plots_canvas, plots_window, plots_inner, allow_horizontal=True)
            rescale = bool(entry.artifacts.get("rescale_to_window", False))
            for plot_payload in entry.artifacts.get("plots", []):
                if not isinstance(plot_payload, dict):
                    continue
                block = ttk.Frame(plots_inner)
                block.grid(sticky="ew", pady=(0, 14))
                block.columnconfigure(0, weight=1)
                self._draw_abundance_plot(
                    block,
                    self._deserialize_abundance_plot(plot_payload),
                    rescale_to_window=rescale,
                    available_width=840,
                )
        elif entry.artifacts.get("kind") == "particle_classification_convergence" and plots_parent is not None:
            plot_payload = entry.artifacts.get("plot")
            if isinstance(plot_payload, dict):
                plots_box = ttk.LabelFrame(plots_parent, text="Saved plots", padding=12)
                plots_box.grid(row=0, column=0, sticky="nsew")
                plots_box.columnconfigure(0, weight=1)
                plots_box.rowconfigure(0, weight=1)
                plots_canvas = tk.Canvas(plots_box, highlightthickness=0, height=320)
                plots_canvas.grid(row=0, column=0, sticky="nsew")
                plots_yscroll = ttk.Scrollbar(plots_box, orient="vertical", command=plots_canvas.yview)
                plots_yscroll.grid(row=0, column=1, sticky="ns")
                plots_xscroll = ttk.Scrollbar(plots_box, orient="horizontal", command=plots_canvas.xview)
                plots_xscroll.grid(row=1, column=0, sticky="ew")
                plots_canvas.configure(yscrollcommand=plots_yscroll.set, xscrollcommand=plots_xscroll.set)
                plots_inner = ttk.Frame(plots_canvas)
                plots_inner.columnconfigure(0, weight=1)
                plots_window = plots_canvas.create_window((0, 0), window=plots_inner, anchor="nw")
                bind_scrollable_canvas(plots_canvas, plots_window, plots_inner, allow_horizontal=True)
                rescale = bool(entry.artifacts.get("rescale_to_window", False))
                block = ttk.Frame(plots_inner)
                block.grid(sticky="ew")
                block.columnconfigure(0, weight=1)
                self._draw_convergence_plots(
                    block,
                    self._deserialize_convergence_plot(plot_payload),
                    rescale_to_window=rescale,
                    available_width=840,
                )

        footer = ttk.Frame(container)
        footer.grid(row=next_row, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(footer, text="Close", command=window.destroy).grid(row=0, column=0)

    def _remove_selected_history_entry(self) -> None:
        selection = self.history_table.selection()
        if not selection:
            messagebox.showinfo("Remove job", "Please select a particle job history entry first.")
            return
        selected = self.history_entry_refs.get(selection[0])
        if selected is None:
            return
        _index, entry = selected
        for dataset in self.app.project.datasets:
            match_index = next(
                (
                    index
                    for index, existing in enumerate(dataset.job_history)
                    if existing is entry or existing.entry_id == entry.entry_id
                ),
                None,
            )
            if match_index is not None:
                del dataset.job_history[match_index]
                break
        self.app.on_project_changed("particles")

    def on_project_loaded(self, project: ProjectData) -> None:
        project_id = id(project)
        if self.bound_project_id != project_id:
            self.bound_project_id = project_id
            self.selected_export_datasets = []
            self.selected_distance_datasets = []
            self.selected_intersect_datasets = []
            self.abundance_star_paths = []
            self.intersect_star_paths = []
            self.merge_split_star_paths = []
            self.dataset_picker_var.set("")
            self.distance_dataset_picker_var.set("")
            self.intersect_dataset_picker_var.set("")
            self.history_dataset_var.set("All datasets")
            self.job_type_var.set("Select job type")
            self.distance_input_star_var.set("")
            self.distance_radius_px_var.set("")
            self.distance_radius_ang_var.set("")
            self.intersect_radius_px_var.set("")
            self.intersect_radius_ang_var.set("")
            self.intersect_output_name_var.set("Output.star")
            self.intersect_identification_mode_var.set("By distance")
            self.intersect_radius_ang_canonical = None
            self.selected_abundance_star_path = ""
            self.selected_intersect_star_path = ""
            self.selected_merge_split_star_path = ""
            self.convergence_mode_var.set("-")
            self.convergence_pixel_size_var.set("-")
            self.convergence_iteration_count_var.set("-")
            self.convergence_iteration_span_var.set("-")
            self._apply_particle_custom_defaults()
            self._build_parameter_form()
            self.environment_var.set(self._particle_environment_default())

        dataset_options = self._dataset_options()
        self.dataset_picker.configure(values=dataset_options)
        self.distance_dataset_picker.configure(values=dataset_options)
        self.intersect_dataset_picker.configure(values=dataset_options)
        history_options = ["All datasets"] + dataset_options if dataset_options else ["All datasets"]
        self.history_dataset_combo.configure(values=history_options)
        if self.history_dataset_var.get() not in history_options:
            self.history_dataset_var.set("All datasets")
        self.selected_export_datasets = [name for name in self.selected_export_datasets if name in dataset_options]
        self.selected_distance_datasets = [
            name for name in self.selected_distance_datasets if name in dataset_options
        ]
        self.selected_intersect_datasets = [
            name for name in self.selected_intersect_datasets if name in dataset_options
        ]
        self._refresh_selected_dataset_list()
        self._refresh_distance_selected_dataset_list()
        self._refresh_intersect_selected_dataset_list()
        self._refresh_slurm_profiles()
        self._refresh_abundance_star_list(show_busy=False)
        self._refresh_intersect_star_list(show_busy=False)
        self._refresh_merge_split_star_list()
        self._refresh_convergence_directory_metadata()
        self._on_intersect_identification_changed()
        self._on_job_type_changed()
        self._refresh_history()
