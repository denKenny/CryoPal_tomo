from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from cryoet_organizer.dialogs import bind_scrollable_canvas, show_detail_dialog
from cryoet_organizer.environments import environment_titles
from cryoet_organizer.file_resolver import resolve_dataset_file
from cryoet_organizer.job_execution import (
    build_slurm_override_metadata,
    display_history_timestamp,
    execute_command_sequence,
    is_scheduled_history_entry,
    slurm_override_payload,
)
from cryoet_organizer.job_defaults import resolve_job_default
from cryoet_organizer.project import (
    DatasetRecord,
    JobHistoryEntry,
    ProjectData,
    best_matching_paths_for_ts,
    best_matching_path_for_ts,
    dataset_ts_names,
    find_dataset_for_ts_name,
)
from cryoet_organizer.scheduled_slurm_dialog import CollectiveSlurmSubmissionDialog, ask_scheduled_slurm_mode
from cryoet_organizer.slurm import SlurmSubmissionResult, find_slurm_profile, render_sbatch_script, wait_for_slurm_job
from cryoet_organizer.slurm_override_ui import SlurmOverrideUI
from cryoet_organizer.tabs.base import SidebarTab
from cryoet_organizer.tomograms_catalog import tomogram_job_titles, tomogram_jobs_by_title


