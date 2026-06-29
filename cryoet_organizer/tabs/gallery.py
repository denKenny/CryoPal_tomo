from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import tkinter as tk
from queue import Empty, Queue
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from cryoet_organizer.dialogs import autosize_detail_tree_columns, bind_scrollable_canvas
from cryoet_organizer.file_resolver import file_role_order, resolve_dataset_file, role_title
from cryoet_organizer.preferences import project_preference, project_preference_enabled, project_preference_int
from cryoet_organizer.project import (
    DatasetRecord,
    ProjectData,
    ThumbnailRecord,
    best_matching_ts_name,
    dataset_ts_names,
)
from cryoet_organizer.resizable_sections import load_layout_value, save_layout_value
from cryoet_organizer.thumbnail_cache import effective_thumbnail_source_folder, resolve_thumbnail_cache_dir, thumbnail_cache_location
from cryoet_organizer.ts_metadata import collect_ts_metadata, ts_metadata_sections
from cryoet_organizer.tabs.base import SidebarTab

GALLERY_PAGE_SIZE = 50
THUMBNAIL_CACHE_MANIFEST = "index.json"


class _GalleryBusyDialog:
    def __init__(self, parent: tk.Misc, title: str, message: str) -> None:
        self.message_var = tk.StringVar(value=message)
        self.window = tk.Toplevel(parent)
        self.window.title(title)
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
            textvariable=self.message_var,
            wraplength=420,
            justify="left",
        ).grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(body, orient="horizontal", mode="indeterminate", length=320)
        self.progress.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self.progress.start(10)
        self.window.update_idletasks()
        self.window.grab_set()
        self.window.focus_set()

    def set_message(self, message: str) -> None:
        self.message_var.set(message)
        try:
            self.window.update()
        except tk.TclError:
            pass

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


