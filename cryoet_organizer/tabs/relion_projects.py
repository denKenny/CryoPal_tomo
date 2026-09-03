from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from cryoet_organizer.mrc_preview import MrcPlanePreview, MrcPreview, mrc_preview_cache_key, read_mrc_preview
from cryoet_organizer.performance import chunked_treeview_replace
from cryoet_organizer.project import dataset_ts_names
from cryoet_organizer.relion_pipeline import (
    RelionPipelineJob,
    class3d_preview_files_from_list,
    group_relion_jobs_by_type,
    parse_relion_pipeline_processes,
    refine3d_preview_file_from_list,
    relion_job_files,
    relion_jobs_descending,
)
from cryoet_organizer.relion_projects import (
    RelionProjectDefinition,
    get_project_relion_default,
    get_project_relion_projects,
    set_project_relion_projects,
)
from cryoet_organizer.star_merge import (
    OperationAborted,
    ParticleClassificationConvergencePlot,
    particle_classification_convergence_data,
)
from cryoet_organizer.tabs.base import SidebarTab

try:
    from PIL import Image, ImageTk
except Exception:  # pragma: no cover - optional preview accelerator
    Image = None
    ImageTk = None


MRC_SUFFIXES = {".mrc", ".mrcs"}
PREVIEW_READ_SIZE = 60
PREVIEW_ZOOM = 2


@dataclass
class _CachedJobPreview:
    title: str
    results: list[tuple[str, MrcPreview]]
    convergence_plot: ParticleClassificationConvergencePlot | None = None
    convergence_error: str = ""


@dataclass
class _PipelineLoadResult:
    root_directory: str
    jobs: list[RelionPipelineJob]
    error: str = ""


@dataclass
class _FileTreeRow:
    path: Path
    modified: str