class TomogramsTab(SidebarTab):
    tab_id = "tomograms"
    title = "Processing: TS jobs"
    refresh_domains = ("tomograms", "datasets", "file_registry", "defaults", "custom", "ts_selection", "environments")

    def build(self) -> None:
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(0, weight=1)
        self.dataset_var = tk.StringVar()
        self.ts_var = tk.StringVar()
        self.job_type_var = tk.StringVar(value="Select job type")
        self.history_dataset_var = tk.StringVar(value="All datasets")
        self.selected_entries: list[dict[str, str]] = []
        self.history_entry_refs: dict[str, tuple[DatasetRecord, JobHistoryEntry]] = {}
        self.history_sort_column = "timestamp"
        self.history_sort_descending = True
        self.bound_project_id: int | None = None
        self.job_catalog = tomogram_jobs_by_title()
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
        self.slurm_profile_combos: list[ttk.Combobox] = []
        self.environment_combos: list[ttk.Combobox] = []
        self.execution_target_labels: list[ttk.Label] = []
        self.slurm_override_frames: list[ttk.Frame] = []
        self.advanced_visible_vars: dict[str, tk.BooleanVar] = {}

        self.cryolithe_model_dir_var = tk.StringVar()
        self.cryolithe_save_dir_var = tk.StringVar()
        self.cryolithe_device_var = tk.StringVar()
        self.cryolithe_n3_var = tk.StringVar()
        self.cryolithe_batch_size_var = tk.StringVar()

        self.pytom_template_var = tk.StringVar()
        self.pytom_destination_var = tk.StringVar()
        self.pytom_mask_var = tk.StringVar()
        self.pytom_manual_input_var = tk.BooleanVar(value=False)
        self.pytom_manual_dir_var = tk.StringVar()
        self.pytom_non_spherical_mask_var = tk.BooleanVar(value=False)
        self.pytom_particle_diameter_var = tk.StringVar()
        self.pytom_angular_search_var = tk.StringVar()
        self.pytom_z_axis_symmetry_var = tk.StringVar()
        self.pytom_volume_split_var = tk.StringVar()
        self.pytom_search_x_var = tk.StringVar()
        self.pytom_search_y_var = tk.StringVar()
        self.pytom_search_z_var = tk.StringVar()
        self.pytom_tomogram_mask_var = tk.StringVar()
        self.pytom_found_masks_var = tk.StringVar(value="0/0 tomogram masks found.")
        self.pytom_per_tilt_weighting_var = tk.BooleanVar(value=False)
        self.pytom_voxel_size_var = tk.StringVar()
        self.pytom_low_pass_var = tk.StringVar()
        self.pytom_high_pass_var = tk.StringVar()
        self.pytom_dose_accumulation_var = tk.StringVar()
        self.pytom_defocus_var = tk.StringVar()
        self.pytom_amplitude_contrast_var = tk.StringVar()
        self.pytom_spherical_aberration_var = tk.StringVar()
        self.pytom_voltage_var = tk.StringVar()
        self.pytom_phase_shift_var = tk.StringVar()
        self.pytom_ctf_model_var = tk.StringVar()
        self.pytom_defocus_handedness_var = tk.StringVar()
        self.pytom_spectral_whitening_var = tk.BooleanVar(value=False)
        self.pytom_random_phase_correction_var = tk.BooleanVar(value=False)
        self.pytom_half_precision_var = tk.BooleanVar(value=False)
        self.pytom_rng_seed_var = tk.StringVar()
        self.pytom_relion5_star_var = tk.StringVar()
        self.pytom_warp_xml_var = tk.BooleanVar(value=False)
        self.pytom_gpu_ids_var = tk.StringVar()
        self.pytom_log_var = tk.StringVar()

        self.extract_tm_output_folder_var = tk.StringVar()
        self.extract_found_jobs_var = tk.StringVar(value="0 finished TM jobs found.")
        self.extract_tomogram_mask_var = tk.StringVar()
        self.extract_found_masks_var = tk.StringVar(value="0/0 tomogram masks found.")
        self.extract_ignore_tomogram_mask_var = tk.BooleanVar(value=False)
        self.extract_number_of_particles_var = tk.StringVar()
        self.extract_number_of_false_positives_var = tk.StringVar()
        self.extract_particle_diameter_var = tk.StringVar()
        self.extract_cut_off_var = tk.StringVar()
        self.extract_tophat_filter_var = tk.BooleanVar(value=False)
        self.extract_tophat_connectivity_var = tk.StringVar()
        self.extract_relion5_compat_var = tk.BooleanVar(value=False)
        self.extract_log_var = tk.StringVar()
        self.extract_tophat_bins_var = tk.StringVar()
        self.extract_plot_bins_var = tk.StringVar()

        self.slabify_manual_input_var = tk.BooleanVar(value=False)
        self.slabify_input_directory_var = tk.StringVar()
        self.slabify_output_directory_var = tk.StringVar()
        self.slabify_output_masked_directory_var = tk.StringVar()
        self.slabify_border_var = tk.StringVar()
        self.slabify_offset_var = tk.StringVar()
        self.slabify_angpix_var = tk.StringVar()
        self.slabify_measure_var = tk.BooleanVar(value=False)
        self.slabify_points_var = tk.StringVar()
        self.slabify_n_samples_var = tk.StringVar()
        self.slabify_boxsize_var = tk.StringVar()
        self.slabify_z_min_var = tk.StringVar()
        self.slabify_z_max_var = tk.StringVar()
        self.slabify_iterations_var = tk.StringVar()
        self.slabify_simple_var = tk.BooleanVar(value=False)
        self.slabify_thickness_var = tk.StringVar()
        self.slabify_percentile_var = tk.StringVar()
        self.slabify_seed_var = tk.StringVar()

        self.membrain_manual_input_var = tk.BooleanVar(value=False)
        self.membrain_input_directory_var = tk.StringVar()
        self.membrain_ckpt_path_var = tk.StringVar()
        self.membrain_out_folder_var = tk.StringVar()
        self.membrain_rescale_patches_var = tk.BooleanVar(value=False)
        self.membrain_in_pixel_size_var = tk.StringVar()
        self.membrain_out_pixel_size_var = tk.StringVar()
        self.membrain_store_probabilities_var = tk.BooleanVar(value=False)
        self.membrain_store_connected_components_var = tk.BooleanVar(value=False)
        self.membrain_connected_component_threshold_var = tk.StringVar()
        self.membrain_test_time_augmentation_var = tk.BooleanVar(value=True)
        self.membrain_segmentation_threshold_var = tk.StringVar()
        self.membrain_sliding_window_size_var = tk.StringVar()

        self.outer_canvas = tk.Canvas(self.frame, highlightthickness=0)
        self.outer_canvas.grid(row=0, column=0, sticky="nsew")
        self.outer_scrollbar = ttk.Scrollbar(self.frame, orient="vertical", command=self.outer_canvas.yview)
        self.outer_scrollbar.grid(row=0, column=1, sticky="ns")
        self.outer_canvas.configure(yscrollcommand=self.outer_scrollbar.set)

        self.content = ttk.Frame(self.outer_canvas, padding=2)
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(1, weight=0, minsize=180)
        self.content.rowconfigure(3, weight=1)
        self.outer_window = self.outer_canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", self._on_outer_frame_configure)
        self.outer_canvas.bind("<Configure>", self._on_outer_canvas_configure)

        selector_box = ttk.LabelFrame(self.content, text="Select TS to process", padding=12)
        selector_box.grid(row=0, column=0, sticky="ew")
        selector_box.columnconfigure(0, weight=1)
        selector_box.columnconfigure(1, weight=1)

        ttk.Label(selector_box, text="Dataset").grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(selector_box, text="TS").grid(row=0, column=1, sticky="w", pady=(0, 4))

        self.dataset_combo = ttk.Combobox(selector_box, textvariable=self.dataset_var, state="readonly")
        self.dataset_combo.grid(row=1, column=0, sticky="ew", padx=(0, 10))
        self.dataset_combo.bind("<<ComboboxSelected>>", self._on_dataset_selected)

        self.ts_combo = ttk.Combobox(selector_box, textvariable=self.ts_var, state="readonly")
        self.ts_combo.grid(row=1, column=1, sticky="ew")

        selector_actions = ttk.Frame(selector_box)
        selector_actions.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        selector_actions.columnconfigure(2, weight=1)
        ttk.Button(selector_actions, text="Add TS to list", command=self._add_selected_ts).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(selector_actions, text="Remove TS from list", command=self._remove_selected_ts).grid(
            row=0, column=1, sticky="w", padx=(8, 0)
        )
        ttk.Button(selector_actions, text="Select TS in Gallery", command=self._open_gallery_selection).grid(
            row=0, column=3, sticky="e"
        )

        list_box = ttk.LabelFrame(self.content, text="TS processing list", padding=12)
        list_box.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        list_box.columnconfigure(0, weight=1)
        list_box.rowconfigure(0, weight=1)

        self.ts_table = ttk.Treeview(
            list_box,
            columns=("dataset_name", "ts_name"),
            show="headings",
            selectmode="extended",
            height=5,
        )
        self.ts_table.heading("dataset_name", text="Dataset")
        self.ts_table.heading("ts_name", text="TS")
        self.ts_table.column("dataset_name", width=220, anchor="w")
        self.ts_table.column("ts_name", width=260, anchor="w")
        self.ts_table.grid(row=0, column=0, sticky="nsew")

        list_scroll = ttk.Scrollbar(list_box, orient="vertical", command=self.ts_table.yview)
        list_scroll.grid(row=0, column=1, sticky="ns")
        self.ts_table.configure(yscrollcommand=list_scroll.set)

        list_actions = ttk.Frame(list_box)
        list_actions.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        list_actions.columnconfigure(2, weight=1)
        ttk.Button(list_actions, text="Remove selected", command=self._remove_selected_ts).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(list_actions, text="Clear list", command=self._clear_ts_list).grid(
            row=0, column=1, sticky="w", padx=(8, 0)
        )

        self.selection_summary = ttk.Label(list_box, text="0 TS in list")
        self.selection_summary.grid(row=2, column=0, sticky="w", pady=(8, 0))

        jobs_header = ttk.LabelFrame(self.content, text="Job type", padding=12)
        jobs_header.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        jobs_header.columnconfigure(1, weight=1)
        ttk.Label(jobs_header, text="Select job type").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.job_type_combo = ttk.Combobox(
            jobs_header,
            textvariable=self.job_type_var,
            state="readonly",
            values=(
                "Select job type",
                *tomogram_job_titles(),
                "Job history",
            ),
        )
        self.job_type_combo.grid(row=0, column=1, sticky="ew")
        self.job_type_combo.bind("<<ComboboxSelected>>", self._on_job_type_changed)

        self.cryolithe_frame = ttk.Frame(self.content)
        self.cryolithe_frame.grid(row=3, column=0, sticky="nsew", pady=(12, 0))
        self.cryolithe_frame.columnconfigure(0, weight=1)
        self.cryolithe_frame.rowconfigure(1, weight=1)

        self.cryolithe_command_text = self._build_command_section(
            self.cryolithe_frame,
            0,
            "Command preview",
            self._copy_cryolithe_commands,
            self._schedule_cryolithe_commands,
            self._run_cryolithe_commands,
        )

        self.cryolithe_params = self._create_scrollable_group(self.cryolithe_frame, 1, "CryoLithe parameters")
        ttk.Label(self.cryolithe_params, text="Save directory").grid(row=0, column=0, sticky="w", pady=(0, 4))
        save_row = ttk.Frame(self.cryolithe_params)
        save_row.grid(row=0, column=1, sticky="ew", pady=(0, 8))
        save_row.columnconfigure(0, weight=1)
        ttk.Entry(save_row, textvariable=self.cryolithe_save_dir_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(save_row, text="Browse...", command=self._browse_cryolithe_save_dir).grid(
            row=0, column=1, padx=(8, 0)
        )

        cryolithe_advanced = self._create_advanced_section(self.cryolithe_params, 1, "cryolithe")
        ttk.Label(cryolithe_advanced, text="Model directory").grid(row=0, column=0, sticky="w", pady=(0, 4))
        model_row = ttk.Frame(cryolithe_advanced)
        model_row.grid(row=0, column=1, sticky="ew", pady=(0, 8))
        model_row.columnconfigure(0, weight=1)
        ttk.Entry(model_row, textvariable=self.cryolithe_model_dir_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(model_row, text="Browse...", command=self._browse_cryolithe_model_dir).grid(
            row=0, column=1, padx=(8, 0)
        )
        ttk.Label(cryolithe_advanced, text="Device").grid(row=1, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(cryolithe_advanced, textvariable=self.cryolithe_device_var).grid(
            row=1, column=1, sticky="ew", pady=(0, 8)
        )
        ttk.Label(cryolithe_advanced, text="n3").grid(row=2, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(cryolithe_advanced, textvariable=self.cryolithe_n3_var).grid(
            row=2, column=1, sticky="ew", pady=(0, 8)
        )
        ttk.Label(cryolithe_advanced, text="Batch size").grid(row=3, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(cryolithe_advanced, textvariable=self.cryolithe_batch_size_var).grid(
            row=3, column=1, sticky="ew"
        )
        ttk.Label(
            self.cryolithe_params,
            text=(
                "`--proj-file`, `--angle-file`, and `--save-name` are generated automatically for each TS. "
                "`--save-name` is set to `<TS name>_CryoLithe.mrc`."
            ),
            wraplength=940,
            justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))

        self.pytom_frame = ttk.Frame(self.content)
        self.pytom_frame.grid(row=3, column=0, sticky="nsew", pady=(12, 0))
        self.pytom_frame.columnconfigure(0, weight=1)
        self.pytom_frame.rowconfigure(1, weight=1)

        self.pytom_command_text = self._build_command_section(
            self.pytom_frame,
            0,
            "Command preview",
            self._copy_pytom_commands,
            self._schedule_pytom_commands,
            self._run_pytom_commands,
        )

        pytom_params_box = ttk.LabelFrame(self.pytom_frame, text="PyTom parameters", padding=12)
        pytom_params_box.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        pytom_params_box.columnconfigure(0, weight=1)
        pytom_params_box.rowconfigure(0, weight=1)

        self.pytom_canvas = tk.Canvas(pytom_params_box, highlightthickness=0)
        self.pytom_canvas.grid(row=0, column=0, sticky="nsew")
        pytom_scroll = ttk.Scrollbar(pytom_params_box, orient="vertical", command=self.pytom_canvas.yview)
        pytom_scroll.grid(row=0, column=1, sticky="ns")
        self.pytom_canvas.configure(yscrollcommand=pytom_scroll.set)
        self.pytom_params = ttk.Frame(self.pytom_canvas)
        self.pytom_params.columnconfigure(1, weight=1)
        self.pytom_window = self.pytom_canvas.create_window((0, 0), window=self.pytom_params, anchor="nw")
        self.pytom_params.bind(
            "<Configure>", lambda _event: self.pytom_canvas.configure(scrollregion=self.pytom_canvas.bbox("all"))
        )
        self.pytom_canvas.bind(
            "<Configure>",
            lambda event: self.pytom_canvas.itemconfigure(self.pytom_window, width=event.width),
        )
        self._build_pytom_parameter_form()

        self.extract_frame = ttk.Frame(self.content)
        self.extract_frame.grid(row=3, column=0, sticky="nsew", pady=(12, 0))
        self.extract_frame.columnconfigure(0, weight=1)
        self.extract_frame.rowconfigure(1, weight=1)

        self.extract_command_text = self._build_command_section(
            self.extract_frame,
            0,
            "Command preview",
            self._copy_extract_commands,
            self._schedule_extract_commands,
            self._run_extract_commands,
        )

        self.extract_params = self._create_scrollable_group(self.extract_frame, 1, "PyTom extract parameters")

        self._add_path_row(
            self.extract_params,
            0,
            "PyTom TM output folder",
            self.extract_tm_output_folder_var,
            self._browse_extract_tm_output_folder,
        )
        ttk.Label(self.extract_params, textvariable=self.extract_found_jobs_var).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        self._add_text_row(self.extract_params, 2, "Number of particles", self.extract_number_of_particles_var)
        extract_advanced = self._create_advanced_section(self.extract_params, 3, "extract")
        self._add_path_row(
            extract_advanced,
            0,
            "Tomogram mask directory",
            self.extract_tomogram_mask_var,
            self._browse_extract_tomogram_mask,
        )
        ttk.Label(
            extract_advanced,
            textvariable=self.extract_found_masks_var,
            wraplength=940,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Checkbutton(
            extract_advanced,
            text="Ignore tomogram mask",
            variable=self.extract_ignore_tomogram_mask_var,
            command=self._update_extract_preview,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 8))
        extract_fields = [
            (3, "Number of false positives", self.extract_number_of_false_positives_var),
            (4, "Particle diameter", self.extract_particle_diameter_var),
            (5, "Cut-off", self.extract_cut_off_var),
            (7, "Tophat connectivity", self.extract_tophat_connectivity_var),
            (9, "Log level", self.extract_log_var),
            (10, "Tophat bins", self.extract_tophat_bins_var),
            (11, "Plot bins", self.extract_plot_bins_var),
        ]
        for row_index, label, variable in extract_fields:
            ttk.Label(extract_advanced, text=label).grid(row=row_index, column=0, sticky="w", pady=(0, 4))
            if label == "Log level":
                ttk.Combobox(
                    extract_advanced,
                    textvariable=variable,
                    state="readonly",
                    values=("INFO", "DEBUG", "info", "debug"),
                ).grid(row=row_index, column=1, sticky="ew", pady=(0, 8))
            else:
                ttk.Entry(extract_advanced, textvariable=variable).grid(
                    row=row_index, column=1, sticky="ew", pady=(0, 8)
                )
        ttk.Checkbutton(
            extract_advanced,
            text="Tophat filter",
            variable=self.extract_tophat_filter_var,
            command=self._update_extract_preview,
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Checkbutton(
            extract_advanced,
            text="RELION5 compat",
            variable=self.extract_relion5_compat_var,
            command=self._update_extract_preview,
        ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(0, 8))

        self.slabify_frame = ttk.Frame(self.content)
        self.slabify_frame.grid(row=3, column=0, sticky="nsew", pady=(12, 0))
        self.slabify_frame.columnconfigure(0, weight=1)
        self.slabify_frame.rowconfigure(1, weight=1)

        self.slabify_command_text = self._build_command_section(
            self.slabify_frame,
            0,
            "Command preview",
            self._copy_slabify_commands,
            self._schedule_slabify_commands,
            self._run_slabify_commands,
        )

        self.slabify_params = self._create_scrollable_group(self.slabify_frame, 1, "Slabify parameters")
        ttk.Checkbutton(
            self.slabify_params,
            text="Input directory manually",
            variable=self.slabify_manual_input_var,
            command=self._update_slabify_preview,
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))
        self._add_path_row(
            self.slabify_params,
            1,
            "Input directory",
            self.slabify_input_directory_var,
            self._browse_slabify_input_directory,
        )
        self._add_path_row(
            self.slabify_params,
            2,
            "Output directory",
            self.slabify_output_directory_var,
            self._browse_slabify_output_directory,
        )
        slabify_advanced = self._create_advanced_section(self.slabify_params, 3, "slabify")
        self._add_path_row(
            slabify_advanced,
            0,
            "Output masked directory",
            self.slabify_output_masked_directory_var,
            self._browse_slabify_output_masked_directory,
        )
        self._add_text_row(slabify_advanced, 1, "Border", self.slabify_border_var)
        self._add_text_row(slabify_advanced, 2, "Offset", self.slabify_offset_var)
        self._add_text_row(slabify_advanced, 3, "Angpix", self.slabify_angpix_var)
        ttk.Checkbutton(
            slabify_advanced,
            text="Measure",
            variable=self.slabify_measure_var,
            command=self._update_slabify_preview,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self._add_path_row(
            slabify_advanced,
            5,
            "Points model",
            self.slabify_points_var,
            self._browse_slabify_points,
        )
        self._add_text_row(slabify_advanced, 6, "Number of samples", self.slabify_n_samples_var)
        self._add_text_row(slabify_advanced, 7, "Box size", self.slabify_boxsize_var)
        self._add_text_row(slabify_advanced, 8, "Z min", self.slabify_z_min_var)
        self._add_text_row(slabify_advanced, 9, "Z max", self.slabify_z_max_var)
        self._add_text_row(slabify_advanced, 10, "Iterations", self.slabify_iterations_var)
        ttk.Checkbutton(
            slabify_advanced,
            text="Simple fit",
            variable=self.slabify_simple_var,
            command=self._update_slabify_preview,
        ).grid(row=11, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self._add_text_row(slabify_advanced, 12, "Thickness", self.slabify_thickness_var)
        self._add_text_row(slabify_advanced, 13, "Percentile", self.slabify_percentile_var)
        self._add_text_row(slabify_advanced, 14, "Seed", self.slabify_seed_var)

        self.history_frame = ttk.Frame(self.content)
        self.history_frame.grid(row=3, column=0, sticky="nsew", pady=(12, 0))
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
            columns=("job_name", "dataset_name", "ts_name", "timestamp", "action"),
            show="headings",
            height=12,
        )
        self.history_table.heading("job_name", text="Job", command=lambda: self._sort_history("job_name"))
        self.history_table.heading("dataset_name", text="Dataset", command=lambda: self._sort_history("dataset_name"))
        self.history_table.heading("ts_name", text="TS", command=lambda: self._sort_history("ts_name"))
        self.history_table.heading("timestamp", text="Timestamp", command=lambda: self._sort_history("timestamp"))
        self.history_table.heading("action", text="Action", command=lambda: self._sort_history("action"))
        self.history_table.column("job_name", width=220, anchor="w")
        self.history_table.column("dataset_name", width=160, anchor="w")
        self.history_table.column("ts_name", width=170, anchor="w")
        self.history_table.column("timestamp", width=180, anchor="w")
        self.history_table.column("action", width=90, anchor="w")
        self.history_table.grid(row=0, column=0, sticky="nsew")
        self.history_table.tag_configure("scheduled", background="#ececec")
        self.history_table.tag_configure("waiting", background="#dbeeff")
        self.history_table.tag_configure("running", background="#dff4d8")
        self.history_table.tag_configure("completed", background="#dde8ff")
        history_scroll = ttk.Scrollbar(history_box, orient="vertical", command=self.history_table.yview)
        history_scroll.grid(row=0, column=1, sticky="ns")
        self.history_table.configure(yscrollcommand=history_scroll.set)

        history_actions = ttk.Frame(history_box)
        history_actions.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        history_actions.columnconfigure(4, weight=1)
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
        ttk.Button(
            history_actions,
            text="Run scheduled jobs",
            command=self._run_scheduled_jobs,
        ).grid(row=0, column=2, sticky="w", padx=(8, 0))
        ttk.Button(
            history_actions,
            text="Submit scheduled jobs to Slurm",
            command=self._submit_scheduled_jobs_to_slurm,
        ).grid(row=0, column=3, sticky="w", padx=(8, 0))
        history_abort = ttk.Button(
            history_actions,
            text="Abort",
            command=self.app.abort_running_commands,
            state="disabled",
        )
        history_abort.grid(row=0, column=5, sticky="e", padx=(8, 0))
        self.app.attach_abort_button(history_abort)
        self.history_table.bind("<Double-1>", self._show_selected_history_details)

        self.membrain_frame = ttk.Frame(self.content)
        self.membrain_frame.grid(row=3, column=0, sticky="nsew", pady=(12, 0))
        self.membrain_frame.columnconfigure(0, weight=1)
        self.membrain_frame.rowconfigure(1, weight=1)

        self.membrain_command_text = self._build_command_section(
            self.membrain_frame,
            0,
            "Command preview",
            self._copy_membrain_commands,
            self._schedule_membrain_commands,
            self._run_membrain_commands,
        )

        self.membrain_params = self._create_scrollable_group(self.membrain_frame, 1, "MemBrain-seg parameters")
        ttk.Checkbutton(
            self.membrain_params,
            text="Input TS directory manually",
            variable=self.membrain_manual_input_var,
            command=self._update_membrain_preview,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self._add_path_row(
            self.membrain_params,
            1,
            "Input directory",
            self.membrain_input_directory_var,
            self._browse_membrain_input_directory,
        )
        self._add_path_row(
            self.membrain_params,
            2,
            "Checkpoint path",
            self.membrain_ckpt_path_var,
            self._browse_membrain_ckpt_path,
        )
        membrain_advanced = self._create_advanced_section(self.membrain_params, 3, "membrain")
        self._add_path_row(
            membrain_advanced,
            0,
            "Output folder",
            self.membrain_out_folder_var,
            self._browse_membrain_out_folder,
        )
        ttk.Checkbutton(
            membrain_advanced,
            text="Rescale patches",
            variable=self.membrain_rescale_patches_var,
            command=self._update_membrain_preview,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self._add_text_row(membrain_advanced, 2, "Input pixel size", self.membrain_in_pixel_size_var)
        self._add_text_row(membrain_advanced, 3, "Output pixel size", self.membrain_out_pixel_size_var)
        ttk.Checkbutton(
            membrain_advanced,
            text="Store probabilities",
            variable=self.membrain_store_probabilities_var,
            command=self._update_membrain_preview,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Checkbutton(
            membrain_advanced,
            text="Store connected components",
            variable=self.membrain_store_connected_components_var,
            command=self._update_membrain_preview,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self._add_text_row(
            membrain_advanced,
            6,
            "Connected component threshold",
            self.membrain_connected_component_threshold_var,
        )
        ttk.Checkbutton(
            membrain_advanced,
            text="Test-time augmentation",
            variable=self.membrain_test_time_augmentation_var,
            command=self._update_membrain_preview,
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self._add_text_row(
            membrain_advanced,
            8,
            "Segmentation threshold",
            self.membrain_segmentation_threshold_var,
        )
        self._add_text_row(
            membrain_advanced,
            9,
            "Sliding window size",
            self.membrain_sliding_window_size_var,
        )

        self._attach_variable_traces()
        self._apply_custom_defaults()
        self.cryolithe_frame.grid_remove()
        self.pytom_frame.grid_remove()
        self.extract_frame.grid_remove()
        self.history_frame.grid_remove()
        self.membrain_frame.grid_remove()

    def _on_outer_frame_configure(self, _event=None) -> None:
        self.outer_canvas.configure(scrollregion=self.outer_canvas.bbox("all"))

    def _on_outer_canvas_configure(self, event) -> None:
        self.outer_canvas.itemconfigure(self.outer_window, width=event.width)

    def _build_command_section(self, parent, row: int, title: str, copy_command, schedule_command, run_command):
        box = ttk.LabelFrame(parent, text=title, padding=12)
        box.grid(row=row, column=0, sticky="ew")
        box.columnconfigure(0, weight=1)
        actions = ttk.Frame(box)
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        actions.columnconfigure(0, weight=1)
        ttk.Button(actions, text="Copy command", command=copy_command).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(actions, text="Schedule command", command=schedule_command).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(actions, text="Run command", command=run_command).grid(row=0, column=3, padx=(8, 0))
        abort_button = ttk.Button(
            actions,
            text="Abort",
            command=self.app.abort_running_commands,
            state="disabled",
        )
        abort_button.grid(row=0, column=4, padx=(8, 0))
        self.app.attach_abort_button(abort_button)
        execution_row = ttk.Frame(box)
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
        target_label = ttk.Label(execution_row, text="Select environment")
        target_label.grid(row=0, column=2, sticky="e", padx=(16, 8))
        profile_combo = ttk.Combobox(
            execution_row,
            textvariable=self.slurm_profile_var,
            state="readonly",
            width=22,
        )
        profile_combo.grid(row=0, column=3, sticky="w")
        profile_combo.bind("<<ComboboxSelected>>", lambda _e: self.slurm_overrides_ui.rebuild(preserve_existing=False))
        environment_combo = ttk.Combobox(
            execution_row,
            textvariable=self.environment_var,
            state="readonly",
            width=22,
            values=environment_titles(self.app.project),
        )
        environment_combo.grid(row=0, column=3, sticky="w")
        self.slurm_profile_combos.append(profile_combo)
        self.environment_combos.append(environment_combo)
        self.execution_target_labels.append(target_label)
        overrides = ttk.Frame(box)
        overrides.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self.slurm_overrides_ui.register_frame(overrides)
        self.slurm_override_frames.append(overrides)
        text = tk.Text(box, height=6, wrap="word", font="TkDefaultFont")
        text.grid(row=3, column=0, sticky="ew")
        return text

    def _create_scrollable_group(self, parent, row: int, title: str) -> ttk.Frame:
        box = ttk.LabelFrame(parent, text=title, padding=12)
        box.grid(row=row, column=0, sticky="nsew", pady=(12, 0))
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        canvas = tk.Canvas(box, highlightthickness=0, height=360)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(box, orient="vertical", command=canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        xscrollbar = ttk.Scrollbar(box, orient="horizontal", command=canvas.xview)
        xscrollbar.grid(row=1, column=0, sticky="ew")
        canvas.configure(yscrollcommand=scrollbar.set, xscrollcommand=xscrollbar.set)
        inner = ttk.Frame(canvas)
        inner.columnconfigure(1, weight=1)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        bind_scrollable_canvas(canvas, window, inner, allow_horizontal=True)
        setattr(inner, "_scroll_canvas", canvas)
        return inner

    def _scroll_job_view_to_top(self) -> None:
        for canvas in (
            getattr(self, "pytom_canvas", None),
            getattr(self.cryolithe_params, "_scroll_canvas", None) if hasattr(self, "cryolithe_params") else None,
            getattr(self.extract_params, "_scroll_canvas", None) if hasattr(self, "extract_params") else None,
            getattr(self.slabify_params, "_scroll_canvas", None) if hasattr(self, "slabify_params") else None,
            getattr(self.membrain_params, "_scroll_canvas", None) if hasattr(self, "membrain_params") else None,
        ):
            if canvas is None:
                continue
            canvas.yview_moveto(0)
            canvas.xview_moveto(0)

    def _create_advanced_section(self, parent, row: int, key: str) -> ttk.Frame:
        visible_var = self.advanced_visible_vars.setdefault(key, tk.BooleanVar(value=False))

        def toggle() -> None:
            if visible_var.get():
                advanced_frame.grid()
                toggle_button.config(text="Hide advanced settings")
            else:
                advanced_frame.grid_remove()
                toggle_button.config(text="Show advanced settings")

        toggle_button = ttk.Button(
            parent,
            text="Show advanced settings" if not visible_var.get() else "Hide advanced settings",
            command=lambda: (visible_var.set(not visible_var.get()), toggle()),
        )
        toggle_button.grid(row=row, column=0, sticky="w", pady=(8, 8))
        advanced_frame = ttk.LabelFrame(parent, text="Advanced settings", padding=12)
        advanced_frame.grid(row=row + 1, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        advanced_frame.columnconfigure(1, weight=1)
        toggle()
        return advanced_frame

    def _build_pytom_parameter_form(self) -> None:
        for child in self.pytom_params.winfo_children():
            child.destroy()
        row = 0
        self._add_path_row(self.pytom_params, row, "Template", self.pytom_template_var, self._browse_pytom_template)
        row += 1
        self._add_path_row(
            self.pytom_params,
            row,
            "Destination",
            self.pytom_destination_var,
            self._browse_pytom_destination,
        )
        row += 1
        self._add_path_row(self.pytom_params, row, "Mask", self.pytom_mask_var, self._browse_pytom_mask)
        row += 1
        ttk.Checkbutton(
            self.pytom_params,
            text="Use Warp XML file",
            variable=self.pytom_warp_xml_var,
            command=self._update_pytom_preview,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 8))
        row += 1
        self._add_text_row(self.pytom_params, row, "GPU IDs", self.pytom_gpu_ids_var)
        row += 1

        ttk.Checkbutton(
            self.pytom_params,
            text="Input TS directory manually",
            variable=self.pytom_manual_input_var,
            command=self._update_pytom_preview,
        ).grid(row=row, column=0, sticky="w", pady=(0, 8))
        manual_row = ttk.Frame(self.pytom_params)
        manual_row.grid(row=row, column=1, sticky="ew", pady=(0, 8))
        manual_row.columnconfigure(0, weight=1)
        ttk.Entry(manual_row, textvariable=self.pytom_manual_dir_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(manual_row, text="Browse...", command=self._browse_pytom_manual_dir).grid(
            row=0, column=1, padx=(8, 0)
        )
        row += 1

        advanced = self._create_advanced_section(self.pytom_params, row, "pytom_template")
        advanced_row = 0
        field_specs = [
            ("Particle diameter", self.pytom_particle_diameter_var),
            ("Angular search", self.pytom_angular_search_var),
            ("Z-axis rotational symmetry", self.pytom_z_axis_symmetry_var),
            ("Volume split", self.pytom_volume_split_var),
            ("Search X", self.pytom_search_x_var),
            ("Search Y", self.pytom_search_y_var),
            ("Search Z", self.pytom_search_z_var),
        ]
        for label, variable in field_specs:
            ttk.Label(advanced, text=label).grid(row=advanced_row, column=0, sticky="w", pady=(0, 4))
            ttk.Entry(advanced, textvariable=variable).grid(row=advanced_row, column=1, sticky="ew", pady=(0, 8))
            advanced_row += 1

        self._add_path_row(
            advanced,
            advanced_row,
            "Tomogram mask directory",
            self.pytom_tomogram_mask_var,
            self._browse_pytom_tomogram_mask,
        )
        advanced_row += 1
        ttk.Label(
            advanced,
            textvariable=self.pytom_found_masks_var,
            wraplength=940,
            justify="left",
        ).grid(row=advanced_row, column=0, columnspan=2, sticky="w", pady=(0, 8))
        advanced_row += 1

        bool_specs = [
            ("Non-spherical mask", self.pytom_non_spherical_mask_var),
            ("Per-tilt weighting", self.pytom_per_tilt_weighting_var),
            ("Spectral whitening", self.pytom_spectral_whitening_var),
            ("Random phase correction", self.pytom_random_phase_correction_var),
            ("Half precision", self.pytom_half_precision_var),
        ]
        for label, variable in bool_specs:
            ttk.Checkbutton(advanced, text=label, variable=variable, command=self._update_pytom_preview).grid(
                row=advanced_row, column=0, columnspan=2, sticky="w", pady=(0, 8)
            )
            advanced_row += 1

        more_fields = [
            ("Voxel size angstrom", self.pytom_voxel_size_var),
            ("Low-pass", self.pytom_low_pass_var),
            ("High-pass", self.pytom_high_pass_var),
            ("Amplitude contrast", self.pytom_amplitude_contrast_var),
            ("Spherical aberration", self.pytom_spherical_aberration_var),
            ("Voltage", self.pytom_voltage_var),
            ("Phase shift", self.pytom_phase_shift_var),
            ("RNG seed", self.pytom_rng_seed_var),
        ]
        for label, variable in more_fields:
            ttk.Label(advanced, text=label).grid(row=advanced_row, column=0, sticky="w", pady=(0, 4))
            ttk.Entry(advanced, textvariable=variable).grid(row=advanced_row, column=1, sticky="ew", pady=(0, 8))
            advanced_row += 1

        self._add_path_row(
            advanced,
            advanced_row,
            "Defocus",
            self.pytom_defocus_var,
            self._browse_pytom_defocus,
        )
        advanced_row += 1

        ttk.Label(advanced, text="Tomogram CTF model").grid(row=advanced_row, column=0, sticky="w", pady=(0, 4))
        ttk.Combobox(
            advanced,
            textvariable=self.pytom_ctf_model_var,
            state="readonly",
            values=("", "phase-flip"),
        ).grid(row=advanced_row, column=1, sticky="ew", pady=(0, 8))
        advanced_row += 1

        ttk.Label(advanced, text="Defocus handedness").grid(row=advanced_row, column=0, sticky="w", pady=(0, 4))
        ttk.Combobox(
            advanced,
            textvariable=self.pytom_defocus_handedness_var,
            state="readonly",
            values=("-1", "0", "1"),
        ).grid(row=advanced_row, column=1, sticky="ew", pady=(0, 8))
        advanced_row += 1

        self._add_path_row(
            advanced,
            advanced_row,
            "Dose accumulation",
            self.pytom_dose_accumulation_var,
            self._browse_pytom_dose_accumulation,
        )
        advanced_row += 1
        self._add_path_row(
            advanced,
            advanced_row,
            "RELION5 tomograms.star",
            self.pytom_relion5_star_var,
            self._browse_pytom_relion5_star,
        )
        advanced_row += 1
        ttk.Label(advanced, text="Log level").grid(row=advanced_row, column=0, sticky="w", pady=(0, 4))
        ttk.Combobox(
            advanced,
            textvariable=self.pytom_log_var,
            state="readonly",
            values=("INFO", "DEBUG", "info", "debug"),
        ).grid(row=advanced_row, column=1, sticky="ew")
        row += 2

        ttk.Label(
            self.pytom_params,
            text=(
                "The tomogram and tilt-angle input are detected automatically per TS. "
                "If manual input is enabled, the program searches the chosen directory for `.mrc` files "
                "containing each TS name."
            ),
            wraplength=940,
            justify="left",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(10, 0))

    def _add_path_row(self, parent, row: int, label: str, variable: tk.StringVar, browse_command) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=(0, 4))
        path_row = ttk.Frame(parent)
        path_row.grid(row=row, column=1, sticky="ew", pady=(0, 8))
        path_row.columnconfigure(0, weight=1)
        ttk.Entry(path_row, textvariable=variable).grid(row=0, column=0, sticky="ew")
        ttk.Button(path_row, text="Browse...", command=browse_command).grid(row=0, column=1, padx=(8, 0))

    def _add_text_row(self, parent, row: int, label: str, variable: tk.StringVar) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=(0, 8))

    def _attach_variable_traces(self) -> None:
        for variable in (
            self.cryolithe_model_dir_var,
            self.cryolithe_save_dir_var,
            self.cryolithe_device_var,
            self.cryolithe_n3_var,
            self.cryolithe_batch_size_var,
            self.pytom_template_var,
            self.pytom_destination_var,
            self.pytom_mask_var,
            self.pytom_manual_dir_var,
            self.pytom_particle_diameter_var,
            self.pytom_angular_search_var,
            self.pytom_z_axis_symmetry_var,
            self.pytom_volume_split_var,
            self.pytom_search_x_var,
            self.pytom_search_y_var,
            self.pytom_search_z_var,
            self.pytom_tomogram_mask_var,
            self.pytom_voxel_size_var,
            self.pytom_low_pass_var,
            self.pytom_high_pass_var,
            self.pytom_dose_accumulation_var,
            self.pytom_defocus_var,
            self.pytom_amplitude_contrast_var,
            self.pytom_spherical_aberration_var,
            self.pytom_voltage_var,
            self.pytom_phase_shift_var,
            self.pytom_rng_seed_var,
            self.pytom_relion5_star_var,
            self.pytom_warp_xml_var,
            self.pytom_gpu_ids_var,
            self.pytom_log_var,
            self.pytom_ctf_model_var,
            self.pytom_defocus_handedness_var,
            self.extract_tm_output_folder_var,
            self.extract_tomogram_mask_var,
            self.extract_number_of_particles_var,
            self.extract_number_of_false_positives_var,
            self.extract_particle_diameter_var,
            self.extract_cut_off_var,
            self.extract_tophat_connectivity_var,
            self.extract_log_var,
            self.extract_tophat_bins_var,
            self.extract_plot_bins_var,
            self.slabify_input_directory_var,
            self.slabify_output_directory_var,
            self.slabify_output_masked_directory_var,
            self.slabify_border_var,
            self.slabify_offset_var,
            self.slabify_angpix_var,
            self.slabify_points_var,
            self.slabify_n_samples_var,
            self.slabify_boxsize_var,
            self.slabify_z_min_var,
            self.slabify_z_max_var,
            self.slabify_iterations_var,
            self.slabify_thickness_var,
            self.slabify_percentile_var,
            self.slabify_seed_var,
            self.membrain_input_directory_var,
            self.membrain_ckpt_path_var,
            self.membrain_out_folder_var,
            self.membrain_in_pixel_size_var,
            self.membrain_out_pixel_size_var,
            self.membrain_connected_component_threshold_var,
            self.membrain_segmentation_threshold_var,
            self.membrain_sliding_window_size_var,
        ):
            variable.trace_add("write", lambda *_args: self._update_active_preview())

        for variable in (
            self.pytom_manual_input_var,
            self.pytom_non_spherical_mask_var,
            self.pytom_per_tilt_weighting_var,
            self.pytom_spectral_whitening_var,
            self.pytom_random_phase_correction_var,
            self.pytom_half_precision_var,
            self.extract_ignore_tomogram_mask_var,
            self.extract_tophat_filter_var,
            self.extract_relion5_compat_var,
            self.slabify_manual_input_var,
            self.slabify_measure_var,
            self.slabify_simple_var,
            self.membrain_manual_input_var,
            self.membrain_rescale_patches_var,
            self.membrain_store_probabilities_var,
            self.membrain_store_connected_components_var,
            self.membrain_test_time_augmentation_var,
        ):
            variable.trace_add("write", lambda *_args: self._update_active_preview())

    def _tomogram_default(self, group: str, job_key: str, field_key: str, base_value: str) -> str:
        return resolve_job_default(self.app.project, "Tomograms", group, job_key, field_key, base_value)

    def _tomogram_default_bool(self, group: str, job_key: str, field_key: str, base_value: bool) -> bool:
        return self._tomogram_default(group, job_key, field_key, "true" if base_value else "").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    def _job_environment_default(self, *, title: str | None = None, job_key: str | None = None) -> str:
        target_title = title or self.job_type_var.get()
        definition = self.job_catalog.get(target_title)
        resolved_job_key = job_key or (definition.job_key if definition is not None else "")
        if not target_title or not resolved_job_key:
            return "None"
        default_value = self._tomogram_default(target_title, resolved_job_key, "execution_environment", "None").strip() or "None"
        available = set(environment_titles(self.app.project))
        return default_value if default_value in available else "None"

    def _apply_custom_defaults(self) -> None:
        self.cryolithe_model_dir_var.set(
            self._tomogram_default("CryoLithe: Denoising", "cryolithe_denoising", "model_dir", "")
        )
        self.cryolithe_save_dir_var.set(
            self._tomogram_default("CryoLithe: Denoising", "cryolithe_denoising", "save_dir", "")
        )
        self.cryolithe_device_var.set(
            self._tomogram_default("CryoLithe: Denoising", "cryolithe_denoising", "device", "0")
        )
        self.cryolithe_n3_var.set(self._tomogram_default("CryoLithe: Denoising", "cryolithe_denoising", "n3", "256"))
        self.cryolithe_batch_size_var.set(
            self._tomogram_default("CryoLithe: Denoising", "cryolithe_denoising", "batch_size", "50000")
        )

        self.pytom_template_var.set(
            self._tomogram_default("PyTom: Template matching", "pytom_template_matching", "template", "")
        )
        self.pytom_destination_var.set(
            self._tomogram_default("PyTom: Template matching", "pytom_template_matching", "destination", "")
        )
        self.pytom_mask_var.set(
            self._tomogram_default("PyTom: Template matching", "pytom_template_matching", "mask", "")
        )
        self.pytom_manual_input_var.set(
            self._tomogram_default_bool(
                "PyTom: Template matching",
                "pytom_template_matching",
                "manual_tomogram_input",
                False,
            )
        )
        self.pytom_manual_dir_var.set(
            self._tomogram_default("PyTom: Template matching", "pytom_template_matching", "manual_tomogram_dir", "")
        )
        self.pytom_non_spherical_mask_var.set(
            self._tomogram_default_bool(
                "PyTom: Template matching",
                "pytom_template_matching",
                "non_spherical_mask",
                False,
            )
        )
        self.pytom_particle_diameter_var.set(
            self._tomogram_default("PyTom: Template matching", "pytom_template_matching", "particle_diameter", "")
        )
        self.pytom_angular_search_var.set(
            self._tomogram_default("PyTom: Template matching", "pytom_template_matching", "angular_search", "")
        )
        self.pytom_z_axis_symmetry_var.set(
            self._tomogram_default(
                "PyTom: Template matching",
                "pytom_template_matching",
                "z_axis_rotational_symmetry",
                "1",
            )
        )
        self.pytom_volume_split_var.set(
            self._tomogram_default("PyTom: Template matching", "pytom_template_matching", "volume_split", "1 1 1")
        )
        self.pytom_search_x_var.set(
            self._tomogram_default("PyTom: Template matching", "pytom_template_matching", "search_x", "")
        )
        self.pytom_search_y_var.set(
            self._tomogram_default("PyTom: Template matching", "pytom_template_matching", "search_y", "")
        )
        self.pytom_search_z_var.set(
            self._tomogram_default("PyTom: Template matching", "pytom_template_matching", "search_z", "")
        )
        self.pytom_tomogram_mask_var.set(
            self._tomogram_default("PyTom: Template matching", "pytom_template_matching", "tomogram_mask", "")
        )
        self.pytom_per_tilt_weighting_var.set(
            self._tomogram_default_bool(
                "PyTom: Template matching",
                "pytom_template_matching",
                "per_tilt_weighting",
                False,
            )
        )
        self.pytom_voxel_size_var.set(
            self._tomogram_default("PyTom: Template matching", "pytom_template_matching", "voxel_size_angstrom", "")
        )
        self.pytom_low_pass_var.set(
            self._tomogram_default("PyTom: Template matching", "pytom_template_matching", "low_pass", "")
        )
        self.pytom_high_pass_var.set(
            self._tomogram_default("PyTom: Template matching", "pytom_template_matching", "high_pass", "")
        )
        self.pytom_dose_accumulation_var.set(
            self._tomogram_default("PyTom: Template matching", "pytom_template_matching", "dose_accumulation", "")
        )
        self.pytom_defocus_var.set(
            self._tomogram_default("PyTom: Template matching", "pytom_template_matching", "defocus", "")
        )
        self.pytom_amplitude_contrast_var.set(
            self._tomogram_default(
                "PyTom: Template matching",
                "pytom_template_matching",
                "amplitude_contrast",
                "",
            )
        )
        self.pytom_spherical_aberration_var.set(
            self._tomogram_default(
                "PyTom: Template matching",
                "pytom_template_matching",
                "spherical_aberration",
                "",
            )
        )
        self.pytom_voltage_var.set(
            self._tomogram_default("PyTom: Template matching", "pytom_template_matching", "voltage", "")
        )
        self.pytom_phase_shift_var.set(
            self._tomogram_default("PyTom: Template matching", "pytom_template_matching", "phase_shift", "0.0")
        )
        self.pytom_ctf_model_var.set(
            self._tomogram_default("PyTom: Template matching", "pytom_template_matching", "tomogram_ctf_model", "")
        )
        self.pytom_defocus_handedness_var.set(
            self._tomogram_default(
                "PyTom: Template matching",
                "pytom_template_matching",
                "defocus_handedness",
                "0",
            )
        )
        self.pytom_spectral_whitening_var.set(
            self._tomogram_default_bool(
                "PyTom: Template matching",
                "pytom_template_matching",
                "spectral_whitening",
                False,
            )
        )
        self.pytom_random_phase_correction_var.set(
            self._tomogram_default_bool(
                "PyTom: Template matching",
                "pytom_template_matching",
                "random_phase_correction",
                False,
            )
        )
        self.pytom_half_precision_var.set(
            self._tomogram_default_bool(
                "PyTom: Template matching",
                "pytom_template_matching",
                "half_precision",
                False,
            )
        )
        self.pytom_rng_seed_var.set(
            self._tomogram_default("PyTom: Template matching", "pytom_template_matching", "rng_seed", "")
        )
        self.pytom_relion5_star_var.set(
            self._tomogram_default(
                "PyTom: Template matching",
                "pytom_template_matching",
                "relion5_tomograms_star",
                "",
            )
        )
        self.pytom_warp_xml_var.set(
            self._tomogram_default_bool(
                "PyTom: Template matching",
                "pytom_template_matching",
                "warp_xml_file",
                False,
            )
        )
        self.pytom_gpu_ids_var.set(
            self._tomogram_default("PyTom: Template matching", "pytom_template_matching", "gpu_ids", "0")
        )
        self.pytom_log_var.set(
            self._tomogram_default("PyTom: Template matching", "pytom_template_matching", "log", "INFO")
        )
        self.extract_tm_output_folder_var.set(
            self._tomogram_default("PyTom: Extract coordinates", "pytom_extract_coordinates", "tm_output_folder", "")
        )
        self.extract_tomogram_mask_var.set(
            self._tomogram_default("PyTom: Extract coordinates", "pytom_extract_coordinates", "tomogram_mask", "")
        )
        self.extract_ignore_tomogram_mask_var.set(
            self._tomogram_default_bool(
                "PyTom: Extract coordinates",
                "pytom_extract_coordinates",
                "ignore_tomogram_mask",
                False,
            )
        )
        self.extract_number_of_particles_var.set(
            self._tomogram_default(
                "PyTom: Extract coordinates", "pytom_extract_coordinates", "number_of_particles", ""
            )
        )
        self.extract_number_of_false_positives_var.set(
            self._tomogram_default(
                "PyTom: Extract coordinates",
                "pytom_extract_coordinates",
                "number_of_false_positives",
                "1.0",
            )
        )
        self.extract_particle_diameter_var.set(
            self._tomogram_default(
                "PyTom: Extract coordinates", "pytom_extract_coordinates", "particle_diameter", ""
            )
        )
        self.extract_cut_off_var.set(
            self._tomogram_default("PyTom: Extract coordinates", "pytom_extract_coordinates", "cut_off", "")
        )
        self.extract_tophat_filter_var.set(
            self._tomogram_default_bool(
                "PyTom: Extract coordinates",
                "pytom_extract_coordinates",
                "tophat_filter",
                False,
            )
        )
        self.extract_tophat_connectivity_var.set(
            self._tomogram_default(
                "PyTom: Extract coordinates",
                "pytom_extract_coordinates",
                "tophat_connectivity",
                "1",
            )
        )
        self.extract_relion5_compat_var.set(
            self._tomogram_default_bool(
                "PyTom: Extract coordinates",
                "pytom_extract_coordinates",
                "relion5_compat",
                False,
            )
        )
        self.extract_log_var.set(
            self._tomogram_default("PyTom: Extract coordinates", "pytom_extract_coordinates", "log", "INFO")
        )
        self.extract_tophat_bins_var.set(
            self._tomogram_default("PyTom: Extract coordinates", "pytom_extract_coordinates", "tophat_bins", "50")
        )
        self.extract_plot_bins_var.set(
            self._tomogram_default("PyTom: Extract coordinates", "pytom_extract_coordinates", "plot_bins", "20")
        )
        self.slabify_manual_input_var.set(
            self._tomogram_default_bool("Slabify: Mask creation", "slabify_mask_creation", "manual_input", False)
        )
        self.slabify_input_directory_var.set(
            self._tomogram_default("Slabify: Mask creation", "slabify_mask_creation", "input_directory", "")
        )
        self.slabify_output_directory_var.set(
            self._tomogram_default("Slabify: Mask creation", "slabify_mask_creation", "output_directory", "")
        )
        self.slabify_output_masked_directory_var.set(
            self._tomogram_default(
                "Slabify: Mask creation",
                "slabify_mask_creation",
                "output_masked_directory",
                "",
            )
        )
        self.slabify_border_var.set(
            self._tomogram_default("Slabify: Mask creation", "slabify_mask_creation", "border", "0")
        )
        self.slabify_offset_var.set(
            self._tomogram_default("Slabify: Mask creation", "slabify_mask_creation", "offset", "0")
        )
        self.slabify_angpix_var.set(
            self._tomogram_default("Slabify: Mask creation", "slabify_mask_creation", "angpix", "")
        )
        self.slabify_measure_var.set(
            self._tomogram_default_bool("Slabify: Mask creation", "slabify_mask_creation", "measure", False)
        )
        self.slabify_points_var.set(
            self._tomogram_default("Slabify: Mask creation", "slabify_mask_creation", "points", "")
        )
        self.slabify_n_samples_var.set(
            self._tomogram_default("Slabify: Mask creation", "slabify_mask_creation", "n_samples", "50000")
        )
        self.slabify_boxsize_var.set(
            self._tomogram_default("Slabify: Mask creation", "slabify_mask_creation", "boxsize", "32")
        )
        self.slabify_z_min_var.set(
            self._tomogram_default("Slabify: Mask creation", "slabify_mask_creation", "z_min", "1")
        )
        self.slabify_z_max_var.set(
            self._tomogram_default("Slabify: Mask creation", "slabify_mask_creation", "z_max", "")
        )
        self.slabify_iterations_var.set(
            self._tomogram_default("Slabify: Mask creation", "slabify_mask_creation", "iterations", "3")
        )
        self.slabify_simple_var.set(
            self._tomogram_default_bool("Slabify: Mask creation", "slabify_mask_creation", "simple", False)
        )
        self.slabify_thickness_var.set(
            self._tomogram_default("Slabify: Mask creation", "slabify_mask_creation", "thickness", "")
        )
        self.slabify_percentile_var.set(
            self._tomogram_default("Slabify: Mask creation", "slabify_mask_creation", "percentile", "95")
        )
        self.slabify_seed_var.set(
            self._tomogram_default("Slabify: Mask creation", "slabify_mask_creation", "seed", "4056")
        )
        self.membrain_manual_input_var.set(
            self._tomogram_default_bool(
                "MemBrain-seg: Segmentation",
                "membrain_segmentation",
                "manual_input",
                False,
            )
        )
        self.membrain_input_directory_var.set(
            self._tomogram_default("MemBrain-seg: Segmentation", "membrain_segmentation", "input_directory", "")
        )
        self.membrain_ckpt_path_var.set(
            self._tomogram_default("MemBrain-seg: Segmentation", "membrain_segmentation", "ckpt_path", "")
        )
        self.membrain_out_folder_var.set(
            self._tomogram_default("MemBrain-seg: Segmentation", "membrain_segmentation", "out_folder", "predictions")
        )
        self.membrain_rescale_patches_var.set(
            self._tomogram_default_bool(
                "MemBrain-seg: Segmentation",
                "membrain_segmentation",
                "rescale_patches",
                False,
            )
        )
        self.membrain_in_pixel_size_var.set(
            self._tomogram_default("MemBrain-seg: Segmentation", "membrain_segmentation", "in_pixel_size", "")
        )
        self.membrain_out_pixel_size_var.set(
            self._tomogram_default(
                "MemBrain-seg: Segmentation",
                "membrain_segmentation",
                "out_pixel_size",
                "10.0",
            )
        )
        self.membrain_store_probabilities_var.set(
            self._tomogram_default_bool(
                "MemBrain-seg: Segmentation",
                "membrain_segmentation",
                "store_probabilities",
                False,
            )
        )
        self.membrain_store_connected_components_var.set(
            self._tomogram_default_bool(
                "MemBrain-seg: Segmentation",
                "membrain_segmentation",
                "store_connected_components",
                False,
            )
        )
        self.membrain_connected_component_threshold_var.set(
            self._tomogram_default(
                "MemBrain-seg: Segmentation",
                "membrain_segmentation",
                "connected_component_threshold",
                "",
            )
        )
        self.membrain_test_time_augmentation_var.set(
            self._tomogram_default_bool(
                "MemBrain-seg: Segmentation",
                "membrain_segmentation",
                "test_time_augmentation",
                True,
            )
        )
        self.membrain_segmentation_threshold_var.set(
            self._tomogram_default(
                "MemBrain-seg: Segmentation",
                "membrain_segmentation",
                "segmentation_threshold",
                "0.0",
            )
        )
        self.membrain_sliding_window_size_var.set(
            self._tomogram_default(
                "MemBrain-seg: Segmentation",
                "membrain_segmentation",
                "sliding_window_size",
                "160",
            )
        )
        self._extract_job_files()
        self._update_active_preview()

    def _dataset_options(self, project: ProjectData) -> list[str]:
        return [dataset.dataset_name for dataset in project.datasets]

    def _dataset_map(self) -> dict[str, DatasetRecord]:
        return {dataset.dataset_name: dataset for dataset in self.app.project.datasets}

    def _ts_names_for_dataset(self, dataset_name: str) -> list[str]:
        dataset = self._dataset_map().get(dataset_name)
        if dataset is None:
            return []
        return dataset_ts_names(dataset)

    def _persist_selection(self) -> None:
        self.app.project.state.tomograms_selection = [dict(item) for item in self.selected_entries]

    def _refresh_table(self) -> None:
        self.ts_table.delete(*self.ts_table.get_children())
        for index, entry in enumerate(self.selected_entries):
            self.ts_table.insert("", "end", iid=str(index), values=(entry["dataset_name"], entry["ts_name"]))
        dataset_count = len({entry["dataset_name"] for entry in self.selected_entries})
        self.selection_summary.config(text=f"{len(self.selected_entries)} TS in list across {dataset_count} dataset(s)")
        self._update_active_preview()

    def _refresh_dataset_options(self) -> None:
        options = self._dataset_options(self.app.project)
        self.dataset_combo.configure(values=options)
        history_options = ["All datasets"] + options if options else ["All datasets"]
        self.history_dataset_combo.configure(values=history_options)
        if self.history_dataset_var.get() not in history_options:
            self.history_dataset_var.set("All datasets")
        if not options:
            self.dataset_var.set("")
            self.ts_combo.configure(values=())
            self.ts_var.set("")
            return
        if self.dataset_var.get() not in options:
            self.dataset_var.set(options[0])
        self._refresh_ts_options()

    def _refresh_ts_options(self) -> None:
        dataset_name = self.dataset_var.get()
        ts_options = self._ts_names_for_dataset(dataset_name)
        values = ["All TS"] + ts_options if ts_options else []
        self.ts_combo.configure(values=values)
        if values:
            if self.ts_var.get() not in values:
                self.ts_var.set("All TS")
        else:
            self.ts_var.set("")

    def _on_dataset_selected(self, _event=None) -> None:
        self._refresh_ts_options()

    def add_ts_entries(self, entries: list[tuple[str, str]]) -> int:
        known = {(item["dataset_name"], item["ts_name"]) for item in self.selected_entries}
        added = 0
        for dataset_name, ts_name in entries:
            key = (dataset_name, ts_name)
            if not dataset_name or not ts_name or key in known:
                continue
            self.selected_entries.append({"dataset_name": dataset_name, "ts_name": ts_name})
            known.add(key)
            added += 1
        if added:
            self._persist_selection()
            self._refresh_table()
        return added

    def _add_selected_ts(self) -> None:
        dataset_name = self.dataset_var.get()
        ts_name = self.ts_var.get()
        if not dataset_name or not ts_name:
            messagebox.showinfo("Add TS to list", "Please select a dataset and TS first.")
            return
        entries = (
            [(dataset_name, ts) for ts in self._ts_names_for_dataset(dataset_name)]
            if ts_name == "All TS"
            else [(dataset_name, ts_name)]
        )
        added = self.add_ts_entries(entries)
        self.app.status_var.set(f"Added {added} TS to the tomogram processing list")

    def _selected_table_indices(self) -> list[int]:
        return sorted((int(item) for item in self.ts_table.selection()), reverse=True)

    def _remove_selected_ts(self) -> None:
        indices = self._selected_table_indices()
        if not indices:
            messagebox.showinfo("Remove TS from list", "Please select one or more TS entries first.")
            return
        for index in indices:
            if 0 <= index < len(self.selected_entries):
                self.selected_entries.pop(index)
        self._persist_selection()
        self._refresh_table()
        self.app.status_var.set("Removed selected TS from the tomogram processing list")

    def _clear_ts_list(self) -> None:
        if not self.selected_entries:
            return
        if not messagebox.askyesno("Clear TS list", "Remove all TS from the tomogram processing list?"):
            return
        self.selected_entries.clear()
        self._persist_selection()
        self._refresh_table()
        self.app.status_var.set("Cleared the tomogram processing list")

    def _open_gallery_selection(self) -> None:
        gallery_tab = self.app.tabs.get("gallery")
        if gallery_tab is None:
            return
        if hasattr(gallery_tab, "prepare_multi_selection"):
            gallery_tab.prepare_multi_selection()
        self.app._show_tab("gallery")
        self.app.status_var.set("Gallery multi selection enabled for TS transfer")

    def _browse_cryolithe_save_dir(self) -> None:
        path = filedialog.askdirectory(title="Select CryoLithe save directory")
        if path:
            self.cryolithe_save_dir_var.set(path)

    def _browse_cryolithe_model_dir(self) -> None:
        path = filedialog.askdirectory(title="Select CryoLithe model directory")
        if path:
            self.cryolithe_model_dir_var.set(path)

    def _browse_pytom_template(self) -> None:
        path = filedialog.askopenfilename(title="Select template MRC file")
        if path:
            self.pytom_template_var.set(path)

    def _browse_pytom_destination(self) -> None:
        path = filedialog.askdirectory(title="Select destination directory")
        if path:
            self.pytom_destination_var.set(path)

    def _browse_pytom_mask(self) -> None:
        path = filedialog.askopenfilename(title="Select mask MRC file")
        if path:
            self.pytom_mask_var.set(path)

    def _browse_pytom_manual_dir(self) -> None:
        path = filedialog.askdirectory(title="Select manual tomogram directory")
        if path:
            self.pytom_manual_dir_var.set(path)

    def _browse_pytom_tomogram_mask(self) -> None:
        path = filedialog.askopenfilename(title="Select tomogram mask")
        if path:
            self.pytom_tomogram_mask_var.set(path)

    def _browse_pytom_dose_accumulation(self) -> None:
        path = filedialog.askopenfilename(title="Select dose accumulation file")
        if path:
            self.pytom_dose_accumulation_var.set(path)

    def _browse_pytom_defocus(self) -> None:
        path = filedialog.askopenfilename(title="Select defocus file")
        if path:
            self.pytom_defocus_var.set(path)

    def _browse_pytom_relion5_star(self) -> None:
        path = filedialog.askopenfilename(title="Select RELION5 tomograms.star")
        if path:
            self.pytom_relion5_star_var.set(path)

    def _browse_extract_tm_output_folder(self) -> None:
        path = filedialog.askdirectory(title="Select PyTom TM output folder")
        if path:
            self.extract_tm_output_folder_var.set(path)
            self._update_extract_preview()

    def _browse_extract_tomogram_mask(self) -> None:
        path = filedialog.askopenfilename(title="Select tomogram mask")
        if path:
            self.extract_tomogram_mask_var.set(path)

    def _browse_slabify_input_directory(self) -> None:
        path = filedialog.askdirectory(title="Select slabify input directory")
        if path:
            self.slabify_input_directory_var.set(path)

    def _browse_slabify_output_directory(self) -> None:
        path = filedialog.askdirectory(title="Select slabify output directory")
        if path:
            self.slabify_output_directory_var.set(path)

    def _browse_slabify_output_masked_directory(self) -> None:
        path = filedialog.askdirectory(title="Select slabify output masked directory")
        if path:
            self.slabify_output_masked_directory_var.set(path)

    def _browse_slabify_points(self) -> None:
        path = filedialog.askopenfilename(title="Select slabify points model")
        if path:
            self.slabify_points_var.set(path)

    def _browse_membrain_input_directory(self) -> None:
        path = filedialog.askdirectory(title="Select MemBrain input directory")
        if path:
            self.membrain_input_directory_var.set(path)

    def _browse_membrain_ckpt_path(self) -> None:
        path = filedialog.askopenfilename(title="Select MemBrain checkpoint")
        if path:
            self.membrain_ckpt_path_var.set(path)

    def _browse_membrain_out_folder(self) -> None:
        path = filedialog.askdirectory(title="Select MemBrain output folder")
        if path:
            self.membrain_out_folder_var.set(path)

    def _selected_job_key(self) -> str:
        definition = self.job_catalog.get(self.job_type_var.get())
        return definition.job_key if definition is not None else ""

    def _on_job_type_changed(self, _event=None) -> None:
        self._scroll_job_view_to_top()
        job_key = self._selected_job_key()
        if job_key == "cryolithe_denoising":
            self.environment_var.set(self._job_environment_default(job_key=job_key))
            self.history_frame.grid_remove()
            self.pytom_frame.grid_remove()
            self.extract_frame.grid_remove()
            self.slabify_frame.grid_remove()
            self.membrain_frame.grid_remove()
            self.cryolithe_frame.grid()
            self._update_cryolithe_preview()
        elif job_key == "pytom_template_matching":
            self.environment_var.set(self._job_environment_default(job_key=job_key))
            self.history_frame.grid_remove()
            self.cryolithe_frame.grid_remove()
            self.extract_frame.grid_remove()
            self.slabify_frame.grid_remove()
            self.membrain_frame.grid_remove()
            self.pytom_frame.grid()
            self._update_pytom_preview()
        elif job_key == "pytom_extract_coordinates":
            self.environment_var.set(self._job_environment_default(job_key=job_key))
            self.history_frame.grid_remove()
            self.cryolithe_frame.grid_remove()
            self.pytom_frame.grid_remove()
            self.slabify_frame.grid_remove()
            self.membrain_frame.grid_remove()
            self.extract_frame.grid()
            self._update_extract_preview()
        elif job_key == "slabify_mask_creation":
            self.environment_var.set(self._job_environment_default(job_key=job_key))
            self.history_frame.grid_remove()
            self.cryolithe_frame.grid_remove()
            self.pytom_frame.grid_remove()
            self.extract_frame.grid_remove()
            self.membrain_frame.grid_remove()
            self.slabify_frame.grid()
            self._update_slabify_preview()
        elif job_key == "membrain_segmentation":
            self.environment_var.set(self._job_environment_default(job_key=job_key))
            self.history_frame.grid_remove()
            self.cryolithe_frame.grid_remove()
            self.pytom_frame.grid_remove()
            self.extract_frame.grid_remove()
            self.slabify_frame.grid_remove()
            self.membrain_frame.grid()
            self._update_membrain_preview()
        elif self.job_type_var.get() == "Job history":
            self.cryolithe_frame.grid_remove()
            self.pytom_frame.grid_remove()
            self.extract_frame.grid_remove()
            self.slabify_frame.grid_remove()
            self.membrain_frame.grid_remove()
            self.history_frame.grid()
            self._refresh_history()
        else:
            self.cryolithe_frame.grid_remove()
            self.pytom_frame.grid_remove()
            self.extract_frame.grid_remove()
            self.slabify_frame.grid_remove()
            self.membrain_frame.grid_remove()
            self.history_frame.grid_remove()
            self.cryolithe_command_text.delete("1.0", "end")
            self.pytom_command_text.delete("1.0", "end")
            self.extract_command_text.delete("1.0", "end")
            self.slabify_command_text.delete("1.0", "end")
            self.membrain_command_text.delete("1.0", "end")

    def _update_active_preview(self) -> None:
        mode = self.job_type_var.get()
        if mode == "PyTom: Template matching":
            self._update_pytom_preview()
        elif mode == "PyTom: Extract coordinates":
            self._update_extract_preview()
        elif mode == "Slabify: Mask creation":
            self._update_slabify_preview()
        elif mode == "MemBrain-seg: Segmentation":
            self._update_membrain_preview()
        else:
            self._update_cryolithe_preview()

    def _quote(self, value: str) -> str:
        return shlex.quote(value)

    def _find_first_matching_file(self, folder: Path, suffixes: tuple[str, ...], token: str) -> Path | None:
        if not folder.exists():
            return None
        matches = [
            item
            for item in folder.iterdir()
            if item.is_file()
            and item.suffix.lower() in suffixes
        ]
        return best_matching_path_for_ts(matches, token)

    def _resolve_cryolithe_files(self, dataset: DatasetRecord, ts_name: str) -> tuple[Path | None, Path | None]:
        proj_file = resolve_dataset_file(self.app.project, dataset, ts_name, "aligned_stack")
        angle_file = resolve_dataset_file(self.app.project, dataset, ts_name, "angle_file")
        return (
            Path(proj_file.path) if proj_file.path else None,
            Path(angle_file.path) if angle_file.path else None,
        )

    def _resolve_default_tomogram_file(self, dataset: DatasetRecord, ts_name: str) -> Path | None:
        resolved = resolve_dataset_file(self.app.project, dataset, ts_name, "tomogram")
        return Path(resolved.path) if resolved.path else None

    def _resolve_manual_tomogram_file(self, ts_name: str) -> Path | None:
        folder_text = self.pytom_manual_dir_var.get().strip()
        if not folder_text:
            return None
        return self._find_first_matching_file(Path(folder_text), (".mrc",), ts_name)

    def _manual_mrc_files(self, folder_text: str) -> tuple[list[Path], list[str]]:
        if not folder_text:
            return [], ["Input directory is missing."]
        folder = Path(folder_text)
        if not folder.exists():
            return [], ["Input directory not found."]
        input_files = sorted(
            [item for item in folder.iterdir() if item.is_file() and item.suffix.lower() == ".mrc"],
            key=lambda item: item.name.casefold(),
        )
        if not input_files:
            return [], ["No .mrc files found in the input directory."]
        return input_files, []

    def _resolve_tilt_angles_file(self, dataset: DatasetRecord, ts_name: str) -> Path | None:
        resolved = resolve_dataset_file(self.app.project, dataset, ts_name, "angle_file")
        return Path(resolved.path) if resolved.path else None

    def _resolve_ts_xml_file(self, dataset: DatasetRecord, ts_name: str) -> Path | None:
        resolved = resolve_dataset_file(self.app.project, dataset, ts_name, "ts_xml")
        return Path(resolved.path) if resolved.path else None

    def _matching_mask_message(self, folder_text: str, ts_names: list[str]) -> tuple[str, dict[str, Path]]:
        if not folder_text:
            return f"0/{len(ts_names)} tomogram masks found.", {}
        folder = Path(folder_text)
        if not folder.exists():
            return f"0/{len(ts_names)} tomogram masks found.", {}
        mapping: dict[str, Path] = {}
        found = 0
        for ts_name in ts_names:
            matches = [
                item
                for item in folder.iterdir()
                if item.is_file()
                and item.suffix.lower() == ".mrc"
            ]
            top_matches = best_matching_paths_for_ts(matches, ts_name)
            if not top_matches:
                continue
            if len(top_matches) > 1:
                return "Warning: Non-unique naming", {}
            mapping[ts_name] = top_matches[0]
            found += 1
        return f"{found}/{len(ts_names)} tomogram masks found.", mapping

    def _cryolithe_specs(self) -> tuple[list[tuple[DatasetRecord, dict[str, str], str]], list[str]]:
        commands: list[tuple[DatasetRecord, dict[str, str], str]] = []
        errors: list[str] = []
        save_dir = self.cryolithe_save_dir_var.get().strip()
        if not save_dir:
            errors.append("Save directory is missing.")
            return commands, errors
        for entry in self.selected_entries:
            dataset = self._dataset_map().get(entry["dataset_name"])
            if dataset is None:
                errors.append(f"{entry['dataset_name']} | {entry['ts_name']}: dataset not found")
                continue
            proj_file, angle_file = self._resolve_cryolithe_files(dataset, entry["ts_name"])
            if proj_file is None:
                errors.append(f"{entry['dataset_name']} | {entry['ts_name']}: projection file not found")
                continue
            if angle_file is None:
                errors.append(f"{entry['dataset_name']} | {entry['ts_name']}: angle file not found")
                continue
            spec = {
                "job_name": "CryoLithe: Denoising",
                "ts_name": entry["ts_name"],
                "model_dir": self.cryolithe_model_dir_var.get().strip(),
                "save_dir": save_dir,
                "device": self.cryolithe_device_var.get().strip() or "0",
                "n3": self.cryolithe_n3_var.get().strip() or "256",
                "batch_size": self.cryolithe_batch_size_var.get().strip() or "50000",
                "proj_file": str(proj_file),
                "angle_file": str(angle_file),
                "save_name": f"{entry['ts_name']}_CryoLithe.mrc",
            }
            command = " ".join(
                [
                    "cryolithe reconstruct",
                    *(
                        [f"--model-dir {self._quote(spec['model_dir'])}"]
                        if spec["model_dir"]
                        else []
                    ),
                    f"--proj-file {self._quote(spec['proj_file'])}",
                    f"--angle-file {self._quote(spec['angle_file'])}",
                    f"--save-dir {self._quote(spec['save_dir'])}",
                    f"--save-name {self._quote(spec['save_name'])}",
                    f"--device {self._quote(spec['device'])}",
                    f"--n3 {self._quote(spec['n3'])}",
                    f"--batch-size {self._quote(spec['batch_size'])}",
                ]
            )
            commands.append((dataset, spec, command))
        return commands, errors

    def _pytom_specs(self) -> tuple[list[tuple[DatasetRecord, dict[str, str], str]], list[str]]:
        commands: list[tuple[DatasetRecord, dict[str, str], str]] = []
        errors: list[str] = []
        template = self.pytom_template_var.get().strip()
        destination = self.pytom_destination_var.get().strip()
        mask = self.pytom_mask_var.get().strip()
        if not template:
            errors.append("Template is missing.")
        if not destination:
            errors.append("Destination is missing.")
        if not mask:
            errors.append("Mask is missing.")
        if errors:
            return commands, errors

        ts_names = [entry["ts_name"] for entry in self.selected_entries]
        mask_message, mask_map = self._matching_mask_message(self.pytom_tomogram_mask_var.get().strip(), ts_names)
        self.pytom_found_masks_var.set(mask_message)
        if mask_message == "Warning: Non-unique naming":
            errors.append(mask_message)
            return commands, errors

        missing_manual = False
        for entry in self.selected_entries:
            dataset = self._dataset_map().get(entry["dataset_name"])
            if dataset is None:
                errors.append(f"{entry['dataset_name']} | {entry['ts_name']}: dataset not found")
                continue
            tomogram = (
                self._resolve_manual_tomogram_file(entry["ts_name"])
                if self.pytom_manual_input_var.get()
                else self._resolve_default_tomogram_file(dataset, entry["ts_name"])
            )
            if tomogram is None:
                if self.pytom_manual_input_var.get():
                    missing_manual = True
                errors.append(f"{entry['dataset_name']} | {entry['ts_name']}: tomogram file not found")
                continue
            tilt_angles = self._resolve_tilt_angles_file(dataset, entry["ts_name"])
            if tilt_angles is None:
                errors.append(f"{entry['dataset_name']} | {entry['ts_name']}: tilt-angle file not found")
                continue
            warp_xml_path: Path | None = None
            if self.pytom_warp_xml_var.get():
                warp_xml_path = self._resolve_ts_xml_file(dataset, entry["ts_name"])
                if warp_xml_path is None:
                    errors.append(f"{entry['dataset_name']} | {entry['ts_name']}: Warp XML file not found")
                    continue
            spec = {
                "job_name": "PyTom: Template matching",
                "ts_name": entry["ts_name"],
                "template": template,
                "tomogram": str(tomogram),
                "destination": destination,
                "mask": mask,
                "tilt_angles": str(tilt_angles),
            }
            command_parts = [
                "pytom_match_template.py",
                f"-t {self._quote(template)}",
                f"-v {self._quote(spec['tomogram'])}",
                f"-d {self._quote(destination)}",
                f"-m {self._quote(mask)}",
                f"-a {self._quote(spec['tilt_angles'])}",
                f"-g {self._quote(self.pytom_gpu_ids_var.get().strip() or '0')}",
            ]
            optional_pairs = [
                ("--particle-diameter", self.pytom_particle_diameter_var.get().strip()),
                ("--angular-search", self.pytom_angular_search_var.get().strip()),
                ("--z-axis-rotational-symmetry", self.pytom_z_axis_symmetry_var.get().strip()),
                ("-s", self.pytom_volume_split_var.get().strip()),
                ("--search-x", self.pytom_search_x_var.get().strip()),
                ("--search-y", self.pytom_search_y_var.get().strip()),
                ("--search-z", self.pytom_search_z_var.get().strip()),
                ("--voxel-size-angstrom", self.pytom_voxel_size_var.get().strip()),
                ("--low-pass", self.pytom_low_pass_var.get().strip()),
                ("--high-pass", self.pytom_high_pass_var.get().strip()),
                ("--amplitude-contrast", self.pytom_amplitude_contrast_var.get().strip()),
                ("--spherical-aberration", self.pytom_spherical_aberration_var.get().strip()),
                ("--voltage", self.pytom_voltage_var.get().strip()),
                ("--phase-shift", self.pytom_phase_shift_var.get().strip()),
                ("--defocus-handedness", self.pytom_defocus_handedness_var.get().strip()),
                ("--rng-seed", self.pytom_rng_seed_var.get().strip()),
                ("--log", self.pytom_log_var.get().strip()),
            ]
            for flag, value in optional_pairs:
                if value:
                    if flag in {"-s", "--search-x", "--search-y", "--search-z"}:
                        command_parts.append(f"{flag} {value}")
                    else:
                        command_parts.append(f"{flag} {self._quote(value)}")
            optional_paths = [
                ("--tomogram-mask", str(mask_map.get(entry["ts_name"], ""))),
                ("--dose-accumulation", self.pytom_dose_accumulation_var.get().strip()),
                ("--defocus", self.pytom_defocus_var.get().strip()),
                ("--relion5-tomograms-star", self.pytom_relion5_star_var.get().strip()),
                ("--warp-xml-file", str(warp_xml_path) if warp_xml_path is not None else ""),
            ]
            for flag, value in optional_paths:
                if value:
                    command_parts.append(f"{flag} {self._quote(value)}")
            if self.pytom_ctf_model_var.get().strip():
                command_parts.append(f"--tomogram-ctf-model {self._quote(self.pytom_ctf_model_var.get().strip())}")
            bool_flags = [
                ("--non-spherical-mask", self.pytom_non_spherical_mask_var.get()),
                ("--per-tilt-weighting", self.pytom_per_tilt_weighting_var.get()),
                ("--spectral-whitening", self.pytom_spectral_whitening_var.get()),
                ("-r", self.pytom_random_phase_correction_var.get()),
                ("--half-precision", self.pytom_half_precision_var.get()),
            ]
            for flag, enabled in bool_flags:
                if enabled:
                    command_parts.append(flag)
            command = " ".join(command_parts)
            full_spec = {
                **spec,
                "manual_input": "true" if self.pytom_manual_input_var.get() else "",
                "manual_dir": self.pytom_manual_dir_var.get().strip(),
                "particle_diameter": self.pytom_particle_diameter_var.get().strip(),
                "angular_search": self.pytom_angular_search_var.get().strip(),
                "z_axis_rotational_symmetry": self.pytom_z_axis_symmetry_var.get().strip(),
                "volume_split": self.pytom_volume_split_var.get().strip(),
                "search_x": self.pytom_search_x_var.get().strip(),
                "search_y": self.pytom_search_y_var.get().strip(),
                "search_z": self.pytom_search_z_var.get().strip(),
                "tomogram_mask": str(mask_map.get(entry["ts_name"], "")),
                "per_tilt_weighting": "true" if self.pytom_per_tilt_weighting_var.get() else "",
                "voxel_size_angstrom": self.pytom_voxel_size_var.get().strip(),
                "low_pass": self.pytom_low_pass_var.get().strip(),
                "high_pass": self.pytom_high_pass_var.get().strip(),
                "dose_accumulation": self.pytom_dose_accumulation_var.get().strip(),
                "defocus": self.pytom_defocus_var.get().strip(),
                "amplitude_contrast": self.pytom_amplitude_contrast_var.get().strip(),
                "spherical_aberration": self.pytom_spherical_aberration_var.get().strip(),
                "voltage": self.pytom_voltage_var.get().strip(),
                "phase_shift": self.pytom_phase_shift_var.get().strip(),
                "tomogram_ctf_model": self.pytom_ctf_model_var.get().strip(),
                "defocus_handedness": self.pytom_defocus_handedness_var.get().strip(),
                "spectral_whitening": "true" if self.pytom_spectral_whitening_var.get() else "",
                "random_phase_correction": "true" if self.pytom_random_phase_correction_var.get() else "",
                "half_precision": "true" if self.pytom_half_precision_var.get() else "",
                "rng_seed": self.pytom_rng_seed_var.get().strip(),
                "relion5_tomograms_star": self.pytom_relion5_star_var.get().strip(),
                "warp_xml_file": "true" if self.pytom_warp_xml_var.get() else "",
                "gpu_ids": self.pytom_gpu_ids_var.get().strip() or "0",
                "log": self.pytom_log_var.get().strip(),
            }
            commands.append((dataset, full_spec, command))

        if self.pytom_manual_input_var.get() and missing_manual:
            errors.append("Not all TS found in the given directory.")
        return commands, errors

    def _extract_job_files(self) -> list[Path]:
        folder_text = self.extract_tm_output_folder_var.get().strip()
        if not folder_text:
            self.extract_found_jobs_var.set("0 finished TM jobs found.")
            return []
        folder = Path(folder_text)
        if not folder.exists():
            self.extract_found_jobs_var.set("0 finished TM jobs found.")
            return []
        files = sorted(folder.glob("*_job.json"), key=lambda item: item.name.casefold())
        self.extract_found_jobs_var.set(f"{len(files)} finished TM jobs found.")
        return files

    def _extract_specs(self) -> tuple[list[tuple[DatasetRecord | None, dict[str, str], str]], list[str]]:
        commands: list[tuple[DatasetRecord | None, dict[str, str], str]] = []
        errors: list[str] = []
        job_files = self._extract_job_files()
        if not job_files:
            errors.append("No finished TM jobs found.")
            return commands, errors
        number_of_particles = self.extract_number_of_particles_var.get().strip()
        if not number_of_particles:
            errors.append("Number of particles is missing.")
            return commands, errors
        ts_names = [job_file.stem.removesuffix("_job") for job_file in job_files]
        mask_message, mask_map = self._matching_mask_message(self.extract_tomogram_mask_var.get().strip(), ts_names)
        self.extract_found_masks_var.set(mask_message)
        if mask_message == "Warning: Non-unique naming":
            errors.append(mask_message)
            return commands, errors
        for job_file in job_files:
            ts_name = job_file.stem.removesuffix("_job")
            dataset = find_dataset_for_ts_name(self.app.project, ts_name)
            spec = {
                "job_name": "PyTom: Extract coordinates",
                "ts_name": ts_name,
                "job_file": str(job_file),
                "tm_output_folder": self.extract_tm_output_folder_var.get().strip(),
                "number_of_particles": number_of_particles,
                "number_of_false_positives": self.extract_number_of_false_positives_var.get().strip(),
                "particle_diameter": self.extract_particle_diameter_var.get().strip(),
                "cut_off": self.extract_cut_off_var.get().strip(),
                "tomogram_mask": str(mask_map.get(ts_name, "")),
                "ignore_tomogram_mask": "true" if self.extract_ignore_tomogram_mask_var.get() else "",
                "tophat_filter": "true" if self.extract_tophat_filter_var.get() else "",
                "tophat_connectivity": self.extract_tophat_connectivity_var.get().strip(),
                "relion5_compat": "true" if self.extract_relion5_compat_var.get() else "",
                "log": self.extract_log_var.get().strip(),
                "tophat_bins": self.extract_tophat_bins_var.get().strip(),
                "plot_bins": self.extract_plot_bins_var.get().strip(),
            }
            command_parts = [
                "pytom_extract_candidates.py",
                f"-j {self._quote(spec['job_file'])}",
                f"-n {self._quote(spec['number_of_particles'])}",
            ]
            if spec["tomogram_mask"]:
                command_parts.append(f"--tomogram-mask {self._quote(spec['tomogram_mask'])}")
            if spec["ignore_tomogram_mask"]:
                command_parts.append("--ignore_tomogram_mask")
            if spec["number_of_false_positives"]:
                command_parts.append(
                    f"--number-of-false-positives {self._quote(spec['number_of_false_positives'])}"
                )
            if spec["particle_diameter"]:
                command_parts.append(f"--particle-diameter {self._quote(spec['particle_diameter'])}")
            if spec["cut_off"]:
                command_parts.append(f"-c {self._quote(spec['cut_off'])}")
            if spec["tophat_filter"]:
                command_parts.append("--tophat-filter")
            if spec["tophat_connectivity"]:
                command_parts.append(f"--tophat-connectivity {self._quote(spec['tophat_connectivity'])}")
            if spec["relion5_compat"]:
                command_parts.append("--relion5-compat")
            if spec["log"]:
                command_parts.append(f"--log {self._quote(spec['log'])}")
            if spec["tophat_bins"]:
                command_parts.append(f"--tophat-bins {self._quote(spec['tophat_bins'])}")
            if spec["plot_bins"]:
                command_parts.append(f"--plot-bins {self._quote(spec['plot_bins'])}")
            commands.append((dataset, spec, " ".join(command_parts)))
        return commands, errors

    def _slabify_specs(self) -> tuple[list[tuple[DatasetRecord | None, dict[str, str], str]], list[str]]:
        commands: list[tuple[DatasetRecord | None, dict[str, str], str]] = []
        errors: list[str] = []
        output_directory = self.slabify_output_directory_var.get().strip()
        output_masked_directory = self.slabify_output_masked_directory_var.get().strip()
        if not output_directory:
            errors.append("Output directory is missing.")
            return commands, errors

        def enrich_spec(
            base_spec: dict[str, str],
            input_stem: str,
            dataset: DatasetRecord | None,
        ) -> tuple[dict[str, str], str]:
            output_masked = ""
            if output_masked_directory:
                output_masked = str(Path(output_masked_directory) / f"{input_stem}_slabmasked.mrc")
            angpix = self.slabify_angpix_var.get().strip()
            if not angpix and dataset is not None and dataset.pixel_size:
                angpix = str(dataset.pixel_size)
            full_spec = {
                **base_spec,
                "output_masked_directory": output_masked_directory,
                "output_masked": output_masked,
                "border": self.slabify_border_var.get().strip(),
                "offset": self.slabify_offset_var.get().strip(),
                "angpix": angpix,
                "measure": "true" if self.slabify_measure_var.get() else "",
                "points": self.slabify_points_var.get().strip(),
                "n_samples": self.slabify_n_samples_var.get().strip(),
                "boxsize": self.slabify_boxsize_var.get().strip(),
                "z_min": self.slabify_z_min_var.get().strip(),
                "z_max": self.slabify_z_max_var.get().strip(),
                "iterations": self.slabify_iterations_var.get().strip(),
                "simple": "true" if self.slabify_simple_var.get() else "",
                "thickness": self.slabify_thickness_var.get().strip(),
                "percentile": self.slabify_percentile_var.get().strip(),
                "seed": self.slabify_seed_var.get().strip(),
            }
            command_parts = [
                "slabify",
                f"--input {self._quote(full_spec['input'])}",
                f"--output {self._quote(full_spec['output'])}",
            ]
            optional_pairs = [
                ("--output-masked", full_spec["output_masked"]),
                ("--border", full_spec["border"]),
                ("--offset", full_spec["offset"]),
                ("--angpix", full_spec["angpix"]),
                ("--points", full_spec["points"]),
                ("--n-samples", full_spec["n_samples"]),
                ("--boxsize", full_spec["boxsize"]),
                ("--z-min", full_spec["z_min"]),
                ("--z-max", full_spec["z_max"]),
                ("--iterations", full_spec["iterations"]),
                ("--thickness", full_spec["thickness"]),
                ("--percentile", full_spec["percentile"]),
                ("--seed", full_spec["seed"]),
            ]
            for flag, value in optional_pairs:
                if value:
                    command_parts.append(f"{flag} {self._quote(value)}")
            if self.slabify_measure_var.get():
                command_parts.append("--measure")
            if self.slabify_simple_var.get():
                command_parts.append("--simple")
            return full_spec, " ".join(command_parts)

        if self.slabify_manual_input_var.get():
            input_directory = self.slabify_input_directory_var.get().strip()
            if not input_directory:
                errors.append("Input directory is missing.")
                return commands, errors
            folder = Path(input_directory)
            if not folder.exists():
                errors.append("Input directory not found.")
                return commands, errors
            input_files = sorted(
                [item for item in folder.iterdir() if item.is_file() and item.suffix.lower() == ".mrc"],
                key=lambda item: item.name.casefold(),
            )
            for item in input_files:
                ts_name = item.stem
                matched_dataset = next(
                    (
                        dataset
                        for dataset in self.app.project.datasets
                        if any(entry["dataset_name"] == dataset.dataset_name for entry in self.selected_entries)
                        and any(ts_name.casefold() == entry["ts_name"].casefold() for entry in self.selected_entries)
                    ),
                    None,
                )
                save_name = f"{item.stem}_slabmask.mrc"
                spec = {
                    "job_name": "Slabify: Mask creation",
                    "ts_name": ts_name,
                    "input": str(item),
                    "output_directory": output_directory,
                    "output": str(Path(output_directory) / save_name),
                    "manual_input": "true",
                }
                full_spec, command = enrich_spec(spec, item.stem, matched_dataset)
                commands.append((matched_dataset, full_spec, command))
            if not input_files:
                errors.append("No .mrc files found in the input directory.")
            return commands, errors

        for entry in self.selected_entries:
            dataset = self._dataset_map().get(entry["dataset_name"])
            if dataset is None:
                errors.append(f"{entry['dataset_name']} | {entry['ts_name']}: dataset not found")
                continue
            tomogram = self._resolve_default_tomogram_file(dataset, entry["ts_name"])
            if tomogram is None:
                errors.append(f"{entry['dataset_name']} | {entry['ts_name']}: tomogram file not found")
                continue
            save_name = f"{entry['ts_name']}_slabmask.mrc"
            spec = {
                "job_name": "Slabify: Mask creation",
                "ts_name": entry["ts_name"],
                "input": str(tomogram),
                "output_directory": output_directory,
                "output": str(Path(output_directory) / save_name),
            }
            full_spec, command = enrich_spec(spec, entry["ts_name"], dataset)
            commands.append((dataset, full_spec, command))
        return commands, errors

    def _membrain_specs(self) -> tuple[list[tuple[DatasetRecord | None, dict[str, str], str]], list[str]]:
        commands: list[tuple[DatasetRecord | None, dict[str, str], str]] = []
        errors: list[str] = []
        ckpt_path = self.membrain_ckpt_path_var.get().strip()
        if not ckpt_path:
            errors.append("Checkpoint path is missing.")
            return commands, errors

        def build_spec(
            input_file: Path,
            dataset: DatasetRecord | None,
            ts_name: str,
            manual_input: bool,
        ) -> tuple[dict[str, str], str]:
            in_pixel_size = self.membrain_in_pixel_size_var.get().strip()
            if not in_pixel_size and dataset is not None and dataset.pixel_size:
                in_pixel_size = str(dataset.pixel_size)
            spec = {
                "job_name": "MemBrain-seg: Segmentation",
                "ts_name": ts_name,
                "tomogram_path": str(input_file),
                "ckpt_path": ckpt_path,
                "out_folder": self.membrain_out_folder_var.get().strip(),
                "manual_input": "true" if manual_input else "",
                "rescale_patches": "true" if self.membrain_rescale_patches_var.get() else "",
                "in_pixel_size": in_pixel_size,
                "out_pixel_size": self.membrain_out_pixel_size_var.get().strip(),
                "store_probabilities": "true" if self.membrain_store_probabilities_var.get() else "",
                "store_connected_components": "true" if self.membrain_store_connected_components_var.get() else "",
                "connected_component_threshold": self.membrain_connected_component_threshold_var.get().strip(),
                "test_time_augmentation": "true" if self.membrain_test_time_augmentation_var.get() else "false",
                "segmentation_threshold": self.membrain_segmentation_threshold_var.get().strip(),
                "sliding_window_size": self.membrain_sliding_window_size_var.get().strip(),
            }
            command_parts = [
                "membrain segment",
                f"--tomogram-path {self._quote(spec['tomogram_path'])}",
                f"--ckpt-path {self._quote(spec['ckpt_path'])}",
            ]
            if spec["out_folder"]:
                command_parts.append(f"--out-folder {self._quote(spec['out_folder'])}")
            if spec["rescale_patches"]:
                command_parts.append("--rescale-patches")
            if spec["in_pixel_size"]:
                command_parts.append(f"--in-pixel-size {self._quote(spec['in_pixel_size'])}")
            if spec["out_pixel_size"]:
                command_parts.append(f"--out-pixel-size {self._quote(spec['out_pixel_size'])}")
            if spec["store_probabilities"]:
                command_parts.append("--store-probabilities")
            if spec["store_connected_components"]:
                command_parts.append("--store-connected-components")
            if spec["connected_component_threshold"]:
                command_parts.append(
                    f"--connected-component-threshold {self._quote(spec['connected_component_threshold'])}"
                )
            command_parts.append(
                "--test-time-augmentation" if self.membrain_test_time_augmentation_var.get() else "--no-test-time-augmentation"
            )
            if spec["segmentation_threshold"]:
                command_parts.append(f"--segmentation-threshold {self._quote(spec['segmentation_threshold'])}")
            if spec["sliding_window_size"]:
                command_parts.append(f"--sliding-window-size {self._quote(spec['sliding_window_size'])}")
            return spec, " ".join(command_parts)

        if self.membrain_manual_input_var.get():
            input_files, manual_errors = self._manual_mrc_files(self.membrain_input_directory_var.get().strip())
            if manual_errors:
                return commands, manual_errors
            for item in input_files:
                matched_dataset = next(
                    (
                        dataset
                        for dataset in self.app.project.datasets
                        if any(entry["dataset_name"] == dataset.dataset_name for entry in self.selected_entries)
                        and any(item.stem.casefold() == entry["ts_name"].casefold() for entry in self.selected_entries)
                    ),
                    None,
                )
                spec, command = build_spec(item, matched_dataset, item.stem, True)
                commands.append((matched_dataset, spec, command))
            return commands, errors

        if not self.selected_entries:
            errors.append("Please add at least one TS to the list.")
            return commands, errors
        for entry in self.selected_entries:
            dataset = self._dataset_map().get(entry["dataset_name"])
            if dataset is None:
                errors.append(f"{entry['dataset_name']} | {entry['ts_name']}: dataset not found")
                continue
            tomogram = self._resolve_default_tomogram_file(dataset, entry["ts_name"])
            if tomogram is None:
                errors.append(f"{entry['dataset_name']} | {entry['ts_name']}: tomogram file not found")
                continue
            spec, command = build_spec(tomogram, dataset, entry["ts_name"], False)
            commands.append((dataset, spec, command))
        return commands, errors

    def _update_cryolithe_preview(self) -> None:
        commands, errors = self._cryolithe_specs()
        lines = [command for _dataset, _spec, command in commands]
        lines.extend(f"# {error}" for error in errors)
        self.cryolithe_command_text.delete("1.0", "end")
        self.cryolithe_command_text.insert("1.0", "\n".join(lines))

    def _update_pytom_preview(self) -> None:
        commands, errors = self._pytom_specs()
        lines = [command for _dataset, _spec, command in commands]
        lines.extend(f"# {error}" for error in errors)
        self.pytom_command_text.delete("1.0", "end")
        self.pytom_command_text.insert("1.0", "\n".join(lines))

    def _update_extract_preview(self) -> None:
        commands, errors = self._extract_specs()
        lines = [command for _dataset, _spec, command in commands]
        lines.extend(f"# {error}" for error in errors)
        self.extract_command_text.delete("1.0", "end")
        self.extract_command_text.insert("1.0", "\n".join(lines))

    def _update_slabify_preview(self) -> None:
        commands, errors = self._slabify_specs()
        lines = [command for _dataset, _spec, command in commands]
        lines.extend(f"# {error}" for error in errors)
        self.slabify_command_text.delete("1.0", "end")
        self.slabify_command_text.insert("1.0", "\n".join(lines))

    def _update_membrain_preview(self) -> None:
        commands, errors = self._membrain_specs()
        lines = [command for _dataset, _spec, command in commands]
        lines.extend(f"# {error}" for error in errors)
        self.membrain_command_text.delete("1.0", "end")
        self.membrain_command_text.insert("1.0", "\n".join(lines))

    def _record_job_history(
        self,
        dataset: DatasetRecord,
        spec: dict[str, str],
        action: str,
        command: str,
        scheduled: bool = False,
    ) -> JobHistoryEntry:
        dataset.job_history.append(
            JobHistoryEntry(
                timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                action=action,
                group="Tomograms",
                job_name=spec["job_name"],
                command=command,
                processing_tab="Processing: TS jobs",
                dataset_name=dataset.dataset_name,
                execution_mode="slurm" if self.execution_mode_var.get() == "Submit to Slurm" else "local",
                slurm_profile=self.slurm_profile_var.get().strip(),
                environment_title=self.environment_var.get().strip() if self.execution_mode_var.get() == "Run locally" else "",
                parameters={key: value for key, value in spec.items() if value},
            )
        )
        if self.execution_mode_var.get() == "Run locally" and self.environment_var.get().strip():
            dataset.job_history[-1].parameters["execution_environment"] = self.environment_var.get().strip()
        dataset.job_history[-1].parameters.update(self._current_slurm_overrides())
        return dataset.job_history[-1]

    def _preview_commands_from_widget(
        self,
        widget: tk.Text,
        job_name: str,
    ) -> list[tuple[DatasetRecord | None, dict[str, str], str]]:
        lines = [
            line.strip()
            for line in widget.get("1.0", "end").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if not lines:
            return []
        self.app.debug_log(
            "WARN",
            f"No concrete {job_name} commands resolved; simulating the current command preview instead.",
        )
        return [
            (
                None,
                {"job_name": job_name, "ts_name": "Debug preview"},
                line,
            )
            for line in lines
        ]

    def _debug_prepare_commands(
        self,
        commands: list[tuple[DatasetRecord | None, dict[str, str], str]],
        errors: list[str],
        *,
        preview_widget: tk.Text,
        empty_title: str,
        empty_message: str,
        job_name: str,
    ) -> list[tuple[DatasetRecord | None, dict[str, str], str]]:
        if not self.app.is_debug_mode_enabled():
            if errors:
                messagebox.showerror("Cannot run commands", "\n".join(errors))
                return []
            if not commands:
                messagebox.showinfo(empty_title, empty_message)
                return []
            return commands

        if errors:
            self.app.debug_log(
                "WARN",
                f"Ignoring {job_name} resolution errors in Debug mode: " + "; ".join(errors),
            )
        if commands:
            return commands
        return self._preview_commands_from_widget(preview_widget, job_name)

    def _current_slurm_overrides(self) -> dict[str, str]:
        return self.slurm_overrides_ui.metadata()

    def _slurm_override_payload(self, parameters: dict[str, str]) -> dict[str, str]:
        return slurm_override_payload(parameters)

    def _combined_slurm_command(
        self,
        commands: list[tuple[DatasetRecord | None, dict[str, str], str]],
        *,
        fallback_cwd: str | None = None,
    ) -> tuple[str, str | None, str]:
        lines: list[str] = []
        last_cwd: str | None = None
        dataset_names: list[str] = []
        for dataset, spec, command in commands:
            dataset_name = dataset.dataset_name if dataset is not None else ""
            if dataset_name:
                dataset_names.append(dataset_name)
            cwd = (
                spec.get("destination")
                or spec.get("save_dir")
                or spec.get("tm_output_folder")
                or spec.get("out_folder")
                or spec.get("output_directory")
                or (dataset.processing_folder if dataset is not None else "")
                or fallback_cwd
                or ""
            ).strip()
            if cwd and cwd != last_cwd:
                lines.append(f"cd {shlex.quote(cwd)}")
                last_cwd = cwd
            lines.append(command)
        dataset_label = dataset_names[0] if len(set(dataset_names)) == 1 and dataset_names else "multiple_datasets"
        return "\n".join(lines), None, dataset_label

    def _mark_command_sequence_submissions(
        self,
        commands: list[tuple[DatasetRecord | None, dict[str, str], str]],
        result: SlurmSubmissionResult,
        profile_name: str,
    ) -> None:
        used_entry_ids: set[int] = set()
        for dataset, spec, _command in commands:
            if dataset is None:
                continue
            target_job_name = spec.get("job_name", "")
            target_ts_name = spec.get("ts_name", "")
            for entry in reversed(dataset.job_history):
                if id(entry) in used_entry_ids:
                    continue
                if entry.group != "Tomograms":
                    continue
                if target_job_name and entry.job_name != target_job_name:
                    continue
                if target_ts_name and entry.parameters.get("ts_name", "") != target_ts_name:
                    continue
                entry.action = "submitted"
                entry.execution_mode = "slurm"
                entry.slurm_profile = profile_name
                entry.slurm_job_id = result.job_id
                entry.slurm_script_path = result.script_path
                used_entry_ids.add(id(entry))
                break

    def _toggle_slurm_controls(self) -> None:
        use_slurm = self.execution_mode_var.get() == "Submit to Slurm"
        for combo in list(self.slurm_profile_combos):
            combo.config(state="readonly" if use_slurm else "disabled")
            if use_slurm:
                combo.grid()
            else:
                combo.grid_remove()
        for combo in list(self.environment_combos):
            combo.config(state="disabled" if use_slurm else "readonly")
            if use_slurm:
                combo.grid_remove()
            else:
                combo.grid()
        for label in list(self.execution_target_labels):
            label.configure(text="Slurm profile" if use_slurm else "Select environment")
        for frame in list(self.slurm_override_frames):
            if use_slurm:
                frame.grid()
            else:
                frame.grid_remove()
        if use_slurm:
            self.slurm_overrides_ui.rebuild()

    def _refresh_slurm_profiles(self) -> None:
        profiles = self.app.slurm_profile_names()
        for combo in self.slurm_profile_combos:
            combo.configure(values=profiles)
        available_environments = environment_titles(self.app.project)
        for combo in self.environment_combos:
            combo.configure(values=available_environments)
        if self.slurm_profile_var.get() and self.slurm_profile_var.get() not in profiles:
            self.slurm_profile_var.set("")
        if self.environment_var.get() not in set(available_environments):
            self.environment_var.set("None")
        self.slurm_overrides_ui.rebuild(preserve_existing=False)
        self._toggle_slurm_controls()

    def _selected_entries_snapshot(self) -> str:
        return json.dumps(self.selected_entries)

    def _current_job_dataset_name(self) -> str:
        datasets = sorted({entry["dataset_name"] for entry in self.selected_entries if entry.get("dataset_name")})
        if len(datasets) == 1:
            return datasets[0]
        if len(datasets) > 1:
            return "Multiple datasets"
        return "All datasets"

    def _schedule_deferred_tomogram_job(self, job_name: str, extra_parameters: dict[str, str]) -> None:
        dataset_name = self._current_job_dataset_name()
        pseudo_dataset = self._dataset_map().get(dataset_name) or next(iter(self._dataset_map().values()), None)
        if pseudo_dataset is None:
            messagebox.showinfo("Schedule command", "Please add at least one dataset to the project first.")
            return
        command_preview = "Resolved at runtime"
        if self.app.is_debug_mode_enabled():
            preview_widget = self._preview_widget_for_job(job_name)
            if preview_widget is not None:
                preview_commands = self._preview_commands_from_widget(preview_widget, job_name)
                if preview_commands:
                    command_preview = "\n".join(command for _dataset, _spec, command in preview_commands)
        spec = {
            "job_name": job_name,
            "schedule_mode": "deferred",
            "selected_entries": self._selected_entries_snapshot(),
            "ts_name": f"{len(self.selected_entries)} TS selected" if self.selected_entries else "Deferred selection",
            **extra_parameters,
        }
        self._record_job_history(pseudo_dataset, spec, "scheduled", command_preview, scheduled=True)
        pseudo_dataset.job_history[-1].dataset_name = dataset_name

    def _preview_widget_for_job(self, job_name: str) -> tk.Text | None:
        mapping: dict[str, tk.Text] = {
            "CryoLithe: Denoising": self.cryolithe_command_text,
            "PyTom: Template matching": self.pytom_command_text,
            "PyTom: Extract coordinates": self.extract_command_text,
            "Slabify: Mask creation": self.slabify_command_text,
            "MemBrain-seg: Segmentation": self.membrain_command_text,
        }
        return mapping.get(job_name)

    def _bool_from_parameter(self, value: str, default: bool = False) -> bool:
        if value == "":
            return default
        return value.lower() in {"1", "true", "yes", "on"}

    def _capture_widget_state(self) -> dict[str, object]:
        return {
            "selected_entries": [dict(item) for item in self.selected_entries],
            "cryolithe_model_dir": self.cryolithe_model_dir_var.get(),
            "cryolithe_save_dir": self.cryolithe_save_dir_var.get(),
            "cryolithe_device": self.cryolithe_device_var.get(),
            "cryolithe_n3": self.cryolithe_n3_var.get(),
            "cryolithe_batch_size": self.cryolithe_batch_size_var.get(),
            "pytom_template": self.pytom_template_var.get(),
            "pytom_destination": self.pytom_destination_var.get(),
            "pytom_mask": self.pytom_mask_var.get(),
            "pytom_manual_input": self.pytom_manual_input_var.get(),
            "pytom_manual_dir": self.pytom_manual_dir_var.get(),
            "pytom_non_spherical_mask": self.pytom_non_spherical_mask_var.get(),
            "pytom_particle_diameter": self.pytom_particle_diameter_var.get(),
            "pytom_angular_search": self.pytom_angular_search_var.get(),
            "pytom_z_axis_symmetry": self.pytom_z_axis_symmetry_var.get(),
            "pytom_volume_split": self.pytom_volume_split_var.get(),
            "pytom_search_x": self.pytom_search_x_var.get(),
            "pytom_search_y": self.pytom_search_y_var.get(),
            "pytom_search_z": self.pytom_search_z_var.get(),
            "pytom_tomogram_mask": self.pytom_tomogram_mask_var.get(),
            "pytom_per_tilt_weighting": self.pytom_per_tilt_weighting_var.get(),
            "pytom_voxel_size": self.pytom_voxel_size_var.get(),
            "pytom_low_pass": self.pytom_low_pass_var.get(),
            "pytom_high_pass": self.pytom_high_pass_var.get(),
            "pytom_dose_accumulation": self.pytom_dose_accumulation_var.get(),
            "pytom_defocus": self.pytom_defocus_var.get(),
            "pytom_amplitude_contrast": self.pytom_amplitude_contrast_var.get(),
            "pytom_spherical_aberration": self.pytom_spherical_aberration_var.get(),
            "pytom_voltage": self.pytom_voltage_var.get(),
            "pytom_phase_shift": self.pytom_phase_shift_var.get(),
            "pytom_ctf_model": self.pytom_ctf_model_var.get(),
            "pytom_defocus_handedness": self.pytom_defocus_handedness_var.get(),
            "pytom_spectral_whitening": self.pytom_spectral_whitening_var.get(),
            "pytom_random_phase_correction": self.pytom_random_phase_correction_var.get(),
            "pytom_half_precision": self.pytom_half_precision_var.get(),
            "pytom_rng_seed": self.pytom_rng_seed_var.get(),
            "pytom_relion5_star": self.pytom_relion5_star_var.get(),
            "pytom_warp_xml": self.pytom_warp_xml_var.get(),
            "pytom_gpu_ids": self.pytom_gpu_ids_var.get(),
            "pytom_log": self.pytom_log_var.get(),
            "extract_tm_output_folder": self.extract_tm_output_folder_var.get(),
            "extract_tomogram_mask": self.extract_tomogram_mask_var.get(),
            "extract_ignore_tomogram_mask": self.extract_ignore_tomogram_mask_var.get(),
            "extract_number_of_particles": self.extract_number_of_particles_var.get(),
            "extract_number_of_false_positives": self.extract_number_of_false_positives_var.get(),
            "extract_particle_diameter": self.extract_particle_diameter_var.get(),
            "extract_cut_off": self.extract_cut_off_var.get(),
            "extract_tophat_filter": self.extract_tophat_filter_var.get(),
            "extract_tophat_connectivity": self.extract_tophat_connectivity_var.get(),
            "extract_relion5_compat": self.extract_relion5_compat_var.get(),
            "extract_log": self.extract_log_var.get(),
            "extract_tophat_bins": self.extract_tophat_bins_var.get(),
            "extract_plot_bins": self.extract_plot_bins_var.get(),
            "slabify_manual_input": self.slabify_manual_input_var.get(),
            "slabify_input_directory": self.slabify_input_directory_var.get(),
            "slabify_output_directory": self.slabify_output_directory_var.get(),
            "slabify_output_masked_directory": self.slabify_output_masked_directory_var.get(),
            "slabify_border": self.slabify_border_var.get(),
            "slabify_offset": self.slabify_offset_var.get(),
            "slabify_angpix": self.slabify_angpix_var.get(),
            "slabify_measure": self.slabify_measure_var.get(),
            "slabify_points": self.slabify_points_var.get(),
            "slabify_n_samples": self.slabify_n_samples_var.get(),
            "slabify_boxsize": self.slabify_boxsize_var.get(),
            "slabify_z_min": self.slabify_z_min_var.get(),
            "slabify_z_max": self.slabify_z_max_var.get(),
            "slabify_iterations": self.slabify_iterations_var.get(),
            "slabify_simple": self.slabify_simple_var.get(),
            "slabify_thickness": self.slabify_thickness_var.get(),
            "slabify_percentile": self.slabify_percentile_var.get(),
            "slabify_seed": self.slabify_seed_var.get(),
            "membrain_manual_input": self.membrain_manual_input_var.get(),
            "membrain_input_directory": self.membrain_input_directory_var.get(),
            "membrain_ckpt_path": self.membrain_ckpt_path_var.get(),
            "membrain_out_folder": self.membrain_out_folder_var.get(),
            "membrain_rescale_patches": self.membrain_rescale_patches_var.get(),
            "membrain_in_pixel_size": self.membrain_in_pixel_size_var.get(),
            "membrain_out_pixel_size": self.membrain_out_pixel_size_var.get(),
            "membrain_store_probabilities": self.membrain_store_probabilities_var.get(),
            "membrain_store_connected_components": self.membrain_store_connected_components_var.get(),
            "membrain_connected_component_threshold": self.membrain_connected_component_threshold_var.get(),
            "membrain_test_time_augmentation": self.membrain_test_time_augmentation_var.get(),
            "membrain_segmentation_threshold": self.membrain_segmentation_threshold_var.get(),
            "membrain_sliding_window_size": self.membrain_sliding_window_size_var.get(),
            "execution_mode": self.execution_mode_var.get(),
            "execution_environment": self.environment_var.get(),
            "slurm_profile": self.slurm_profile_var.get(),
            "slurm_overrides": json.dumps(self._current_slurm_overrides()),
        }

    def _restore_widget_state(self, state: dict[str, object]) -> None:
        self.selected_entries = [dict(item) for item in state["selected_entries"]]  # type: ignore[index]
        self.cryolithe_model_dir_var.set(str(state["cryolithe_model_dir"]))
        self.cryolithe_save_dir_var.set(str(state["cryolithe_save_dir"]))
        self.cryolithe_device_var.set(str(state["cryolithe_device"]))
        self.cryolithe_n3_var.set(str(state["cryolithe_n3"]))
        self.cryolithe_batch_size_var.set(str(state["cryolithe_batch_size"]))
        self.pytom_template_var.set(str(state["pytom_template"]))
        self.pytom_destination_var.set(str(state["pytom_destination"]))
        self.pytom_mask_var.set(str(state["pytom_mask"]))
        self.pytom_manual_input_var.set(bool(state["pytom_manual_input"]))
        self.pytom_manual_dir_var.set(str(state["pytom_manual_dir"]))
        self.pytom_non_spherical_mask_var.set(bool(state["pytom_non_spherical_mask"]))
        self.pytom_particle_diameter_var.set(str(state["pytom_particle_diameter"]))
        self.pytom_angular_search_var.set(str(state["pytom_angular_search"]))
        self.pytom_z_axis_symmetry_var.set(str(state["pytom_z_axis_symmetry"]))
        self.pytom_volume_split_var.set(str(state["pytom_volume_split"]))
        self.pytom_search_x_var.set(str(state["pytom_search_x"]))
        self.pytom_search_y_var.set(str(state["pytom_search_y"]))
        self.pytom_search_z_var.set(str(state["pytom_search_z"]))
        self.pytom_tomogram_mask_var.set(str(state["pytom_tomogram_mask"]))
        self.pytom_per_tilt_weighting_var.set(bool(state["pytom_per_tilt_weighting"]))
        self.pytom_voxel_size_var.set(str(state["pytom_voxel_size"]))
        self.pytom_low_pass_var.set(str(state["pytom_low_pass"]))
        self.pytom_high_pass_var.set(str(state["pytom_high_pass"]))
        self.pytom_dose_accumulation_var.set(str(state["pytom_dose_accumulation"]))
        self.pytom_defocus_var.set(str(state["pytom_defocus"]))
        self.pytom_amplitude_contrast_var.set(str(state["pytom_amplitude_contrast"]))
        self.pytom_spherical_aberration_var.set(str(state["pytom_spherical_aberration"]))
        self.pytom_voltage_var.set(str(state["pytom_voltage"]))
        self.pytom_phase_shift_var.set(str(state["pytom_phase_shift"]))
        self.pytom_ctf_model_var.set(str(state["pytom_ctf_model"]))
        self.pytom_defocus_handedness_var.set(str(state["pytom_defocus_handedness"]))
        self.pytom_spectral_whitening_var.set(bool(state["pytom_spectral_whitening"]))
        self.pytom_random_phase_correction_var.set(bool(state["pytom_random_phase_correction"]))
        self.pytom_half_precision_var.set(bool(state["pytom_half_precision"]))
        self.pytom_rng_seed_var.set(str(state["pytom_rng_seed"]))
        self.pytom_relion5_star_var.set(str(state["pytom_relion5_star"]))
        self.pytom_warp_xml_var.set(bool(state["pytom_warp_xml"]))
        self.pytom_gpu_ids_var.set(str(state["pytom_gpu_ids"]))
        self.pytom_log_var.set(str(state["pytom_log"]))
        self.extract_tm_output_folder_var.set(str(state["extract_tm_output_folder"]))
        self.extract_tomogram_mask_var.set(str(state["extract_tomogram_mask"]))
        self.extract_ignore_tomogram_mask_var.set(bool(state["extract_ignore_tomogram_mask"]))
        self.extract_number_of_particles_var.set(str(state["extract_number_of_particles"]))
        self.extract_number_of_false_positives_var.set(str(state["extract_number_of_false_positives"]))
        self.extract_particle_diameter_var.set(str(state["extract_particle_diameter"]))
        self.extract_cut_off_var.set(str(state["extract_cut_off"]))
        self.extract_tophat_filter_var.set(bool(state["extract_tophat_filter"]))
        self.extract_tophat_connectivity_var.set(str(state["extract_tophat_connectivity"]))
        self.extract_relion5_compat_var.set(bool(state["extract_relion5_compat"]))
        self.extract_log_var.set(str(state["extract_log"]))
        self.extract_tophat_bins_var.set(str(state["extract_tophat_bins"]))
        self.extract_plot_bins_var.set(str(state["extract_plot_bins"]))
        self.slabify_manual_input_var.set(bool(state["slabify_manual_input"]))
        self.slabify_input_directory_var.set(str(state["slabify_input_directory"]))
        self.slabify_output_directory_var.set(str(state["slabify_output_directory"]))
        self.slabify_output_masked_directory_var.set(str(state["slabify_output_masked_directory"]))
        self.slabify_border_var.set(str(state["slabify_border"]))
        self.slabify_offset_var.set(str(state["slabify_offset"]))
        self.slabify_angpix_var.set(str(state["slabify_angpix"]))
        self.slabify_measure_var.set(bool(state["slabify_measure"]))
        self.slabify_points_var.set(str(state["slabify_points"]))
        self.slabify_n_samples_var.set(str(state["slabify_n_samples"]))
        self.slabify_boxsize_var.set(str(state["slabify_boxsize"]))
        self.slabify_z_min_var.set(str(state["slabify_z_min"]))
        self.slabify_z_max_var.set(str(state["slabify_z_max"]))
        self.slabify_iterations_var.set(str(state["slabify_iterations"]))
        self.slabify_simple_var.set(bool(state["slabify_simple"]))
        self.slabify_thickness_var.set(str(state["slabify_thickness"]))
        self.slabify_percentile_var.set(str(state["slabify_percentile"]))
        self.slabify_seed_var.set(str(state["slabify_seed"]))
        self.membrain_manual_input_var.set(bool(state["membrain_manual_input"]))
        self.membrain_input_directory_var.set(str(state["membrain_input_directory"]))
        self.membrain_ckpt_path_var.set(str(state["membrain_ckpt_path"]))
        self.membrain_out_folder_var.set(str(state["membrain_out_folder"]))
        self.membrain_rescale_patches_var.set(bool(state["membrain_rescale_patches"]))
        self.membrain_in_pixel_size_var.set(str(state["membrain_in_pixel_size"]))
        self.membrain_out_pixel_size_var.set(str(state["membrain_out_pixel_size"]))
        self.membrain_store_probabilities_var.set(bool(state["membrain_store_probabilities"]))
        self.membrain_store_connected_components_var.set(bool(state["membrain_store_connected_components"]))
        self.membrain_connected_component_threshold_var.set(str(state["membrain_connected_component_threshold"]))
        self.membrain_test_time_augmentation_var.set(bool(state["membrain_test_time_augmentation"]))
        self.membrain_segmentation_threshold_var.set(str(state["membrain_segmentation_threshold"]))
        self.membrain_sliding_window_size_var.set(str(state["membrain_sliding_window_size"]))
        self.execution_mode_var.set(str(state["execution_mode"]))
        self.environment_var.set(str(state.get("execution_environment", "None")) or "None")
        self.slurm_profile_var.set(str(state["slurm_profile"]))
        overrides_payload = str(state.get("slurm_overrides", "{}"))
        try:
            slurm_parameters = json.loads(overrides_payload)
        except Exception:
            slurm_parameters = {}
        if not isinstance(slurm_parameters, dict):
            slurm_parameters = {}
        self.slurm_overrides_ui.rebuild(
            {str(key): str(value) for key, value in slurm_parameters.items()},
            preserve_existing=False,
        )
        self._toggle_slurm_controls()

    def _apply_scheduled_entry_parameters(self, entry: JobHistoryEntry) -> None:
        params = entry.parameters
        self.selected_entries = [
            {"dataset_name": str(item.get("dataset_name", "")), "ts_name": str(item.get("ts_name", ""))}
            for item in json.loads(params.get("selected_entries", "[]"))
            if isinstance(item, dict)
        ]
        job_name = entry.job_name
        if job_name == "CryoLithe: Denoising":
            self.cryolithe_model_dir_var.set(params.get("model_dir", ""))
            self.cryolithe_save_dir_var.set(params.get("save_dir", ""))
            self.cryolithe_device_var.set(params.get("device", "0"))
            self.cryolithe_n3_var.set(params.get("n3", "256"))
            self.cryolithe_batch_size_var.set(params.get("batch_size", "50000"))
        elif job_name == "PyTom: Template matching":
            self.pytom_template_var.set(params.get("template", ""))
            self.pytom_destination_var.set(params.get("destination", ""))
            self.pytom_mask_var.set(params.get("mask", ""))
            self.pytom_manual_input_var.set(self._bool_from_parameter(params.get("manual_input", "")))
            self.pytom_manual_dir_var.set(params.get("manual_dir", ""))
            self.pytom_non_spherical_mask_var.set(self._bool_from_parameter(params.get("non_spherical_mask", "")))
            self.pytom_particle_diameter_var.set(params.get("particle_diameter", ""))
            self.pytom_angular_search_var.set(params.get("angular_search", ""))
            self.pytom_z_axis_symmetry_var.set(params.get("z_axis_rotational_symmetry", "1"))
            self.pytom_volume_split_var.set(params.get("volume_split", "1 1 1"))
            self.pytom_search_x_var.set(params.get("search_x", ""))
            self.pytom_search_y_var.set(params.get("search_y", ""))
            self.pytom_search_z_var.set(params.get("search_z", ""))
            self.pytom_tomogram_mask_var.set(params.get("tomogram_mask", ""))
            self.pytom_per_tilt_weighting_var.set(self._bool_from_parameter(params.get("per_tilt_weighting", "")))
            self.pytom_voxel_size_var.set(params.get("voxel_size_angstrom", ""))
            self.pytom_low_pass_var.set(params.get("low_pass", ""))
            self.pytom_high_pass_var.set(params.get("high_pass", ""))
            self.pytom_dose_accumulation_var.set(params.get("dose_accumulation", ""))
            self.pytom_defocus_var.set(params.get("defocus", ""))
            self.pytom_amplitude_contrast_var.set(params.get("amplitude_contrast", ""))
            self.pytom_spherical_aberration_var.set(params.get("spherical_aberration", ""))
            self.pytom_voltage_var.set(params.get("voltage", ""))
            self.pytom_phase_shift_var.set(params.get("phase_shift", "0.0"))
            self.pytom_ctf_model_var.set(params.get("tomogram_ctf_model", ""))
            self.pytom_defocus_handedness_var.set(params.get("defocus_handedness", "0"))
            self.pytom_spectral_whitening_var.set(self._bool_from_parameter(params.get("spectral_whitening", "")))
            self.pytom_random_phase_correction_var.set(self._bool_from_parameter(params.get("random_phase_correction", "")))
            self.pytom_half_precision_var.set(self._bool_from_parameter(params.get("half_precision", "")))
            self.pytom_rng_seed_var.set(params.get("rng_seed", ""))
            self.pytom_relion5_star_var.set(params.get("relion5_tomograms_star", ""))
            self.pytom_warp_xml_var.set(self._bool_from_parameter(params.get("warp_xml_file", "")))
            self.pytom_gpu_ids_var.set(params.get("gpu_ids", "0"))
            self.pytom_log_var.set(params.get("log", "INFO"))
        elif job_name == "PyTom: Extract coordinates":
            self.extract_tm_output_folder_var.set(params.get("tm_output_folder", ""))
            self.extract_tomogram_mask_var.set(params.get("tomogram_mask", ""))
            self.extract_ignore_tomogram_mask_var.set(self._bool_from_parameter(params.get("ignore_tomogram_mask", "")))
            self.extract_number_of_particles_var.set(params.get("number_of_particles", ""))
            self.extract_number_of_false_positives_var.set(params.get("number_of_false_positives", "1.0"))
            self.extract_particle_diameter_var.set(params.get("particle_diameter", ""))
            self.extract_cut_off_var.set(params.get("cut_off", ""))
            self.extract_tophat_filter_var.set(self._bool_from_parameter(params.get("tophat_filter", "")))
            self.extract_tophat_connectivity_var.set(params.get("tophat_connectivity", "1"))
            self.extract_relion5_compat_var.set(self._bool_from_parameter(params.get("relion5_compat", "")))
            self.extract_log_var.set(params.get("log", "INFO"))
            self.extract_tophat_bins_var.set(params.get("tophat_bins", "50"))
            self.extract_plot_bins_var.set(params.get("plot_bins", "20"))
        elif job_name == "Slabify: Mask creation":
            self.slabify_manual_input_var.set(self._bool_from_parameter(params.get("manual_input", "")))
            self.slabify_input_directory_var.set(params.get("input_directory", ""))
            self.slabify_output_directory_var.set(params.get("output_directory", ""))
            self.slabify_output_masked_directory_var.set(params.get("output_masked_directory", ""))
            self.slabify_border_var.set(params.get("border", "0"))
            self.slabify_offset_var.set(params.get("offset", "0"))
            self.slabify_angpix_var.set(params.get("angpix", ""))
            self.slabify_measure_var.set(self._bool_from_parameter(params.get("measure", "")))
            self.slabify_points_var.set(params.get("points", ""))
            self.slabify_n_samples_var.set(params.get("n_samples", "50000"))
            self.slabify_boxsize_var.set(params.get("boxsize", "32"))
            self.slabify_z_min_var.set(params.get("z_min", "1"))
            self.slabify_z_max_var.set(params.get("z_max", ""))
            self.slabify_iterations_var.set(params.get("iterations", "3"))
            self.slabify_simple_var.set(self._bool_from_parameter(params.get("simple", "")))
            self.slabify_thickness_var.set(params.get("thickness", ""))
            self.slabify_percentile_var.set(params.get("percentile", "95"))
            self.slabify_seed_var.set(params.get("seed", "4056"))
        elif job_name == "MemBrain-seg: Segmentation":
            self.membrain_manual_input_var.set(self._bool_from_parameter(params.get("manual_input", "")))
            self.membrain_input_directory_var.set(params.get("input_directory", ""))
            self.membrain_ckpt_path_var.set(params.get("ckpt_path", ""))
            self.membrain_out_folder_var.set(params.get("out_folder", "predictions"))
            self.membrain_rescale_patches_var.set(self._bool_from_parameter(params.get("rescale_patches", "")))
            self.membrain_in_pixel_size_var.set(params.get("in_pixel_size", ""))
            self.membrain_out_pixel_size_var.set(params.get("out_pixel_size", "10.0"))
            self.membrain_store_probabilities_var.set(self._bool_from_parameter(params.get("store_probabilities", "")))
            self.membrain_store_connected_components_var.set(
                self._bool_from_parameter(params.get("store_connected_components", ""))
            )
            self.membrain_connected_component_threshold_var.set(params.get("connected_component_threshold", ""))
            self.membrain_test_time_augmentation_var.set(
                self._bool_from_parameter(params.get("test_time_augmentation", "true"), True)
            )
            self.membrain_segmentation_threshold_var.set(params.get("segmentation_threshold", "0.0"))
            self.membrain_sliding_window_size_var.set(params.get("sliding_window_size", "160"))
        self.execution_mode_var.set("Submit to Slurm" if entry.execution_mode == "slurm" else "Run locally")
        self.environment_var.set(
            entry.environment_title
            or params.get("execution_environment", self._job_environment_default(title=entry.job_name))
            or "None"
        )
        self.slurm_profile_var.set(entry.slurm_profile)
        self.slurm_overrides_ui.rebuild(params, preserve_existing=False)
        self._toggle_slurm_controls()

    def _resolve_scheduled_entry(
        self,
        entry: JobHistoryEntry,
    ) -> tuple[list[tuple[DatasetRecord | None, dict[str, str], str]], list[str]]:
        state = self._capture_widget_state()
        try:
            self._apply_scheduled_entry_parameters(entry)
            if entry.job_name == "CryoLithe: Denoising":
                return self._cryolithe_specs()
            if entry.job_name == "PyTom: Template matching":
                return self._pytom_specs()
            if entry.job_name == "PyTom: Extract coordinates":
                return self._extract_specs()
            if entry.job_name == "Slabify: Mask creation":
                return self._slabify_specs()
            if entry.job_name == "MemBrain-seg: Segmentation":
                return self._membrain_specs()
            return [], [f"Unsupported scheduled job: {entry.job_name}"]
        finally:
            self._restore_widget_state(state)

    def _copy_cryolithe_commands(self) -> None:
        commands, errors = self._cryolithe_specs()
        if errors:
            messagebox.showerror("Cannot copy commands", "\n".join(errors))
            return
        if not commands:
            messagebox.showinfo("No TS selected", "Please add at least one TS to the list.")
            return
        preview = "\n".join(command for _dataset, _spec, command in commands)
        self.frame.clipboard_clear()
        self.frame.clipboard_append(preview)
        for dataset, spec, command in commands:
            self._record_job_history(dataset, spec, "copied", command)
        self.app.on_project_changed("tomograms", "custom")
        self.app.status_var.set("CryoLithe commands copied to clipboard")

    def _schedule_cryolithe_commands(self) -> None:
        if not self.selected_entries:
            messagebox.showinfo("No TS selected", "Please add at least one TS to the list.")
            return
        self._schedule_deferred_tomogram_job(
            "CryoLithe: Denoising",
            {
                "model_dir": self.cryolithe_model_dir_var.get().strip(),
                "save_dir": self.cryolithe_save_dir_var.get().strip(),
                "device": self.cryolithe_device_var.get().strip() or "0",
                "n3": self.cryolithe_n3_var.get().strip() or "256",
                "batch_size": self.cryolithe_batch_size_var.get().strip() or "50000",
            },
        )
        self.app.on_project_changed("tomograms", "custom")
        self._refresh_history()
        self.app.status_var.set("Scheduled CryoLithe job")

    def _run_cryolithe_commands(self) -> None:
        commands, errors = self._cryolithe_specs()
        commands = self._debug_prepare_commands(
            commands,
            errors,
            preview_widget=self.cryolithe_command_text,
            empty_title="No TS selected",
            empty_message="Please add at least one TS to the list.",
            job_name="CryoLithe: Denoising",
        )
        if not commands:
            return
        running_entries = [
            self._record_job_history(dataset, spec, "ran", command)
            for dataset, spec, command in commands
            if dataset is not None
        ]
        self.app.mark_history_entries_running([entry.entry_id for entry in running_entries])
        self.app.on_project_changed("tomograms", "custom")
        self._run_command_sequence(commands, "CryoLithe", [entry.entry_id for entry in running_entries])

    def _copy_pytom_commands(self) -> None:
        commands, errors = self._pytom_specs()
        if errors:
            messagebox.showerror("Cannot copy commands", "\n".join(errors))
            return
        if not commands:
            messagebox.showinfo("No TS selected", "Please add at least one TS to the list.")
            return
        preview = "\n".join(command for _dataset, _spec, command in commands)
        self.frame.clipboard_clear()
        self.frame.clipboard_append(preview)
        for dataset, spec, command in commands:
            self._record_job_history(dataset, spec, "copied", command)
        self.app.on_project_changed("tomograms", "custom")
        self.app.status_var.set("PyTom commands copied to clipboard")

    def _schedule_pytom_commands(self) -> None:
        if not self.selected_entries:
            messagebox.showinfo("No TS selected", "Please add at least one TS to the list.")
            return
        self._schedule_deferred_tomogram_job(
            "PyTom: Template matching",
            {
                "template": self.pytom_template_var.get().strip(),
                "destination": self.pytom_destination_var.get().strip(),
                "mask": self.pytom_mask_var.get().strip(),
                "manual_input": "true" if self.pytom_manual_input_var.get() else "",
                "manual_dir": self.pytom_manual_dir_var.get().strip(),
                "non_spherical_mask": "true" if self.pytom_non_spherical_mask_var.get() else "",
                "particle_diameter": self.pytom_particle_diameter_var.get().strip(),
                "angular_search": self.pytom_angular_search_var.get().strip(),
                "z_axis_rotational_symmetry": self.pytom_z_axis_symmetry_var.get().strip(),
                "volume_split": self.pytom_volume_split_var.get().strip(),
                "search_x": self.pytom_search_x_var.get().strip(),
                "search_y": self.pytom_search_y_var.get().strip(),
                "search_z": self.pytom_search_z_var.get().strip(),
                "tomogram_mask": self.pytom_tomogram_mask_var.get().strip(),
                "per_tilt_weighting": "true" if self.pytom_per_tilt_weighting_var.get() else "",
                "voxel_size_angstrom": self.pytom_voxel_size_var.get().strip(),
                "low_pass": self.pytom_low_pass_var.get().strip(),
                "high_pass": self.pytom_high_pass_var.get().strip(),
                "dose_accumulation": self.pytom_dose_accumulation_var.get().strip(),
                "defocus": self.pytom_defocus_var.get().strip(),
                "amplitude_contrast": self.pytom_amplitude_contrast_var.get().strip(),
                "spherical_aberration": self.pytom_spherical_aberration_var.get().strip(),
                "voltage": self.pytom_voltage_var.get().strip(),
                "phase_shift": self.pytom_phase_shift_var.get().strip(),
                "tomogram_ctf_model": self.pytom_ctf_model_var.get().strip(),
                "defocus_handedness": self.pytom_defocus_handedness_var.get().strip(),
                "spectral_whitening": "true" if self.pytom_spectral_whitening_var.get() else "",
                "random_phase_correction": "true" if self.pytom_random_phase_correction_var.get() else "",
                "half_precision": "true" if self.pytom_half_precision_var.get() else "",
                "rng_seed": self.pytom_rng_seed_var.get().strip(),
                "relion5_tomograms_star": self.pytom_relion5_star_var.get().strip(),
                "warp_xml_file": "true" if self.pytom_warp_xml_var.get() else "",
                "gpu_ids": self.pytom_gpu_ids_var.get().strip() or "0",
                "log": self.pytom_log_var.get().strip(),
            },
        )
        self.app.on_project_changed("tomograms", "custom")
        self._refresh_history()
        self.app.status_var.set("Scheduled PyTom template-matching job")

    def _run_pytom_commands(self) -> None:
        commands, errors = self._pytom_specs()
        commands = self._debug_prepare_commands(
            commands,
            errors,
            preview_widget=self.pytom_command_text,
            empty_title="No TS selected",
            empty_message="Please add at least one TS to the list.",
            job_name="PyTom: Template matching",
        )
        if not commands:
            return
        running_entries = [
            self._record_job_history(dataset, spec, "ran", command)
            for dataset, spec, command in commands
            if dataset is not None
        ]
        self.app.mark_history_entries_running([entry.entry_id for entry in running_entries])
        self.app.on_project_changed("tomograms", "custom")
        self._run_command_sequence(commands, "PyTom", [entry.entry_id for entry in running_entries])

    def _copy_extract_commands(self) -> None:
        commands, errors = self._extract_specs()
        if errors:
            messagebox.showerror("Cannot copy commands", "\n".join(errors))
            return
        if not commands:
            messagebox.showinfo("No TM jobs found", "Please select a TM output folder first.")
            return
        preview = "\n".join(command for _dataset, _spec, command in commands)
        self.frame.clipboard_clear()
        self.frame.clipboard_append(preview)
        for dataset, spec, command in commands:
            if dataset is None:
                continue
            self._record_job_history(dataset, spec, "copied", command)
        self.app.on_project_changed("tomograms", "custom")
        self.app.status_var.set("PyTom extract commands copied to clipboard")

    def _schedule_extract_commands(self) -> None:
        self._schedule_deferred_tomogram_job(
            "PyTom: Extract coordinates",
            {
                "tm_output_folder": self.extract_tm_output_folder_var.get().strip(),
                "tomogram_mask": self.extract_tomogram_mask_var.get().strip(),
                "ignore_tomogram_mask": "true" if self.extract_ignore_tomogram_mask_var.get() else "",
                "number_of_particles": self.extract_number_of_particles_var.get().strip(),
                "number_of_false_positives": self.extract_number_of_false_positives_var.get().strip(),
                "particle_diameter": self.extract_particle_diameter_var.get().strip(),
                "cut_off": self.extract_cut_off_var.get().strip(),
                "tophat_filter": "true" if self.extract_tophat_filter_var.get() else "",
                "tophat_connectivity": self.extract_tophat_connectivity_var.get().strip(),
                "relion5_compat": "true" if self.extract_relion5_compat_var.get() else "",
                "log": self.extract_log_var.get().strip(),
                "tophat_bins": self.extract_tophat_bins_var.get().strip(),
                "plot_bins": self.extract_plot_bins_var.get().strip(),
            },
        )
        self.app.on_project_changed("tomograms", "custom")
        self._refresh_history()
        self.app.status_var.set("Scheduled PyTom extract job")

    def _run_extract_commands(self) -> None:
        commands, errors = self._extract_specs()
        commands = self._debug_prepare_commands(
            commands,
            errors,
            preview_widget=self.extract_command_text,
            empty_title="No TM jobs found",
            empty_message="Please select a TM output folder first.",
            job_name="PyTom: Extract coordinates",
        )
        if not commands:
            return
        running_entries = [
            self._record_job_history(dataset, spec, "ran", command)
            for dataset, spec, command in commands
            if dataset is not None
        ]
        self.app.mark_history_entries_running([entry.entry_id for entry in running_entries])
        self.app.on_project_changed("tomograms", "custom")
        self._run_command_sequence(commands, "PyTom extract", [entry.entry_id for entry in running_entries])

    def _copy_slabify_commands(self) -> None:
        commands, errors = self._slabify_specs()
        if errors:
            messagebox.showerror("Cannot copy commands", "\n".join(errors))
            return
        if not commands:
            messagebox.showinfo("No inputs found", "Please add TS or define a manual input directory first.")
            return
        preview = "\n".join(command for _dataset, _spec, command in commands)
        self.frame.clipboard_clear()
        self.frame.clipboard_append(preview)
        for dataset, spec, command in commands:
            if dataset is None:
                continue
            self._record_job_history(dataset, spec, "copied", command)
        self.app.on_project_changed("tomograms", "custom")
        self.app.status_var.set("Slabify commands copied to clipboard")

    def _schedule_slabify_commands(self) -> None:
        if not self.slabify_manual_input_var.get() and not self.selected_entries:
            messagebox.showinfo("No inputs found", "Please add TS or define a manual input directory first.")
            return
        self._schedule_deferred_tomogram_job(
            "Slabify: Mask creation",
            {
                "manual_input": "true" if self.slabify_manual_input_var.get() else "",
                "input_directory": self.slabify_input_directory_var.get().strip(),
                "output_directory": self.slabify_output_directory_var.get().strip(),
                "output_masked_directory": self.slabify_output_masked_directory_var.get().strip(),
                "border": self.slabify_border_var.get().strip(),
                "offset": self.slabify_offset_var.get().strip(),
                "angpix": self.slabify_angpix_var.get().strip(),
                "measure": "true" if self.slabify_measure_var.get() else "",
                "points": self.slabify_points_var.get().strip(),
                "n_samples": self.slabify_n_samples_var.get().strip(),
                "boxsize": self.slabify_boxsize_var.get().strip(),
                "z_min": self.slabify_z_min_var.get().strip(),
                "z_max": self.slabify_z_max_var.get().strip(),
                "iterations": self.slabify_iterations_var.get().strip(),
                "simple": "true" if self.slabify_simple_var.get() else "",
                "thickness": self.slabify_thickness_var.get().strip(),
                "percentile": self.slabify_percentile_var.get().strip(),
                "seed": self.slabify_seed_var.get().strip(),
            },
        )
        self.app.on_project_changed("tomograms", "custom")
        self._refresh_history()
        self.app.status_var.set("Scheduled Slabify job")

    def _run_slabify_commands(self) -> None:
        commands, errors = self._slabify_specs()
        commands = self._debug_prepare_commands(
            commands,
            errors,
            preview_widget=self.slabify_command_text,
            empty_title="No inputs found",
            empty_message="Please add TS or define a manual input directory first.",
            job_name="Slabify: Mask creation",
        )
        if not commands:
            return
        running_entries = [
            self._record_job_history(dataset, spec, "ran", command)
            for dataset, spec, command in commands
            if dataset is not None
        ]
        self.app.mark_history_entries_running([entry.entry_id for entry in running_entries])
        self.app.on_project_changed("tomograms", "custom")
        self._run_command_sequence(commands, "Slabify", [entry.entry_id for entry in running_entries])

    def _copy_membrain_commands(self) -> None:
        commands, errors = self._membrain_specs()
        if errors:
            messagebox.showerror("Cannot copy commands", "\n".join(errors))
            return
        if not commands:
            messagebox.showinfo("No inputs found", "Please add TS or define a manual input directory first.")
            return
        preview = "\n".join(command for _dataset, _spec, command in commands)
        self.frame.clipboard_clear()
        self.frame.clipboard_append(preview)
        for dataset, spec, command in commands:
            if dataset is None:
                continue
            self._record_job_history(dataset, spec, "copied", command)
        self.app.on_project_changed("tomograms", "custom")
        self.app.status_var.set("MemBrain commands copied to clipboard")

    def _schedule_membrain_commands(self) -> None:
        if not self.membrain_manual_input_var.get() and not self.selected_entries:
            messagebox.showinfo("No inputs found", "Please add TS or define a manual input directory first.")
            return
        self._schedule_deferred_tomogram_job(
            "MemBrain-seg: Segmentation",
            {
                "manual_input": "true" if self.membrain_manual_input_var.get() else "",
                "input_directory": self.membrain_input_directory_var.get().strip(),
                "ckpt_path": self.membrain_ckpt_path_var.get().strip(),
                "out_folder": self.membrain_out_folder_var.get().strip(),
                "rescale_patches": "true" if self.membrain_rescale_patches_var.get() else "",
                "in_pixel_size": self.membrain_in_pixel_size_var.get().strip(),
                "out_pixel_size": self.membrain_out_pixel_size_var.get().strip(),
                "store_probabilities": "true" if self.membrain_store_probabilities_var.get() else "",
                "store_connected_components": "true" if self.membrain_store_connected_components_var.get() else "",
                "connected_component_threshold": self.membrain_connected_component_threshold_var.get().strip(),
                "test_time_augmentation": "true" if self.membrain_test_time_augmentation_var.get() else "false",
                "segmentation_threshold": self.membrain_segmentation_threshold_var.get().strip(),
                "sliding_window_size": self.membrain_sliding_window_size_var.get().strip(),
            },
        )
        self.app.on_project_changed("tomograms", "custom")
        self._refresh_history()
        self.app.status_var.set("Scheduled MemBrain segmentation job")

    def _run_membrain_commands(self) -> None:
        commands, errors = self._membrain_specs()
        commands = self._debug_prepare_commands(
            commands,
            errors,
            preview_widget=self.membrain_command_text,
            empty_title="No inputs found",
            empty_message="Please add TS or define a manual input directory first.",
            job_name="MemBrain-seg: Segmentation",
        )
        if not commands:
            return
        running_entries = [
            self._record_job_history(dataset, spec, "ran", command)
            for dataset, spec, command in commands
            if dataset is not None
        ]
        self.app.mark_history_entries_running([entry.entry_id for entry in running_entries])
        self.app.on_project_changed("tomograms", "custom")
        self._run_command_sequence(commands, "MemBrain-seg", [entry.entry_id for entry in running_entries])

    def _run_command_sequence(self, commands, label: str, running_entry_ids: list[str]) -> None:
        use_slurm = self.execution_mode_var.get() == "Submit to Slurm"
        profile_name = self.slurm_profile_var.get().strip()
        if use_slurm and not profile_name and not self.app.is_debug_mode_enabled():
            messagebox.showerror("Slurm profile missing", "Please select a Slurm profile first.")
            return

        if use_slurm:
            command_text, cwd, dataset_name = self._combined_slurm_command(commands)
            try:
                result = self.app.submit_slurm_command(
                    command_text,
                    profile_name=profile_name,
                    cwd=cwd,
                    dataset_name=dataset_name,
                    job_name=label,
                    overrides=self._slurm_override_payload(self._current_slurm_overrides()),
                )
            except Exception as exc:
                self.app.clear_abort_request()
                self.app.status_var.set(f"{label} submission failed: {exc}")
                return
            self._mark_command_sequence_submissions(commands, result, profile_name)
            self.app.on_project_changed("tomograms", "custom")
            self.app.status_var.set(f"Submitted {label} for {len(commands)} TS")
            return

        items: list[dict[str, object]] = []
        activation_command = self.app.resolve_environment_activation(self.environment_var.get())
        for dataset, spec, command in commands:
            processing_folder = dataset.processing_folder if dataset is not None else ""
            dataset_name = dataset.dataset_name if dataset is not None else "unknown_dataset"
            cwd = (
                spec.get("destination")
                or spec.get("save_dir")
                or spec.get("tm_output_folder")
                or spec.get("out_folder")
                or spec.get("output_directory")
                or processing_folder
                or None
            )
            if cwd and not self.app.is_debug_mode_enabled():
                Path(str(cwd)).mkdir(parents=True, exist_ok=True)
            items.append(
                {
                    "command": command,
                    "dataset_name": dataset_name,
                    "job_name": spec.get("job_name", label),
                    "cwd": cwd or "",
                    "error_label": f"{dataset_name}/{spec.get('ts_name', '-')}",
                    "dataset": dataset,
                    "activation_command": activation_command,
                }
            )
        execute_command_sequence(
            self.app,
            items,
            use_slurm=False,
            profile_name=profile_name,
            overrides=self._slurm_override_payload(self._current_slurm_overrides()),
            on_submitted=lambda item, result: self._mark_command_sequence_submission(
                item.get("dataset"),
                result,
                profile_name,
            ),
            on_completed=None,
            on_finished=lambda count, failures: self._finish_command_sequence(
                label,
                use_slurm,
                count,
                failures,
                running_entry_ids,
            ),
        )
        self.app.status_var.set(
            (f"Submitting {label} for {len(commands)} TS" if use_slurm else f"Started {label} for {len(commands)} TS")
        )

    def _mark_command_sequence_submission(
        self,
        dataset: DatasetRecord | None,
        result: SlurmSubmissionResult,
        profile_name: str,
    ) -> None:
        if dataset is None or not dataset.job_history:
            return
        entry = dataset.job_history[-1]
        entry.action = "submitted"
        entry.execution_mode = "slurm"
        entry.slurm_profile = profile_name
        entry.slurm_job_id = result.job_id
        entry.slurm_script_path = result.script_path

    def _finish_command_sequence(
        self,
        label: str,
        use_slurm: bool,
        command_count: int,
        failures: list[str],
        running_entry_ids: list[str],
    ) -> None:
        self.app.clear_history_entries_running(running_entry_ids)
        self.app.clear_abort_request()
        self.app.on_project_changed("tomograms", "custom")
        if failures:
            self.app.status_var.set(f"{label} stopped with failure: " + "; ".join(failures))
            return
        self.app.status_var.set(
            f"{label} {'submitted' if use_slurm else 'finished'} for {command_count} TS"
        )

    def _history_entries(self) -> list[tuple[DatasetRecord, JobHistoryEntry]]:
        entries: list[tuple[DatasetRecord, JobHistoryEntry]] = []
        for dataset in self.app.project.datasets:
            for entry in dataset.job_history:
                if entry.group == "Tomograms":
                    entries.append((dataset, entry))
        return entries

    def _history_sort_value(self, entry: JobHistoryEntry, column: str):
        if column == "job_name":
            return entry.job_name.casefold()
        if column == "dataset_name":
            return entry.dataset_name.casefold()
        if column == "ts_name":
            return entry.parameters.get("ts_name", "").casefold()
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

    def _refresh_history(self, _event=None) -> None:
        self.history_table.delete(*self.history_table.get_children())
        dataset_filter = self.history_dataset_var.get()
        entries = self._history_entries()
        if dataset_filter and dataset_filter != "All datasets":
            entries = [(dataset, entry) for dataset, entry in entries if dataset.dataset_name == dataset_filter]
        if not entries:
            self.history_entry_refs = {}
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
        self.history_entry_refs = {entry.entry_id: (dataset, entry) for dataset, entry in entries}
        for _index, (_dataset, entry) in enumerate(entries):
            self.history_table.insert(
                "",
                "end",
                iid=entry.entry_id,
                values=(
                    entry.job_name,
                    entry.dataset_name or "-",
                    entry.parameters.get("ts_name", "-"),
                    display_history_timestamp(entry),
                    entry.action,
                ),
                tags=(self.app.history_entry_state_tag(entry),),
            )

    def _selected_history_entry(self) -> tuple[DatasetRecord, JobHistoryEntry] | None:
        selection = self.history_table.selection()
        if not selection:
            return None
        return self.history_entry_refs.get(selection[0])

    def _show_selected_history_details(self, _event=None) -> None:
        selected = self._selected_history_entry()
        if selected is None:
            messagebox.showinfo("Job details", "Please select a job history entry first.")
            return
        _dataset, entry = selected
        sections = [
            (
                "Overview",
                [
                    ("Job", entry.job_name),
                    ("Dataset", entry.dataset_name),
                    ("TS", entry.parameters.get("ts_name", "-")),
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

    def _remove_selected_history_entry(self) -> None:
        selected = self._selected_history_entry()
        if selected is None:
            messagebox.showinfo("Remove job", "Please select a job history entry first.")
            return
        dataset, entry = selected
        dataset.job_history = [item for item in dataset.job_history if item is not entry]
        self.app.on_project_changed("tomograms", "custom")
        self._refresh_history()
        self.app.status_var.set("Removed selected tomogram job from history")

    def _scheduled_history_entries(self) -> list[tuple[DatasetRecord, JobHistoryEntry]]:
        dataset_filter = self.history_dataset_var.get()
        entries = self._history_entries()
        if dataset_filter and dataset_filter != "All datasets":
            entries = [(dataset, entry) for dataset, entry in entries if dataset.dataset_name == dataset_filter]
        return [(dataset, entry) for dataset, entry in entries if is_scheduled_history_entry(entry)]

    def _resolved_scheduled_entry_commands(
        self,
        dataset: DatasetRecord,
        entry: JobHistoryEntry,
    ) -> tuple[list[tuple[DatasetRecord | None, dict[str, str], str]], list[str]]:
        commands, errors = self._resolve_scheduled_entry(entry)
        if errors and self.app.is_debug_mode_enabled():
            self.app.debug_log(
                "WARN",
                f"Ignoring scheduled {entry.job_name} resolution errors in Debug mode: " + "; ".join(errors),
            )
        if not commands and self.app.is_debug_mode_enabled():
            fallback_lines = [
                line.strip()
                for line in entry.command.splitlines()
                if line.strip() and line != "Resolved at runtime" and not line.lstrip().startswith("#")
            ]
            commands = [
                (
                    dataset,
                    {"job_name": entry.job_name, "ts_name": entry.parameters.get("ts_name", "Debug preview")},
                    line,
                )
                for line in fallback_lines
            ]
            if commands:
                self.app.debug_log(
                    "WARN",
                    f"Using stored command preview for scheduled {entry.job_name} in Debug mode.",
                )
        return commands, errors

    def _collective_scheduled_script(
        self,
        scheduled_entries: list[tuple[DatasetRecord, JobHistoryEntry]],
        profile_name: str,
        overrides: dict[str, str],
    ) -> str:
        profile = find_slurm_profile(self.app.project, profile_name)
        if profile is None:
            raise ValueError("Please select a valid Slurm profile.")
        all_commands: list[tuple[DatasetRecord | None, dict[str, str], str]] = []
        errors: list[str] = []
        for dataset, entry in scheduled_entries:
            commands, command_errors = self._resolved_scheduled_entry_commands(dataset, entry)
            if command_errors and not self.app.is_debug_mode_enabled():
                errors.append(f"{entry.dataset_name}/{entry.parameters.get('ts_name', '-')}: " + "; ".join(command_errors))
            if not commands:
                errors.append(f"{entry.dataset_name}/{entry.parameters.get('ts_name', '-')}: no commands resolved")
            all_commands.extend(commands)
        if errors:
            raise ValueError("\n".join(errors))
        combined_command, combined_cwd, combined_dataset_name = self._combined_slurm_command(all_commands)
        return render_sbatch_script(
            combined_command,
            profile,
            combined_cwd,
            combined_dataset_name,
            "scheduled_tomograms_batch",
            slurm_override_payload(overrides),
        )

    def _run_scheduled_jobs(self) -> None:
        self._execute_scheduled_jobs(force_slurm=False)

    def _submit_scheduled_jobs_to_slurm(self) -> None:
        self._execute_scheduled_jobs(force_slurm=True)

    def _execute_scheduled_jobs(self, force_slurm: bool) -> None:
        scheduled_entries = self._scheduled_history_entries()
        if not scheduled_entries:
            messagebox.showinfo("Run scheduled jobs", "No scheduled jobs found for the current dataset filter.")
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
                    all_commands: list[tuple[DatasetRecord | None, dict[str, str], str]] = []
                    per_entry_command_text: dict[int, str] = {}
                    for dataset, entry in scheduled_entries:
                        commands, errors = self._resolved_scheduled_entry_commands(dataset, entry)
                        if errors and not self.app.is_debug_mode_enabled():
                            raise ValueError(
                                f"{entry.dataset_name}/{entry.parameters.get('ts_name', '-')}: " + "; ".join(errors)
                            )
                        if not commands:
                            raise ValueError(
                                f"{entry.dataset_name}/{entry.parameters.get('ts_name', '-')}: no commands resolved"
                            )
                        per_entry_command_text[id(entry)] = "\n".join(command for _ds, _spec, command in commands)
                        all_commands.extend(commands)
                    combined_command, combined_cwd, combined_dataset_name = self._combined_slurm_command(all_commands)
                    result = self.app.submit_slurm_command(
                        combined_command,
                        profile_name=collective_profile,
                        cwd=combined_cwd,
                        dataset_name=combined_dataset_name,
                        job_name="scheduled_tomograms_batch",
                        overrides=self._slurm_override_payload(collective_overrides),
                    )
                except Exception as exc:
                    messagebox.showerror("Slurm submission failed", str(exc))
                    return
                submitted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                for _dataset, entry in scheduled_entries:
                    self._mark_scheduled_entry_submitted(
                        entry,
                        submitted_at,
                        per_entry_command_text.get(id(entry), entry.command),
                        result,
                        collective_profile,
                    )
                self.app.status_var.set(f"Submitted {len(scheduled_entries)} scheduled tomogram job(s) collectively")
                return

        def worker(on_queue_finished=None) -> None:
            failures: list[str] = []
            log_counter = 1
            total_resolved_commands = 0
            if not force_slurm:
                for dataset, entry in scheduled_entries:
                    commands, _errors = self._resolved_scheduled_entry_commands(dataset, entry)
                    total_resolved_commands += max(len(commands), 1)
            for dataset, entry in scheduled_entries:
                try:
                    commands, errors = self._resolved_scheduled_entry_commands(dataset, entry)
                    if errors and not self.app.is_debug_mode_enabled():
                        failures.append(f"{entry.dataset_name}/{entry.parameters.get('ts_name', '-')}: " + "; ".join(errors))
                        break
                    if not commands:
                        failures.append(f"{entry.dataset_name}/{entry.parameters.get('ts_name', '-')}: no commands resolved")
                        break
                    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    command_text = "\n".join(command for _ds, _spec, command in commands)
                    run_as_slurm = force_slurm or entry.execution_mode == "slurm"
                    current_profile = forced_profile or entry.slurm_profile
                    if run_as_slurm and not current_profile and not self.app.is_debug_mode_enabled():
                        failures.append(f"{entry.dataset_name}/{entry.parameters.get('ts_name', '-')}: missing Slurm profile")
                        break
                    if not run_as_slurm:
                        self.app.root.after(
                            0,
                            lambda current_entry=entry, current_time=started_at, current_command=command_text: self._mark_scheduled_entry_started(
                                current_entry,
                                current_time,
                                current_command,
                            ),
                        )
                    if run_as_slurm:
                        combined_command, combined_cwd, combined_dataset_name = self._combined_slurm_command(
                            commands,
                            fallback_cwd=dataset.processing_folder,
                        )
                        result = self.app.submit_slurm_command(
                            combined_command,
                            profile_name=current_profile,
                            cwd=combined_cwd,
                            dataset_name=combined_dataset_name,
                            job_name=entry.job_name,
                            overrides=self._slurm_override_payload(entry.parameters),
                        )
                        self.app.root.after(
                            0,
                            lambda current_entry=entry, current_time=started_at, current_command=command_text, current_result=result, profile_name=current_profile: self._mark_scheduled_entry_submitted(
                                current_entry,
                                current_time,
                                current_command,
                                current_result,
                                profile_name,
                            ),
                        )
                        if not self.app.is_debug_mode_enabled():
                            succeeded, state = wait_for_slurm_job(result.job_id)
                            if not succeeded:
                                failures.append(
                                    f"{entry.dataset_name}/{entry.parameters.get('ts_name', '-')}: Slurm job ended with state {state}"
                                )
                                break
                        continue
                    for resolved_dataset, spec, command in commands:
                        current_dataset_name = (
                            resolved_dataset.dataset_name if resolved_dataset is not None else entry.dataset_name or "unknown_dataset"
                        )
                        cwd = (
                            spec.get("destination")
                            or spec.get("save_dir")
                            or spec.get("tm_output_folder")
                            or spec.get("out_folder")
                            or spec.get("output_directory")
                            or (
                                resolved_dataset.processing_folder
                                if resolved_dataset is not None
                                else dataset.processing_folder
                            )
                            or None
                        )
                        if cwd and not self.app.is_debug_mode_enabled():
                            Path(cwd).mkdir(parents=True, exist_ok=True)
                        process = self.app.start_managed_process_with_log(
                            command,
                            cwd=cwd,
                            title=(
                                f"Scheduled tomogram job output ({log_counter}/{total_resolved_commands}): "
                                f"{entry.job_name}"
                            ),
                        )
                        log_counter += 1
                        return_code = self.app.wait_managed_process(process)
                        if self.app.abort_requested():
                            failures.append(f"{current_dataset_name}/{spec.get('ts_name', '-')}: aborted")
                            break
                        if return_code != 0:
                            failures.append(f"{current_dataset_name}/{spec.get('ts_name', '-')}: exit code {return_code}")
                            break
                    if failures:
                        break
                except Exception as exc:
                    failures.append(f"{entry.dataset_name}/{entry.parameters.get('ts_name', '-')}: {exc}")
                    break
                if self.app.abort_requested():
                    failures.append(f"{entry.dataset_name}/{entry.parameters.get('ts_name', '-')}: aborted")
                    break
            self.app.root.after(
                0,
                lambda: self._finish_tomogram_queue_run(len(scheduled_entries), failures, on_queue_finished),
            )

        def start_batch(on_queue_finished=None) -> None:
            self.app.mark_history_entries_running([entry.entry_id for _dataset, entry in scheduled_entries])
            threading.Thread(target=lambda: worker(on_queue_finished), daemon=True).start()
            self.app.status_var.set(
                ("Submitting sequentially" if force_slurm else "Running")
                + f" {len(scheduled_entries)} scheduled tomogram job(s) sequentially"
            )

        if force_slurm:
            start_batch(None)
            return

        self.app.request_scheduled_batch_start(
            self.frame,
            queue_key="tomograms-local",
            title="Queued scheduled jobs: Processing TS jobs",
            entry_ids=[entry.entry_id for _dataset, entry in scheduled_entries],
            start_batch=start_batch,
        )

    def _mark_scheduled_entry_started(self, entry: JobHistoryEntry, started_at: str, command: str) -> None:
        entry.timestamp = started_at
        entry.action = "ran"
        entry.command = command
        entry.execution_mode = "local"
        entry.slurm_job_id = ""
        entry.slurm_script_path = ""
        self._refresh_history()
        self.app.on_project_changed("tomograms", "custom")

    def _mark_scheduled_entry_submitted(
        self,
        entry: JobHistoryEntry,
        submitted_at: str,
        command: str,
        result: SlurmSubmissionResult,
        profile_name: str,
    ) -> None:
        entry.timestamp = submitted_at
        entry.action = "submitted"
        entry.command = command
        entry.execution_mode = "slurm"
        entry.slurm_profile = profile_name
        entry.slurm_job_id = result.job_id
        entry.slurm_script_path = result.script_path
        self._refresh_history()
        self.app.on_project_changed("tomograms", "custom")

    def _finish_scheduled_jobs_run(self, scheduled_count: int, failures: list[str]) -> None:
        self.app.clear_abort_request()
        self._refresh_history()
        self.app.on_project_changed("tomograms", "custom")
        if failures:
            self.app.status_var.set("Scheduled tomogram jobs stopped: " + "; ".join(failures))
            return
        self.app.status_var.set(f"Finished {scheduled_count} scheduled tomogram job(s)")

    def _finish_tomogram_queue_run(
        self,
        scheduled_count: int,
        failures: list[str],
        on_queue_finished,
    ) -> None:
        self.app.clear_history_entries_running([entry.entry_id for _dataset, entry in self._history_entries()])
        self._finish_scheduled_jobs_run(scheduled_count, failures)
        if on_queue_finished is not None:
            on_queue_finished()

    def on_project_loaded(self, project: ProjectData) -> None:
        project_id = id(project)
        if self.bound_project_id != project_id:
            self.bound_project_id = project_id
            stored = project.state.tomograms_selection
            self.selected_entries = [
                {"dataset_name": str(item.get("dataset_name", "")), "ts_name": str(item.get("ts_name", ""))}
                for item in stored
                if isinstance(item, dict)
            ]
            self.dataset_var.set("")
            self.ts_var.set("")
            self.job_type_var.set("Select job type")
            self.history_dataset_var.set("All datasets")
            self._apply_custom_defaults()
        self._refresh_dataset_options()
        self._refresh_table()
        self._refresh_history()
        self._refresh_slurm_profiles()
        self._on_job_type_changed()

    def sync_to_project(self, project: ProjectData) -> None:
        project.state.tomograms_selection = [dict(item) for item in self.selected_entries]