class GalleryTab(SidebarTab):
    tab_id = "gallery"
    title = "Tomogram Gallery"
    refresh_domains = ("gallery", "datasets", "file_registry", "ts_metadata", "preferences")

    def build(self) -> None:
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(0, weight=1)

        self.dataset_var = tk.StringVar()
        self.min_rating_var = tk.StringVar(value="Any")
        self.tag_include_mode_var = tk.StringVar(value="All selected")
        self.tag_input_var = tk.StringVar()
        self.thumbnail_size_var = tk.IntVar(value=210)
        self.thumbnail_size = 210
        self.selected_thumbnail_key: tuple[str, str] | None = None
        self._pending_single_click_after: str | None = None
        self.multi_selection_var = tk.BooleanVar(value=False)
        self.multi_selected_keys: set[tuple[str, str]] = set()
        self.selection_vars: dict[tuple[str, str], tk.BooleanVar] = {}
        self.card_widgets: dict[tuple[str, str], dict[str, tk.Widget]] = {}
        self.gallery_card_pool: list[dict[str, object]] = []
        self.thumbnail_images: dict[tuple[str, int], tk.PhotoImage] = {}
        self.dataset_match_cache: dict[tuple[str, str], list[ThumbnailRecord]] = {}
        self.thumbnail_display_paths: dict[tuple[str, str], str] = {}
        self.thumbnail_cache_state: dict[str, tuple[object, ...]] = {}
        self.thumbnail_record_index: dict[tuple[str, str], tuple[DatasetRecord, ThumbnailRecord]] = {}
        self._pending_render_after: str | None = None
        self._pending_render_batch_after: str | None = None
        self._pending_multi_details_after: str | None = None
        self._pending_auto_select_after: str | None = None
        self._reuse_prepared_records_once = False
        self._last_render_column_count: int | None = None
        self._loaded_project_id: int | None = None
        self._needs_render_when_shown = False
        self._current_page = 0
        self._current_page_count = 0
        self._render_generation = 0
        self._main_pane_initialized = False
        self._default_details_pane_width = self.app._scale_pixels(360)
        self._details_pane_min_width = self.app._scale_pixels(320)
        self._details_pane_width = self._default_details_pane_width

        self.selected_dataset_var = tk.StringVar(value="-")
        self.selected_ts_var = tk.StringVar(value="-")
        self.selected_pixelsize_var = tk.StringVar(value="-")
        self.selected_tags_var = tk.StringVar(value="-")
        self.selected_mrc_var = tk.StringVar(value="-")
        self.selected_rating_var = tk.IntVar(value=0)
        self.delete_raw_data_var = tk.BooleanVar(value=False)

        self.main_pane = tk.PanedWindow(
            self.frame,
            orient="horizontal",
            sashwidth=6,
            opaqueresize=True,
            bd=0,
            relief="flat",
        )
        self.main_pane.grid(row=0, column=0, sticky="nsew")
        self.main_pane.bind("<Configure>", self._on_main_pane_configure)

        left_panel = ttk.Frame(self.main_pane)
        left_panel.columnconfigure(0, weight=1)
        left_panel.rowconfigure(2, weight=1)

        controls = ttk.LabelFrame(left_panel, text="Gallery selection", padding=12)
        controls.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        for column in range(7):
            controls.columnconfigure(column, weight=1 if column in (0, 4) else 0)

        ttk.Label(controls, text="Dataset").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.dataset_combo = ttk.Combobox(
            controls,
            textvariable=self.dataset_var,
            state="readonly",
        )
        self.dataset_combo.grid(row=1, column=0, sticky="ew", padx=(0, 12))
        self.dataset_combo.bind("<<ComboboxSelected>>", self._on_dataset_selected)

        ttk.Label(controls, text="Min rating").grid(row=0, column=1, sticky="w", pady=(0, 4))
        self.rating_filter_combo = ttk.Combobox(
            controls,
            textvariable=self.min_rating_var,
            state="readonly",
            values=["Any", "1", "2", "3", "4", "5"],
            width=8,
        )
        self.rating_filter_combo.grid(row=1, column=1, sticky="ew", padx=(0, 12))
        self.rating_filter_combo.bind("<<ComboboxSelected>>", self._on_gallery_filter_changed)

        ttk.Label(controls, text="Thumbnail size").grid(row=0, column=3, sticky="w", pady=(0, 4))
        zoom_controls = ttk.Frame(controls)
        zoom_controls.grid(row=1, column=3, sticky="w", padx=(0, 12))
        ttk.Button(zoom_controls, text="-", width=3, command=lambda: self._change_zoom(-1)).grid(
            row=0, column=0
        )
        ttk.Label(zoom_controls, textvariable=self.thumbnail_size_var, width=5).grid(
            row=0, column=1, padx=6
        )
        ttk.Button(zoom_controls, text="+", width=3, command=lambda: self._change_zoom(1)).grid(
            row=0, column=2
        )

        action_buttons = ttk.Frame(controls)
        action_buttons.grid(row=1, column=4, sticky="ew")
        action_buttons.columnconfigure(2, weight=1)
        ttk.Button(
            action_buttons,
            text="Reset filters",
            command=self._reset_gallery_filters,
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.manual_import_button = ttk.Menubutton(
            action_buttons,
            text="Manual import",
        )
        self.manual_import_button.grid(row=0, column=1, columnspan=2, sticky="e")
        self.manual_import_menu = tk.Menu(self.manual_import_button, tearoff=False)
        self.manual_import_menu.add_command(label="Import thumbnail folder", command=self._import_thumbnails)
        self.manual_import_menu.add_command(label="Link .mrc from folder", command=self._link_mrc_folder)
        self.manual_import_button.configure(menu=self.manual_import_menu)
        ttk.Button(
            controls,
            text="Multi selection",
            command=self._toggle_multi_selection,
        ).grid(row=1, column=5, sticky="e", padx=(8, 0))
        self.select_all_button = ttk.Button(
            controls,
            text="Select all filtered",
            command=self._select_all_visible,
        )
        self.select_all_button.grid(row=1, column=6, sticky="e", padx=(8, 0))
        self.select_all_button.grid_remove()
        tag_filters = ttk.LabelFrame(controls, text="Tag filters", padding=10)
        tag_filters.grid(row=2, column=0, columnspan=7, sticky="ew", pady=(12, 0))
        tag_filters.columnconfigure(0, weight=1)
        tag_filters.columnconfigure(1, weight=1)

        include_header = ttk.Frame(tag_filters)
        include_header.grid(row=0, column=0, sticky="ew", padx=(0, 8), pady=(0, 6))
        include_header.columnconfigure(1, weight=1)
        ttk.Label(include_header, text="Must include tags").grid(row=0, column=0, sticky="w")
        self.include_mode_combo = ttk.Combobox(
            include_header,
            textvariable=self.tag_include_mode_var,
            state="readonly",
            values=["All selected", "Any selected"],
            width=14,
        )
        self.include_mode_combo.grid(row=0, column=1, sticky="e")
        self.include_mode_combo.bind("<<ComboboxSelected>>", self._on_gallery_filter_changed)

        exclude_header = ttk.Frame(tag_filters)
        exclude_header.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=(0, 6))
        ttk.Label(exclude_header, text="Must exclude tags").grid(row=0, column=0, sticky="w")

        include_box = ttk.Frame(tag_filters)
        include_box.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        include_box.columnconfigure(0, weight=1)
        include_box.rowconfigure(0, weight=1)
        self.include_tags_listbox = tk.Listbox(include_box, selectmode="extended", exportselection=False, height=5)
        self.include_tags_listbox.grid(row=0, column=0, sticky="nsew")
        include_scroll = ttk.Scrollbar(include_box, orient="vertical", command=self.include_tags_listbox.yview)
        include_scroll.grid(row=0, column=1, sticky="ns")
        self.include_tags_listbox.configure(yscrollcommand=include_scroll.set)
        self.include_tags_listbox.bind("<<ListboxSelect>>", self._on_gallery_filter_changed)

        exclude_box = ttk.Frame(tag_filters)
        exclude_box.grid(row=1, column=1, sticky="nsew", padx=(8, 0))
        exclude_box.columnconfigure(0, weight=1)
        exclude_box.rowconfigure(0, weight=1)
        self.exclude_tags_listbox = tk.Listbox(exclude_box, selectmode="extended", exportselection=False, height=5)
        self.exclude_tags_listbox.grid(row=0, column=0, sticky="nsew")
        exclude_scroll = ttk.Scrollbar(exclude_box, orient="vertical", command=self.exclude_tags_listbox.yview)
        exclude_scroll.grid(row=0, column=1, sticky="ns")
        self.exclude_tags_listbox.configure(yscrollcommand=exclude_scroll.set)
        self.exclude_tags_listbox.bind("<<ListboxSelect>>", self._on_gallery_filter_changed)

        self.summary_label = ttk.Label(left_panel, text="", wraplength=900, justify="left")
        self.summary_label.grid(row=1, column=0, sticky="nw", pady=(12, 8))

        self.pager_row = ttk.Frame(controls)
        self.pager_row.grid(row=1, column=2, sticky="ew", padx=(0, 12))
        self.pager_status_var = tk.StringVar(value="")
        self.prev_page_button = ttk.Button(self.pager_row, text="Previous", command=lambda: self._change_page(-1))
        self.prev_page_button.grid(row=0, column=0, padx=(0, 6))
        ttk.Label(self.pager_row, textvariable=self.pager_status_var).grid(row=0, column=1, padx=4)
        self.next_page_button = ttk.Button(self.pager_row, text="Next", command=lambda: self._change_page(1))
        self.next_page_button.grid(row=0, column=2, padx=(6, 0))
        self.pager_row.grid_remove()

        gallery_container = ttk.Frame(left_panel)
        gallery_container.grid(row=2, column=0, sticky="nsew")
        gallery_container.columnconfigure(0, weight=1)
        gallery_container.rowconfigure(0, weight=1)

        self.gallery_canvas = tk.Canvas(gallery_container, highlightthickness=0)
        self.gallery_canvas.grid(row=0, column=0, sticky="nsew")
        self.gallery_scrollbar = ttk.Scrollbar(
            gallery_container,
            orient="vertical",
            command=self.gallery_canvas.yview,
        )
        self.gallery_scrollbar.grid(row=0, column=1, sticky="ns")
        self.gallery_canvas.configure(yscrollcommand=self.gallery_scrollbar.set)

        self.gallery_frame = ttk.Frame(self.gallery_canvas)
        self.gallery_window = self.gallery_canvas.create_window((0, 0), window=self.gallery_frame, anchor="nw")
        self.gallery_frame.bind("<Configure>", self._on_gallery_frame_configure)
        self.gallery_canvas.bind("<Configure>", self._on_gallery_canvas_configure)

        details_host = ttk.Frame(self.main_pane)
        details_host.columnconfigure(0, weight=1)
        details_host.rowconfigure(0, weight=1)
        self.details_canvas = tk.Canvas(details_host, highlightthickness=0)
        self.details_canvas.grid(row=0, column=0, sticky="nsew")
        details_yscroll = ttk.Scrollbar(details_host, orient="vertical", command=self.details_canvas.yview)
        details_yscroll.grid(row=0, column=1, sticky="ns")
        self.details_canvas.configure(yscrollcommand=details_yscroll.set)

        details = ttk.LabelFrame(self.details_canvas, text="Thumbnail details", padding=12)
        details.columnconfigure(0, weight=1)
        self.details_window = self.details_canvas.create_window((0, 0), window=details, anchor="nw")
        bind_scrollable_canvas(self.details_canvas, self.details_window, details, allow_horizontal=False)
        self.details_canvas.bind("<Configure>", self._on_details_canvas_configure, add="+")

        ttk.Label(details, text="Dataset").grid(row=0, column=0, sticky="w")
        ttk.Label(details, textvariable=self.selected_dataset_var).grid(row=1, column=0, sticky="w", pady=(0, 8))
        ttk.Label(details, text="TS name").grid(row=2, column=0, sticky="w")
        ttk.Label(details, textvariable=self.selected_ts_var).grid(row=3, column=0, sticky="w", pady=(0, 8))
        ttk.Label(details, text="Pixel size").grid(row=4, column=0, sticky="w")
        ttk.Label(details, textvariable=self.selected_pixelsize_var).grid(row=5, column=0, sticky="w", pady=(0, 12))
        ttk.Label(details, text="Associated MRC").grid(row=6, column=0, sticky="w")
        self.selected_mrc_label = ttk.Label(details, textvariable=self.selected_mrc_var, justify="left")
        self.selected_mrc_label.grid(row=7, column=0, sticky="ew", pady=(0, 8))
        mrc_button_row = ttk.Frame(details)
        mrc_button_row.grid(row=8, column=0, sticky="ew", pady=(0, 12))
        mrc_button_row.columnconfigure(1, weight=1)
        self.open_mrc_button = ttk.Button(
            mrc_button_row,
            text="Open .mrc",
            command=self._open_selected_mrc,
            state="disabled",
        )
        self.open_mrc_button.grid(row=0, column=0, sticky="w")
        self.link_mrc_button = ttk.Button(
            mrc_button_row,
            text="Link .mrc",
            command=self._link_selected_mrc_file,
            state="disabled",
        )
        self.link_mrc_button.grid(row=0, column=1, sticky="e", padx=(8, 0))
        self.add_to_ts_list_button = ttk.Button(
            details,
            text="Add to TS processing list",
            command=self._add_selected_to_ts_processing_list,
            state="disabled",
        )
        self.add_to_ts_list_button.grid(row=9, column=0, sticky="ew", pady=(0, 12))

        ttk.Label(details, text="Rating").grid(row=10, column=0, sticky="w")
        rating_row = ttk.Frame(details)
        rating_row.grid(row=11, column=0, sticky="w", pady=(0, 12))
        for value in range(1, 6):
            ttk.Radiobutton(
                rating_row,
                text=str(value),
                value=value,
                variable=self.selected_rating_var,
                command=self._save_selected_thumbnail_metadata,
            ).grid(row=0, column=value - 1, padx=(0, 6))
        ttk.Button(rating_row, text="Clear", command=self._clear_selected_rating).grid(
            row=0, column=5, padx=(6, 0)
        )

        ttk.Label(details, text="Tags").grid(row=12, column=0, sticky="w")
        self.tag_input_combo = ttk.Combobox(details, textvariable=self.tag_input_var)
        self.tag_input_combo.grid(row=13, column=0, sticky="ew", pady=(0, 6))
        add_tag_row = ttk.Frame(details)
        add_tag_row.grid(row=14, column=0, sticky="w", pady=(0, 12))
        ttk.Button(add_tag_row, text="Add tag", command=self._add_tag_to_selected).grid(
            row=0, column=0, padx=(0, 6)
        )
        ttk.Button(add_tag_row, text="Remove selected tag", command=self._remove_selected_tag).grid(
            row=0, column=1
        )

        self.tag_listbox = tk.Listbox(details, height=8)
        self.tag_listbox.grid(row=15, column=0, sticky="nsew")
        details.rowconfigure(15, weight=1)
        self.selected_tags_label = ttk.Label(details, textvariable=self.selected_tags_var, justify="left")
        self.selected_tags_label.grid(row=16, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(
            details,
            text="Delete TS data",
            command=self._confirm_delete_selected_ts_data,
        ).grid(row=17, column=0, sticky="ew", pady=(12, 6))
        ttk.Checkbutton(
            details,
            text="Delete raw data too",
            variable=self.delete_raw_data_var,
        ).grid(row=18, column=0, sticky="w")
        self.details_frame = details
        self.details_host = details_host
        details_host.configure(width=self._details_pane_width)
        details_host.grid_propagate(False)
        self.main_pane.add(left_panel, minsize=420, stretch="always", sticky="nsew")
        self.main_pane.add(
            details_host,
            minsize=self._details_pane_min_width,
            width=self._details_pane_width,
            stretch="never",
            sticky="nsew",
        )
        self.frame.after_idle(self._initialize_main_pane)

    def _initialize_main_pane(self) -> None:
        if self._main_pane_initialized or not self.main_pane.winfo_exists():
            return
        if self.main_pane.winfo_width() < 600:
            self.frame.after(60, self._initialize_main_pane)
            return
        self._apply_main_pane_width()
        self._update_details_wraplength()
        self._main_pane_initialized = True

    def _apply_main_pane_width(self) -> None:
        self.main_pane.update_idletasks()
        total_width = self.main_pane.winfo_width()
        details_width = max(self._details_pane_min_width, self._details_pane_width)
        try:
            self.details_host.configure(width=details_width)
            self.main_pane.paneconfigure(self.details_host, minsize=self._details_pane_min_width, width=details_width)
            self.main_pane.sash_place(0, total_width - details_width, 0)
        except tk.TclError:
            pass
        self._update_details_wraplength()

    def _on_main_pane_configure(self, _event=None) -> None:
        if not self._main_pane_initialized:
            self._initialize_main_pane()
            return
        self._capture_details_pane_width()

    def _on_details_canvas_configure(self, _event=None) -> None:
        self._update_details_wraplength()

    def _update_details_wraplength(self) -> None:
        available_width = max(140, self.details_canvas.winfo_width() - self.app._scale_pixels(28))
        for label in (getattr(self, "selected_mrc_label", None), getattr(self, "selected_tags_label", None)):
            if label is not None:
                label.configure(wraplength=available_width)

    def _capture_details_pane_width(self) -> None:
        try:
            current_width = int(self.details_host.winfo_width())
        except tk.TclError:
            return
        if current_width <= 0:
            return
        self._details_pane_width = max(self._details_pane_min_width, current_width)
        self._update_details_wraplength()

    def _on_gallery_frame_configure(self, _event=None) -> None:
        self.gallery_canvas.configure(scrollregion=self.gallery_canvas.bbox("all"))

    def _on_gallery_canvas_configure(self, event) -> None:
        self.gallery_canvas.itemconfigure(self.gallery_window, width=event.width)
        column_count = self._column_count_for_width(event.width)
        if column_count != self._last_render_column_count:
            self._request_gallery_render()

    def _on_gallery_filter_changed(self, _event=None) -> None:
        self._reset_gallery_page()
        self.multi_selected_keys.clear()
        self._request_gallery_render(reuse_prepared_records=True)

    def _dataset_options(self, project: ProjectData) -> list[str]:
        if not project.datasets:
            return []
        return ["All datasets"] + [dataset.dataset_name for dataset in project.datasets]

    def _datasets_for_selection(self) -> list[DatasetRecord]:
        selected = self.dataset_var.get().strip()
        if not selected or selected == "All datasets":
            return list(self.app.project.datasets)
        matches = [d for d in self.app.project.datasets if d.dataset_name == selected]
        return matches if matches else list(self.app.project.datasets)

    def _supported_image_files(self, folder: str) -> list[Path]:
        path = Path(folder)
        if not path.exists():
            return []
        return sorted(
            [item for item in path.iterdir() if item.is_file() and item.suffix.lower() in {".png", ".jpg", ".jpeg"}],
            key=lambda item: item.name.casefold(),
        )

    def _effective_thumbnail_folder(self, dataset: DatasetRecord) -> str:
        return effective_thumbnail_source_folder(dataset)

    def _matching_mrc_path(self, dataset: DatasetRecord, ts_name: str) -> str:
        resolved = resolve_dataset_file(self.app.project, dataset, ts_name, "tomogram")
        return resolved.path

    def _refresh_dataset_mrc_paths(self, dataset: DatasetRecord) -> int:
        matched = 0
        for record in dataset.thumbnails:
            record.mrc_path = self._matching_mrc_path(dataset, record.ts_name)
            if record.mrc_path:
                matched += 1
        return matched

    def _ts_stems_for_matching(self, dataset: DatasetRecord) -> list[str]:
        return dataset_ts_names(dataset)

    def _path_mtime(self, path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def _scan_thumbnails_for_dataset(self, dataset: DatasetRecord) -> list[ThumbnailRecord]:
        folder = self._effective_thumbnail_folder(dataset)
        if not folder:
            return []

        image_paths = self._supported_image_files(folder)
        ts_stems = self._ts_stems_for_matching(dataset)
        by_path = {record.image_path: record for record in dataset.thumbnails}
        by_ts_name = {record.ts_name: record for record in dataset.thumbnails if record.ts_name}
        matched_by_ts: dict[str, ThumbnailRecord] = {}

        for image_path in image_paths:
            matched_stem = best_matching_ts_name(image_path.stem, ts_stems)
            if not matched_stem:
                continue

            record = by_path.get(str(image_path)) or by_ts_name.get(matched_stem)
            if record is None:
                record = ThumbnailRecord(image_path=str(image_path), ts_name=matched_stem)
            record.image_path = str(image_path)
            record.ts_name = matched_stem
            record.mrc_path = self._matching_mrc_path(dataset, matched_stem)
            current = matched_by_ts.get(matched_stem)
            if current is None or self._path_mtime(image_path) >= self._path_mtime(Path(current.image_path)):
                matched_by_ts[matched_stem] = record

        matched = sorted(
            matched_by_ts.values(),
            key=lambda record: (record.ts_name.casefold(), Path(record.image_path).name.casefold()),
        )
        if not matched and dataset.thumbnails:
            existing_records = [
                record for record in dataset.thumbnails
                if record.image_path and Path(record.image_path).is_file()
            ]
            if existing_records:
                matched = sorted(
                    existing_records,
                    key=lambda record: (record.ts_name.casefold(), Path(record.image_path).name.casefold()),
                )
        dataset.thumbnails = matched
        self.dataset_match_cache[(dataset.dataset_name, folder)] = matched
        self._reindex_dataset_records(dataset, matched)
        return matched

    def _dataset_thumbnail_records(self, dataset: DatasetRecord) -> list[ThumbnailRecord]:
        folder = self._effective_thumbnail_folder(dataset)
        cache_key = (dataset.dataset_name, folder)
        if cache_key in self.dataset_match_cache:
            cached = self.dataset_match_cache[cache_key]
            if all(Path(record.image_path).is_file() for record in cached if record.image_path):
                self._reindex_dataset_records(dataset, cached)
                return cached
        elif dataset.thumbnails:
            existing_records = [
                record for record in dataset.thumbnails
                if record.image_path and Path(record.image_path).is_file()
            ]
            if existing_records:
                matched = sorted(
                    existing_records,
                    key=lambda record: (record.ts_name.casefold(), Path(record.image_path).name.casefold()),
                )
                self.dataset_match_cache[cache_key] = matched
                self._reindex_dataset_records(dataset, matched)
                return matched
        return self._scan_thumbnails_for_dataset(dataset)

    def _reindex_dataset_records(self, dataset: DatasetRecord, records: list[ThumbnailRecord]) -> None:
        stale_keys = [key for key in self.thumbnail_record_index if key[0] == dataset.dataset_name]
        for key in stale_keys:
            self.thumbnail_record_index.pop(key, None)
        for record in records:
            self.thumbnail_record_index[(dataset.dataset_name, record.image_path)] = (dataset, record)

    def _downscaled_thumbnails_enabled(self) -> bool:
        return project_preference_enabled(self.app.project, "use_downscaled_thumbnails", default=True)

    def _downscaled_thumbnail_size(self) -> int:
        return project_preference_int(
            self.app.project,
            "thumbnail_cache_size",
            default=256,
            minimum=32,
            maximum=4096,
        )

    def _gallery_page_size(self) -> int:
        return project_preference_int(
            self.app.project,
            "gallery_page_size",
            default=GALLERY_PAGE_SIZE,
            minimum=8,
            maximum=500,
        )

    def _thumbnail_cache_location(self) -> str:
        return thumbnail_cache_location(self.app.project)

    def _resolve_thumbnail_cache_dir(self, dataset: DatasetRecord) -> Path:
        return resolve_thumbnail_cache_dir(self.app.project, dataset)

    def _thumbnail_cache_manifest_path(self, cache_dir: Path) -> Path:
        return cache_dir / THUMBNAIL_CACHE_MANIFEST

    def _thumbnail_cache_target_stem(self, source_path: Path) -> str:
        digest = hashlib.sha1(str(source_path).encode("utf-8")).hexdigest()[:10]
        safe_stem = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in source_path.stem)
        safe_stem = safe_stem[:80] or "thumbnail"
        return f"{safe_stem}_{digest}"

    def _thumbnail_cache_target_path(self, cache_dir: Path, source_path: Path, suffix: str = ".png") -> Path:
        return cache_dir / f"{self._thumbnail_cache_target_stem(source_path)}{suffix}"

    def _thumbnail_source_signature(self, source_path: Path) -> tuple[int, int]:
        try:
            stat = source_path.stat()
        except OSError:
            return (0, 0)
        return (int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))), int(stat.st_size))

    def _directory_signature(self, folder: str) -> int:
        if not folder:
            return 0
        try:
            stat = Path(folder).stat()
        except OSError:
            return 0
        return int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)))

    def _load_thumbnail_cache_manifest(self, cache_dir: Path) -> dict:
        manifest_path = self._thumbnail_cache_manifest_path(cache_dir)
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _save_thumbnail_cache_manifest(self, cache_dir: Path, payload: dict) -> None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = self._thumbnail_cache_manifest_path(cache_dir)
        fd, tmp_name = tempfile.mkstemp(dir=cache_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
            os.replace(tmp_name, manifest_path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def _generate_cached_thumbnail(self, source_path: Path, cache_dir: Path, target_size: int) -> Path:
        image = tk.PhotoImage(file=str(source_path))
        width = max(image.width(), 1)
        height = max(image.height(), 1)
        scale = max(width / target_size, height / target_size, 1)
        subsample = max(1, int(scale))
        if subsample > 1:
            image = image.subsample(subsample, subsample)
        cache_dir.mkdir(parents=True, exist_ok=True)
        errors: list[str] = []
        for image_format, suffix in (("png", ".png"), ("gif", ".gif"), ("ppm", ".ppm")):
            target_path = self._thumbnail_cache_target_path(cache_dir, source_path, suffix)
            try:
                image.write(str(target_path), format=image_format)
                return target_path
            except Exception as exc:
                errors.append(f"{image_format.upper()}: {exc}")
                try:
                    target_path.unlink()
                except OSError:
                    pass
        raise OSError(
            "Could not write a cached thumbnail for "
            f"{source_path.name}. Tried PNG, GIF, and PPM.\n" + "\n".join(errors)
        )

    def _display_thumbnail_path(self, dataset: DatasetRecord, thumbnail: ThumbnailRecord) -> str:
        return self.thumbnail_display_paths.get((dataset.dataset_name, thumbnail.image_path), thumbnail.image_path)

    def _prepare_thumbnail_cache(self, datasets: list[DatasetRecord]) -> dict[str, list[ThumbnailRecord]]:
        records_by_dataset: dict[str, list[ThumbnailRecord]] = {}
        enabled = self._downscaled_thumbnails_enabled()
        size = self._downscaled_thumbnail_size()
        location = self._thumbnail_cache_location()
        plans: list[dict[str, object]] = []
        total_to_generate = 0

        for dataset in datasets:
            records = self._dataset_thumbnail_records(dataset)
            records_by_dataset[dataset.dataset_name] = records
            source_paths = [Path(record.image_path) for record in records if record.image_path]
            source_key = tuple(str(path) for path in source_paths)
            folder_signature = self._directory_signature(self._effective_thumbnail_folder(dataset))
            state_key: tuple[object, ...] = (enabled, size, location, folder_signature, source_key)
            if self.thumbnail_cache_state.get(dataset.dataset_name) == state_key:
                if not enabled:
                    continue
                missing_display_file = any(
                    not Path(
                        self.thumbnail_display_paths.get((dataset.dataset_name, record.image_path), record.image_path)
                    ).is_file()
                    for record in records
                )
                if not missing_display_file:
                    continue

            if not enabled:
                for record in records:
                    self.thumbnail_display_paths[(dataset.dataset_name, record.image_path)] = record.image_path
                self.thumbnail_cache_state[dataset.dataset_name] = state_key
                continue

            cache_dir = self._resolve_thumbnail_cache_dir(dataset)
            cache_dir.mkdir(parents=True, exist_ok=True)
            manifest = self._load_thumbnail_cache_manifest(cache_dir)
            manifest_entries = manifest.get("entries", {}) if isinstance(manifest.get("entries"), dict) else {}
            cached_size = int(manifest.get("thumbnail_size", 0) or 0)
            display_map: dict[str, str] = {}
            generation_tasks: list[tuple[Path, tuple[int, int]]] = []
            updated_entries: dict[str, dict[str, object]] = {}

            for path in source_paths:
                signature_pair = self._thumbnail_source_signature(path)
                entry = manifest_entries.get(str(path), {}) if cached_size == size else {}
                cache_file = str(entry.get("cache_file", "")).strip() if isinstance(entry, dict) else ""
                target_path = self._thumbnail_cache_target_path(cache_dir, path)
                if cache_file:
                    target_path = cache_dir / cache_file
                entry_mtime = int(entry.get("source_mtime_ns", -1)) if isinstance(entry, dict) else -1
                entry_size = int(entry.get("source_size", -1)) if isinstance(entry, dict) else -1
                if (
                    cached_size == size
                    and cache_file
                    and target_path.is_file()
                    and entry_mtime == signature_pair[0]
                    and entry_size == signature_pair[1]
                ):
                    display_map[str(path)] = str(target_path)
                    updated_entries[str(path)] = {
                        "cache_file": target_path.name,
                        "source_mtime_ns": signature_pair[0],
                        "source_size": signature_pair[1],
                    }
                else:
                    generation_tasks.append((path, signature_pair))

            plans.append(
                {
                    "dataset": dataset,
                    "records": records,
                    "state_key": state_key,
                    "cache_dir": cache_dir,
                    "display_map": display_map,
                    "entries": updated_entries,
                    "tasks": generation_tasks,
                    "size": size,
                }
            )
            total_to_generate += len(generation_tasks)

        if total_to_generate:
            busy = _GalleryBusyDialog(
                self.frame,
                "Preparing thumbnails",
                "Generating downscaled gallery thumbnails. Please wait.",
            )
            cache_failures: list[str] = []
            try:
                completed = 0
                for plan in plans:
                    tasks = plan["tasks"]
                    if not isinstance(tasks, list) or not tasks:
                        continue
                    dataset = plan["dataset"]
                    cache_dir = plan["cache_dir"]
                    if not isinstance(dataset, DatasetRecord) or not isinstance(cache_dir, Path):
                        continue
                    entries = plan["entries"]
                    display_map = plan["display_map"]
                    target_size = int(plan["size"])
                    for source_path, signature_pair in tasks:
                        completed += 1
                        busy.set_message(
                            "Generating downscaled gallery thumbnails. Please wait.\n\n"
                            f"[{completed}/{total_to_generate}] {dataset.dataset_name}: {source_path.name}"
                        )
                        try:
                            target_path = self._generate_cached_thumbnail(source_path, cache_dir, target_size)
                            display_map[str(source_path)] = str(target_path)
                            entries[str(source_path)] = {
                                "cache_file": target_path.name,
                                "source_mtime_ns": signature_pair[0],
                                "source_size": signature_pair[1],
                            }
                        except Exception as exc:
                            display_map[str(source_path)] = str(source_path)
                            cache_failures.append(f"{dataset.dataset_name} / {source_path.name}: {exc}")
                    try:
                        self._save_thumbnail_cache_manifest(
                            cache_dir,
                            {
                                "version": 1,
                                "thumbnail_size": target_size,
                                "entries": entries,
                            },
                        )
                    except OSError as exc:
                        messagebox.showerror(
                            "Thumbnail cache",
                            f"Could not update the thumbnail cache for {dataset.dataset_name}.\n\n{exc}",
                            parent=self.frame.winfo_toplevel(),
                        )
            finally:
                busy.close()
            if cache_failures:
                preview = "\n".join(cache_failures[:8])
                if len(cache_failures) > 8:
                    preview += f"\n... and {len(cache_failures) - 8} more"
                messagebox.showwarning(
                    "Thumbnail cache",
                    "Some downscaled thumbnails could not be written, so CryoPal fell back to the original "
                    f"thumbnail files.\n\n{preview}",
                    parent=self.frame.winfo_toplevel(),
                )

        for plan in plans:
            dataset = plan["dataset"]
            records = plan["records"]
            state_key = plan["state_key"]
            display_map = plan["display_map"]
            if not isinstance(dataset, DatasetRecord) or not isinstance(records, list):
                continue
            for record in records:
                self.thumbnail_display_paths[(dataset.dataset_name, record.image_path)] = display_map.get(
                    record.image_path,
                    record.image_path,
                )
            self.thumbnail_cache_state[dataset.dataset_name] = state_key
        return records_by_dataset

    def _all_known_tags(self) -> list[str]:
        tags = set()
        for dataset in self.app.project.datasets:
            for thumbnail in dataset.thumbnails:
                tags.update(tag for tag in thumbnail.tags if tag)
        return sorted(tags, key=str.casefold)

    def _update_tag_suggestions(self) -> None:
        tags = self._all_known_tags()
        self.tag_input_combo.configure(values=tags)
        self._populate_tag_filter_listbox(self.include_tags_listbox, tags)
        self._populate_tag_filter_listbox(self.exclude_tags_listbox, tags)

    def _populate_tag_filter_listbox(self, listbox: tk.Listbox, tags: list[str]) -> None:
        selected = {listbox.get(index) for index in listbox.curselection()}
        listbox.delete(0, "end")
        for index, tag in enumerate(tags):
            listbox.insert("end", tag)
            if tag in selected:
                listbox.selection_set(index)

    def _clear_filter_tag_selections(self) -> None:
        self.include_tags_listbox.selection_clear(0, "end")
        self.exclude_tags_listbox.selection_clear(0, "end")

    def _reset_gallery_filters(self) -> None:
        options = self._dataset_options(self.app.project)
        if options:
            self.dataset_var.set("All datasets")
        else:
            self.dataset_var.set("")
        self.min_rating_var.set("Any")
        self.tag_include_mode_var.set("All selected")
        self._clear_filter_tag_selections()
        self.multi_selected_keys.clear()
        self.selected_thumbnail_key = None
        self._reset_gallery_page()
        self._update_details_for_current_selection()
        self._request_gallery_render(reuse_prepared_records=True)

    def _selected_filter_tags(self, listbox: tk.Listbox) -> list[str]:
        return [str(listbox.get(index)) for index in listbox.curselection()]

    def _selection_has_thumbnails(self) -> bool:
        for dataset in self._datasets_for_selection():
            if dataset.thumbnails:
                return True
            folder = self._effective_thumbnail_folder(dataset)
            cached = self.dataset_match_cache.get((dataset.dataset_name, folder))
            if cached:
                return True
        return False

    def _filtered_item_keys(self) -> list[tuple[str, str]]:
        return [(dataset.dataset_name, thumbnail.image_path) for dataset, thumbnail in self._filtered_items()]

    def _selected_records(self) -> list[tuple[DatasetRecord, ThumbnailRecord]]:
        records: list[tuple[DatasetRecord, ThumbnailRecord]] = []
        selected_keys = self.multi_selected_keys
        if not selected_keys:
            return records
        for key in selected_keys:
            record = self.thumbnail_record_index.get(key)
            if record is not None:
                records.append(record)
        return records

    def _format_summary_preview(self, values: list[str], *, empty: str = "-", limit: int = 3) -> str:
        cleaned = [value for value in values if value]
        if not cleaned:
            return empty
        preview = ", ".join(cleaned[:limit])
        if len(cleaned) > limit:
            preview += f", +{len(cleaned) - limit} more"
        return preview

    def _update_import_button_label(self) -> None:
        if hasattr(self, "manual_import_button"):
            self.manual_import_button.config(text="Manual import")

    def _clear_dataset_thumbnail_runtime_cache(self, dataset_name: str) -> None:
        keys_to_remove = [key for key in self.thumbnail_display_paths if key[0] == dataset_name]
        for key in keys_to_remove:
            self.thumbnail_display_paths.pop(key, None)
        index_keys = [key for key in self.thumbnail_record_index if key[0] == dataset_name]
        for key in index_keys:
            self.thumbnail_record_index.pop(key, None)
        match_keys = [key for key in self.dataset_match_cache if key[0] == dataset_name]
        for key in match_keys:
            self.dataset_match_cache.pop(key, None)
        self.thumbnail_cache_state.pop(dataset_name, None)
        self._reuse_prepared_records_once = False

    def _column_count_for_width(self, width: int | None = None) -> int:
        available_width = max(width or self.gallery_canvas.winfo_width(), 640)
        return max(1, available_width // max(self.thumbnail_size + 36, 120))

    def _request_gallery_render(self, delay_ms: int = 0, *, reuse_prepared_records: bool = False) -> None:
        if reuse_prepared_records:
            self._reuse_prepared_records_once = True
        if self.app.active_tab_id != self.tab_id or not self.frame.winfo_ismapped():
            self._needs_render_when_shown = True
            return
        if self._pending_render_after is not None:
            self.frame.after_cancel(self._pending_render_after)
        self._pending_render_after = self.frame.after(delay_ms, self._render_gallery)

    def _cancel_pending_render_batch(self) -> None:
        if self._pending_render_batch_after is not None:
            try:
                self.frame.after_cancel(self._pending_render_batch_after)
            except tk.TclError:
                pass
        self._pending_render_batch_after = None

    def _existing_records_by_dataset(self, datasets: list[DatasetRecord]) -> dict[str, list[ThumbnailRecord]]:
        records_by_dataset: dict[str, list[ThumbnailRecord]] = {}
        for dataset in datasets:
            records = list(dataset.thumbnails)
            records_by_dataset[dataset.dataset_name] = records
            self._reindex_dataset_records(dataset, records)
            for record in records:
                self.thumbnail_display_paths.setdefault((dataset.dataset_name, record.image_path), record.image_path)
        return records_by_dataset

    def _cancel_pending_auto_select(self) -> None:
        if self._pending_auto_select_after is not None:
            try:
                self.frame.after_cancel(self._pending_auto_select_after)
            except tk.TclError:
                pass
        self._pending_auto_select_after = None

    def _schedule_auto_select_thumbnail(self, dataset_name: str, image_path: str) -> None:
        self._cancel_pending_auto_select()

        def run() -> None:
            self._pending_auto_select_after = None
            if self.multi_selection_var.get():
                return
            if self.selected_thumbnail_key is not None:
                return
            if (dataset_name, image_path) not in self.card_widgets:
                return
            self._select_thumbnail(dataset_name, image_path)

        self._pending_auto_select_after = self.frame.after_idle(run)

    def _change_page(self, delta: int) -> None:
        if self._current_page_count <= 1:
            return
        next_page = max(0, min(self._current_page + delta, self._current_page_count - 1))
        if next_page == self._current_page:
            return
        self._current_page = next_page
        self.gallery_canvas.yview_moveto(0)
        self._request_gallery_render(reuse_prepared_records=True)

    def _reset_gallery_page(self) -> None:
        self._current_page = 0

    def _paged_items(
        self, items: list[tuple[DatasetRecord, ThumbnailRecord]]
    ) -> tuple[list[tuple[DatasetRecord, ThumbnailRecord]], int, int, int]:
        total = len(items)
        page_size = self._gallery_page_size()
        if total == 0:
            self._current_page = 0
            self._current_page_count = 0
            return [], 0, 0, 0
        page_count = max(1, (total + page_size - 1) // page_size)
        if self._current_page >= page_count:
            self._current_page = page_count - 1
        start = self._current_page * page_size
        end = min(start + page_size, total)
        self._current_page_count = page_count
        return items[start:end], start, end, total

    def _update_pager_controls(self, start: int, end: int, total: int) -> None:
        if total <= self._gallery_page_size():
            self.pager_status_var.set("")
            self.pager_row.grid_remove()
            self.prev_page_button.configure(state="disabled")
            self.next_page_button.configure(state="disabled")
            return
        self.pager_status_var.set(f"{start + 1}-{end} of {total} | Page {self._current_page + 1}/{self._current_page_count}")
        self.prev_page_button.configure(state="normal" if self._current_page > 0 else "disabled")
        self.next_page_button.configure(
            state="normal" if self._current_page < self._current_page_count - 1 else "disabled"
        )
        self.pager_row.grid()

    def _prune_thumbnail_cache(self, items: list[tuple[DatasetRecord, ThumbnailRecord]]) -> None:
        visible_paths = {self._display_thumbnail_path(dataset, thumbnail) for dataset, thumbnail in items}
        keep_keys = {(path, self.thumbnail_size) for path in visible_paths}
        stale_keys = [key for key in self.thumbnail_images if key not in keep_keys]
        for key in stale_keys:
            self.thumbnail_images.pop(key, None)

    def _ensure_gallery_card_pool(self, count: int) -> None:
        while len(self.gallery_card_pool) < count:
            slot: dict[str, object] = {
                "current_key": None,
                "dataset": None,
                "thumbnail": None,
            }
            card = tk.Frame(
                self.gallery_frame,
                bd=1,
                relief="solid",
                background=self.app._current_appearance.main_background,
                padx=8,
                pady=8,
            )
            variable = tk.BooleanVar(value=False)
            checkbutton = ttk.Checkbutton(
                card,
                variable=variable,
                command=lambda current_slot=slot: self._select_card_slot(current_slot),
            )
            checkbutton.grid(row=0, column=0, sticky="nw")
            checkbutton.grid_remove()
            image_canvas = tk.Canvas(
                card,
                width=self.thumbnail_size,
                height=self.thumbnail_size,
                highlightthickness=0,
                background=self.app._current_appearance.main_background,
            )
            image_canvas.grid(row=1, column=0)
            title = tk.Label(
                card,
                wraplength=self.thumbnail_size,
                justify="center",
                bg=card["background"],
                fg=self.app._current_appearance.main_foreground,
            )
            title.grid(row=2, column=0, sticky="ew", pady=(6, 0))
            subtitle = tk.Label(
                card,
                wraplength=self.thumbnail_size,
                justify="center",
                bg=card["background"],
                fg=self.app._current_appearance.main_foreground,
            )
            subtitle.grid(row=3, column=0, sticky="ew", pady=(2, 0))
            slot.update(
                {
                    "card": card,
                    "checkbutton": checkbutton,
                    "image_canvas": image_canvas,
                    "title": title,
                    "subtitle": subtitle,
                    "variable": variable,
                }
            )
            for widget in (card, image_canvas, title, subtitle):
                widget.bind(
                    "<Button-1>",
                    lambda _event, current_slot=slot: self._queue_card_slot_single_click(current_slot),
                )
                widget.bind(
                    "<Double-Button-1>",
                    lambda _event, current_slot=slot: self._queue_card_slot_double_click(current_slot),
                )
            self.gallery_card_pool.append(slot)

    def _hide_unused_gallery_cards(self, used_count: int) -> None:
        for slot in self.gallery_card_pool[used_count:]:
            card = slot.get("card")
            if isinstance(card, tk.Frame):
                card.grid_remove()
            image_canvas = slot.get("image_canvas")
            if isinstance(image_canvas, tk.Canvas):
                image_canvas.delete("all")
            variable = slot.get("variable")
            if isinstance(variable, tk.BooleanVar):
                variable.set(False)
            slot["current_key"] = None
            slot["dataset"] = None
            slot["thumbnail"] = None

    def _select_card_slot(self, slot: dict[str, object]) -> None:
        key = slot.get("current_key")
        if not isinstance(key, tuple) or len(key) != 2:
            return
        dataset_name, image_path = key
        if not isinstance(dataset_name, str) or not isinstance(image_path, str):
            return
        self._select_thumbnail(dataset_name, image_path)

    def _queue_card_slot_single_click(self, slot: dict[str, object]) -> None:
        key = slot.get("current_key")
        if not isinstance(key, tuple) or len(key) != 2:
            return
        dataset_name, image_path = key
        if not isinstance(dataset_name, str) or not isinstance(image_path, str):
            return
        self._queue_thumbnail_single_click(dataset_name, image_path)

    def _queue_card_slot_double_click(self, slot: dict[str, object]) -> None:
        key = slot.get("current_key")
        dataset = slot.get("dataset")
        thumbnail = slot.get("thumbnail")
        if (
            not isinstance(key, tuple)
            or len(key) != 2
            or not isinstance(dataset, DatasetRecord)
            or not isinstance(thumbnail, ThumbnailRecord)
        ):
            return
        dataset_name, image_path = key
        if not isinstance(dataset_name, str) or not isinstance(image_path, str):
            return
        self._queue_thumbnail_double_click(dataset_name, image_path, dataset, thumbnail)

    def _prepare_gallery_card_slot(
        self,
        slot: dict[str, object],
        dataset: DatasetRecord,
        thumbnail: ThumbnailRecord,
        row: int,
        column: int,
    ) -> None:
        key = (dataset.dataset_name, thumbnail.image_path)
        slot["current_key"] = key
        slot["dataset"] = dataset
        slot["thumbnail"] = thumbnail

        card = slot.get("card")
        title = slot.get("title")
        subtitle = slot.get("subtitle")
        image_canvas = slot.get("image_canvas")
        variable = slot.get("variable")
        if not isinstance(card, tk.Frame):
            return
        if isinstance(variable, tk.BooleanVar):
            variable.set(key in self.multi_selected_keys)
        if isinstance(title, tk.Label):
            title.configure(text=thumbnail.ts_name, wraplength=self.thumbnail_size)
        if isinstance(subtitle, tk.Label):
            subtitle.configure(
                text=f"{dataset.dataset_name} | Rating: {thumbnail.rating or '-'}",
                wraplength=self.thumbnail_size,
            )
        if isinstance(image_canvas, tk.Canvas):
            image_canvas.configure(
                width=self.thumbnail_size,
                height=self.thumbnail_size,
                background=self.app._current_appearance.main_background,
            )
            image_canvas.delete("all")
            image_canvas.create_text(
                self.thumbnail_size // 2,
                self.thumbnail_size // 2,
                text="Loading preview...",
                width=max(self.thumbnail_size - 16, 40),
                justify="center",
                fill=self.app._current_appearance.main_foreground,
            )
        card.grid(row=row, column=column, sticky="n", padx=6, pady=6)
        self.card_widgets[key] = {
            "card": card,
            "checkbutton": slot["checkbutton"],
            "image_canvas": image_canvas,
            "title": title,
            "subtitle": subtitle,
        }
        if isinstance(variable, tk.BooleanVar):
            self.selection_vars[key] = variable

    def _render_gallery_batch(
        self,
        page_items: list[tuple[DatasetRecord, ThumbnailRecord]],
        start_index: int,
        generation: int,
        batch_size: int = 8,
    ) -> None:
        if generation != self._render_generation:
            return
        end_index = min(start_index + batch_size, len(page_items))
        for slot_index in range(start_index, end_index):
            dataset, thumbnail = page_items[slot_index]
            slot = self.gallery_card_pool[slot_index]
            image_canvas = slot.get("image_canvas")
            if not isinstance(image_canvas, tk.Canvas):
                continue
            image = self._resolve_thumbnail_image(dataset, thumbnail)
            image_canvas.delete("all")
            if image is None:
                image_canvas.create_text(
                    self.thumbnail_size // 2,
                    self.thumbnail_size // 2,
                    text="Preview unavailable",
                    width=max(self.thumbnail_size - 16, 40),
                    justify="center",
                    fill=self.app._current_appearance.main_foreground,
                )
                continue
            image_canvas.create_image(
                self.thumbnail_size // 2,
                self.thumbnail_size // 2,
                image=image,
                anchor="center",
            )
            image_canvas.image = image
        if end_index < len(page_items):
            self._pending_render_batch_after = self.frame.after_idle(
                lambda: self._render_gallery_batch(page_items, end_index, generation, batch_size)
            )
        else:
            self._pending_render_batch_after = None

    def _thumbnail_image(self, image_path: str) -> tk.PhotoImage | None:
        key = (image_path, self.thumbnail_size)
        if key in self.thumbnail_images:
            return self.thumbnail_images[key]
        try:
            image = tk.PhotoImage(file=image_path)
        except tk.TclError:
            return None

        width = max(image.width(), 1)
        height = max(image.height(), 1)
        scale = max(width / self.thumbnail_size, height / self.thumbnail_size, 1)
        subsample = max(1, int(scale))
        if subsample > 1:
            image = image.subsample(subsample, subsample)
        self.thumbnail_images[key] = image
        return image

    def _resolve_thumbnail_image(self, dataset: DatasetRecord, thumbnail: ThumbnailRecord) -> tk.PhotoImage | None:
        display_path = self._display_thumbnail_path(dataset, thumbnail)
        image = self._thumbnail_image(display_path)
        if image is not None:
            return image
        if display_path != thumbnail.image_path:
            self.thumbnail_display_paths[(dataset.dataset_name, thumbnail.image_path)] = thumbnail.image_path
            return self._thumbnail_image(thumbnail.image_path)
        return None

    def _filtered_items(
        self,
        records_by_dataset: dict[str, list[ThumbnailRecord]] | None = None,
    ) -> list[tuple[DatasetRecord, ThumbnailRecord]]:
        items: list[tuple[DatasetRecord, ThumbnailRecord]] = []
        min_rating = 0 if self.min_rating_var.get() == "Any" else int(self.min_rating_var.get())
        include_tags = self._selected_filter_tags(self.include_tags_listbox)
        exclude_tags = self._selected_filter_tags(self.exclude_tags_listbox)
        include_mode = self.tag_include_mode_var.get().strip() or "All selected"

        for dataset in self._datasets_for_selection():
            dataset_records = (
                records_by_dataset.get(dataset.dataset_name, [])
                if records_by_dataset is not None
                else self._dataset_thumbnail_records(dataset)
            )
            for thumbnail in dataset_records:
                if thumbnail.rating < min_rating:
                    continue
                thumbnail_tags = {tag.casefold() for tag in thumbnail.tags if tag}
                include_tags_folded = [tag.casefold() for tag in include_tags if tag]
                exclude_tags_folded = [tag.casefold() for tag in exclude_tags if tag]
                if include_tags_folded:
                    if include_mode == "Any selected":
                        if not any(tag in thumbnail_tags for tag in include_tags_folded):
                            continue
                    elif not all(tag in thumbnail_tags for tag in include_tags_folded):
                        continue
                if exclude_tags_folded and any(tag in thumbnail_tags for tag in exclude_tags_folded):
                    continue
                items.append((dataset, thumbnail))
        return items

    def _select_thumbnail(self, dataset_name: str, image_path: str) -> None:
        if self.multi_selection_var.get():
            key = (dataset_name, image_path)
            if key in self.multi_selected_keys:
                self.multi_selected_keys.remove(key)
            else:
                self.multi_selected_keys.add(key)
            variable = self.selection_vars.get(key)
            if variable is not None:
                variable.set(key in self.multi_selected_keys)
            self._apply_selection_style_for_key(key)
            self._schedule_multi_selection_details_update()
            return

        self.selected_thumbnail_key = (dataset_name, image_path)
        dataset = next((d for d in self.app.project.datasets if d.dataset_name == dataset_name), None)
        if dataset is None:
            return
        thumbnail = next((t for t in dataset.thumbnails if t.image_path == image_path), None)
        if thumbnail is None:
            return

        try:
            metadata = collect_ts_metadata(
                self.app.project,
                dataset,
                thumbnail.ts_name,
                thumbnail_path=thumbnail.image_path,
                mrc_path=thumbnail.mrc_path,
            )
        except Exception as exc:
            messagebox.showerror(
                "TS metadata",
                f"Could not collect TS metadata for {thumbnail.ts_name}.\n\n{exc}",
            )
            metadata = None
        self.selected_dataset_var.set(dataset.dataset_name)
        self.selected_ts_var.set(thumbnail.ts_name)
        self.selected_pixelsize_var.set(
            f"{metadata.pixel_size:.4f}"
            if metadata is not None and metadata.pixel_size
            else (str(dataset.pixel_size) if dataset.pixel_size else "-")
        )
        self.selected_mrc_var.set(thumbnail.mrc_path or "-")
        self.selected_rating_var.set(thumbnail.rating)
        self.selected_tags_var.set(", ".join(thumbnail.tags) if thumbnail.tags else "-")
        self.open_mrc_button.config(state="normal" if thumbnail.mrc_path else "disabled")
        self.link_mrc_button.config(text="Update .mrc file", state="normal")
        self.add_to_ts_list_button.config(state="normal")

        self.tag_listbox.delete(0, "end")
        for tag in thumbnail.tags:
            self.tag_listbox.insert("end", tag)
        self._apply_selection_styles()
        self._update_details_for_current_selection()

    def _apply_selection_styles(self) -> None:
        selected_sidebar_background = self.app._current_appearance.sidebar_background
        main_background = self.app._current_appearance.main_background
        text_foreground = self.app._current_appearance.main_foreground
        for key, widgets in self.card_widgets.items():
            is_selected = (
                key in self.multi_selected_keys
                if self.multi_selection_var.get()
                else self.selected_thumbnail_key == key
            )
            card_background = selected_sidebar_background if is_selected else main_background
            border_width = 2 if is_selected else 1
            card = widgets.get("card")
            if isinstance(card, tk.Frame):
                card.configure(background=card_background, bd=border_width)
            image_canvas = widgets.get("image_canvas")
            if isinstance(image_canvas, tk.Canvas):
                image_canvas.configure(background=self.app._current_appearance.main_background)
            title = widgets.get("title")
            if isinstance(title, tk.Label):
                title.configure(bg=card_background, fg=text_foreground)
            subtitle = widgets.get("subtitle")
            if isinstance(subtitle, tk.Label):
                subtitle.configure(bg=card_background, fg=text_foreground)

    def _apply_selection_style_for_key(self, key: tuple[str, str]) -> None:
        widgets = self.card_widgets.get(key)
        if not widgets:
            return
        selected_sidebar_background = self.app._current_appearance.sidebar_background
        main_background = self.app._current_appearance.main_background
        text_foreground = self.app._current_appearance.main_foreground
        is_selected = (
            key in self.multi_selected_keys
            if self.multi_selection_var.get()
            else self.selected_thumbnail_key == key
        )
        card_background = selected_sidebar_background if is_selected else main_background
        border_width = 2 if is_selected else 1
        card = widgets.get("card")
        if isinstance(card, tk.Frame):
            card.configure(background=card_background, bd=border_width)
        image_canvas = widgets.get("image_canvas")
        if isinstance(image_canvas, tk.Canvas):
            image_canvas.configure(background=self.app._current_appearance.main_background)
        title = widgets.get("title")
        if isinstance(title, tk.Label):
            title.configure(bg=card_background, fg=text_foreground)
        subtitle = widgets.get("subtitle")
        if isinstance(subtitle, tk.Label):
            subtitle.configure(bg=card_background, fg=text_foreground)

    def _update_multi_selection_widgets(self) -> None:
        enabled = self.multi_selection_var.get()
        for key, widgets in self.card_widgets.items():
            checkbutton = widgets.get("checkbutton")
            variable = self.selection_vars.get(key)
            if variable is not None:
                variable.set(key in self.multi_selected_keys)
            if isinstance(checkbutton, ttk.Checkbutton):
                if enabled:
                    checkbutton.grid()
                else:
                    checkbutton.grid_remove()
        if enabled:
            self.select_all_button.grid()
        else:
            self.select_all_button.grid_remove()

    def _schedule_multi_selection_details_update(self) -> None:
        if self._pending_multi_details_after is not None:
            try:
                self.frame.after_cancel(self._pending_multi_details_after)
            except tk.TclError:
                pass
        self._pending_multi_details_after = self.frame.after_idle(self._run_multi_selection_details_update)

    def _run_multi_selection_details_update(self) -> None:
        self._pending_multi_details_after = None
        self._update_details_for_current_selection()

    def _update_details_for_current_selection(self) -> None:
        if self.multi_selection_var.get():
            records = self._selected_records()
            if not records:
                self._clear_selected_thumbnail_details()
                self.selected_dataset_var.set("0 TS selected")
                self.selected_ts_var.set("-")
                self.selected_pixelsize_var.set("-")
                self.selected_tags_var.set("-")
                self.selected_mrc_var.set("-")
                self.open_mrc_button.config(state="disabled")
                self.link_mrc_button.config(text="Link .mrc file", state="disabled")
                self.add_to_ts_list_button.config(state="disabled")
                return
            datasets = sorted({dataset.dataset_name for dataset, _thumbnail in records}, key=str.casefold)
            ratings = [thumbnail.rating for _dataset, thumbnail in records if thumbnail.rating > 0]
            tags = sorted({tag for _dataset, thumbnail in records for tag in thumbnail.tags}, key=str.casefold)
            mrc_count = sum(1 for _dataset, thumbnail in records if thumbnail.mrc_path)
            self.selected_dataset_var.set(f"{len(records)} TS selected")
            self.selected_ts_var.set(
                f"{len(datasets)} dataset(s): {self._format_summary_preview(datasets, empty='-')}"
            )
            self.selected_pixelsize_var.set(
                (
                    f"Ratings: {min(ratings)}-{max(ratings)}"
                    if ratings
                    else "Ratings: none"
                )
            )
            self.selected_tags_var.set(self._format_summary_preview(tags))
            self.selected_mrc_var.set(f"MRC available for {mrc_count}/{len(records)}")
            self.open_mrc_button.config(state="normal" if mrc_count else "disabled")
            self.link_mrc_button.config(text="Update .mrc file", state="disabled")
            self.add_to_ts_list_button.config(state="normal")
            self.selected_rating_var.set(0)
            self.tag_listbox.delete(0, "end")
            for tag in tags[:200]:
                self.tag_listbox.insert("end", tag)
            return

        if self.selected_thumbnail_key is None:
            self._clear_selected_thumbnail_details()

    def _open_selected_mrc(self) -> None:
        if self.multi_selection_var.get():
            records = self._selected_records()
            mrc_paths = [thumbnail.mrc_path for _dataset, thumbnail in records if thumbnail.mrc_path]
            if not mrc_paths:
                return
            try:
                for path in mrc_paths:
                    self.app.open_external_file(path)
            except Exception as exc:
                messagebox.showerror("Open file failed", str(exc))
                return
            self.app.status_var.set(f"Opened {len(mrc_paths)} associated .mrc file(s)")
            return

        if self.selected_thumbnail_key is None:
            return
        dataset_name, image_path = self.selected_thumbnail_key
        dataset = next((d for d in self.app.project.datasets if d.dataset_name == dataset_name), None)
        if dataset is None:
            return
        thumbnail = next((t for t in dataset.thumbnails if t.image_path == image_path), None)
        if thumbnail is None or not thumbnail.mrc_path:
            return
        try:
            self.app.open_external_file(thumbnail.mrc_path)
        except Exception as exc:
            messagebox.showerror("Open file failed", str(exc))
            return
        self.app.status_var.set(f"Opened file: {Path(thumbnail.mrc_path).name}")

    def _save_selected_thumbnail_metadata(self) -> None:
        if self.selected_thumbnail_key is None:
            return
        dataset_name, image_path = self.selected_thumbnail_key
        dataset = next((d for d in self.app.project.datasets if d.dataset_name == dataset_name), None)
        if dataset is None:
            return
        thumbnail = next((t for t in dataset.thumbnails if t.image_path == image_path), None)
        if thumbnail is None:
            return
        thumbnail.rating = int(self.selected_rating_var.get())
        self.selected_tags_var.set(", ".join(thumbnail.tags) if thumbnail.tags else "-")
        self._update_tag_suggestions()
        self._request_gallery_render(reuse_prepared_records=True)

    def _link_selected_mrc_file(self) -> None:
        if self.multi_selection_var.get():
            messagebox.showinfo("Link .mrc file", "Please select a single thumbnail to link an .mrc file.")
            return
        dataset, thumbnail = self._selected_dataset_and_thumbnail()
        if dataset is None or thumbnail is None:
            messagebox.showinfo("Link .mrc file", "Please select a thumbnail first.")
            return
        path = filedialog.askopenfilename(
            title="Select associated .mrc file",
            filetypes=[("MRC files", "*.mrc"), ("All files", "*.*")],
        )
        if not path:
            return
        thumbnail.mrc_path = path
        self.selected_mrc_var.set(path)
        self.open_mrc_button.config(state="normal")
        self.link_mrc_button.config(text="Update .mrc file", state="normal")
        self.app.on_project_changed("gallery")
        self.app.status_var.set(f"Linked .mrc file to {thumbnail.ts_name}: {Path(path).name}")

    def _selected_dataset_and_thumbnail(self) -> tuple[DatasetRecord | None, ThumbnailRecord | None]:
        if self.selected_thumbnail_key is None:
            return None, None
        dataset_name, image_path = self.selected_thumbnail_key
        for dataset in self.app.project.datasets:
            if dataset.dataset_name != dataset_name:
                continue
            for thumbnail in dataset.thumbnails:
                if thumbnail.image_path == image_path:
                    return dataset, thumbnail
            return dataset, None
        return None, None

    def _queue_thumbnail_single_click(self, dataset_name: str, image_path: str) -> None:
        if self.multi_selection_var.get():
            self._select_thumbnail(dataset_name, image_path)
            return

        if self._pending_single_click_after is not None:
            try:
                self.frame.after_cancel(self._pending_single_click_after)
            except tk.TclError:
                pass
            self._pending_single_click_after = None

        def run_single_click() -> None:
            self._pending_single_click_after = None
            self._select_thumbnail(dataset_name, image_path)

        self._pending_single_click_after = self.frame.after(180, run_single_click)

    def _queue_thumbnail_double_click(
        self,
        dataset_name: str,
        image_path: str,
        dataset: DatasetRecord,
        thumbnail: ThumbnailRecord,
    ) -> None:
        if self._pending_single_click_after is not None:
            try:
                self.frame.after_cancel(self._pending_single_click_after)
            except tk.TclError:
                pass
        self._pending_single_click_after = None
        self._select_thumbnail(dataset_name, image_path)
        self._show_thumbnail_details_dialog(dataset, thumbnail)

    def _show_thumbnail_details_dialog(self, dataset: DatasetRecord, thumbnail: ThumbnailRecord) -> None:
        try:
            metadata = collect_ts_metadata(
                self.app.project,
                dataset,
                thumbnail.ts_name,
                thumbnail_path=thumbnail.image_path,
                mrc_path=thumbnail.mrc_path,
            )
        except Exception as exc:
            messagebox.showerror(
                "TS details",
                f"Could not open TS details for {thumbnail.ts_name}.\n\n{exc}",
            )
            return
        sections = ts_metadata_sections(metadata)
        sections.append(
            (
                "Gallery annotations",
                [
                    ("Rating", str(thumbnail.rating or "-")),
                    ("Tags", ", ".join(thumbnail.tags) if thumbnail.tags else "-"),
                ],
            )
        )
        associated_files = self._associated_file_entries(dataset, thumbnail.ts_name)

        window = tk.Toplevel(self.frame)
        window.title(f"TS details: {thumbnail.ts_name}")
        window.geometry("1100x760")
        window.minsize(860, 560)
        window.transient(self.frame.winfo_toplevel())
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)

        container = ttk.Frame(window, padding=12)
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)
        container.rowconfigure(2, weight=1)

        tree = ttk.Treeview(container, columns=("field", "value"), show="tree headings")
        tree.heading("#0", text="Section")
        tree.heading("field", text="Field")
        tree.heading("value", text="Value")
        tree.column("#0", width=180, anchor="w", stretch=False)
        tree.column("field", width=240, anchor="w", stretch=False)
        tree.column("value", width=620, anchor="w", stretch=False)
        tree.grid(row=0, column=0, sticky="nsew")

        tree_yscroll = ttk.Scrollbar(container, orient="vertical", command=tree.yview)
        tree_yscroll.grid(row=0, column=1, sticky="ns")
        tree_xscroll = ttk.Scrollbar(container, orient="horizontal", command=tree.xview)
        tree_xscroll.grid(row=1, column=0, sticky="ew")
        tree.configure(yscrollcommand=tree_yscroll.set, xscrollcommand=tree_xscroll.set)

        for section_title, rows in sections:
            section_id = tree.insert("", "end", text=section_title, open=True, values=("", ""))
            for field, value in rows:
                tree.insert(section_id, "end", text="", values=(field, value))
        autosize_detail_tree_columns(tree, sections)

        associated_box = ttk.LabelFrame(container, text="Associated files", padding=12)
        associated_box.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(12, 0))
        associated_box.columnconfigure(0, weight=1)
        associated_box.rowconfigure(0, weight=1)

        assoc_canvas = tk.Canvas(associated_box, highlightthickness=0, height=220)
        assoc_canvas.grid(row=0, column=0, sticky="nsew")
        assoc_yscroll = ttk.Scrollbar(associated_box, orient="vertical", command=assoc_canvas.yview)
        assoc_yscroll.grid(row=0, column=1, sticky="ns")
        assoc_xscroll = ttk.Scrollbar(associated_box, orient="horizontal", command=assoc_canvas.xview)
        assoc_xscroll.grid(row=1, column=0, sticky="ew")
        assoc_canvas.configure(yscrollcommand=assoc_yscroll.set, xscrollcommand=assoc_xscroll.set)

        assoc_rows = ttk.Frame(assoc_canvas)
        assoc_rows.columnconfigure(1, weight=1)
        assoc_window = assoc_canvas.create_window((0, 0), window=assoc_rows, anchor="nw")
        bind_scrollable_canvas(assoc_canvas, assoc_window, assoc_rows, allow_horizontal=True)

        if associated_files:
            ttk.Label(assoc_rows, text="Role", style="Heading.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 12))
            ttk.Label(assoc_rows, text="Path", style="Heading.TLabel").grid(row=0, column=1, sticky="w")
            for row_index, (role_name, path_value) in enumerate(associated_files, start=1):
                ttk.Label(assoc_rows, text=role_name).grid(row=row_index, column=0, sticky="nw", padx=(0, 12), pady=4)
                ttk.Label(assoc_rows, text=path_value, justify="left").grid(row=row_index, column=1, sticky="w", pady=4)
                ttk.Button(
                    assoc_rows,
                    text="Open file",
                    command=lambda current_path=path_value: self._open_associated_file(current_path),
                ).grid(row=row_index, column=2, sticky="e", padx=(12, 0), pady=4)
        else:
            ttk.Label(
                assoc_rows,
                text="No associated File Registry entries were found for this TS.",
                justify="left",
            ).grid(row=0, column=0, sticky="w")

        footer = ttk.Frame(container)
        footer.grid(row=3, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(footer, text="Close", command=window.destroy).grid(row=0, column=0)

    def _associated_file_entries(self, dataset: DatasetRecord, ts_name: str) -> list[tuple[str, str]]:
        entries: list[tuple[str, str]] = []
        for role in file_role_order(self.app.project):
            record = resolve_dataset_file(self.app.project, dataset, ts_name, role)
            path_value = record.path.strip()
            if not path_value:
                continue
            if record.source in {"missing", "ambiguous"}:
                continue
            candidate = Path(path_value).expanduser()
            if not candidate.exists():
                continue
            entries.append((role_title(self.app.project, role), str(candidate)))
        entries.sort(key=lambda item: (item[0].casefold(), item[1].casefold()))
        return entries

    def _open_associated_file(self, path: str) -> None:
        try:
            self.app.open_external_file(path)
        except Exception as exc:
            messagebox.showerror("Open file", str(exc))
            return
        self.app.status_var.set(f"Opened file: {Path(path).name}")

    def _show_selected_thumbnail_details_dialog(self) -> None:
        dataset, thumbnail = self._selected_dataset_and_thumbnail()
        if dataset is None or thumbnail is None:
            return
        self._show_thumbnail_details_dialog(dataset, thumbnail)

    def _confirm_delete_selected_ts_data(self) -> None:
        if self.multi_selection_var.get():
            batch = self._selected_records()
            if not batch:
                messagebox.showinfo("Delete TS data", "Please select at least one thumbnail first.")
                return
            label = f"{len(batch)} selected TS"
        else:
            dataset, thumbnail = self._selected_dataset_and_thumbnail()
            if dataset is None or thumbnail is None:
                messagebox.showinfo("Delete TS data", "Please select a thumbnail first.")
                return
            batch = [(dataset, thumbnail)]
            label = thumbnail.ts_name

        first_confirm = messagebox.askyesno(
            "Delete TS data",
            f"Are you sure you want to delete ALL data associated to {label} ?",
        )
        if not first_confirm:
            return

        delete_raw = self.delete_raw_data_var.get()
        if delete_raw:
            second_confirm = messagebox.askyesno(
                "Delete RAW data too",
                "Are you sure you want to delete the RAW data too? If so, the data might be irreversibly gone.",
            )
            if not second_confirm:
                return

        self._open_delete_log_window(batch, delete_raw)

    def _parse_tomostar_frame_stems(self, tomostar_path: Path) -> list[str]:
        frame_stems: list[str] = []
        if not tomostar_path.exists():
            return frame_stems
        try:
            lines = tomostar_path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            lines = tomostar_path.read_text(encoding="utf-8-sig").splitlines()

        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("_") or stripped == "loop_" or stripped == "data_":
                continue
            first_token = stripped.split()[0]
            movie_path = Path(first_token)
            frame_stems.append(movie_path.stem)
        return frame_stems

    def _delete_matching_files(self, root_folder: str, identifiers: list[str], log, cancel_event: threading.Event) -> int:
        folder = Path(root_folder)
        if not folder.exists():
            log(f"Folder not found, skipping: {folder}")
            return 0

        normalized = [identifier.casefold() for identifier in identifiers if identifier]
        deleted = 0
        for path in sorted(folder.rglob("*"), key=lambda item: str(item).casefold()):
            if cancel_event.is_set():
                log("Deletion cancelled by user.")
                break
            if not path.is_file():
                continue
            name_lower = path.name.casefold()
            if any(identifier in name_lower for identifier in normalized):
                try:
                    path.unlink()
                    deleted += 1
                    log(f"Deleted: {path}")
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    log(f"Failed to delete {path}: {exc}")
        return deleted

    def _update_processed_items_file(
        self,
        json_path: Path,
        identifiers: list[str],
        log,
        cancel_event: threading.Event,
    ) -> None:
        if cancel_event.is_set():
            return
        if not json_path.exists():
            log(f"processed_items.json not found, skipping: {json_path}")
            return
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log(f"Failed to read {json_path}: {exc}")
            return
        if not isinstance(payload, list):
            log(f"Unexpected processed_items format in {json_path}")
            return

        normalized = [identifier.casefold() for identifier in identifiers if identifier]
        updated = []
        removed = 0
        for item in payload:
            current_path = str(item.get("Path", "")) if isinstance(item, dict) else ""
            current_name = Path(current_path).name.casefold()
            current_stem = Path(current_path).stem.casefold()
            if any(identifier in current_name or identifier in current_stem for identifier in normalized):
                removed += 1
                continue
            updated.append(item)

        try:
            json_path.write_text(json.dumps(updated, indent=2, ensure_ascii=False), encoding="utf-8")
            log(f"Updated {json_path}: removed {removed} entries")
        except Exception as exc:
            log(f"Failed to update {json_path}: {exc}")

    def _delete_raw_frame_files(
        self,
        raw_folder: str,
        frame_stems: list[str],
        log,
        cancel_event: threading.Event,
    ) -> int:
        folder = Path(raw_folder)
        if not folder.exists():
            log(f"Raw folder not found, skipping: {folder}")
            return 0
        normalized = [stem.casefold() for stem in frame_stems if stem]
        deleted = 0
        for path in sorted(folder.rglob("*"), key=lambda item: str(item).casefold()):
            if cancel_event.is_set():
                log("Deletion cancelled by user.")
                break
            if not path.is_file():
                continue
            if Path(path.name).stem.casefold() in normalized:
                try:
                    path.unlink()
                    deleted += 1
                    log(f"Deleted raw file: {path}")
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    log(f"Failed to delete raw file {path}: {exc}")
        return deleted

    def _clear_selected_thumbnail_details(self) -> None:
        self.selected_thumbnail_key = None
        self.selected_dataset_var.set("-")
        self.selected_ts_var.set("-")
        self.selected_pixelsize_var.set("-")
        self.selected_mrc_var.set("-")
        self.selected_tags_var.set("-")
        self.selected_rating_var.set(0)
        self.tag_listbox.delete(0, "end")
        self.open_mrc_button.config(state="disabled")
        self.link_mrc_button.config(text="Link .mrc file", state="disabled")
        self.add_to_ts_list_button.config(state="disabled")

    def _toggle_multi_selection(self) -> None:
        enabled = not self.multi_selection_var.get()
        self.multi_selection_var.set(enabled)
        self.multi_selected_keys.clear()
        if enabled:
            self.selected_thumbnail_key = None
        self._update_multi_selection_widgets()
        self._update_details_for_current_selection()
        self._apply_selection_styles()

    def prepare_multi_selection(self) -> None:
        if not self.multi_selection_var.get():
            self.multi_selection_var.set(True)
        self.multi_selected_keys.clear()
        self.selected_thumbnail_key = None
        self._update_multi_selection_widgets()
        self._update_details_for_current_selection()
        self._apply_selection_styles()

    def _records_for_ts_transfer(self) -> list[tuple[DatasetRecord, ThumbnailRecord]]:
        if self.multi_selection_var.get():
            return self._selected_records()
        dataset, thumbnail = self._selected_dataset_and_thumbnail()
        if dataset is None or thumbnail is None:
            return []
        return [(dataset, thumbnail)]

    def _add_selected_to_ts_processing_list(self) -> None:
        records = self._records_for_ts_transfer()
        if not records:
            messagebox.showinfo("Add to TS processing list", "Please select one or more tomograms first.")
            return
        tomograms_tab = self.app.tabs.get("tomograms")
        if tomograms_tab is None or not hasattr(tomograms_tab, "add_ts_entries"):
            return
        entries = [(dataset.dataset_name, thumbnail.ts_name) for dataset, thumbnail in records]
        added = tomograms_tab.add_ts_entries(entries)
        self.app.status_var.set(f"Added {added} TS to the tomogram processing list")

    def _select_all_visible(self) -> None:
        if not self.multi_selection_var.get():
            return
        datasets = self._datasets_for_selection()
        records_by_dataset = self._existing_records_by_dataset(datasets)
        items = self._filtered_items(records_by_dataset)
        self.multi_selected_keys = {
            (dataset.dataset_name, thumbnail.image_path)
            for dataset, thumbnail in items
        }
        for key, variable in self.selection_vars.items():
            variable.set(key in self.multi_selected_keys)
        self._update_details_for_current_selection()
        self._apply_selection_styles()

    def _run_ts_delete(
        self,
        dataset: DatasetRecord,
        thumbnail: ThumbnailRecord,
        delete_raw: bool,
        log,
        cancel_event: threading.Event,
    ) -> dict[str, int | str]:
        ts_name = thumbnail.ts_name
        resolved_tomostar = resolve_dataset_file(self.app.project, dataset, ts_name, "tomostar")
        tomostar_path = Path(resolved_tomostar.path) if resolved_tomostar.path else Path(dataset.tilt_series_data_folder) / f"{ts_name}.tomostar"
        frame_stems = self._parse_tomostar_frame_stems(tomostar_path)
        log(f"Deleting data for {ts_name}")
        log(f"Reading tomostar: {tomostar_path}")
        log(f"Found {len(frame_stems)} frame names")

        if cancel_event.is_set():
            log("Deletion cancelled by user.")
            return {"ts_name": ts_name, "cancelled": 1}

        frameseries_deleted = self._delete_matching_files(
            dataset.frame_series_processing_folder,
            frame_stems,
            log,
            cancel_event,
        )
        frameseries_json = Path(dataset.frame_series_processing_folder) / "processed_items.json"
        self._update_processed_items_file(frameseries_json, frame_stems, log, cancel_event)

        if cancel_event.is_set():
            return {"ts_name": ts_name, "cancelled": 1}

        if tomostar_path.exists():
            try:
                tomostar_path.unlink()
                log(f"Deleted tomostar file: {tomostar_path}")
            except OSError as exc:
                log(f"Failed to delete tomostar file {tomostar_path}: {exc}")
        else:
            log(f"Tomostar file not found, skipping: {tomostar_path}")

        ts_deleted = self._delete_matching_files(
            dataset.tilt_series_processing_folder,
            [ts_name],
            log,
            cancel_event,
        )
        ts_json = Path(dataset.tilt_series_processing_folder) / "processed_items.json"
        self._update_processed_items_file(ts_json, [ts_name], log, cancel_event)

        raw_deleted = 0
        if delete_raw and not cancel_event.is_set():
            raw_deleted = self._delete_raw_frame_files(
                dataset.raw_frames_folder,
                frame_stems,
                log,
                cancel_event,
            )

        if cancel_event.is_set():
            return {"ts_name": ts_name, "cancelled": 1}

        log(
            "Finished deletion. "
            f"Frameseries files deleted: {frameseries_deleted}, "
            f"Tiltseries files deleted: {ts_deleted}, "
            f"Raw files deleted: {raw_deleted}"
        )
        return {
            "ts_name": ts_name,
            "frameseries_deleted": frameseries_deleted,
            "tiltseries_deleted": ts_deleted,
            "raw_deleted": raw_deleted,
            "cancelled": 0,
        }

    def _open_delete_log_window(
        self,
        batch: list[tuple[DatasetRecord, ThumbnailRecord]],
        delete_raw: bool,
    ) -> None:
        dialog = tk.Toplevel(self.frame)
        dialog.title("Deleting files...")
        dialog.geometry("900x520")
        dialog.transient(self.frame.winfo_toplevel())
        dialog.grab_set()
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)

        text = tk.Text(dialog, wrap="word", state="disabled")
        text.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=12, pady=(12, 8))

        cancel_event = threading.Event()
        queue: Queue[tuple[str, object]] = Queue()

        def append_log(message: str) -> None:
            queue.put(("log", message))

        def finish(status: str, result: dict[str, int | str] | None = None) -> None:
            queue.put(("done", (status, result or {})))

        ok_button = ttk.Button(dialog, text="OK", state="disabled", command=dialog.destroy)
        ok_button.grid(row=1, column=0, sticky="e", padx=(12, 6), pady=(0, 12))

        def on_cancel() -> None:
            cancel_event.set()
            cancel_button.config(state="disabled")
            append_log("Cancellation requested. Stopping as soon as possible...")

        cancel_button = ttk.Button(dialog, text="Cancel", command=on_cancel)
        cancel_button.grid(row=1, column=1, sticky="w", padx=(6, 12), pady=(0, 12))

        def pump_queue() -> None:
            try:
                while True:
                    kind, payload = queue.get_nowait()
                    if kind == "log":
                        text.configure(state="normal")
                        text.insert("end", payload + "\n")
                        text.see("end")
                        text.configure(state="disabled")
                    elif kind == "done":
                        status_text, result = payload
                        if isinstance(result, dict) and not result.get("cancelled"):
                            deleted_keys = {
                                (str(item.get("dataset_name", "")), str(item.get("image_path", "")))
                                for item in result.get("deleted_items", [])
                                if isinstance(item, dict)
                            }
                            for dataset_name, image_path in deleted_keys:
                                dataset = next(
                                    (entry for entry in self.app.project.datasets if entry.dataset_name == dataset_name),
                                    None,
                                )
                                if dataset is None:
                                    continue
                                dataset.thumbnails = [
                                    item for item in dataset.thumbnails if item.image_path != image_path
                                ]
                                folder = self._effective_thumbnail_folder(dataset)
                                self.dataset_match_cache.pop((dataset.dataset_name, folder), None)
                                self._clear_dataset_thumbnail_runtime_cache(dataset.dataset_name)
                            if self.selected_thumbnail_key in deleted_keys:
                                self._clear_selected_thumbnail_details()
                            self.multi_selected_keys.difference_update(deleted_keys)
                        cancel_button.config(state="disabled")
                        ok_button.config(state="normal")
                        self._request_gallery_render()
                        self.app.on_project_changed("gallery", "file_registry", "tomograms", "custom")
                        self.app.status_var.set(str(status_text))
            except Empty:
                pass
            if ok_button.cget("state") == "disabled":
                dialog.after(100, pump_queue)

        def worker() -> None:
            try:
                deleted_items: list[dict[str, str]] = []
                total = len(batch)
                result: dict[str, object] = {"deleted_items": deleted_items, "cancelled": 0}
                for index, (dataset, thumbnail) in enumerate(batch, start=1):
                    if cancel_event.is_set():
                        result["cancelled"] = 1
                        break
                    append_log(f"[{index}/{total}] Starting {thumbnail.ts_name} ({dataset.dataset_name})")
                    item_result = self._run_ts_delete(dataset, thumbnail, delete_raw, append_log, cancel_event)
                    if item_result.get("cancelled"):
                        result["cancelled"] = 1
                        break
                    deleted_items.append(
                        {
                            "dataset_name": dataset.dataset_name,
                            "image_path": thumbnail.image_path,
                            "ts_name": thumbnail.ts_name,
                        }
                    )
                if cancel_event.is_set():
                    finish("Deletion cancelled", result)
                else:
                    finish(f"Deleted data for {len(deleted_items)} TS", result)
            except Exception as exc:
                append_log(f"Deletion failed: {exc}")
                finish("Deletion failed")

        threading.Thread(target=worker, daemon=True).start()
        pump_queue()

    def _clear_selected_rating(self) -> None:
        self.selected_rating_var.set(0)
        self._save_selected_thumbnail_metadata()

    def _add_tag_to_selected(self) -> None:
        if self.selected_thumbnail_key is None:
            return
        new_tag = self.tag_input_var.get().strip()
        if not new_tag:
            return
        dataset_name, image_path = self.selected_thumbnail_key
        dataset = next((d for d in self.app.project.datasets if d.dataset_name == dataset_name), None)
        if dataset is None:
            return
        thumbnail = next((t for t in dataset.thumbnails if t.image_path == image_path), None)
        if thumbnail is None:
            return
        if new_tag not in thumbnail.tags:
            thumbnail.tags.append(new_tag)
        self.tag_input_var.set("")
        self._select_thumbnail(dataset_name, image_path)
        self._update_tag_suggestions()
        self._request_gallery_render(reuse_prepared_records=True)

    def _remove_selected_tag(self) -> None:
        if self.selected_thumbnail_key is None:
            return
        selection = self.tag_listbox.curselection()
        if not selection:
            return
        tag_to_remove = self.tag_listbox.get(selection[0])
        dataset_name, image_path = self.selected_thumbnail_key
        dataset = next((d for d in self.app.project.datasets if d.dataset_name == dataset_name), None)
        if dataset is None:
            return
        thumbnail = next((t for t in dataset.thumbnails if t.image_path == image_path), None)
        if thumbnail is None:
            return
        thumbnail.tags = [tag for tag in thumbnail.tags if tag != tag_to_remove]
        self._select_thumbnail(dataset_name, image_path)
        self._update_tag_suggestions()
        self._request_gallery_render(reuse_prepared_records=True)

    def _render_gallery(self, _event=None) -> None:
        self._pending_render_after = None
        self._needs_render_when_shown = False
        self._cancel_pending_auto_select()
        self._cancel_pending_render_batch()
        self.card_widgets.clear()
        self.selection_vars.clear()

        datasets = self._datasets_for_selection()
        if not datasets:
            self.summary_label.config(text="No datasets available yet.")
            self._hide_unused_gallery_cards(0)
            self._update_pager_controls(0, 0, 0)
            self._update_details_for_current_selection()
            self._update_import_button_label()
            return

        reuse_prepared_records = self._reuse_prepared_records_once
        self._reuse_prepared_records_once = False
        if reuse_prepared_records:
            records_by_dataset = self._existing_records_by_dataset(datasets)
        else:
            records_by_dataset = self._prepare_thumbnail_cache(datasets)
        items = self._filtered_items(records_by_dataset)
        self._update_import_button_label()

        if not items:
            self.summary_label.config(
                text="No thumbnails matched the current selection or filters. Use 'Reset filters' to show all available TS."
            )
            self._hide_unused_gallery_cards(0)
            self._update_pager_controls(0, 0, 0)
            self._update_details_for_current_selection()
            return

        page_items, start, end, total_items = self._paged_items(items)
        self._prune_thumbnail_cache(page_items)
        columns = self._column_count_for_width()
        previous_columns = self._last_render_column_count or 0
        for column in range(max(columns, previous_columns)):
            self.gallery_frame.columnconfigure(column, weight=1 if column < columns else 0)
        self._last_render_column_count = columns

        self.summary_label.config(
            text=(
                f"Showing {start + 1}-{end} of {total_items} thumbnails."
                if total_items > self._gallery_page_size()
                else f"Showing {total_items} thumbnails."
            )
        )
        self._update_pager_controls(start, end, total_items)
        filtered_keys = {(dataset.dataset_name, thumbnail.image_path) for dataset, thumbnail in items}
        if self.multi_selection_var.get():
            self.multi_selected_keys.intersection_update(filtered_keys)
        elif self.selected_thumbnail_key not in filtered_keys:
            self.selected_thumbnail_key = None

        self._render_generation += 1
        generation = self._render_generation
        self._ensure_gallery_card_pool(len(page_items))
        for index, (dataset, thumbnail) in enumerate(page_items):
            row = index // columns
            column = index % columns
            self._prepare_gallery_card_slot(self.gallery_card_pool[index], dataset, thumbnail, row, column)
        self._hide_unused_gallery_cards(len(page_items))
        self._update_multi_selection_widgets()

        if self.multi_selection_var.get():
            self._apply_selection_styles()
            self._update_details_for_current_selection()
        elif self.selected_thumbnail_key is None and page_items:
            self._apply_selection_styles()
            self._schedule_auto_select_thumbnail(page_items[0][0].dataset_name, page_items[0][1].image_path)
        else:
            self._apply_selection_styles()
            self._update_details_for_current_selection()
        self._render_gallery_batch(page_items, 0, generation)

    def _import_thumbnails(self) -> None:
        datasets = self._datasets_for_selection()
        if not datasets:
            messagebox.showinfo("No dataset selected", "Please load a dataset first.")
            return

        folder = filedialog.askdirectory(title="Select thumbnail folder")
        if not folder:
            return

        imported = 0
        for dataset in datasets:
            dataset.thumbnail_folder = folder
            self.dataset_match_cache.pop((dataset.dataset_name, folder), None)
            self._clear_dataset_thumbnail_runtime_cache(dataset.dataset_name)
            imported += len(self._scan_thumbnails_for_dataset(dataset))

        self._reset_gallery_page()
        self._update_tag_suggestions()
        self._request_gallery_render()
        self.app.status_var.set(f"Thumbnail folder set to: {folder} ({imported} matches)")

    def _link_mrc_folder(self) -> None:
        datasets = self._datasets_for_selection()
        if not datasets:
            messagebox.showinfo("No dataset selected", "Please load a dataset first.")
            return

        folder = filedialog.askdirectory(title="Select tomogram (.mrc) folder")
        if not folder:
            return

        matched = 0
        thumbnails_seen = 0
        for dataset in datasets:
            dataset.tomogram_folder = folder
            matched += self._refresh_dataset_mrc_paths(dataset)
            thumbnails_seen += len(dataset.thumbnails)

        if self.selected_thumbnail_key is not None:
            dataset_name, image_path = self.selected_thumbnail_key
            self._select_thumbnail(dataset_name, image_path)
        else:
            self._update_details_for_current_selection()
        self.app.on_project_changed("gallery")
        self.app.status_var.set(
            f"Tomogram folder set to: {folder} ({matched}/{thumbnails_seen} thumbnails matched to .mrc files)"
        )

    def _change_zoom(self, direction: int) -> None:
        factor = 1.1 if direction > 0 else 1 / 1.1
        new_size = int(round(self.thumbnail_size * factor))
        self.thumbnail_size = max(80, min(420, new_size))
        self.thumbnail_size_var.set(self.thumbnail_size)
        self.thumbnail_images.clear()
        self._request_gallery_render(reuse_prepared_records=True)

    def _on_dataset_selected(self, _event=None) -> None:
        self._cancel_pending_auto_select()
        self.selected_thumbnail_key = None
        self.multi_selected_keys.clear()
        self._reset_gallery_page()
        self._request_gallery_render()

    def on_project_loaded(self, project: ProjectData) -> None:
        if self._loaded_project_id != id(project):
            self.dataset_match_cache.clear()
            self.thumbnail_images.clear()
            self.thumbnail_display_paths.clear()
            self.thumbnail_cache_state.clear()
            self.thumbnail_record_index.clear()
            self._loaded_project_id = id(project)
            self._details_pane_width = load_layout_value(
                project,
                "gallery",
                "details",
                dimension="width",
                default=self._default_details_pane_width,
                minimum=self._details_pane_min_width,
                maximum=self.app._scale_pixels(900),
            )
            self.dataset_var.set("All datasets")
            self.min_rating_var.set("Any")
            self.tag_include_mode_var.set("All selected")
            self._clear_filter_tag_selections()
            self.multi_selection_var.set(False)
            self.multi_selected_keys.clear()
            self.selection_vars.clear()
            self._cancel_pending_auto_select()
            self.selected_thumbnail_key = None
            self.select_all_button.grid_remove()
            self._clear_selected_thumbnail_details()
            self._reset_gallery_page()
        options = self._dataset_options(project)
        self.dataset_combo.configure(values=options)
        if not options:
            self.dataset_var.set("")
            self._update_import_button_label()
            self.summary_label.config(text="No datasets available yet.")
            self._update_pager_controls(0, 0, 0)
            return

        if self.dataset_var.get() not in options:
            self.dataset_var.set("All datasets")

        self._update_tag_suggestions()
        for dataset in project.datasets:
            folder = self._effective_thumbnail_folder(dataset)
            if dataset.thumbnails and folder:
                self.dataset_match_cache[(dataset.dataset_name, folder)] = list(dataset.thumbnails)
                self._reindex_dataset_records(dataset, list(dataset.thumbnails))
        if self.app.active_tab_id == self.tab_id and self.frame.winfo_ismapped():
            self._request_gallery_render()
        else:
            self._needs_render_when_shown = True

    def preload_view(self) -> None:
        self._needs_render_when_shown = True

    def on_tab_shown(self) -> None:
        self.frame.after_idle(self._initialize_main_pane)
        self.frame.after_idle(self._apply_main_pane_width)
        if self.app.project.datasets and not self.dataset_var.get().strip():
            self.dataset_var.set("All datasets")
        if self.app.project.datasets:
            self.frame.after_idle(self._request_gallery_render)
            return
        if self._needs_render_when_shown:
            self.frame.after_idle(self._request_gallery_render)

    def sync_to_project(self, project: ProjectData) -> None:
        save_layout_value(project, "gallery", "details", self._details_pane_width, dimension="width")

    def reset_window_sizes(self) -> None:
        self._details_pane_width = self._default_details_pane_width
        if self.main_pane.winfo_exists():
            self._apply_main_pane_width()
            self._update_details_wraplength()
