from __future__ import annotations

import tkinter as tk
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from xml.etree import ElementTree as ET

from cryoet_organizer.dialogs import create_scrollable_frame, show_detail_dialog
from cryoet_organizer.job_defaults import resolve_job_default
from cryoet_organizer.preferences import project_preference, project_preference_enabled, project_preference_int
from cryoet_organizer.project import (
    DatasetRecord,
    JobHistoryEntry,
    ProjectData,
    assert_unique_dataset_names,
    dataset_ts_names,
    filtered_mdoc_paths,
    prepare_unified_mdocs_directory,
)
from cryoet_organizer.thumbnail_cache import effective_thumbnail_source_folder, resolve_thumbnail_cache_dir
from cryoet_organizer.resizable_sections import ResizableSectionStack
from cryoet_organizer.tabs.base import LabeledEntry, LabeledPathEntry, SidebarTab
from cryoet_organizer.warp_settings import WarpSettingsSummary, parse_warp_settings
from cryoet_organizer.dialogs import bind_scrollable_canvas, fit_outer_canvas_to_viewport


class ProjectOverviewTab(SidebarTab):
    tab_id = "project_overview"
    title = "Project Overview"
    refresh_domains = ("project_overview", "datasets")

    def build(self) -> None:
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(0, weight=1)
        self.sort_column = "created_at"
        self.sort_descending = False
        self.dataset_action_var = tk.StringVar(value="Project actions")
        self.import_overwrite_visible = False
        self.import_frame_settings_summary: WarpSettingsSummary | None = None
        self.import_tilt_settings_summary: WarpSettingsSummary | None = None
        self.remove_selected_datasets: set[str] = set()
        self._layout_project_id: int | None = None
        self._table_pane_default_height = self.app._scale_pixels(420)
        self._table_pane_minsize = self.app._scale_pixels(260)
        self._forms_pane_default_height = self.app._scale_pixels(620)
        self._forms_pane_minsize = self.app._scale_pixels(320)

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

        intro = (
            "Add datasets here or import already processed datasets so Processing, Gallery, "
            "and Particles can use the same stored paths."
        )
        ttk.Label(self.content, text=intro, wraplength=900, justify="left").grid(
            row=0, column=0, sticky="w", pady=(0, 12)
        )

        header = ttk.Frame(self.content)
        header.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)
        header.columnconfigure(1, weight=0)

        self.project_name_entry = LabeledEntry(header, "Project name")
        self.project_name_entry.grid(row=0, column=0, sticky="ew", padx=(0, 12))

        action_box = ttk.Frame(header)
        action_box.grid(row=0, column=1, sticky="e")
        ttk.Label(action_box, text="Dataset actions").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.dataset_action_combo = ttk.Combobox(
            action_box,
            textvariable=self.dataset_action_var,
            state="readonly",
            values=(
                "Project actions",
                "Add dataset for processing",
                "Import already processed dataset",
                "Remove Dataset",
            ),
            width=32,
        )
        self.dataset_action_combo.grid(row=1, column=0, sticky="ew")
        self.dataset_action_combo.bind("<<ComboboxSelected>>", self._on_dataset_action_changed)

        self.layout_pane = ResizableSectionStack(
            self.content,
            app=self.app,
            preference_namespace="project_overview",
            bottom_spacing=self.app._scale_pixels(140),
            on_layout_changed=self._schedule_outer_layout_refresh,
        )
        self.layout_pane.grid(row=2, column=0, sticky="nsew", pady=(0, 0))

        table_section = self.layout_pane.add_section(
            "dataset_table",
            default_height=self._table_pane_default_height,
            min_height=self._table_pane_minsize,
        )
        table_section.columnconfigure(0, weight=1)
        table_section.rowconfigure(0, weight=1)

        forms_section = self.layout_pane.add_section(
            "forms",
            default_height=self._forms_pane_default_height,
            min_height=self._forms_pane_minsize,
        )
        forms_section.columnconfigure(0, weight=1)
        forms_section.rowconfigure(0, weight=1)

        table_box = ttk.LabelFrame(table_section, text="Datasets in project", padding=12)
        table_box.grid(row=0, column=0, sticky="nsew")
        table_box.columnconfigure(0, weight=1)
        table_box.rowconfigure(0, weight=1)

        columns = (
            "dataset_name",
            "sample",
            "number_of_ts",
            "pixel_size",
            "exposure",
            "dimensions",
            "raw_frames_folder",
            "processing_folder",
            "created_at",
        )
        self.dataset_table = ttk.Treeview(
            table_box,
            columns=columns,
            show="headings",
            height=20,
            style="Technical.Treeview",
        )
        headings = {
            "dataset_name": "Dataset",
            "sample": "Sample",
            "number_of_ts": "Number of TS",
            "pixel_size": "Pixelsize",
            "exposure": "Exposure",
            "dimensions": "Tomogram (X,Y,Z)",
            "raw_frames_folder": "Raw frames folder",
            "processing_folder": "Processing folder",
            "created_at": "Added",
        }
        widths = {
            "dataset_name": 180,
            "sample": 140,
            "number_of_ts": 110,
            "pixel_size": 90,
            "exposure": 90,
            "dimensions": 140,
            "raw_frames_folder": 250,
            "processing_folder": 240,
            "created_at": 150,
        }
        for column in columns:
            self.dataset_table.heading(
                column,
                text=headings[column],
                command=lambda current=column: self._sort_by_column(current),
            )
            self.dataset_table.column(column, width=widths[column], anchor="w")

        scrollbar = ttk.Scrollbar(table_box, orient="vertical", command=self.dataset_table.yview)
        self.dataset_table.configure(yscrollcommand=scrollbar.set)
        self.dataset_table.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.dataset_table.bind("<Double-1>", self._show_selected_dataset_details)

        self.dataset_count_label = ttk.Label(table_box, text="0 datasets")
        self.dataset_count_label.grid(row=1, column=0, sticky="w", pady=(8, 0))

        self.forms_host, self.forms_container, self.forms_scroll_canvas = create_scrollable_frame(
            forms_section,
            allow_horizontal=True,
            fill_vertical=True,
        )
        self.forms_host.grid(row=0, column=0, sticky="nsew")
        self.forms_container.columnconfigure(0, weight=1)

        self.add_dataset_form = self._build_add_dataset_form(self.forms_container)
        self.add_dataset_form.grid(row=0, column=0, sticky="ew", pady=(12, 0))
        self.add_dataset_form.grid_remove()

        self.import_dataset_form = self._build_import_dataset_form(self.forms_container)
        self.import_dataset_form.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self.import_dataset_form.grid_remove()

        self.remove_dataset_form = self._build_remove_dataset_form(self.forms_container)
        self.remove_dataset_form.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        self.remove_dataset_form.grid_remove()
        self.layout_pane.set_section_visible("forms", False)
        self._apply_custom_defaults()

    def _on_outer_frame_configure(self, _event=None) -> None:
        self.outer_canvas.configure(scrollregion=self.outer_canvas.bbox("all"))

    def _on_outer_canvas_configure(self, event) -> None:
        fit_outer_canvas_to_viewport(self.outer_canvas, self.outer_window, self.content, event)

    def _schedule_outer_layout_refresh(self) -> None:
        self.outer_canvas.after_idle(self._on_outer_frame_configure)

    def _project_default(self, group: str, job_key: str, field_key: str, base_value: str) -> str:
        return resolve_job_default(
            self.app.project,
            "Project Overview",
            group,
            job_key,
            field_key,
            base_value,
        )

    def _project_default_bool(self, group: str, job_key: str, field_key: str, base_value: bool) -> bool:
        base_text = "true" if base_value else ""
        return self._project_default(group, job_key, field_key, base_text).lower() in {"1", "true", "yes", "on"}

    def _apply_custom_defaults(self) -> None:
        self.clear_form()
        self.clear_import_form()

    def _build_add_dataset_form(self, parent: tk.Misc) -> ttk.LabelFrame:
        form = ttk.LabelFrame(parent, text="Add dataset for processing", padding=12)
        for column in range(2):
            form.columnconfigure(column, weight=1)

        self.dataset_name_entry = LabeledEntry(form, "Dataset name")
        self.dataset_name_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10), pady=4)
        self.sample_entry = LabeledEntry(form, "Sample")
        self.sample_entry.grid(row=0, column=1, sticky="ew", pady=4)
        self.comment_entry = LabeledEntry(form, "Comment")
        self.comment_entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=4)

        self.raw_frames_entry = LabeledPathEntry(form, "Raw frames folder", "Browse...", self._browse_raw_frames)
        self.raw_frames_entry.grid(row=2, column=0, sticky="ew", padx=(0, 10), pady=4)
        self.mdocs_entry = LabeledPathEntry(form, "Mdocs folder", "Browse...", self._browse_mdocs)
        self.mdocs_entry.grid(row=2, column=1, sticky="ew", pady=4)

        self.gain_file_entry = LabeledPathEntry(
            form,
            "Gain file (optional)",
            "Browse...",
            self._browse_gain_file,
        )
        self.gain_file_entry.grid(row=2, column=0, sticky="ew", padx=(0, 10), pady=4)
        self.gain_file_entry.grid_configure(row=3)
        self.processing_folder_entry = LabeledPathEntry(
            form,
            "Processing folder",
            "Browse...",
            self._browse_processing_folder,
        )
        self.processing_folder_entry.grid(row=3, column=1, sticky="ew", pady=4)

        self.unify_mdoc_names_var = tk.BooleanVar(value=True)
        self.ignore_override_mdocs_var = tk.BooleanVar(value=False)
        self.ignore_custom_mdocs_var = tk.BooleanVar(value=False)
        self.ignore_custom_mdocs_pattern_var = tk.StringVar()
        filter_row = ttk.Frame(form)
        filter_row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        filter_row.columnconfigure(1, weight=1)
        ttk.Checkbutton(
            filter_row,
            text="Unify mdoc names",
            variable=self.unify_mdoc_names_var,
        ).grid(row=0, column=0, sticky="w", padx=(0, 12))
        ttk.Checkbutton(
            filter_row,
            text="Ignore override.mdoc",
            variable=self.ignore_override_mdocs_var,
        ).grid(row=0, column=1, sticky="w")
        custom_row = ttk.Frame(form)
        custom_row.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        custom_row.columnconfigure(1, weight=1)
        ttk.Checkbutton(
            custom_row,
            text="Ignore custom.mdoc",
            variable=self.ignore_custom_mdocs_var,
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(custom_row, textvariable=self.ignore_custom_mdocs_pattern_var).grid(
            row=0,
            column=1,
            sticky="ew",
        )

        self.pixel_size_entry = LabeledEntry(form, "Pixelsize")
        self.pixel_size_entry.grid(row=6, column=0, sticky="ew", padx=(0, 10), pady=4)
        self.exposure_entry = LabeledEntry(form, "Exposure")
        self.exposure_entry.grid(row=6, column=1, sticky="ew", pady=4)

        dims = ttk.Frame(form)
        dims.grid(row=7, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Label(dims, text="Tomogram dimensions (X, Y, Z)").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 4)
        )
        for column in range(3):
            dims.columnconfigure(column, weight=1)
        self.tomogram_x_entry = LabeledEntry(dims, "X")
        self.tomogram_x_entry.grid(row=1, column=0, sticky="ew", padx=(0, 8))
        self.tomogram_y_entry = LabeledEntry(dims, "Y")
        self.tomogram_y_entry.grid(row=1, column=1, sticky="ew", padx=4)
        self.tomogram_z_entry = LabeledEntry(dims, "Z")
        self.tomogram_z_entry.grid(row=1, column=2, sticky="ew", padx=(8, 0))

        action_row = ttk.Frame(form)
        action_row.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        action_row.columnconfigure(0, weight=1)
        ttk.Button(action_row, text="Add dataset", command=self.add_dataset).grid(row=0, column=0, sticky="w")
        ttk.Button(action_row, text="Clear form", command=self.clear_form).grid(
            row=0,
            column=1,
            sticky="w",
            padx=(10, 0),
        )
        return form

    def _build_import_dataset_form(self, parent: tk.Misc) -> ttk.LabelFrame:
        form = ttk.LabelFrame(parent, text="Import already processed dataset", padding=12)
        for column in range(2):
            form.columnconfigure(column, weight=1)

        self.import_dataset_name_entry = LabeledEntry(form, "Dataset name")
        self.import_dataset_name_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10), pady=4)
        self.import_sample_entry = LabeledEntry(form, "Sample")
        self.import_sample_entry.grid(row=0, column=1, sticky="ew", pady=4)
        self.import_comment_entry = LabeledEntry(form, "Comment")
        self.import_comment_entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=4)

        self.import_frame_settings_entry = LabeledPathEntry(
            form,
            "Frameseries.settings file",
            "Browse...",
            self._browse_import_frame_settings,
        )
        self.import_frame_settings_entry.grid(row=2, column=0, sticky="ew", padx=(0, 10), pady=4)
        self.import_tilt_settings_entry = LabeledPathEntry(
            form,
            "Tiltseries.settings file",
            "Browse...",
            self._browse_import_tilt_settings,
        )
        self.import_tilt_settings_entry.grid(row=2, column=1, sticky="ew", pady=4)

        self.import_mdocs_entry = LabeledPathEntry(
            form,
            "Mdocs folder",
            "Browse...",
            self._browse_import_mdocs,
        )
        self.import_mdocs_entry.grid(row=3, column=0, sticky="ew", padx=(0, 10), pady=4)
        import_filter_row = ttk.Frame(form)
        import_filter_row.grid(row=3, column=1, sticky="ew", pady=4)
        import_filter_row.columnconfigure(0, weight=1)
        self.import_ignore_override_mdocs_var = tk.BooleanVar(value=False)
        self.import_ignore_custom_mdocs_var = tk.BooleanVar(value=False)
        self.import_ignore_custom_mdocs_pattern_var = tk.StringVar()
        ttk.Checkbutton(
            import_filter_row,
            text="Ignore override.mdoc",
            variable=self.import_ignore_override_mdocs_var,
        ).grid(row=0, column=0, sticky="w")
        import_custom_row = ttk.Frame(import_filter_row)
        import_custom_row.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        import_custom_row.columnconfigure(1, weight=1)
        ttk.Checkbutton(
            import_custom_row,
            text="Ignore custom.mdoc",
            variable=self.import_ignore_custom_mdocs_var,
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(import_custom_row, textvariable=self.import_ignore_custom_mdocs_pattern_var).grid(
            row=0,
            column=1,
            sticky="ew",
        )

        self.import_settings_hint = ttk.Label(
            form,
            text=(
                "Die wichtigsten Pfade werden direkt aus den geladenen .settings-Dateien gelesen "
                "und relativ zur jeweiligen Datei aufgeloest."
            ),
            wraplength=900,
            justify="left",
        )
        self.import_settings_hint.grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 8))

        self.import_overwrite_toggle = ttk.Button(
            form,
            text="Show overwrite parameter",
            command=self._toggle_import_overwrite,
        )
        self.import_overwrite_toggle.grid(row=5, column=0, sticky="w", pady=(0, 4))

        overwrite = ttk.LabelFrame(form, text="Overwrite parameter", padding=12)
        overwrite.grid(row=6, column=0, columnspan=2, sticky="ew")
        for column in range(2):
            overwrite.columnconfigure(column, weight=1)
        self.import_overwrite_frame = overwrite

        self.import_raw_frames_entry = LabeledPathEntry(
            overwrite,
            "Raw frames folder",
            "Browse...",
            self._browse_import_raw_frames,
        )
        self.import_raw_frames_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10), pady=4)
        self.import_gain_file_entry = LabeledPathEntry(
            overwrite,
            "Gain file (optional)",
            "Browse...",
            self._browse_import_gain_file,
        )
        self.import_gain_file_entry.grid(row=0, column=1, sticky="ew", pady=4)

        self.import_processing_folder_entry = LabeledPathEntry(
            overwrite,
            "Processing folder",
            "Browse...",
            self._browse_import_processing_folder,
        )
        self.import_processing_folder_entry.grid(row=1, column=0, sticky="ew", padx=(0, 10), pady=4)
        self.import_pixel_size_entry = LabeledEntry(overwrite, "Pixelsize")
        self.import_pixel_size_entry.grid(row=1, column=1, sticky="ew", pady=4)

        self.import_exposure_entry = LabeledEntry(overwrite, "Exposure")
        self.import_exposure_entry.grid(row=2, column=0, sticky="ew", padx=(0, 10), pady=4)
        self.import_tomostar_folder_entry = LabeledPathEntry(
            overwrite,
            "Tomostar folder",
            "Browse...",
            self._browse_import_tomostar_folder,
        )
        self.import_tomostar_folder_entry.grid(row=2, column=1, sticky="ew", pady=4)

        dims = ttk.Frame(overwrite)
        dims.grid(row=3, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Label(dims, text="Tomogram dimensions (X, Y, Z)").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 4)
        )
        for column in range(3):
            dims.columnconfigure(column, weight=1)
        self.import_tomogram_x_entry = LabeledEntry(dims, "X")
        self.import_tomogram_x_entry.grid(row=1, column=0, sticky="ew", padx=(0, 8))
        self.import_tomogram_y_entry = LabeledEntry(dims, "Y")
        self.import_tomogram_y_entry.grid(row=1, column=1, sticky="ew", padx=4)
        self.import_tomogram_z_entry = LabeledEntry(dims, "Z")
        self.import_tomogram_z_entry.grid(row=1, column=2, sticky="ew", padx=(8, 0))

        self.import_frame_processing_folder_entry = LabeledPathEntry(
            overwrite,
            "Frameseries processing folder",
            "Browse...",
            self._browse_import_frame_processing_folder,
        )
        self.import_frame_processing_folder_entry.grid(row=4, column=0, sticky="ew", padx=(0, 10), pady=4)
        self.import_tilt_processing_folder_entry = LabeledPathEntry(
            overwrite,
            "Tiltseries processing folder",
            "Browse...",
            self._browse_import_tilt_processing_folder,
        )
        self.import_tilt_processing_folder_entry.grid(row=4, column=1, sticky="ew", pady=4)

        action_row = ttk.Frame(form)
        action_row.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        action_row.columnconfigure(0, weight=1)
        ttk.Button(
            action_row,
            text="Import dataset",
            command=self.import_processed_dataset,
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            action_row,
            text="Clear form",
            command=self.clear_import_form,
        ).grid(row=0, column=1, sticky="w", padx=(10, 0))

        self.import_overwrite_frame.grid_remove()
        return form

    def _apply_dataset_action_view(self, selection: str) -> None:
        show_forms = selection != "Project actions"
        self.layout_pane.set_section_visible("forms", show_forms)
        if show_forms:
            self.forms_host.grid(row=0, column=0, sticky="nsew")
        else:
            self.forms_host.grid_remove()

        if selection == "Add dataset for processing":
            self.add_dataset_form.grid()
            self.import_dataset_form.grid_remove()
            self.remove_dataset_form.grid_remove()
        elif selection == "Import already processed dataset":
            self.add_dataset_form.grid_remove()
            self.import_dataset_form.grid()
            self.remove_dataset_form.grid_remove()
        elif selection == "Remove Dataset":
            self.add_dataset_form.grid_remove()
            self.import_dataset_form.grid_remove()
            self._refresh_remove_dataset_table(self.app.project)
            self.remove_dataset_form.grid()
        else:
            self.add_dataset_form.grid_remove()
            self.import_dataset_form.grid_remove()
            self.remove_dataset_form.grid_remove()

    def _on_dataset_action_changed(self, _event=None) -> None:
        selection = self.dataset_action_var.get()
        self._apply_dataset_action_view(selection)

    def _build_remove_dataset_form(self, parent: tk.Misc) -> ttk.LabelFrame:
        form = ttk.LabelFrame(parent, text="Remove dataset", padding=12)
        form.columnconfigure(0, weight=1)
        form.rowconfigure(1, weight=1)

        ttk.Label(
            form,
            text=(
                "Select one or more datasets to remove them from CryoPal_tomo. "
                "The underlying data on disk will not be deleted."
            ),
            wraplength=900,
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        table_box = ttk.Frame(form)
        table_box.grid(row=1, column=0, sticky="nsew")
        table_box.columnconfigure(0, weight=1)
        table_box.rowconfigure(0, weight=1)

        columns = ("selected", "dataset_name", "sample", "number_of_ts", "processing_folder")
        self.remove_dataset_table = ttk.Treeview(
            table_box,
            columns=columns,
            show="headings",
            height=8,
            style="Technical.Treeview",
        )
        headings = {
            "selected": "",
            "dataset_name": "Dataset",
            "sample": "Sample",
            "number_of_ts": "Number of TS",
            "processing_folder": "Processing folder",
        }
        widths = {
            "selected": 52,
            "dataset_name": 220,
            "sample": 160,
            "number_of_ts": 110,
            "processing_folder": 420,
        }
        for column in columns:
            self.remove_dataset_table.heading(column, text=headings[column])
            anchor = "center" if column == "selected" else "w"
            self.remove_dataset_table.column(column, width=widths[column], anchor=anchor)
        self.remove_dataset_table.grid(row=0, column=0, sticky="nsew")
        y_scrollbar = ttk.Scrollbar(table_box, orient="vertical", command=self.remove_dataset_table.yview)
        y_scrollbar.grid(row=0, column=1, sticky="ns")
        self.remove_dataset_table.configure(yscrollcommand=y_scrollbar.set)
        x_scrollbar = ttk.Scrollbar(table_box, orient="horizontal", command=self.remove_dataset_table.xview)
        x_scrollbar.grid(row=1, column=0, sticky="ew")
        self.remove_dataset_table.configure(xscrollcommand=x_scrollbar.set)
        self.remove_dataset_table.bind("<Button-1>", self._on_remove_dataset_table_click)

        footer = ttk.Frame(form)
        footer.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        footer.columnconfigure(0, weight=1)
        self.remove_dataset_selection_label = ttk.Label(footer, text="0 datasets selected")
        self.remove_dataset_selection_label.grid(row=0, column=0, sticky="w")
        ttk.Button(
            footer,
            text="Remove selection",
            command=self._confirm_remove_selected_datasets,
        ).grid(row=0, column=1, sticky="e")
        return form

    def _refresh_remove_dataset_table(self, project: ProjectData) -> None:
        existing_names = {dataset.dataset_name for dataset in project.datasets}
        self.remove_selected_datasets.intersection_update(existing_names)
        for item in self.remove_dataset_table.get_children():
            self.remove_dataset_table.delete(item)
        for dataset in self._sorted_datasets(project):
            checked = "[x]" if dataset.dataset_name in self.remove_selected_datasets else "[ ]"
            self.remove_dataset_table.insert(
                "",
                "end",
                iid=dataset.dataset_name,
                values=(
                    checked,
                    dataset.dataset_name,
                    dataset.sample,
                    self._count_number_of_ts(dataset),
                    dataset.processing_folder,
                ),
            )
        label = "dataset" if len(self.remove_selected_datasets) == 1 else "datasets"
        self.remove_dataset_selection_label.config(
            text=f"{len(self.remove_selected_datasets)} {label} selected"
        )

    def _on_remove_dataset_table_click(self, event) -> str | None:
        region = self.remove_dataset_table.identify("region", event.x, event.y)
        column = self.remove_dataset_table.identify_column(event.x)
        item_id = self.remove_dataset_table.identify_row(event.y)
        if region == "cell" and column == "#1" and item_id:
            if item_id in self.remove_selected_datasets:
                self.remove_selected_datasets.remove(item_id)
            else:
                self.remove_selected_datasets.add(item_id)
            self._refresh_remove_dataset_table(self.app.project)
            return "break"
        return None

    def _confirm_remove_selected_datasets(self) -> None:
        selected_names = sorted(self.remove_selected_datasets)
        if not selected_names:
            messagebox.showinfo("Remove Dataset", "Please select at least one dataset first.")
            return
        confirmed = self._confirm_dataset_removal()
        if not confirmed:
            return
        removed_count = self._remove_selected_dataset_records(selected_names)
        self.remove_selected_datasets.clear()
        self._refresh_remove_dataset_table(self.app.project)
        self.dataset_action_var.set("Project actions")
        self._apply_dataset_action_view("Project actions")
        self.app.on_project_changed(
            "datasets",
            "processing_m",
            status_message=f"Removed {removed_count} dataset(s) from CryoPal_tomo",
        )

    def _confirm_dataset_removal(self) -> bool:
        dialog = tk.Toplevel(self.frame)
        dialog.title("Remove Dataset")
        dialog.transient(self.frame.winfo_toplevel())
        dialog.grab_set()
        dialog.resizable(False, False)

        body = ttk.Frame(dialog, padding=16)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)

        ttk.Label(
            body,
            text=(
                "Are you sure you want to remove the selected Datasets?\n\n"
                "The data itself will not be affected, but associated information such as "
                "tomogram-gallery annotations and job histories will be lost."
            ),
            wraplength=460,
            justify="left",
        ).grid(row=0, column=0, sticky="w")

        button_row = ttk.Frame(body)
        button_row.grid(row=1, column=0, sticky="ew", pady=(16, 0))
        button_row.columnconfigure(0, weight=1)
        result = {"confirmed": False}

        def _close(confirm: bool) -> None:
            result["confirmed"] = confirm
            dialog.destroy()

        ttk.Button(button_row, text="Cancel", command=lambda: _close(False)).grid(
            row=0,
            column=0,
            sticky="w",
        )
        ttk.Button(button_row, text="Confirm", command=lambda: _close(True)).grid(
            row=0,
            column=1,
            sticky="e",
        )

        dialog.bind("<Escape>", lambda _event: _close(False))
        dialog.wait_window()
        return bool(result["confirmed"])

    def _normalized_existing_path(self, value: str) -> str:
        cleaned = str(value).strip()
        if not cleaned:
            return ""
        candidate = Path(cleaned).expanduser()
        try:
            return str(candidate.resolve())
        except OSError:
            return str(candidate)

    def _source_file_references_dataset(self, source_path: str, dataset: DatasetRecord, base_dir: str) -> bool:
        source_candidate = Path(source_path).expanduser()
        if not source_candidate.is_absolute():
            source_candidate = Path(base_dir).expanduser() / source_candidate
        try:
            resolved_source = source_candidate.resolve()
        except OSError:
            resolved_source = source_candidate
        if not resolved_source.exists():
            return False

        dataset_setting_paths = {
            self._normalized_existing_path(dataset.frame_series_settings_file),
            self._normalized_existing_path(dataset.tilt_series_settings_file),
        }
        dataset_setting_paths.discard("")
        if not dataset_setting_paths:
            return False

        try:
            root = ET.parse(resolved_source).getroot()
        except Exception:
            return False

        values: list[str] = []
        for element in root.iter():
            text = str(element.text or "").strip()
            if text:
                values.append(text)
            for attr_value in element.attrib.values():
                cleaned = str(attr_value).strip()
                if cleaned:
                    values.append(cleaned)

        for value in values:
            lower = value.casefold()
            if ".settings" not in lower:
                continue
            candidate = Path(value).expanduser()
            if not candidate.is_absolute():
                candidate = resolved_source.parent / candidate
            try:
                normalized_candidate = str(candidate.resolve())
            except OSError:
                normalized_candidate = str(candidate)
            if normalized_candidate in dataset_setting_paths:
                return True
        return False

    def _remove_selected_dataset_records(self, selected_names: list[str]) -> int:
        self.sync_to_project(self.app.project)
        selected_name_set = set(selected_names)
        removed_datasets = [
            dataset for dataset in self.app.project.datasets if dataset.dataset_name in selected_name_set
        ]
        self.app.project.datasets = [
            dataset for dataset in self.app.project.datasets if dataset.dataset_name not in selected_name_set
        ]
        for dataset_name in selected_name_set:
            self.app.project.state.file_registry_overrides.pop(dataset_name, None)
        self.app.project.state.tomograms_selection = [
            item
            for item in self.app.project.state.tomograms_selection
            if item.get("dataset_name", "") not in selected_name_set
        ]

        for population in self.app.project.m_populations:
            population.sources = [
                source
                for source in population.sources
                if not any(
                    self._source_file_references_dataset(source.get("path", ""), dataset, population.directory)
                    for dataset in removed_datasets
                )
            ]
        return len(removed_datasets)

    def _toggle_import_overwrite(self) -> None:
        self.import_overwrite_visible = not self.import_overwrite_visible
        if self.import_overwrite_visible:
            self.import_overwrite_frame.grid()
            self.import_overwrite_toggle.configure(text="Hide overwrite parameter")
        else:
            self.import_overwrite_frame.grid_remove()
            self.import_overwrite_toggle.configure(text="Show overwrite parameter")

    def _browse_raw_frames(self) -> None:
        path = filedialog.askdirectory(title="Select raw frames folder")
        if path:
            self.raw_frames_entry.set(path)

    def _browse_mdocs(self) -> None:
        path = filedialog.askdirectory(title="Select mdocs folder")
        if path:
            self.mdocs_entry.set(path)

    def _browse_gain_file(self) -> None:
        path = filedialog.askopenfilename(title="Select gain file")
        if path:
            self.gain_file_entry.set(path)

    def _browse_processing_folder(self) -> None:
        path = filedialog.askdirectory(title="Select processing folder")
        if path:
            self.processing_folder_entry.set(path)

    def _browse_import_frame_settings(self) -> None:
        path = filedialog.askopenfilename(
            title="Select frameseries settings file",
            filetypes=[("Warp settings", "*.settings"), ("All files", "*.*")],
        )
        if path:
            self.import_frame_settings_entry.set(path)
            self._refresh_import_settings_defaults()

    def _browse_import_tilt_settings(self) -> None:
        path = filedialog.askopenfilename(
            title="Select tiltseries settings file",
            filetypes=[("Warp settings", "*.settings"), ("All files", "*.*")],
        )
        if path:
            self.import_tilt_settings_entry.set(path)
            self._refresh_import_settings_defaults()

    def _browse_import_raw_frames(self) -> None:
        path = filedialog.askdirectory(title="Select raw frames folder")
        if path:
            self.import_raw_frames_entry.set(path)

    def _browse_import_mdocs(self) -> None:
        path = filedialog.askdirectory(title="Select mdocs folder")
        if path:
            self.import_mdocs_entry.set(path)

    def _browse_import_gain_file(self) -> None:
        path = filedialog.askopenfilename(title="Select gain file")
        if path:
            self.import_gain_file_entry.set(path)

    def _browse_import_processing_folder(self) -> None:
        path = filedialog.askdirectory(title="Select processing folder")
        if path:
            self.import_processing_folder_entry.set(path)

    def _browse_import_tomostar_folder(self) -> None:
        path = filedialog.askdirectory(title="Select tomostar folder")
        if path:
            self.import_tomostar_folder_entry.set(path)

    def _browse_import_frame_processing_folder(self) -> None:
        path = filedialog.askdirectory(title="Select frameseries processing folder")
        if path:
            self.import_frame_processing_folder_entry.set(path)

    def _browse_import_tilt_processing_folder(self) -> None:
        path = filedialog.askdirectory(title="Select tiltseries processing folder")
        if path:
            self.import_tilt_processing_folder_entry.set(path)

    def _load_settings_summary(self, path: str, label: str) -> WarpSettingsSummary | None:
        if not path:
            return None
        try:
            return parse_warp_settings(path)
        except Exception as exc:
            messagebox.showerror("Cannot read settings file", f"{label}:\n{exc}")
            return None

    def _default_import_processing_root(self) -> str:
        candidates = [
            Path(path)
            for path in (
                self.import_frame_settings_entry.get(),
                self.import_tilt_settings_entry.get(),
            )
            if path
        ]
        if not candidates:
            return ""
        parents = [path.expanduser().resolve().parent for path in candidates]
        first = parents[0]
        if all(parent == first for parent in parents[1:]):
            return str(first)
        return str(parents[-1])

    def _refresh_import_settings_defaults(self) -> None:
        frame_summary = self._load_settings_summary(
            self.import_frame_settings_entry.get(),
            "Frameseries settings file",
        )
        tilt_summary = self._load_settings_summary(
            self.import_tilt_settings_entry.get(),
            "Tiltseries settings file",
        )
        self.import_frame_settings_summary = frame_summary
        self.import_tilt_settings_summary = tilt_summary

        if frame_summary is None and tilt_summary is None:
            return

        processing_root = self._default_import_processing_root()
        pixel_size = (
            tilt_summary.pixel_size
            if tilt_summary and tilt_summary.pixel_size
            else frame_summary.pixel_size
            if frame_summary
            else 0.0
        )
        exposure = (
            tilt_summary.exposure
            if tilt_summary and tilt_summary.exposure
            else frame_summary.exposure
            if frame_summary
            else 0.0
        )
        if frame_summary is not None:
            self.import_raw_frames_entry.set(frame_summary.data_folder)
            self.import_gain_file_entry.set(frame_summary.gain_path)
            self.import_frame_processing_folder_entry.set(frame_summary.processing_folder)
        if tilt_summary is not None:
            self.import_tilt_processing_folder_entry.set(tilt_summary.processing_folder)
            self.import_tomostar_folder_entry.set(tilt_summary.data_folder)
            if not self.import_gain_file_entry.get():
                self.import_gain_file_entry.set(tilt_summary.gain_path)
        if processing_root:
            self.import_processing_folder_entry.set(processing_root)
        if pixel_size:
            self.import_pixel_size_entry.set(str(pixel_size))
        if exposure:
            self.import_exposure_entry.set(str(exposure))
        if tilt_summary is not None:
            self.import_tomogram_x_entry.set(str(tilt_summary.tomo_x or ""))
            self.import_tomogram_y_entry.set(str(tilt_summary.tomo_y or ""))
            self.import_tomogram_z_entry.set(str(tilt_summary.tomo_z or ""))
        elif not self.import_tilt_settings_entry.get():
            self.import_tomogram_x_entry.set("")
            self.import_tomogram_y_entry.set("")
            self.import_tomogram_z_entry.set("")

    def _sorted_datasets(self, project: ProjectData) -> list[DatasetRecord]:
        sort_key_map = {
            "dataset_name": lambda item: item.dataset_name.casefold(),
            "sample": lambda item: item.sample.casefold(),
            "number_of_ts": lambda item: self._count_number_of_ts(item),
            "pixel_size": lambda item: item.pixel_size,
            "exposure": lambda item: item.exposure,
            "dimensions": lambda item: (item.tomogram_x, item.tomogram_y, item.tomogram_z),
            "raw_frames_folder": lambda item: item.raw_frames_folder.casefold(),
            "processing_folder": lambda item: item.processing_folder.casefold(),
            "created_at": lambda item: item.created_at,
        }
        sort_key = sort_key_map.get(project.dataset_sort_column, sort_key_map["created_at"])
        return sorted(project.datasets, key=sort_key, reverse=project.dataset_sort_descending)

    def _sort_by_column(self, column: str) -> None:
        if self.sort_column == column:
            self.sort_descending = not self.sort_descending
        else:
            self.sort_column = column
            self.sort_descending = False
        self.sync_to_project(self.app.project)
        self._refresh_table(self.app.project)
        self.app.status_var.set(f"Sorted datasets by {column}")

    def _build_dataset_processing_folder(self, base_folder: str, dataset_name: str) -> str:
        dataset_folder = Path(base_folder) / _sanitize_dataset_folder_name(dataset_name)
        dataset_folder.mkdir(parents=True, exist_ok=True)
        return str(dataset_folder)

    def _apply_default_processing_paths(self, dataset: DatasetRecord) -> None:
        base = Path(dataset.processing_folder)
        dataset.frame_series_settings_file = str(base / "warp_frameseries.settings")
        dataset.tilt_series_settings_file = str(base / "warp_tiltseries.settings")
        dataset.frame_series_processing_folder = str(base / "warp_frameseries")
        dataset.tilt_series_processing_folder = str(base / "warp_tiltseries")
        dataset.tilt_series_data_folder = str(base / "tomostar")

    def _effective_thumbnail_source_folder(self, dataset: DatasetRecord) -> str:
        return effective_thumbnail_source_folder(dataset)

    def _thumbnail_cache_folder(self, dataset: DatasetRecord) -> str:
        return str(resolve_thumbnail_cache_dir(self.app.project, dataset))

    def _count_tomostar_files(self, dataset: DatasetRecord) -> int:
        folder_value = dataset.tilt_series_data_folder.strip()
        if not folder_value:
            return 0
        folder = Path(folder_value)
        if not folder.exists():
            return 0
        try:
            return sum(1 for path in folder.rglob("*.tomostar") if path.is_file())
        except OSError:
            return 0

    def _count_filtered_mdocs(self, dataset: DatasetRecord) -> int:
        filtered = filtered_mdoc_paths(dataset)
        if filtered:
            return len(filtered)
        if not dataset.mdocs_folder.strip():
            return 0
        folder = Path(dataset.mdocs_folder)
        if not folder.exists():
            return 0
        try:
            return sum(1 for path in folder.iterdir() if path.is_file() and path.suffix.lower() == ".mdoc")
        except OSError:
            return 0

    def _count_number_of_ts(self, dataset: DatasetRecord) -> int:
        tomostar_count = self._count_tomostar_files(dataset)
        if tomostar_count:
            return tomostar_count
        return self._count_filtered_mdocs(dataset)

    def _filtered_mdoc_files(self, dataset: DatasetRecord) -> list[Path]:
        return filtered_mdoc_paths(dataset)

    def _prepare_mdocs_folder(self, dataset: DatasetRecord) -> tuple[str, int, dict[str, str]]:
        return prepare_unified_mdocs_directory(dataset)

    def _ensure_unique_dataset_name(self, dataset_name: str, *, parent: tk.Misc | None = None) -> bool:
        candidate = dataset_name.strip()
        try:
            assert_unique_dataset_names(
                [
                    *self.app.project.datasets,
                    DatasetRecord(
                        dataset_name=candidate,
                        sample="",
                        pixel_size=0.0,
                        exposure=0.0,
                        tomogram_x=0,
                        tomogram_y=0,
                        tomogram_z=0,
                        raw_frames_folder="",
                        mdocs_folder="",
                    ),
                ]
            )
        except ValueError as exc:
            messagebox.showerror("Duplicate dataset name", str(exc), parent=parent or self.frame)
            return False
        return True

    def _selected_dataset_from_table(self) -> DatasetRecord | None:
        selection = self.dataset_table.selection()
        if not selection:
            return None
        values = self.dataset_table.item(selection[0]).get("values", [])
        if not values:
            return None
        dataset_name = str(values[0])
        return next(
            (dataset for dataset in self.app.project.datasets if dataset.dataset_name == dataset_name),
            None,
        )

    def _show_selected_dataset_details(self, _event=None) -> None:
        dataset = self._selected_dataset_from_table()
        if dataset is None:
            return

        cache_enabled = project_preference_enabled(self.app.project, "use_downscaled_thumbnails", default=True)
        cache_size = project_preference_int(
            self.app.project,
            "thumbnail_cache_size",
            default=256,
            minimum=32,
            maximum=4096,
        )
        effective_thumbnail_source = self._effective_thumbnail_source_folder(dataset)
        thumbnail_cache_folder = self._thumbnail_cache_folder(dataset)

        sections = [
            (
                "General",
                [
                    ("Dataset", dataset.dataset_name),
                    ("Sample", dataset.sample),
                    ("Comment", dataset.comment or "-"),
                    ("Added", dataset.created_at.replace("T", " ")),
                ],
            ),
            (
                "Acquisition",
                [
                    ("Pixelsize", str(dataset.pixel_size)),
                    ("Exposure", str(dataset.exposure)),
                    ("Tomogram dimensions", f"{dataset.tomogram_x} x {dataset.tomogram_y} x {dataset.tomogram_z}"),
                ],
            ),
            (
                "Input paths",
                [
                    ("Raw frames folder", dataset.raw_frames_folder or "-"),
                    ("Mdocs source folder", dataset.mdocs_source_folder or "-"),
                    ("Active mdocs folder", dataset.mdocs_folder or "-"),
                    ("Prepared mdocs folder", dataset.unified_mdocs_folder or "-"),
                    ("Gain file", dataset.gain_file or "-"),
                ],
            ),
            (
                "Processing paths",
                [
                    ("Processing root folder", dataset.processing_root_folder or "-"),
                    ("Processing folder", dataset.processing_folder or "-"),
                    ("Frameseries settings file", dataset.frame_series_settings_file or "-"),
                    ("Tiltseries settings file", dataset.tilt_series_settings_file or "-"),
                    ("Frameseries processing folder", dataset.frame_series_processing_folder or "-"),
                    ("Tiltseries processing folder", dataset.tilt_series_processing_folder or "-"),
                    ("Tomostar folder", dataset.tilt_series_data_folder or "-"),
                    ("Stored thumbnail folder", dataset.thumbnail_folder or "-"),
                    ("Stored tomogram folder", dataset.tomogram_folder or "-"),
                    ("Effective thumbnail source", effective_thumbnail_source or "-"),
                    ("Thumbnail cache enabled", "Yes" if cache_enabled else "No"),
                    ("Thumbnail cache folder", thumbnail_cache_folder if cache_enabled else "(disabled)"),
                    ("Thumbnail cache size", f"{cache_size} x {cache_size}" if cache_enabled else "-"),
                ],
            ),
            (
                "MDOC handling",
                [
                    ("Unify mdoc names", "Yes" if dataset.unified_mdoc_names else "No"),
                    ("Ignore override.mdoc", "Yes" if dataset.ignore_override_mdocs else "No"),
                    ("Ignore custom.mdoc", "Yes" if dataset.ignore_custom_mdocs else "No"),
                    ("Custom ignore pattern", dataset.ignore_custom_mdocs_pattern or "-"),
                    ("Number of TS", str(self._count_number_of_ts(dataset))),
                    ("Resolved TS names", ", ".join(dataset_ts_names(dataset)) or "-"),
                ],
            ),
            (
                "Stored state",
                [
                    ("Job history entries", str(len(dataset.job_history))),
                    ("Thumbnails tracked", str(len(dataset.thumbnails))),
                ],
            ),
        ]
        show_detail_dialog(self.frame, "Dataset details", sections)

    def _refresh_table(self, project: ProjectData) -> None:
        for item in self.dataset_table.get_children():
            self.dataset_table.delete(item)

        datasets = self._sorted_datasets(project)
        for dataset in datasets:
            self.dataset_table.insert(
                "",
                "end",
                values=(
                    dataset.dataset_name,
                    dataset.sample,
                    self._count_number_of_ts(dataset),
                    dataset.pixel_size,
                    dataset.exposure,
                    f"{dataset.tomogram_x}, {dataset.tomogram_y}, {dataset.tomogram_z}",
                    dataset.raw_frames_folder,
                    dataset.processing_folder,
                    dataset.created_at.replace("T", " "),
                ),
            )

        label = "dataset" if len(datasets) == 1 else "datasets"
        self.dataset_count_label.config(text=f"{len(datasets)} {label}")

    def _require_float(self, value: str, label: str) -> float:
        try:
            return float(value)
        except ValueError as exc:
            raise ValueError(f"{label} must be a numerical value.") from exc

    def _require_int(self, value: str, label: str) -> int:
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"{label} must be an integer.") from exc

    def add_dataset(self) -> None:
        try:
            dataset = DatasetRecord(
                dataset_name=self.dataset_name_entry.get(),
                sample=self.sample_entry.get(),
                pixel_size=self._require_float(self.pixel_size_entry.get(), "Pixelsize"),
                exposure=self._require_float(self.exposure_entry.get(), "Exposure"),
                tomogram_x=self._require_int(self.tomogram_x_entry.get(), "Tomogram X"),
                tomogram_y=self._require_int(self.tomogram_y_entry.get(), "Tomogram Y"),
                tomogram_z=self._require_int(self.tomogram_z_entry.get(), "Tomogram Z"),
                raw_frames_folder=self.raw_frames_entry.get(),
                mdocs_folder=self.mdocs_entry.get(),
                mdocs_source_folder=self.mdocs_entry.get(),
                unified_mdoc_names=self.unify_mdoc_names_var.get(),
                ignore_override_mdocs=self.ignore_override_mdocs_var.get(),
                ignore_custom_mdocs=self.ignore_custom_mdocs_var.get(),
                ignore_custom_mdocs_pattern=self.ignore_custom_mdocs_pattern_var.get().strip(),
                gain_file=self.gain_file_entry.get(),
                processing_root_folder=self.processing_folder_entry.get(),
                comment=self.comment_entry.get(),
            )
        except ValueError as exc:
            messagebox.showerror("Invalid dataset", str(exc))
            return

        missing_labels = []
        if not dataset.dataset_name:
            missing_labels.append("Dataset name")
        if not dataset.sample:
            missing_labels.append("Sample")
        if not dataset.raw_frames_folder:
            missing_labels.append("Raw frames folder")
        if not dataset.mdocs_folder:
            missing_labels.append("Mdocs folder")
        if not dataset.processing_root_folder:
            missing_labels.append("Processing folder")
        if missing_labels:
            messagebox.showerror(
                "Missing values",
                "Please fill the following fields:\n- " + "\n- ".join(missing_labels),
            )
            return
        if not self._ensure_unique_dataset_name(dataset.dataset_name):
            return

        try:
            dataset.processing_folder = self._build_dataset_processing_folder(
                dataset.processing_root_folder,
                dataset.dataset_name,
            )
            self._apply_default_processing_paths(dataset)
        except OSError as exc:
            messagebox.showerror("Cannot create dataset folder", f"Failed to create processing folder:\n{exc}")
            return

        try:
            if dataset.unified_mdoc_names:
                unified_folder, file_count, prepared_map = self._prepare_mdocs_folder(dataset)
                dataset.unified_mdocs_folder = unified_folder
                dataset.mdocs_folder = unified_folder
                dataset.prepared_mdoc_map = prepared_map
                status_detail = f"{file_count} mdocs prepared"
            else:
                source_mdocs = self._filtered_mdoc_files(dataset)
                if not source_mdocs:
                    raise ValueError("No .mdoc files remained after applying the selected ignore filters.")
                file_count = len(source_mdocs)
                dataset.unified_mdocs_folder = ""
                dataset.mdocs_folder = dataset.mdocs_source_folder
                dataset.prepared_mdoc_map = {}
                status_detail = f"{file_count} original mdocs linked"
        except (OSError, ValueError) as exc:
            messagebox.showerror("Cannot prepare mdoc files", str(exc))
            return

        self.sync_to_project(self.app.project)
        self.app.project.datasets.append(dataset)
        self.clear_form()
        self.dataset_action_var.set("Project actions")
        self._apply_dataset_action_view("Project actions")
        self.app.on_project_changed("datasets")
        self.app.status_var.set(f"Added dataset: {dataset.dataset_name} ({status_detail})")

    def _import_history_entry(self, dataset: DatasetRecord) -> JobHistoryEntry:
        parameters = {
            "dataset_name": dataset.dataset_name,
            "sample": dataset.sample,
            "comment": dataset.comment,
            "frameseries_settings_file": dataset.frame_series_settings_file,
            "tiltseries_settings_file": dataset.tilt_series_settings_file,
            "raw_frames_folder": dataset.raw_frames_folder,
            "gain_file": dataset.gain_file,
            "processing_folder": dataset.processing_folder,
            "pixel_size": str(dataset.pixel_size),
            "exposure": str(dataset.exposure),
            "tomogram_dimensions": f"{dataset.tomogram_x}x{dataset.tomogram_y}x{dataset.tomogram_z}",
            "frameseries_processing_folder": dataset.frame_series_processing_folder,
            "tiltseries_processing_folder": dataset.tilt_series_processing_folder,
            "tomostar_folder": dataset.tilt_series_data_folder,
            "mdocs_folder": dataset.mdocs_source_folder,
            "ignore_override_mdocs": "true" if dataset.ignore_override_mdocs else "false",
            "ignore_custom_mdocs": "true" if dataset.ignore_custom_mdocs else "false",
            "ignore_custom_mdocs_pattern": dataset.ignore_custom_mdocs_pattern,
        }
        return JobHistoryEntry(
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            action="imported",
            group="Project Overview",
            job_name="Import already processed dataset",
            command="Imported processed dataset from existing Warp settings files",
            processing_tab="Project Overview",
            dataset_name=dataset.dataset_name,
            parameters=parameters,
        )

    def import_processed_dataset(self) -> None:
        try:
            dataset = DatasetRecord(
                dataset_name=self.import_dataset_name_entry.get(),
                sample=self.import_sample_entry.get(),
                pixel_size=self._require_float(self.import_pixel_size_entry.get(), "Pixelsize"),
                exposure=self._require_float(self.import_exposure_entry.get(), "Exposure"),
                tomogram_x=self._require_int(self.import_tomogram_x_entry.get(), "Tomogram X"),
                tomogram_y=self._require_int(self.import_tomogram_y_entry.get(), "Tomogram Y"),
                tomogram_z=self._require_int(self.import_tomogram_z_entry.get(), "Tomogram Z"),
                raw_frames_folder=self.import_raw_frames_entry.get(),
                mdocs_folder=self.import_mdocs_entry.get(),
                mdocs_source_folder=self.import_mdocs_entry.get(),
                unified_mdoc_names=False,
                ignore_override_mdocs=self.import_ignore_override_mdocs_var.get(),
                ignore_custom_mdocs=self.import_ignore_custom_mdocs_var.get(),
                ignore_custom_mdocs_pattern=self.import_ignore_custom_mdocs_pattern_var.get().strip(),
                gain_file=self.import_gain_file_entry.get(),
                frame_series_settings_file=self.import_frame_settings_entry.get(),
                tilt_series_settings_file=self.import_tilt_settings_entry.get(),
                frame_series_processing_folder=self.import_frame_processing_folder_entry.get(),
                tilt_series_processing_folder=self.import_tilt_processing_folder_entry.get(),
                tilt_series_data_folder=self.import_tomostar_folder_entry.get(),
                processing_root_folder=self.import_processing_folder_entry.get(),
                processing_folder=self.import_processing_folder_entry.get(),
                comment=self.import_comment_entry.get(),
            )
        except ValueError as exc:
            messagebox.showerror("Invalid dataset", str(exc))
            return

        missing_labels = []
        if not dataset.dataset_name:
            missing_labels.append("Dataset name")
        if not dataset.sample:
            missing_labels.append("Sample")
        if not dataset.frame_series_settings_file:
            missing_labels.append("Frameseries.settings file")
        if not dataset.tilt_series_settings_file:
            missing_labels.append("Tiltseries.settings file")
        if not dataset.mdocs_source_folder:
            missing_labels.append("Mdocs folder")
        if not dataset.processing_folder:
            missing_labels.append("Processing folder")
        if not dataset.frame_series_processing_folder:
            missing_labels.append("Frameseries processing folder")
        if not dataset.tilt_series_processing_folder:
            missing_labels.append("Tiltseries processing folder")
        if not dataset.tilt_series_data_folder:
            missing_labels.append("Tomostar folder")
        if missing_labels:
            messagebox.showerror(
                "Missing values",
                "Please fill the following fields:\n- " + "\n- ".join(missing_labels),
            )
            return
        if not self._ensure_unique_dataset_name(dataset.dataset_name):
            return

        dataset.job_history.append(self._import_history_entry(dataset))
        self.sync_to_project(self.app.project)
        self.app.project.datasets.append(dataset)
        self.clear_import_form()
        self.dataset_action_var.set("Project actions")
        self._apply_dataset_action_view("Project actions")
        self.app.on_project_changed("datasets")
        self.app.status_var.set(f"Imported processed dataset: {dataset.dataset_name}")

    def clear_form(self) -> None:
        defaults = {
            self.dataset_name_entry: "",
            self.sample_entry: self._project_default(
                "Add dataset for processing", "add_dataset_for_processing", "sample", ""
            ),
            self.comment_entry: self._project_default(
                "Add dataset for processing", "add_dataset_for_processing", "comment", ""
            ),
            self.raw_frames_entry: self._project_default(
                "Add dataset for processing", "add_dataset_for_processing", "raw_frames_folder", ""
            ),
            self.mdocs_entry: self._project_default(
                "Add dataset for processing", "add_dataset_for_processing", "mdocs_folder", ""
            ),
            self.gain_file_entry: self._project_default(
                "Add dataset for processing", "add_dataset_for_processing", "gain_file", ""
            ),
            self.processing_folder_entry: self._project_default(
                "Add dataset for processing", "add_dataset_for_processing", "processing_folder", ""
            ),
            self.pixel_size_entry: self._project_default(
                "Add dataset for processing", "add_dataset_for_processing", "pixel_size", ""
            ),
            self.exposure_entry: self._project_default(
                "Add dataset for processing", "add_dataset_for_processing", "exposure", ""
            ),
            self.tomogram_x_entry: self._project_default(
                "Add dataset for processing", "add_dataset_for_processing", "tomogram_x", ""
            ),
            self.tomogram_y_entry: self._project_default(
                "Add dataset for processing", "add_dataset_for_processing", "tomogram_y", ""
            ),
            self.tomogram_z_entry: self._project_default(
                "Add dataset for processing", "add_dataset_for_processing", "tomogram_z", ""
            ),
        }
        for widget, value in defaults.items():
            widget.set(value)
        self.unify_mdoc_names_var.set(
            self._project_default_bool(
                "Add dataset for processing",
                "add_dataset_for_processing",
                "unify_mdoc_names",
                True,
            )
        )
        self.ignore_override_mdocs_var.set(
            self._project_default_bool(
                "Add dataset for processing",
                "add_dataset_for_processing",
                "ignore_override_mdocs",
                False,
            )
        )
        self.ignore_custom_mdocs_var.set(
            self._project_default_bool(
                "Add dataset for processing",
                "add_dataset_for_processing",
                "ignore_custom_mdocs",
                False,
            )
        )
        self.ignore_custom_mdocs_pattern_var.set(
            self._project_default(
                "Add dataset for processing",
                "add_dataset_for_processing",
                "ignore_custom_mdocs_pattern",
                "",
            )
        )

    def clear_import_form(self) -> None:
        defaults = {
            self.import_dataset_name_entry: "",
            self.import_sample_entry: self._project_default(
                "Import already processed dataset", "import_processed_dataset", "sample", ""
            ),
            self.import_comment_entry: self._project_default(
                "Import already processed dataset", "import_processed_dataset", "comment", ""
            ),
            self.import_frame_settings_entry: self._project_default(
                "Import already processed dataset", "import_processed_dataset", "frameseries_settings_file", ""
            ),
            self.import_tilt_settings_entry: self._project_default(
                "Import already processed dataset", "import_processed_dataset", "tiltseries_settings_file", ""
            ),
            self.import_mdocs_entry: self._project_default(
                "Import already processed dataset", "import_processed_dataset", "mdocs_folder", ""
            ),
            self.import_raw_frames_entry: self._project_default(
                "Import already processed dataset", "import_processed_dataset", "raw_frames_folder", ""
            ),
            self.import_gain_file_entry: self._project_default(
                "Import already processed dataset", "import_processed_dataset", "gain_file", ""
            ),
            self.import_processing_folder_entry: self._project_default(
                "Import already processed dataset", "import_processed_dataset", "processing_folder", ""
            ),
            self.import_pixel_size_entry: self._project_default(
                "Import already processed dataset", "import_processed_dataset", "pixel_size", ""
            ),
            self.import_exposure_entry: self._project_default(
                "Import already processed dataset", "import_processed_dataset", "exposure", ""
            ),
            self.import_tomostar_folder_entry: self._project_default(
                "Import already processed dataset", "import_processed_dataset", "tomostar_folder", ""
            ),
            self.import_tomogram_x_entry: self._project_default(
                "Import already processed dataset", "import_processed_dataset", "tomogram_x", ""
            ),
            self.import_tomogram_y_entry: self._project_default(
                "Import already processed dataset", "import_processed_dataset", "tomogram_y", ""
            ),
            self.import_tomogram_z_entry: self._project_default(
                "Import already processed dataset", "import_processed_dataset", "tomogram_z", ""
            ),
            self.import_frame_processing_folder_entry: self._project_default(
                "Import already processed dataset",
                "import_processed_dataset",
                "frameseries_processing_folder",
                "",
            ),
            self.import_tilt_processing_folder_entry: self._project_default(
                "Import already processed dataset",
                "import_processed_dataset",
                "tiltseries_processing_folder",
                "",
            ),
        }
        for widget, value in defaults.items():
            widget.set(value)
        self.import_ignore_override_mdocs_var.set(
            self._project_default_bool(
                "Import already processed dataset",
                "import_processed_dataset",
                "ignore_override_mdocs",
                False,
            )
        )
        self.import_ignore_custom_mdocs_var.set(
            self._project_default_bool(
                "Import already processed dataset",
                "import_processed_dataset",
                "ignore_custom_mdocs",
                False,
            )
        )
        self.import_ignore_custom_mdocs_pattern_var.set(
            self._project_default(
                "Import already processed dataset",
                "import_processed_dataset",
                "ignore_custom_mdocs_pattern",
                "",
            )
        )
        self.import_frame_settings_summary = None
        self.import_tilt_settings_summary = None
        if self.import_overwrite_visible:
            self._toggle_import_overwrite()

    def sync_to_project(self, project: ProjectData) -> None:
        project.name = self.project_name_entry.get() or "Untitled Project"
        project.dataset_sort_column = self.sort_column
        project.dataset_sort_descending = self.sort_descending
        self.layout_pane.write_to_project(project)

    def on_project_loaded(self, project: ProjectData) -> None:
        project_id = id(project)
        if self._layout_project_id != project_id:
            self._layout_project_id = project_id
            self.layout_pane.restore_from_project(project)
        self.project_name_entry.set(project.name)
        self.sort_column = project.dataset_sort_column
        self.sort_descending = project.dataset_sort_descending
        self.remove_selected_datasets.clear()
        self.dataset_action_var.set("Project actions")
        self._apply_dataset_action_view("Project actions")
        if self.import_overwrite_visible:
            self._toggle_import_overwrite()
        self._apply_custom_defaults()
        self._refresh_table(project)
        self._refresh_remove_dataset_table(project)
        self._schedule_outer_layout_refresh()

    def reset_window_sizes(self) -> None:
        self.layout_pane.reset_to_defaults()
        self._schedule_outer_layout_refresh()