class RelionProjectsTab(SidebarTab):
    tab_id = "relion_projects"
    title = "Relion projects"
    refresh_domains = ("relion_projects",)

    def build(self) -> None:
        self.projects: list[RelionProjectDefinition] = []
        self.project_var = tk.StringVar()
        self.project_by_name: dict[str, RelionProjectDefinition] = {}
        self.jobs_by_type: dict[str, list[RelionPipelineJob]] = {}
        self.pipeline_jobs: list[RelionPipelineJob] = []
        self.job_by_iid: dict[str, RelionPipelineJob] = {}
        self.file_by_iid: dict[str, Path] = {}
        self.preview_generation = 0
        self.preview_cache: OrderedDict[tuple[str, float, int, str], MrcPreview] = OrderedDict()
        self.job_preview_cache: OrderedDict[str, _CachedJobPreview] = OrderedDict()
        self.file_list_cache: dict[str, list[Path]] = {}
        self.file_row_cache: dict[str, list[_FileTreeRow]] = {}
        self.preview_cache_lock = threading.Lock()
        self.rendered_image_cache: OrderedDict[tuple[object, ...], tk.PhotoImage] = OrderedDict()
        self.preview_images: list[tk.PhotoImage] = []
        self.current_job: RelionPipelineJob | None = None
        self.current_job_preview_key = ""
        self.convergence_preview_container: ttk.Frame | None = None
        self.convergence_generation = 0
        self._preview_sync_pending = False
        self._preview_sync_after_id: str | None = None
        self._preview_mousewheel_active = False
        self._preview_mousewheel_global_bound = False
        self._pipeline_generation = 0
        self._job_file_generation = 0
        self._file_insert_generation = 0
        self._type_select_after_id: str | None = None
        self._job_select_after_id: str | None = None
        self._file_select_after_id: str | None = None
        self._loading_pipeline = False
        self._loading_job_files = False
        self._preview_busy = False

        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=1)

        header = ttk.LabelFrame(self.frame, text="Select Relion project", padding=12)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        self.project_combo = ttk.Combobox(
            header,
            textvariable=self.project_var,
            state="readonly",
            values=[],
        )
        self.project_combo.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.project_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_project_selected())

        self.open_button = ttk.Button(
            header,
            text="Open in Relion",
            command=self._open_in_relion,
            state="disabled",
        )
        self.open_button.grid(row=0, column=1, sticky="e", padx=(0, 8))

        ttk.Button(
            header,
            text="Add Relion project",
            command=self._add_relion_project,
        ).grid(row=0, column=2, sticky="e")

        main_pane = ttk.Panedwindow(self.frame, orient="vertical")
        main_pane.grid(row=1, column=0, sticky="nsew", pady=(12, 0))

        body = ttk.Frame(main_pane)
        for column in range(3):
            body.columnconfigure(column, weight=1, uniform="relion_columns")
        body.rowconfigure(1, weight=1)

        ttk.Label(body, text="Job Type").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 4))
        ttk.Label(body, text="Jobs").grid(row=0, column=1, sticky="w", padx=(0, 8), pady=(0, 4))
        ttk.Label(body, text="Files").grid(row=0, column=2, sticky="w", pady=(0, 4))

        type_frame, self.type_view = self._build_tree(body, columns=("name",), headings=("Job Type",))
        type_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        self.type_view.bind("<<TreeviewSelect>>", lambda _event: self._schedule_type_selected())

        job_frame, self.job_view = self._build_tree(body, columns=("job",), headings=("Job",))
        job_frame.grid(row=1, column=1, sticky="nsew", padx=(0, 8))
        self.job_view.bind("<<TreeviewSelect>>", lambda _event: self._schedule_job_selected())

        file_frame, self.file_view = self._build_tree(
            body,
            columns=("name", "modified"),
            headings=("File", "Last modified"),
            selectmode="extended",
        )
        self.file_view.column("name", width=320, anchor="w")
        self.file_view.column("modified", width=150, anchor="w", stretch=False)
        file_frame.grid(row=1, column=2, sticky="nsew")
        self.file_view.bind("<Double-Button-1>", lambda _event: self._open_selected_files())
        self.file_view.bind("<<TreeviewSelect>>", lambda _event: self._schedule_file_selection_changed())
        main_pane.add(body, weight=4)

        preview = ttk.LabelFrame(main_pane, text="Preview", padding=8)
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(1, weight=1)
        preview_actions = ttk.Frame(preview)
        preview_actions.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        preview_actions.columnconfigure(0, weight=1)
        preview_actions.columnconfigure(3, weight=1)
        self.preview_title_var = tk.StringVar(value="Relion project overview")
        ttk.Label(preview_actions, textvariable=self.preview_title_var, anchor="w").grid(row=0, column=0, sticky="ew")
        self.convergence_button = ttk.Button(
            preview_actions,
            text="Plot classification convergence",
            command=self._plot_classification_convergence,
            state="disabled",
        )
        self.convergence_button.grid(row=0, column=1, padx=(8, 0))
        self.preview_selected_file_button = ttk.Button(
            preview_actions,
            text="Preview",
            command=self._preview_selection,
            state="disabled",
        )
        self.preview_selected_file_button.grid(row=0, column=2, padx=(8, 0))
        self.preview_progress = ttk.Progressbar(preview_actions, orient="horizontal", mode="indeterminate", length=120)
        self.preview_progress.grid(row=0, column=3, sticky="e", padx=(8, 0))
        self.preview_progress.grid_remove()
        self.refresh_button = ttk.Button(preview_actions, text="Refresh", command=self._refresh_selected_project)
        self.refresh_button.grid(row=0, column=4, padx=(8, 0))
        self.open_selected_button = ttk.Button(
            preview_actions,
            text="Open selected file(s)",
            command=self._open_selected_files,
            state="disabled",
        )
        self.open_selected_button.grid(row=0, column=5, padx=(8, 0))
        self.open_chimerax_button = ttk.Button(
            preview_actions,
            text="ChimeraX",
            command=lambda: self._open_files_with_command("chimerax", self._selected_paths()),
            state="disabled",
        )
        self.open_chimerax_button.grid(row=0, column=6, padx=(8, 0))
        self.open_3dmod_button = ttk.Button(
            preview_actions,
            text="3dmod",
            command=lambda: self._open_files_with_command("3dmod", self._selected_paths()),
            state="disabled",
        )
        self.open_3dmod_button.grid(row=0, column=7, padx=(8, 0))

        preview_body = ttk.Frame(preview)
        preview_body.grid(row=1, column=0, sticky="nsew")
        preview_body.columnconfigure(0, weight=1)
        preview_body.rowconfigure(0, weight=1)
        self.preview_canvas = tk.Canvas(preview_body, highlightthickness=0)
        self.preview_canvas.grid(row=0, column=0, sticky="nsew")
        preview_scroll = ttk.Scrollbar(preview_body, orient="vertical", command=self.preview_canvas.yview)
        preview_scroll.grid(row=0, column=1, sticky="ns")
        self.preview_canvas.configure(yscrollcommand=preview_scroll.set)
        self.preview_content = ttk.Frame(self.preview_canvas)
        self.preview_window = self.preview_canvas.create_window((0, 0), window=self.preview_content, anchor="nw")
        self.preview_content.bind("<Configure>", lambda _event: self._schedule_preview_canvas_sync())
        self.preview_canvas.bind("<Configure>", lambda _event: self._schedule_preview_canvas_sync())
        for widget in (self.preview_canvas, self.preview_content):
            widget.bind("<Enter>", self._enable_preview_mousewheel, add="+")
            widget.bind("<Leave>", self._disable_preview_mousewheel, add="+")
        main_pane.add(preview, weight=1)

        self.status_var = tk.StringVar(value="No Relion project selected.")
        ttk.Label(self.frame, textvariable=self.status_var, anchor="w").grid(
            row=2,
            column=0,
            sticky="ew",
            pady=(8, 0),
        )

    def _build_tree(
        self,
        parent: tk.Misc,
        *,
        columns: tuple[str, ...],
        headings: tuple[str, ...],
        selectmode: str = "browse",
    ) -> tuple[ttk.Frame, ttk.Treeview]:
        frame = ttk.Frame(parent)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        tree = ttk.Treeview(
            frame,
            columns=columns,
            show="headings",
            selectmode=selectmode,
            style="Technical.Treeview",
        )
        for column, heading in zip(columns, headings):
            tree.heading(column, text=heading)
            tree.column(column, anchor="w", width=180)
        tree.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=yscroll.set)
        return frame, tree

    def on_project_loaded(self, project) -> None:
        self.projects = get_project_relion_projects(project)
        self._refresh_project_choices()

    def sync_to_project(self, project) -> None:
        set_project_relion_projects(project, self.projects)

    def _refresh_project_choices(self) -> None:
        self.project_by_name = {project.name: project for project in self.projects}
        names = sorted(self.project_by_name, key=str.casefold)
        self.project_combo.configure(values=names)
        current = self.project_var.get()
        if current not in self.project_by_name:
            self.project_var.set("")
            self._clear_pipeline_view("No Relion project selected.")
            self.open_button.configure(state="disabled")
        elif current:
            self._on_project_selected()

    def _add_relion_project(self) -> None:
        created = RelionProjectDialog(
            self.app.root,
            existing_names=list(self.project_by_name),
            default_project=get_project_relion_default(self.app.project),
        ).show()
        if created is None:
            return
        self.projects.append(created)
        set_project_relion_projects(self.app.project, self.projects)
        self.project_var.set(created.name)
        self.app.on_project_changed("relion_projects", status_message=f"Added Relion project: {created.name}")
        self._refresh_project_choices()
        self._on_project_selected()

    def _selected_project(self) -> RelionProjectDefinition | None:
        return self.project_by_name.get(self.project_var.get())

    def _on_project_selected(self) -> None:
        project = self._selected_project()
        if project is None:
            self.open_button.configure(state="disabled")
            self._clear_pipeline_view("No Relion project selected.")
            return
        self.open_button.configure(state="normal")
        self._clear_preview_caches()
        self._load_pipeline(project)

    def _load_pipeline(self, project: RelionProjectDefinition) -> None:
        self._pipeline_generation += 1
        generation = self._pipeline_generation
        self._set_loading_pipeline(True)
        self.jobs_by_type = {}
        self.pipeline_jobs = []
        self.job_by_iid = {}
        self.file_by_iid = {}
        self.current_job = None
        self.current_job_preview_key = ""
        self._loading_job_files = False
        self._job_file_generation += 1
        self._file_insert_generation += 1
        self._clear_tree(self.type_view)
        self._clear_tree(self.job_view)
        self._clear_tree(self.file_view)
        pipeline_path = Path(project.root_directory) / "default_pipeline.star"
        self.status_var.set("Loading Relion pipeline...")
        self._render_loading("Loading Relion pipeline...")

        def worker() -> None:
            error = ""
            jobs: list[RelionPipelineJob] = []
            if not pipeline_path.exists():
                error = "No default_pipeline.star found in the selected Relion root directory."
            else:
                try:
                    jobs = parse_relion_pipeline_processes(pipeline_path)
                except Exception as exc:
                    error = f"Could not read default_pipeline.star: {exc}"
            result = _PipelineLoadResult(root_directory=str(project.root_directory), jobs=jobs, error=error)
            self.app.root.after(0, lambda: self._finish_pipeline_load(generation, result))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_pipeline_load(self, generation: int, result: _PipelineLoadResult) -> None:
        if generation != self._pipeline_generation:
            return
        self._set_loading_pipeline(False)
        project = self._selected_project()
        if project is None or str(project.root_directory) != result.root_directory:
            return
        if result.error:
            self.status_var.set(result.error)
            self._render_message(result.error)
            return
        self.pipeline_jobs = result.jobs
        self.jobs_by_type = group_relion_jobs_by_type(result.jobs)
        for job_type, jobs_for_type in self.jobs_by_type.items():
            self.type_view.insert("", "end", iid=job_type, values=(f"{job_type} ({len(jobs_for_type)})",))
        self.status_var.set(f"Loaded {len(result.jobs)} Relion pipeline job(s).")
        self._render_idle_preview_message()

    def _on_type_selected(self) -> None:
        selection = self.type_view.selection()
        self.job_by_iid = {}
        self.file_by_iid = {}
        self.current_job = None
        self.current_job_preview_key = ""
        self._loading_job_files = False
        self._job_file_generation += 1
        self._file_insert_generation += 1
        self._clear_tree(self.job_view)
        self._clear_tree(self.file_view)
        self._update_action_buttons()
        if not selection:
            return
        job_type = str(selection[0])
        for job in self.jobs_by_type.get(job_type, []):
            iid = f"{job.job_type}/{job.job_id}"
            self.job_by_iid[iid] = job
            self.job_view.insert("", "end", iid=iid, values=(job.job_id,))

    def _on_job_selected(self) -> None:
        selection = self.job_view.selection()
        self.file_by_iid = {}
        self.current_job = None
        self.current_job_preview_key = ""
        self._loading_job_files = False
        self._job_file_generation += 1
        generation = self._job_file_generation
        self._file_insert_generation += 1
        self._clear_tree(self.file_view)
        project = self._selected_project()
        if project is None or not selection:
            self._update_action_buttons()
            return
        job = self.job_by_iid.get(str(selection[0]))
        if job is None:
            self._update_action_buttons()
            return
        self.current_job = job
        self._loading_job_files = True
        self._update_action_buttons()
        cache_key = self._job_preview_cache_key(project, job)
        cached_files = self.file_list_cache.get(cache_key)
        cached_rows = self.file_row_cache.get(cache_key)
        if cached_files is not None and cached_rows is not None:
            self._finish_job_file_load(generation, str(project.root_directory), job, list(cached_files), list(cached_rows), "")
            return
        self.status_var.set(f"Scanning files for {job.relative_path}...")

        def worker() -> None:
            files: list[Path] = []
            file_rows: list[_FileTreeRow] = []
            error = ""
            try:
                files = relion_job_files(project.root_directory, job)
                file_rows = [_FileTreeRow(path=path, modified=self._format_modified(path)) for path in files]
            except Exception as exc:
                error = str(exc)
            self.app.root.after(
                0,
                lambda: self._finish_job_file_load(generation, str(project.root_directory), job, files, file_rows, error),
            )

        threading.Thread(target=worker, daemon=True).start()

    def _finish_job_file_load(
        self,
        generation: int,
        root_directory: str,
        job: RelionPipelineJob,
        files: list[Path],
        file_rows: list[_FileTreeRow],
        error: str,
    ) -> None:
        if generation != self._job_file_generation:
            return
        self._loading_job_files = False
        project = self._selected_project()
        if project is None or str(project.root_directory) != root_directory:
            return
        if self.current_job is None or self.current_job.relative_path != job.relative_path:
            return
        if error:
            self.status_var.set(f"Could not scan {job.relative_path}: {error}")
            self._render_message(f"Could not scan {job.relative_path}: {error}")
            return
        cache_key = self._job_preview_cache_key(project, job)
        self.file_list_cache[cache_key] = list(files)
        self.file_row_cache[cache_key] = list(file_rows)
        self.status_var.set(f"{job.relative_path}: {len(files)} file(s) found.")
        self._populate_file_tree_chunked(file_rows)

    def _populate_file_tree_chunked(self, file_rows: list[_FileTreeRow]) -> None:
        self._file_insert_generation += 1
        generation = self._file_insert_generation
        self.file_by_iid = {}
        rows: list[tuple[str, tuple[object, ...], tuple[str, ...]]] = []
        for index, row in enumerate(file_rows):
            iid = f"file-{index}"
            self.file_by_iid[iid] = row.path
            rows.append((iid, (row.path.name, row.modified), ()))
        chunked_treeview_replace(
            self.file_view,
            rows,
            widget=self.frame,
            generation=generation,
            is_current=lambda current: current == self._file_insert_generation,
            chunk_size=160,
            on_complete=self._update_action_buttons,
        )

    def _clear_pipeline_view(self, message: str) -> None:
        self.jobs_by_type = {}
        self.pipeline_jobs = []
        self.job_by_iid = {}
        self.file_by_iid = {}
        self.current_job = None
        self.current_job_preview_key = ""
        self._loading_job_files = False
        self._job_file_generation += 1
        self._file_insert_generation += 1
        self._set_loading_pipeline(False)
        for tree in (self.type_view, self.job_view, self.file_view):
            self._clear_tree(tree)
        self.status_var.set(message)
        self._render_message(message)

    def _clear_tree(self, tree: ttk.Treeview) -> None:
        children = tree.get_children()
        if children:
            tree.delete(*children)

    def _set_preview_busy(self, busy: bool) -> None:
        if self._preview_busy == busy:
            return
        self._preview_busy = busy
        if busy:
            self.preview_progress.grid()
            self.preview_progress.start(12)
        else:
            self.preview_progress.stop()
            self.preview_progress.grid_remove()
        self._update_action_buttons()

    def _set_loading_pipeline(self, loading: bool) -> None:
        self._loading_pipeline = loading
        self._set_preview_busy(loading)
        refresh_state = "disabled" if loading else "normal"
        self.refresh_button.configure(state=refresh_state)
        if loading:
            for button in (
                self.preview_selected_file_button,
                self.open_selected_button,
                self.open_chimerax_button,
                self.open_3dmod_button,
                self.convergence_button,
            ):
                button.configure(state="disabled")
        else:
            self._update_action_buttons()

    def _schedule_type_selected(self) -> None:
        if self._loading_pipeline:
            return
        self._cancel_after("_type_select_after_id")
        self._type_select_after_id = self.frame.after(140, self._run_debounced_type_selected)

    def _run_debounced_type_selected(self) -> None:
        self._type_select_after_id = None
        self._on_type_selected()

    def _schedule_job_selected(self) -> None:
        if self._loading_pipeline:
            return
        self._cancel_after("_job_select_after_id")
        self._job_select_after_id = self.frame.after(160, self._run_debounced_job_selected)

    def _run_debounced_job_selected(self) -> None:
        self._job_select_after_id = None
        self._on_job_selected()

    def _schedule_file_selection_changed(self) -> None:
        if self._loading_pipeline:
            return
        self._cancel_after("_file_select_after_id")
        self._file_select_after_id = self.frame.after(90, self._run_debounced_file_selection_changed)

    def _run_debounced_file_selection_changed(self) -> None:
        self._file_select_after_id = None
        self._on_file_selection_changed()

    def _cancel_after(self, attribute: str) -> None:
        after_id = getattr(self, attribute, None)
        if after_id is None:
            return
        try:
            self.frame.after_cancel(after_id)
        except tk.TclError:
            pass
        setattr(self, attribute, None)

    def _format_modified(self, path: Path) -> str:
        try:
            return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return "-"

    def _open_in_relion(self) -> None:
        project = self._selected_project()
        if project is None:
            return
        root = Path(project.root_directory).expanduser()
        if not root.exists() or not root.is_dir():
            messagebox.showerror("Open in Relion", "Relion root directory does not exist.")
            return
        startup = project.startup_command.strip()
        if not startup:
            messagebox.showinfo("Open in Relion", "No startup command is defined for this Relion project.")
            return
        script = f"cd {self._shell_quote(str(root))}\n{startup}"
        try:
            self.app.run_shortcut_script_with_log(f"Open Relion: {project.name}", script)
        except Exception as exc:
            messagebox.showerror("Open in Relion", str(exc))

    def _selected_paths(self) -> list[Path]:
        return [
            self.file_by_iid[item]
            for item in self.file_view.selection()
            if item in self.file_by_iid
        ]

    def _on_file_selection_changed(self) -> None:
        if self._loading_job_files:
            return
        self._update_action_buttons()
        paths = self._selected_paths()
        if not paths:
            self.status_var.set("No file selected.")
            return
        if len(paths) == 1:
            self.status_var.set(f"{paths[0].name} selected. Click 'Preview' to render it.")
        else:
            self.status_var.set(f"{len(paths)} files selected. File preview is available for one file at a time.")

    def _update_action_buttons(self) -> None:
        if self._loading_pipeline or self._loading_job_files or self._preview_busy:
            for button in (
                self.preview_selected_file_button,
                self.open_selected_button,
                self.open_chimerax_button,
                self.open_3dmod_button,
                self.convergence_button,
            ):
                button.configure(state="disabled")
            return
        paths = self._selected_paths()
        state = "normal" if paths else "disabled"
        preview_state = "normal" if self._has_preview_target() else "disabled"
        mrc_state = "normal" if paths and all(path.suffix.casefold() in MRC_SUFFIXES for path in paths) else "disabled"
        self.preview_selected_file_button.configure(state=preview_state)
        self.open_selected_button.configure(state=state)
        self.open_chimerax_button.configure(state=mrc_state)
        self.open_3dmod_button.configure(state=mrc_state)
        convergence_state = "disabled"
        if not paths and self.current_job is not None and self.current_job.job_type.casefold() == "class3d":
            convergence_state = "normal"
        self.convergence_button.configure(state=convergence_state)

    def _has_preview_target(self) -> bool:
        paths = self._selected_paths()
        if len(paths) == 1:
            return paths[0].suffix.casefold() in {".star", *MRC_SUFFIXES}
        if len(paths) > 1:
            return False
        if self.current_job is None:
            return False
        project = self._selected_project()
        if project is None:
            return False
        files = self.file_list_cache.get(self._job_preview_cache_key(project, self.current_job), [])
        job_type = self.current_job.job_type.casefold()
        if job_type == "class3d":
            _iteration, preview_files = class3d_preview_files_from_list(files)
            return bool(preview_files)
        if job_type == "refine3d":
            _iteration, preview_file = refine3d_preview_file_from_list(files)
            return preview_file is not None
        return False

    def _preview_selection(self) -> None:
        paths = self._selected_paths()
        if len(paths) == 1:
            if paths[0].suffix.casefold() not in {".star", *MRC_SUFFIXES}:
                messagebox.showinfo("Preview", "No lightweight preview is available for the selected file.")
                return
            self._render_file_preview(paths[0])
            return
        if len(paths) > 1:
            messagebox.showinfo("Preview", "Please select exactly one file or a previewable Class3D/Refine3D job.")
            return
        if self.current_job is not None and self.current_job.job_type.casefold() in {"class3d", "refine3d"}:
            self._render_job_preview(self.current_job)
            return
        messagebox.showinfo("Preview", "Please select a previewable file or a Class3D/Refine3D job.")

    def _refresh_selected_project(self) -> None:
        self._clear_preview_caches()
        project = self._selected_project()
        if project is None:
            self._clear_pipeline_view("No Relion project selected.")
            return
        self._load_pipeline(project)

    def _render_overview(self) -> None:
        lines = ["Job\tAlias\tStatus", ""]
        for job in relion_jobs_descending(self.pipeline_jobs):
            alias = job.alias or "-"
            status = job.status or "-"
            lines.append(f"{job.relative_path}\t{alias}\t{status}")
        if len(lines) == 2:
            lines.append("No Relion pipeline jobs found.")
        self._render_text("Relion project overview", "\n".join(lines))

    def _render_job_preview(self, job: RelionPipelineJob, files: list[Path] | None = None) -> None:
        self._set_preview_busy(True)
        project = self._selected_project()
        if project is None:
            self._render_message("No Relion project selected.")
            self._set_preview_busy(False)
            return
        if files is None:
            files = self.file_list_cache.get(self._job_preview_cache_key(project, job), [])
        job_type = job.job_type.casefold()
        if job_type == "class3d":
            iteration, paths = class3d_preview_files_from_list(files)
            if paths:
                title = f"{job.relative_path} | iteration {iteration} | {job.status or '-'}"
                items = [(path.stem, path, path.suffix.casefold() == ".mrcs") for path in paths]
                cache_key = self._job_preview_cache_key(project, job)
                cached = self.job_preview_cache.get(cache_key)
                if cached is not None:
                    self.job_preview_cache.move_to_end(cache_key)
                    self._render_cached_job_preview(cache_key, cached)
                    self._set_preview_busy(False)
                else:
                    self._render_mrc_previews_async(title, items, job_cache_key=cache_key)
                return
        elif job_type == "refine3d":
            iteration, path = refine3d_preview_file_from_list(files)
            if path is not None:
                title = f"{job.relative_path} | iteration {iteration} | {job.status or '-'}"
                items = [(path.name, path, path.suffix.casefold() == ".mrcs")]
                cache_key = self._job_preview_cache_key(project, job)
                cached = self.job_preview_cache.get(cache_key)
                if cached is not None:
                    self.job_preview_cache.move_to_end(cache_key)
                    self._render_cached_job_preview(cache_key, cached)
                    self._set_preview_busy(False)
                else:
                    self._render_mrc_previews_async(title, items, job_cache_key=cache_key)
                return
        self._render_message(f"No lightweight preview available for {job.relative_path}.")
        self._set_preview_busy(False)

    def _render_file_preview(self, path: Path) -> None:
        self._set_preview_busy(True)
        suffix = path.suffix.casefold()
        if suffix == ".star":
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[:100]
            except Exception as exc:
                self._render_message(f"Could not read {path.name}: {exc}")
                self._set_preview_busy(False)
                return
            self._render_text(path.name, "\n".join(lines) or "(Empty STAR file)")
            self._set_preview_busy(False)
            return
        if suffix in MRC_SUFFIXES:
            self._render_mrc_previews_async(
                path.name,
                [(path.name, path, suffix == ".mrcs")],
            )
            return
        self._render_message(f"No lightweight preview available for {path.name}.")
        self._set_preview_busy(False)

    def _render_idle_preview_message(self) -> None:
        self._render_message("Select a previewable Class3D/Refine3D job or one previewable file, then click Preview.")

    def _render_message(self, message: str) -> None:
        self._render_text("Preview", message)

    def _render_text(self, title: str, text: str) -> None:
        self.preview_generation += 1
        self.preview_title_var.set(title)
        self._clear_preview()
        text_frame = ttk.Frame(self.preview_content)
        text_frame.grid(row=0, column=0, sticky="nsew")
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)
        widget = tk.Text(
            text_frame,
            wrap="none",
            height=8,
            font=self.app.ui_font("technical"),
        )
        widget.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(text_frame, orient="vertical", command=widget.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll = ttk.Scrollbar(text_frame, orient="horizontal", command=widget.xview)
        xscroll.grid(row=1, column=0, sticky="ew")
        widget.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        widget.insert("1.0", text)
        widget.configure(state="disabled")
        self._schedule_preview_canvas_sync()

    def _render_mrc_previews_async(
        self,
        title: str,
        items: list[tuple[str, Path, bool]],
        *,
        job_cache_key: str = "",
    ) -> None:
        self._set_preview_busy(True)
        self.preview_generation += 1
        generation = self.preview_generation
        self.current_job_preview_key = job_cache_key
        self.preview_title_var.set(title)
        self._render_loading(f"Rendering preview for {len(items)} MRC file(s)...")

        def worker() -> None:
            results: list[tuple[str, MrcPreview]] = []
            error = ""
            try:
                for caption, path, stack_2d in items:
                    kind = "stack2d" if stack_2d else "volume3d"
                    key = mrc_preview_cache_key(path, kind)
                    with self.preview_cache_lock:
                        preview = self.preview_cache.get(key)
                        if preview is not None:
                            self.preview_cache.move_to_end(key)
                    if preview is None:
                        preview = read_mrc_preview(path, stack_2d=stack_2d, max_size=PREVIEW_READ_SIZE)
                        with self.preview_cache_lock:
                            self.preview_cache[key] = preview
                            while len(self.preview_cache) > 48:
                                self.preview_cache.popitem(last=False)
                    results.append((caption, preview))
            except Exception as exc:
                error = str(exc)
            self.app.root.after(0, lambda: self._finish_mrc_preview(generation, title, results, error, job_cache_key))

        threading.Thread(target=worker, daemon=True).start()

    def _render_loading(self, message: str) -> None:
        self._clear_preview()
        ttk.Label(self.preview_content, text=message).grid(row=0, column=0, sticky="w")
        self._schedule_preview_canvas_sync()

    def _finish_mrc_preview(
        self,
        generation: int,
        title: str,
        results: list[tuple[str, MrcPreview]],
        error: str,
        job_cache_key: str = "",
    ) -> None:
        if generation != self.preview_generation:
            return
        if error:
            self._render_message(f"Could not render preview: {error}")
            self._set_preview_busy(False)
            return
        if job_cache_key:
            self._store_job_preview(job_cache_key, _CachedJobPreview(title=title, results=list(results)))
        self._display_mrc_preview(title, results)
        self._set_preview_busy(False)

    def _display_mrc_preview(self, title: str, results: list[tuple[str, MrcPreview]]) -> None:
        self.preview_title_var.set(title)
        self._clear_preview()
        block = ttk.Frame(self.preview_content)
        block.grid(row=0, column=0, sticky="nsew")
        block.columnconfigure(0, weight=1)
        grid = ttk.Frame(block)
        grid.grid(row=0, column=0, sticky="nw")
        columns = self._mrc_preview_columns(results)
        for row_index, (caption, preview) in enumerate(results):
            tile_row = row_index // columns
            tile_column = row_index % columns
            tile = ttk.Frame(grid, padding=(4, 4, 12, 10))
            tile.grid(row=tile_row, column=tile_column, sticky="nw")
            ttk.Label(tile, text=caption, anchor="w").grid(row=0, column=0, sticky="w")
            planes = ttk.Frame(tile)
            planes.grid(row=1, column=0, sticky="w", pady=(4, 0))
            for plane_index, plane in enumerate(preview.planes):
                panel = ttk.Frame(planes)
                panel.grid(row=0, column=plane_index, sticky="nw", padx=(0, 10))
                try:
                    image = self._photo_image_from_plane(preview, plane)
                except Exception as exc:
                    ttk.Label(panel, text=f"{plane.label}: {exc}").grid(row=0, column=0, sticky="nw")
                    continue
                self.preview_images.append(image)
                ttk.Label(panel, text=plane.label).grid(row=0, column=0, sticky="w")
                tk.Label(panel, image=image, bd=1, relief="solid").grid(row=1, column=0, sticky="nw")
        self.convergence_preview_container = ttk.Frame(block)
        self.convergence_preview_container.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self.convergence_preview_container.columnconfigure(0, weight=1)
        if not results:
            ttk.Label(self.preview_content, text="No preview files found.").grid(row=0, column=0, sticky="w")
        self._schedule_preview_canvas_sync()

    def _render_cached_job_preview(self, cache_key: str, cached: _CachedJobPreview) -> None:
        self.preview_generation += 1
        self.current_job_preview_key = cache_key
        self._display_mrc_preview(cached.title, cached.results)
        if cached.convergence_plot is not None or cached.convergence_error:
            self._display_classification_convergence(cached.convergence_plot, cached.convergence_error)

    def _store_job_preview(self, cache_key: str, preview: _CachedJobPreview) -> None:
        existing = self.job_preview_cache.get(cache_key)
        if existing is not None:
            preview.convergence_plot = existing.convergence_plot
            preview.convergence_error = existing.convergence_error
        self.job_preview_cache[cache_key] = preview
        self.job_preview_cache.move_to_end(cache_key)
        while len(self.job_preview_cache) > 64:
            self.job_preview_cache.popitem(last=False)

    def _job_preview_cache_key(self, project: RelionProjectDefinition, job: RelionPipelineJob) -> str:
        return f"{Path(project.root_directory).expanduser()}::{job.relative_path}"

    def _clear_preview_caches(self) -> None:
        self._cancel_after("_type_select_after_id")
        self._cancel_after("_job_select_after_id")
        self._cancel_after("_file_select_after_id")
        self.preview_generation += 1
        self.convergence_generation += 1
        self._job_file_generation += 1
        self._file_insert_generation += 1
        self._loading_job_files = False
        with self.preview_cache_lock:
            self.preview_cache.clear()
        self.job_preview_cache.clear()
        self.file_list_cache.clear()
        self.file_row_cache.clear()
        self.rendered_image_cache.clear()
        self.current_job_preview_key = ""

    def _mrc_preview_columns(self, results: list[tuple[str, MrcPreview]]) -> int:
        if not results:
            return 1
        available_width = self.preview_canvas.winfo_width()
        if available_width <= 1:
            available_width = self.frame.winfo_width()
        available_width = max(240, available_width - 36)
        max_planes = max((len(preview.planes) for _caption, preview in results), default=1)
        plane_size = PREVIEW_READ_SIZE * max(PREVIEW_ZOOM, 1)
        tile_width = max(180, plane_size * max_planes + 58 + max(0, max_planes - 1) * 10)
        return max(1, min(len(results), available_width // tile_width))

    def _photo_image_from_plane(self, preview: MrcPreview, plane: MrcPlanePreview) -> tk.PhotoImage:
        kind = "stack2d" if preview.path.suffix.casefold() == ".mrcs" and len(preview.planes) == 1 else "volume3d"
        try:
            base_key = mrc_preview_cache_key(preview.path, kind)
        except OSError:
            base_key = (str(preview.path), 0.0, 0, kind)
        image_key = (*base_key, plane.label, plane.width, plane.height, PREVIEW_ZOOM)
        cached = self.rendered_image_cache.get(image_key)
        if cached is not None:
            self.rendered_image_cache.move_to_end(image_key)
            return cached
        if Image is not None and ImageTk is not None:
            image = Image.frombytes("L", (plane.width, plane.height), bytes(plane.pixels))
            if PREVIEW_ZOOM > 1:
                resample = getattr(getattr(Image, "Resampling", Image), "NEAREST")
                image = image.resize((plane.width * PREVIEW_ZOOM, plane.height * PREVIEW_ZOOM), resample)
            rendered = ImageTk.PhotoImage(image)
            self.rendered_image_cache[image_key] = rendered
            while len(self.rendered_image_cache) > 96:
                self.rendered_image_cache.popitem(last=False)
            return rendered
        image = tk.PhotoImage(width=plane.width, height=plane.height)
        rows: list[str] = []
        for y_index in range(plane.height):
            start = y_index * plane.width
            row_pixels = plane.pixels[start : start + plane.width]
            rows.append("{" + " ".join(f"#{value:02x}{value:02x}{value:02x}" for value in row_pixels) + "}")
        image.put(" ".join(rows), to=(0, 0))
        if PREVIEW_ZOOM <= 1:
            rendered = image
        else:
            rendered = image.zoom(PREVIEW_ZOOM, PREVIEW_ZOOM)
            self.preview_images.append(image)
        self.rendered_image_cache[image_key] = rendered
        while len(self.rendered_image_cache) > 96:
            self.rendered_image_cache.popitem(last=False)
        return rendered

    def _clear_preview(self) -> None:
        self.preview_images = []
        self.convergence_preview_container = None
        self.convergence_generation += 1
        self.preview_content.columnconfigure(0, weight=1)
        self.preview_content.rowconfigure(0, weight=1)
        for child in self.preview_content.winfo_children():
            child.destroy()
        self._schedule_preview_canvas_sync()

    def _schedule_preview_canvas_sync(self) -> None:
        if self._preview_sync_pending:
            return
        self._preview_sync_pending = True
        try:
            self.preview_canvas.after_idle(self._sync_preview_canvas_window)
            if self._preview_sync_after_id is not None:
                self.preview_canvas.after_cancel(self._preview_sync_after_id)
            self._preview_sync_after_id = self.preview_canvas.after(80, self._sync_preview_canvas_window)
        except tk.TclError:
            self._preview_sync_pending = False

    def _sync_preview_canvas_window(self) -> None:
        self._preview_sync_pending = False
        self._preview_sync_after_id = None
        if not self.preview_canvas.winfo_exists():
            return
        try:
            self.preview_content.update_idletasks()
        except tk.TclError:
            return
        width = max(1, self.preview_canvas.winfo_width())
        height = max(1, self.preview_canvas.winfo_height(), self.preview_content.winfo_reqheight())
        try:
            self.preview_canvas.itemconfigure(self.preview_window, width=width, height=height)
            self.preview_canvas.update_idletasks()
            self.preview_canvas.configure(scrollregion=self.preview_canvas.bbox("all"))
        except tk.TclError:
            pass

    def _enable_preview_mousewheel(self, _event=None) -> None:
        self._preview_mousewheel_active = True
        if self._preview_mousewheel_global_bound:
            return
        self._preview_mousewheel_global_bound = True
        self.preview_canvas.bind_all("<MouseWheel>", self._on_preview_mousewheel, add="+")
        self.preview_canvas.bind_all("<Button-4>", self._on_preview_mousewheel, add="+")
        self.preview_canvas.bind_all("<Button-5>", self._on_preview_mousewheel, add="+")

    def _disable_preview_mousewheel(self, _event=None) -> None:
        self._preview_mousewheel_active = False

    def _on_preview_mousewheel(self, event) -> str | None:
        if not self._pointer_inside_preview_canvas():
            return None
        try:
            scrollregion = self.preview_canvas.cget("scrollregion")
            if not scrollregion:
                return None
            bbox = [float(value) for value in str(scrollregion).split()]
            if len(bbox) == 4 and bbox[3] <= self.preview_canvas.winfo_height():
                return None
            if getattr(event, "num", None) == 4:
                units = -3
            elif getattr(event, "num", None) == 5:
                units = 3
            else:
                delta = int(getattr(event, "delta", 0) or 0)
                units = -1 * (delta // 120) if abs(delta) >= 120 else (-1 if delta > 0 else 1)
            self.preview_canvas.yview_scroll(units, "units")
        except Exception:
            pass
        return "break"

    def _pointer_inside_preview_canvas(self) -> bool:
        try:
            pointer_x = self.preview_canvas.winfo_pointerx()
            pointer_y = self.preview_canvas.winfo_pointery()
            left = self.preview_canvas.winfo_rootx()
            top = self.preview_canvas.winfo_rooty()
            right = left + self.preview_canvas.winfo_width()
            bottom = top + self.preview_canvas.winfo_height()
            return left <= pointer_x <= right and top <= pointer_y <= bottom
        except tk.TclError:
            return False

    def _open_selected_files(self) -> None:
        paths = self._selected_paths()
        if not paths:
            return
        failures: list[str] = []
        for path in paths:
            try:
                self.app.open_external_file(str(path))
            except Exception as exc:
                failures.append(f"{path.name}: {exc}")
        if failures:
            messagebox.showerror("Open selected file(s)", "\n".join(failures))

    def _open_files_with_command(self, command: str, paths: list[Path]) -> None:
        if not paths:
            return
        try:
            subprocess.Popen([command, *[str(path) for path in paths]], start_new_session=True)
        except Exception as exc:
            messagebox.showerror(f"Open in {command}", str(exc))

    def _shell_quote(self, value: str) -> str:
        import shlex

        return shlex.quote(value)

    def _dataset_aliases(self) -> dict[str, list[str]]:
        aliases: dict[str, list[str]] = {}
        for dataset in self.app.project.datasets:
            values = [dataset.dataset_name]
            for ts_name in dataset_ts_names(dataset):
                if ts_name:
                    values.extend((ts_name, f"{ts_name}.tomostar"))
            for thumbnail in dataset.thumbnails:
                if thumbnail.ts_name:
                    values.extend((thumbnail.ts_name, f"{thumbnail.ts_name}.tomostar"))
            unique_values = sorted({value for value in values if value}, key=lambda value: (-len(value), value.casefold()))
            aliases[dataset.dataset_name] = unique_values
        return aliases

    def _plot_classification_convergence(self) -> None:
        project = self._selected_project()
        job = self.current_job
        if project is None or job is None or job.job_type.casefold() != "class3d":
            return
        container = self.convergence_preview_container
        if container is None:
            return
        directory = Path(project.root_directory) / job.relative_path
        for child in container.winfo_children():
            child.destroy()
        ttk.Label(container, text="Calculating classification convergence...").grid(row=0, column=0, sticky="w")
        self._schedule_preview_canvas_sync()
        self.convergence_generation += 1
        generation = self.convergence_generation
        preview_generation = self.preview_generation
        job_cache_key = self.current_job_preview_key
        dataset_aliases = self._dataset_aliases()

        def worker() -> None:
            plot: ParticleClassificationConvergencePlot | None = None
            error = ""
            try:
                plot = particle_classification_convergence_data(directory, dataset_aliases)
            except OperationAborted:
                error = "Classification convergence plotting aborted."
            except Exception as exc:
                error = f"Could not render classification convergence:\n{exc}"
            self.app.root.after(
                0,
                lambda: self._finish_classification_convergence(
                    generation,
                    preview_generation,
                    job_cache_key,
                    plot,
                    error,
                ),
            )

        threading.Thread(target=worker, daemon=True).start()

    def _finish_classification_convergence(
        self,
        generation: int,
        preview_generation: int,
        job_cache_key: str,
        plot: ParticleClassificationConvergencePlot | None,
        error: str,
    ) -> None:
        if generation != self.convergence_generation or preview_generation != self.preview_generation:
            return
        if job_cache_key:
            cached = self.job_preview_cache.get(job_cache_key)
            if cached is not None:
                cached.convergence_plot = plot
                cached.convergence_error = error
                self.job_preview_cache.move_to_end(job_cache_key)
        self._display_classification_convergence(plot, error)

    def _display_classification_convergence(
        self,
        plot: ParticleClassificationConvergencePlot | None,
        error: str,
    ) -> None:
        container = self.convergence_preview_container
        if container is None:
            return
        for child in container.winfo_children():
            child.destroy()
        if error or plot is None:
            ttk.Label(container, text=error or "No classification convergence data available.").grid(
                row=0,
                column=0,
                sticky="w",
            )
            self._schedule_preview_canvas_sync()
            return
        summary = (
            f"Classification convergence | mode: {plot.mode} | pixel size: {plot.pixel_size:.4f} A | "
            f"iterations: {len(plot.iterations)}"
        )
        self._draw_convergence_plots(
            container,
            plot,
            summary=summary,
            rescale_to_window=True,
            available_width=max(self.preview_canvas.winfo_width() - 48, 320),
        )
        self._schedule_preview_canvas_sync()

    def _draw_convergence_plots(
        self,
        parent: ttk.Frame,
        plot: ParticleClassificationConvergencePlot,
        *,
        summary: str,
        rescale_to_window: bool,
        available_width: int,
    ) -> None:
        block = ttk.Frame(parent)
        block.grid(row=0, column=0, sticky="ew")
        block.columnconfigure(0, weight=1)
        ttk.Label(block, text=summary, style="Heading.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))
        iterations = [item.iteration for item in plot.iterations]
        occupancy_series = [
            (class_label, [item.class_counts.get(class_label, 0) for item in plot.iterations])
            for class_label in plot.class_labels
        ]
        convergence_series = [("Changed assignments", [item.changed_count for item in plot.iterations])]
        summary_text = f"Particles={plot.particle_count} | N={plot.dataset_count} | TS={plot.tomogram_count}"

        occupancy_box = ttk.Frame(block)
        occupancy_box.grid(row=1, column=0, sticky="ew", pady=(0, 12))
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

        convergence_box = ttk.Frame(block)
        convergence_box.grid(row=2, column=0, sticky="ew")
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

    def _draw_line_plot(
        self,
        parent: ttk.Frame,
        *,
        title_text: str,
        x_values: list[int],
        series: list[tuple[str, list[int]]],
        summary_text: str,
        show_legend: bool,
        rescale_to_window: bool,
        available_width: int,
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
        canvas_width = max(560, available_width) if rescale_to_window else default_width
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


class RelionProjectDialog:
    def __init__(
        self,
        parent: tk.Misc,
        *,
        existing_names: list[str],
        default_project: RelionProjectDefinition | None = None,
    ) -> None:
        self.parent = parent
        self.existing_names = {name.casefold() for name in existing_names}
        default_project = default_project or RelionProjectDefinition(name="")
        self.result: RelionProjectDefinition | None = None
        self.window = tk.Toplevel(parent)
        self.window.title("Add Relion project")
        self.window.geometry("640x430")
        self.window.minsize(520, 360)
        self.window.transient(parent.winfo_toplevel())
        self.window.grab_set()
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)

        body = ttk.Frame(self.window, padding=16)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(5, weight=1)

        ttk.Label(body, text="Relion Project name").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.name_var = tk.StringVar(value=default_project.name)
        ttk.Entry(body, textvariable=self.name_var).grid(row=1, column=0, sticky="ew", pady=(0, 10))

        ttk.Label(body, text="Relion root directory").grid(row=2, column=0, sticky="w", pady=(0, 4))
        root_row = ttk.Frame(body)
        root_row.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        root_row.columnconfigure(0, weight=1)
        self.root_var = tk.StringVar(value=default_project.root_directory)
        ttk.Entry(root_row, textvariable=self.root_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(root_row, text="Browse...", command=self._browse_root).grid(row=0, column=1, padx=(8, 0))

        ttk.Label(body, text="Startup command").grid(row=4, column=0, sticky="w", pady=(0, 4))
        command_frame = ttk.Frame(body)
        command_frame.grid(row=5, column=0, sticky="nsew")
        command_frame.columnconfigure(0, weight=1)
        command_frame.rowconfigure(0, weight=1)
        self.command_text = tk.Text(command_frame, height=8, wrap="word", font=("TkFixedFont", 10))
        self.command_text.grid(row=0, column=0, sticky="nsew")
        command_scroll = ttk.Scrollbar(command_frame, orient="vertical", command=self.command_text.yview)
        command_scroll.grid(row=0, column=1, sticky="ns")
        self.command_text.configure(yscrollcommand=command_scroll.set)
        if default_project.startup_command:
            self.command_text.insert("1.0", default_project.startup_command)

        buttons = ttk.Frame(body)
        buttons.grid(row=6, column=0, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="Cancel", command=self._cancel).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(buttons, text="Add", command=self._add).grid(row=0, column=1)
        self.window.protocol("WM_DELETE_WINDOW", self._cancel)

    def show(self) -> RelionProjectDefinition | None:
        self.window.wait_window()
        return self.result

    def _browse_root(self) -> None:
        path = filedialog.askdirectory(title="Select Relion root directory", parent=self.window)
        if path:
            self.root_var.set(path)

    def _add(self) -> None:
        name = self.name_var.get().strip()
        root = self.root_var.get().strip()
        startup = self.command_text.get("1.0", "end").strip()
        if not name:
            messagebox.showerror("Add Relion project", "Please enter a Relion project name.", parent=self.window)
            return
        if name.casefold() in self.existing_names:
            messagebox.showerror("Add Relion project", "A Relion project with this name already exists.", parent=self.window)
            return
        if not root:
            messagebox.showerror("Add Relion project", "Please select a Relion root directory.", parent=self.window)
            return
        if not Path(root).expanduser().is_dir():
            messagebox.showerror("Add Relion project", "Relion root directory does not exist.", parent=self.window)
            return
        self.result = RelionProjectDefinition(name=name, root_directory=root, startup_command=startup)
        self.window.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.window.destroy()
