from __future__ import annotations

import csv
import os
import signal
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from copy import deepcopy
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Iterable

from cryoet_organizer import __version__
from cryoet_organizer.appearance import AppearanceConfig, get_project_appearance
from cryoet_organizer.appearance_dialog import AppearanceDialog
from cryoet_organizer.check_paths import PathCheckEntry, collect_project_path_report
from cryoet_organizer.check_paths_dialog import CheckPathsDialog
from cryoet_organizer.debug_mode import DebugLogWindow, DebugSessionState, SimulatedProcess, timestamped_debug_line
from cryoet_organizer.dialogs import choose_grouped_items_dialog
from cryoet_organizer.environments import environment_titles, resolve_environment_activation
from cryoet_organizer.environments_dialog import EnvironmentsDialog
from cryoet_organizer.export_paths_dialog import ExportFilePathsDialog
from cryoet_organizer.job_execution import is_scheduled_history_entry
from cryoet_organizer.log_window import ProcessLogWindow, ShortcutLaunchWindow
from cryoet_organizer.project import (
    JobHistoryEntry,
    PROJECT_SUFFIX,
    SETTINGS_SUFFIX,
    ProjectData,
    load_project,
    save_project,
)
from cryoet_organizer.preferences_dialog import PreferencesDialog
from cryoet_organizer.recent_projects import add_recent_project, load_recent_projects
from cryoet_organizer.custom_jobs_dialog import CustomJobsDialog
from cryoet_organizer.shortcuts_dialog import ManageShortcutsDialog
from cryoet_organizer.settings_bundle import (
    apply_settings_import,
    conflicting_import_items,
    export_settings_bundle,
    exportable_settings_groups,
    importable_settings_groups,
    load_settings_bundle,
    settings_selection_label_map,
)
from cryoet_organizer.settings_dialog import DefaultParametersDialog
from cryoet_organizer.settings_shell import SettingsShellWindow
from cryoet_organizer.scheduled_slurm_dialog import ask_running_scheduled_jobs_mode
from cryoet_organizer.slurm import (
    SlurmSubmissionResult,
    find_slurm_profile,
    render_sbatch_script,
    slurm_profile_names,
    submit_sbatch_script,
    write_sbatch_script,
)
from cryoet_organizer.slurm_dialog import SlurmProfilesDialog
from cryoet_organizer.tabs import SidebarTab, get_tab_classes
from cryoet_organizer.ts_metadata import clear_ts_metadata_cache, collect_ts_metadata
from cryoet_organizer.viewer_defaults import resolve_viewer_command
from cryoet_organizer.viewer_defaults_dialog import ViewerDefaultsDialog


class _LogoSplash:
    def __init__(
        self,
        root: tk.Tk,
        *,
        logo_path: Path | None,
        title: str,
        subtitle: str = "",
        background: str = "#f6f8fb",
    ) -> None:
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(background=background)
        try:
            self.window.attributes("-topmost", True)
        except tk.TclError:
            pass

        self.image: tk.PhotoImage | None = None
        self.title_var = tk.StringVar(value=title)
        self.subtitle_var = tk.StringVar(value=subtitle)

        outer = tk.Frame(
            self.window,
            background=background,
            padx=28,
            pady=24,
            highlightthickness=1,
            highlightbackground="#d8dee8",
        )
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)

        if logo_path is not None and logo_path.exists():
            try:
                image = tk.PhotoImage(file=str(logo_path))
            except tk.TclError:
                image = None
            if image is not None:
                target_width = 420
                if image.width() > target_width:
                    factor = max(1, round(image.width() / target_width))
                    image = image.subsample(factor, factor)
                self.image = image
                tk.Label(
                    outer,
                    image=self.image,
                    bd=0,
                    highlightthickness=0,
                    background=background,
                ).grid(row=0, column=0, sticky="n", pady=(0, 16))

        tk.Label(
            outer,
            textvariable=self.title_var,
            font=("TkDefaultFont", 14, "bold"),
            background=background,
            foreground="#1f2933",
        ).grid(row=1, column=0, sticky="ew")

        if subtitle.strip():
            tk.Label(
                outer,
                textvariable=self.subtitle_var,
                font=("TkDefaultFont", 10),
                background=background,
                foreground="#52606d",
            ).grid(row=2, column=0, sticky="ew", pady=(8, 0))

        self.window.update_idletasks()
        self._center_on_screen()
        self.window.deiconify()
        self.window.lift()
        self.window.update_idletasks()

    def _center_on_screen(self) -> None:
        width = self.window.winfo_reqwidth()
        height = self.window.winfo_reqheight()
        screen_width = self.window.winfo_screenwidth()
        screen_height = self.window.winfo_screenheight()
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)
        self.window.geometry(f"{width}x{height}+{x}+{y}")

    def close(self) -> None:
        try:
            self.window.destroy()
        except tk.TclError:
            pass


class _BusyDialog:
    def __init__(self, parent: tk.Misc, title: str, message: str) -> None:
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
            text=message,
            wraplength=380,
            justify="left",
        ).grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(body, orient="horizontal", mode="indeterminate", length=320)
        self.progress.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self.progress.start(10)

        self.window.update_idletasks()
        self.window.grab_set()
        self.window.focus_set()

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


class CryoETOrganizerApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("CryoPal_tomo")
        self.root.geometry("1280x820")
        self.root.minsize(1120, 700)
        self.logo_image: tk.PhotoImage | None = None
        self.logo_label: ttk.Label | None = None
        self._lifecycle_splash: _LogoSplash | None = None

        self.project = ProjectData()
        self.project_path: Path | None = None
        self.tabs: dict[str, SidebarTab] = {}
        self.nav_buttons: dict[str, ttk.Button] = {}
        self._refresh_domain_map: dict[str, tuple[str, ...]] = {}
        self.active_tab_id: str | None = None
        self.status_var = tk.StringVar(value="Ready")
        self.abort_buttons: list[ttk.Button] = []
        self._managed_processes: dict[int, subprocess.Popen] = {}
        self._managed_process_lock = threading.Lock()
        self._abort_requested = False
        self._modified = False
        self._recent_menu: tk.Menu | None = None
        self._file_menu: tk.Menu | None = None
        self._settings_menu: tk.Menu | None = None
        self._style = ttk.Style()
        self._settings_shell: SettingsShellWindow | None = None
        self._current_appearance = AppearanceConfig()
        self.version_label: tk.Label | None = None
        self.debug_mode = DebugSessionState()
        self._debug_snapshot: ProjectData | None = None
        self._debug_snapshot_path: Path | None = None
        self._debug_snapshot_modified = False
        self._debug_log_window: DebugLogWindow | None = None
        self.debug_banner_var = tk.StringVar(value="")
        self._scheduled_batch_queues: dict[str, list[dict[str, object]]] = {}
        self._scheduled_batch_running: set[str] = set()
        self._scheduled_batch_polling: set[str] = set()
        self._running_history_entry_ids: set[str] = set()
        self._waiting_history_entry_ids: set[str] = set()
        self._pending_tab_refreshes: set[str] = set()
        self._queued_refresh_targets: set[str] = set()
        self._queued_refresh_after_id: str | None = None

        self._show_logo_splash(
            "Waking up CryoPal",
            "Preparing the workspace and loading the interface.",
        )
        self._configure_style()
        self._build_menu()
        self._build_layout()
        self._load_tabs()
        self._apply_project_to_tabs()
        self._show_tab("project_overview")
        self._update_title()
        self.root.protocol("WM_DELETE_WINDOW", self.close_app)
        self._finish_startup()

    def _configure_style(self) -> None:
        try:
            self._style.theme_use("clam")
        except tk.TclError:
            pass
        self._style.configure("Heading.TLabel", font=("TkDefaultFont", 14, "bold"))
        self._style.configure("Error.TLabel", foreground="#aa1f1f")
        self.apply_appearance_config(self._current_appearance)

    def _build_menu(self) -> None:
        menu_bar = tk.Menu(self.root)
        file_menu = tk.Menu(menu_bar, tearoff=False)
        file_menu.add_command(label="New Project", command=self.new_project)
        file_menu.add_command(label="Open Project...", command=self.open_project_dialog)
        self._recent_menu = tk.Menu(file_menu, tearoff=False)
        file_menu.add_cascade(label="Open Recent", menu=self._recent_menu)
        file_menu.add_separator()
        file_menu.add_command(label="Save", command=self.save_project)
        file_menu.add_command(label="Save As...", command=self.save_project_as)
        file_menu.add_separator()
        file_menu.add_command(label="Export job history...", command=self.export_job_history)
        file_menu.add_command(label="Export file paths...", command=self.export_file_paths)
        file_menu.add_command(label="Export TS annotations...", command=self.export_ts_annotations)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.close_app)
        file_menu.configure(postcommand=self._update_recent_menu)
        menu_bar.add_cascade(label="File", menu=file_menu)
        self._file_menu = file_menu
        settings_menu = tk.Menu(menu_bar, tearoff=False)
        settings_menu.add_command(label="Set preferences", command=self.open_preferences_dialog)
        settings_menu.add_command(label="Configure viewer defaults", command=self.open_viewer_defaults_dialog)
        settings_menu.add_command(label="Set default parameters", command=self.open_default_parameters_dialog)
        settings_menu.add_command(label="Slurm submission", command=self.open_slurm_profiles_dialog)
        settings_menu.add_command(label="Manage environments", command=self.open_environments_dialog)
        settings_menu.add_command(label="Manage custom job types", command=self.open_custom_jobs_dialog)
        settings_menu.add_command(label="Manage shortcuts", command=self.open_shortcuts_dialog)
        settings_menu.add_separator()
        settings_menu.add_command(label="Export .cryopal.settings-file", command=self.export_settings_bundle_dialog)
        settings_menu.add_command(label="Import .cryopal.settings-file", command=self.import_settings_bundle_dialog)
        settings_menu.add_separator()
        settings_menu.add_command(label="Appearance", command=self.open_appearance_dialog)
        settings_menu.add_command(label="Check paths", command=self.open_check_paths_dialog)
        settings_menu.add_command(label="Debug mode", command=self.toggle_debug_mode)
        menu_bar.add_cascade(label="Settings", menu=settings_menu)
        self._settings_menu = settings_menu
        self.root.config(menu=menu_bar)

    def _build_layout(self) -> None:
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)

        self.sidebar = ttk.Frame(self.root, style="Sidebar.TFrame", padding=12)
        self.sidebar.grid(row=0, column=0, sticky="nsw")
        self.sidebar.columnconfigure(0, weight=1)
        self.sidebar.rowconfigure(998, weight=1)
        self._build_sidebar_logo()

        self.content = ttk.Frame(self.root, padding=0)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(1, weight=1)

        self.debug_banner = ttk.Label(
            self.content,
            textvariable=self.debug_banner_var,
            style="DebugBanner.TLabel",
            anchor="w",
            padding=(12, 8),
        )
        self.debug_banner.grid(row=0, column=0, sticky="ew")
        self.debug_banner.grid_remove()

        status_frame = ttk.Frame(self.root, padding=0)
        status_frame.grid(row=1, column=0, columnspan=2, sticky="ew")
        status_frame.columnconfigure(0, weight=1)
        status = ttk.Label(
            status_frame,
            textvariable=self.status_var,
            relief="sunken",
            anchor="w",
            padding=(10, 6),
        )
        status.grid(row=0, column=0, sticky="ew")

    def _build_sidebar_logo(self) -> None:
        logo_path = self._logo_asset_path()
        if not logo_path.exists():
            return

        try:
            image = tk.PhotoImage(file=str(logo_path))
        except tk.TclError:
            return

        # Keep the logo compact enough for the sidebar while preserving visibility.
        target_width = 220
        if image.width() > target_width:
            factor = max(1, round(image.width() / target_width))
            image = image.subsample(factor, factor)

        self.logo_image = image
        self.logo_label = tk.Label(
            self.sidebar,
            image=self.logo_image,
            bd=0,
            highlightthickness=0,
            background=self._current_appearance.sidebar_background,
        )
        self.logo_label.grid(row=0, column=0, sticky="w", pady=(0, 12))

    def _logo_asset_path(self) -> Path:
        return Path(__file__).resolve().parent / "assets" / "CryoPal_tomo_logo.png"

    def _splash_logo_asset_path(self) -> Path:
        preferred = Path(__file__).resolve().parent / "assets" / "CryoPal_sleeping_logo.png"
        if preferred.exists():
            return preferred
        return self._logo_asset_path()

    def _show_logo_splash(self, title: str, subtitle: str = "") -> _LogoSplash | None:
        self._close_logo_splash()
        try:
            self._lifecycle_splash = _LogoSplash(
                self.root,
                logo_path=self._splash_logo_asset_path(),
                title=title,
                subtitle=subtitle,
                background=self._current_appearance.main_background,
            )
        except tk.TclError:
            self._lifecycle_splash = None
        return self._lifecycle_splash

    def _close_logo_splash(self) -> None:
        if self._lifecycle_splash is not None:
            self._lifecycle_splash.close()
            self._lifecycle_splash = None

    def _finish_startup(self) -> None:
        self.root.update_idletasks()
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.focus_force()
        except tk.TclError:
            pass
        self.root.update_idletasks()
        self._close_logo_splash()

    def _load_tabs(self) -> None:
        start_row = 1 if self.logo_label is not None else 0
        current_row = start_row
        separator_before = {"processing", "shortcuts", "tomograms"}
        bottom_tab_cls = None
        for tab_cls in get_tab_classes():
            if tab_cls.tab_id == "file_registry":
                bottom_tab_cls = tab_cls
                continue
            if tab_cls.tab_id in separator_before:
                ttk.Separator(self.sidebar, orient="horizontal").grid(
                    row=current_row,
                    column=0,
                    sticky="ew",
                    pady=(16, 10),
                )
                current_row += 1
            tab = tab_cls(self, self.content)
            self.tabs[tab.tab_id] = tab
            tab.frame.grid(row=1, column=0, sticky="nsew")
            tab.frame.grid_remove()

            button = ttk.Button(
                self.sidebar,
                text=tab.title,
                style="Sidebar.TButton",
                command=lambda current=tab.tab_id: self._show_tab(current),
            )
            button.grid(row=current_row, column=0, sticky="ew", pady=4)
            self.nav_buttons[tab.tab_id] = button
            current_row += 1

        if bottom_tab_cls is not None:
            tab = bottom_tab_cls(self, self.content)
            self.tabs[tab.tab_id] = tab
            tab.frame.grid(row=1, column=0, sticky="nsew")
            tab.frame.grid_remove()
            button = ttk.Button(
                self.sidebar,
                text=tab.title,
                style="Sidebar.TButton",
                command=lambda current=tab.tab_id: self._show_tab(current),
            )
            button.grid(row=999, column=0, sticky="ew", pady=4)
            self.nav_buttons[tab.tab_id] = button

        self.version_label = tk.Label(
            self.sidebar,
            text=f"v{__version__} | {date.today().year}",
            background=self._current_appearance.sidebar_background,
            foreground=self._current_appearance.sidebar_button_foreground,
            anchor="w",
        )
        self.version_label.grid(row=1000, column=0, sticky="sw", pady=(16, 0))
        self._build_refresh_domain_map()

    def _show_tab(self, tab_id: str) -> None:
        if self.active_tab_id:
            self.tabs[self.active_tab_id].frame.grid_remove()
            self.nav_buttons[self.active_tab_id].configure(style="Sidebar.TButton")

        self.tabs[tab_id].frame.grid()
        self.nav_buttons[tab_id].configure(style="ActiveSidebar.TButton")
        self.active_tab_id = tab_id
        self._refresh_tab_if_pending(tab_id)
        try:
            self.tabs[tab_id].on_tab_shown()
        except Exception:
            pass
        self.status_var.set(f"Active tab: {self.tabs[tab_id].title}")

    def _build_refresh_domain_map(self) -> None:
        mapping: dict[str, list[str]] = {}
        for tab_id, tab in self.tabs.items():
            domains = set(getattr(tab, "refresh_domains", ()))
            domains.add(tab_id)
            for domain in domains:
                mapping.setdefault(domain, [])
                if tab_id not in mapping[domain]:
                    mapping[domain].append(tab_id)
        self._refresh_domain_map = {
            domain: tuple(tab_ids)
            for domain, tab_ids in mapping.items()
        }

    def _resolve_refresh_targets(self, targets: tuple[str, ...] | None) -> tuple[str, ...]:
        if not targets:
            return tuple(self.tabs.keys())
        selected: set[str] = set()
        for target in targets:
            if target in self.tabs:
                selected.add(target)
                continue
            selected.update(self._refresh_domain_map.get(target, ()))
        return tuple(tab_id for tab_id in self.tabs if tab_id in selected)

    def _apply_project_to_tabs(self, targets: tuple[str, ...] | None = None) -> None:
        self.apply_appearance_config(get_project_appearance(self.project))
        selected_ids = set(self._resolve_refresh_targets(targets))
        self._pending_tab_refreshes.difference_update(selected_ids)
        for tab_id, tab in self.tabs.items():
            if tab_id in selected_ids:
                tab.on_project_loaded(self.project)

    def _refresh_tab_if_pending(self, tab_id: str) -> None:
        if tab_id not in self._pending_tab_refreshes:
            return
        self._pending_tab_refreshes.discard(tab_id)
        self.tabs[tab_id].on_project_loaded(self.project)

    def _cancel_queued_project_refresh(self) -> None:
        if self._queued_refresh_after_id is not None:
            try:
                self.root.after_cancel(self._queued_refresh_after_id)
            except tk.TclError:
                pass
        self._queued_refresh_after_id = None
        self._queued_refresh_targets.clear()

    def _flush_queued_project_refresh(self) -> None:
        self._queued_refresh_after_id = None
        selected_ids = set(self._queued_refresh_targets)
        self._queued_refresh_targets.clear()
        if not selected_ids:
            return
        self.apply_appearance_config(get_project_appearance(self.project))
        active_id = self.active_tab_id
        visible_targets: set[str] = set()
        if active_id and active_id in selected_ids:
            visible_targets.add(active_id)
        self._pending_tab_refreshes.update(selected_ids - visible_targets)
        for tab_id in visible_targets:
            self.tabs[tab_id].on_project_loaded(self.project)

    def _queue_project_refresh(self, targets: tuple[str, ...] | None = None) -> None:
        self.apply_appearance_config(get_project_appearance(self.project))
        self._queued_refresh_targets.update(self._resolve_refresh_targets(targets))
        if self._queued_refresh_after_id is None:
            self._queued_refresh_after_id = self.root.after_idle(self._flush_queued_project_refresh)

    def _preload_tab_views(self) -> None:
        previous_active = self.active_tab_id
        for tab_id, tab in self.tabs.items():
            tab.frame.grid()
            self.content.update_idletasks()
            self.root.update_idletasks()
            preload = getattr(tab, "preload_view", None)
            if callable(preload):
                try:
                    preload()
                except Exception:
                    pass
            tab.frame.grid_remove()
        if previous_active and previous_active in self.tabs:
            self.tabs[previous_active].frame.grid()
            self.content.update_idletasks()

    def _finish_loaded_project(self, *, status_message: str) -> None:
        self._modified = False
        clear_ts_metadata_cache()
        self._cancel_queued_project_refresh()
        self._pending_tab_refreshes.clear()
        busy = _BusyDialog(self.root, "Loading project", "Preparing project views. Please wait.")
        try:
            self._apply_project_to_tabs()
            self._preload_tab_views()
            self._update_title()
            self.status_var.set(status_message)
        finally:
            busy.close()

    def refresh_tabs(self, *domains: str) -> None:
        self._apply_project_to_tabs(tuple(domains) if domains else None)

    def _sync_tabs_to_project(self) -> None:
        for tab in self.tabs.values():
            tab.sync_to_project(self.project)

    def _update_title(self) -> None:
        suffix = self.project_path.name if self.project_path else "unsaved project"
        debug_suffix = " | DEBUG MODE" if self.debug_mode.enabled else ""
        self.root.title(f"CryoPal_tomo | {self.project.name} | {suffix}{debug_suffix}")

    def on_project_changed(self, *domains: str, status_message: str = "Project updated in memory") -> None:
        """Call after project data has already been modified in-memory by a tab.

        Only re-applies the current project state to all tabs and marks the project
        as modified. Does NOT flush GUI state to the project — that is handled by
        _sync_tabs_to_project(), which is called automatically before saving.
        """
        self._modified = True
        if not domains or {"datasets", "file_registry", "ts_metadata"} & set(domains):
            clear_ts_metadata_cache()
        self._queue_project_refresh(tuple(domains) if domains else None)
        self._update_title()
        self.status_var.set(status_message)
        if self.debug_mode.enabled:
            self.debug_log("STATE", f"Project changed in memory for domains: {', '.join(domains) if domains else 'all'}")

    def _confirm_discard_changes(self) -> bool:
        if not self._modified:
            return True
        return messagebox.askyesno(
            "Unsaved changes",
            "The current project has unsaved changes.\nDiscard and continue?",
            icon="warning",
        )

    def new_project(self) -> None:
        if not self._prepare_for_context_change("start a new project"):
            return
        if not self._confirm_discard_changes():
            return
        self.project = ProjectData()
        self.project_path = None
        self._modified = False
        clear_ts_metadata_cache()
        self._cancel_queued_project_refresh()
        self._pending_tab_refreshes.clear()
        self._apply_project_to_tabs()
        self._update_title()
        self.status_var.set("Started a new project")

    def open_project_dialog(self) -> None:
        if not self._prepare_for_context_change("open another project"):
            return
        if not self._confirm_discard_changes():
            return
        path = filedialog.askopenfilename(
            title="Open project",
            filetypes=[("CryoPal_tomo project", f"*{PROJECT_SUFFIX}"), ("JSON", "*.json")],
        )
        if not path:
            return

        try:
            self.project = load_project(path)
        except Exception as exc:  # pragma: no cover - UI feedback path
            messagebox.showerror("Open project failed", str(exc))
            return

        self.project_path = Path(path)
        add_recent_project(path)
        self._finish_loaded_project(status_message=f"Loaded project: {self.project_path.name}")

    def save_project(self) -> None:
        if self.debug_mode.enabled:
            messagebox.showinfo(
                "Debug mode active",
                "Saving is disabled while Debug mode is active.\n\nExit Debug mode first if you want to save the project.",
            )
            return
        self._sync_tabs_to_project()

        if self.project_path is None:
            self.save_project_as()
            return

        try:
            self.project_path = save_project(self.project_path, self.project)
        except Exception as exc:  # pragma: no cover - UI feedback path
            messagebox.showerror("Save failed", str(exc))
            return

        self._modified = False
        add_recent_project(self.project_path)
        self._update_title()
        self.status_var.set(f"Saved project: {self.project_path.name}")

    def save_project_as(self) -> None:
        if self.debug_mode.enabled:
            messagebox.showinfo(
                "Debug mode active",
                "Saving is disabled while Debug mode is active.\n\nExit Debug mode first if you want to save the project.",
            )
            return
        self._sync_tabs_to_project()

        path = filedialog.asksaveasfilename(
            title="Save project as",
            defaultextension=PROJECT_SUFFIX,
            filetypes=[("CryoPal_tomo project", f"*{PROJECT_SUFFIX}"), ("JSON", "*.json")],
        )
        if not path:
            return

        try:
            self.project_path = save_project(path, self.project)
        except Exception as exc:  # pragma: no cover - UI feedback path
            messagebox.showerror("Save failed", str(exc))
            return

        self._modified = False
        add_recent_project(self.project_path)
        self._update_title()
        self.status_var.set(f"Saved project: {self.project_path.name}")

    def _update_recent_menu(self) -> None:
        if self._recent_menu is None:
            return
        self._recent_menu.delete(0, "end")
        recent = load_recent_projects()
        if not recent:
            self._recent_menu.add_command(label="(no recent projects)", state="disabled")
        else:
            for path_str in recent:
                self._recent_menu.add_command(
                    label=Path(path_str).name,
                    command=lambda p=path_str: self._open_recent_project(p),
                )

    def _open_recent_project(self, path_str: str) -> None:
        if not self._prepare_for_context_change("open another project"):
            return
        if not self._confirm_discard_changes():
            return
        try:
            self.project = load_project(path_str)
        except Exception as exc:
            messagebox.showerror("Open project failed", str(exc))
            return
        self.project_path = Path(path_str)
        add_recent_project(path_str)
        self._finish_loaded_project(status_message=f"Loaded project: {self.project_path.name}")

    def export_job_history(self) -> None:
        self._sync_tabs_to_project()
        has_history = any(dataset.job_history for dataset in self.project.datasets)
        if not has_history:
            messagebox.showinfo(
                "Export job history",
                "No job history entries found in this project.",
            )
            return
        path = filedialog.asksaveasfilename(
            title="Export job history",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("HTML", "*.html"), ("All files", "*")],
        )
        if not path:
            return
        try:
            if path.lower().endswith(".html"):
                _export_history_html(path, self.project)
            else:
                _export_history_csv(path, self.project)
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))
            return
        self.status_var.set(f"Job history exported: {Path(path).name}")

    def export_file_paths(self) -> None:
        self._sync_tabs_to_project()
        report = collect_project_path_report(self.project)
        found_entries = [
            entry
            for entry in report.entries
            if entry.status == "found" and entry.path.strip()
        ]
        if not found_entries:
            messagebox.showinfo(
                "Export file paths",
                "No existing file paths were found in this project.",
            )
            return

        dialog = ExportFilePathsDialog(self.root, found_entries)
        selected_entries = dialog.show()
        if selected_entries is None:
            return
        if not selected_entries:
            messagebox.showinfo(
                "Export file paths",
                "Please select at least one dataset or file-path category to export.",
            )
            return

        path = filedialog.asksaveasfilename(
            title="Export file paths",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*")],
        )
        if not path:
            return
        try:
            _export_file_paths_csv(path, selected_entries)
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))
            return
        self.status_var.set(f"File paths exported: {Path(path).name}")

    def export_ts_annotations(self) -> None:
        self._sync_tabs_to_project()
        has_annotations = any(dataset.thumbnails for dataset in self.project.datasets)
        if not has_annotations:
            messagebox.showinfo(
                "Export TS annotations",
                "No TS annotations were found in this project.",
            )
            return

        path = filedialog.asksaveasfilename(
            title="Export TS annotations",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*")],
        )
        if not path:
            return
        try:
            _export_ts_annotations_csv(path, self.project)
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))
            return
        self.status_var.set(f"TS annotations exported: {Path(path).name}")

    def run_managed_process_with_log(
        self,
        command: str,
        cwd: str | None = None,
        title: str = "Process output",
        on_finished: Callable[[int], None] | None = None,
        kill_process_on_close: bool = False,
        activation_command: str = "",
    ) -> subprocess.Popen:
        """Start *command* and stream its output to a floating log window."""
        prepared_command = self._local_command_with_environment(command, activation_command)
        if self.debug_mode.enabled:
            self.open_debug_log_window()
            process = self.start_managed_process(command, cwd=cwd, activation_command=activation_command)
            threading.Thread(
                target=lambda: self._wait_process_and_callback(process, on_finished),
                daemon=True,
            ).start()
            self.debug_log("INFO", f"Would stream command output in '{title}'")
            return process  # type: ignore[return-value]

        process = subprocess.Popen(
            prepared_command,
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            text=True,
            bufsize=1,
        )
        with self._managed_process_lock:
            self._managed_processes[process.pid] = process
            self._abort_requested = False
        self.root.after(0, self._refresh_abort_button)

        log_win = ProcessLogWindow(self.root, title=title)
        log_win.attach_process(process, terminate_process_on_close=kill_process_on_close)

        threading.Thread(
            target=lambda: self._wait_process_and_callback(process, on_finished),
            daemon=True,
        ).start()
        return process

    def start_managed_process_with_log(
        self,
        command: str,
        cwd: str | None = None,
        title: str = "Process output",
        activation_command: str = "",
    ) -> subprocess.Popen:
        prepared_command = self._local_command_with_environment(command, activation_command)
        if self.debug_mode.enabled:
            self.open_debug_log_window()
            process = self.start_managed_process(command, cwd=cwd, activation_command=activation_command)
            self.debug_log("INFO", f"Would stream command output in '{title}'")
            return process  # type: ignore[return-value]

        process = subprocess.Popen(
            prepared_command,
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            text=True,
            bufsize=1,
        )
        with self._managed_process_lock:
            self._managed_processes[process.pid] = process
            self._abort_requested = False
        self.root.after(0, self._refresh_abort_button)

        log_win = ProcessLogWindow(self.root, title=title)
        log_win.attach_process(process)
        return process

    def _wait_process_and_callback(
        self,
        process: subprocess.Popen,
        on_finished: Callable[[int], None] | None = None,
    ) -> None:
        return_code = self.wait_managed_process(process)
        if on_finished is not None:
            self.root.after(0, lambda: on_finished(return_code))

    def run(self) -> None:
        self.root.mainloop()

    def open_default_parameters_dialog(self) -> None:
        self.settings_shell().open_section("default_parameters")

    def open_preferences_dialog(self) -> None:
        self.settings_shell().open_section("preferences")

    def open_appearance_dialog(self) -> None:
        self.settings_shell().open_section("appearance")

    def open_check_paths_dialog(self) -> None:
        CheckPathsDialog(self)

    def open_viewer_defaults_dialog(self) -> None:
        self.settings_shell().open_section("viewer_defaults")

    def history_entry_state_tag(self, entry: JobHistoryEntry) -> str:
        if entry.entry_id in self._running_history_entry_ids:
            return "running"
        if entry.entry_id in self._waiting_history_entry_ids:
            return "waiting"
        if entry.action == "ran":
            return "completed"
        if is_scheduled_history_entry(entry):
            return "scheduled"
        return "completed"

    def mark_history_entries_running(self, entry_ids: Iterable[str]) -> None:
        self._running_history_entry_ids.update(str(entry_id) for entry_id in entry_ids if entry_id)
        self._refresh_history_views()

    def clear_history_entries_running(self, entry_ids: Iterable[str]) -> None:
        for entry_id in entry_ids:
            self._running_history_entry_ids.discard(str(entry_id))
        self._refresh_history_views()

    def mark_history_entries_waiting(self, entry_ids: Iterable[str]) -> None:
        self._waiting_history_entry_ids.update(str(entry_id) for entry_id in entry_ids if entry_id)
        self._refresh_history_views()

    def clear_history_entries_waiting(self, entry_ids: Iterable[str]) -> None:
        for entry_id in entry_ids:
            self._waiting_history_entry_ids.discard(str(entry_id))
        self._refresh_history_views()

    def _refresh_history_views(self) -> None:
        self.on_project_changed("processing", "processing_m", "tomograms", "custom", "particles")

    def request_scheduled_batch_start(
        self,
        parent: tk.Misc,
        *,
        queue_key: str,
        title: str,
        entry_ids: Iterable[str],
        start_batch: Callable[[Callable[[], None] | None], None],
    ) -> bool:
        entry_ids = [str(entry_id) for entry_id in entry_ids if entry_id]
        queue_busy = bool(self._scheduled_batch_queues.get(queue_key)) or queue_key in self._scheduled_batch_running
        commands_busy = self._has_running_commands()
        if not queue_busy and not commands_busy:
            start_batch(None)
            return True

        mode = ask_running_scheduled_jobs_mode(parent)
        if mode is None:
            return False
        if mode == "parallel":
            start_batch(None)
            return True

        self.mark_history_entries_waiting(entry_ids)
        wait_window = ProcessLogWindow(self.root, title=title)
        queue_position = len(self._scheduled_batch_queues.get(queue_key, [])) + 1
        if queue_busy:
            queue_position += 1
        wait_window.append_message("Scheduled jobs queued.\n")
        if queue_busy:
            wait_window.append_message(
                f"Another scheduled batch is already active for this processing view. "
                f"This batch has been queued at position {queue_position}.\n"
            )
        else:
            wait_window.append_message(
                "Another command is still running. This scheduled batch will start automatically afterwards.\n"
            )
        wait_window.set_status("Waiting for current jobs to finish…")

        self._scheduled_batch_queues.setdefault(queue_key, []).append(
            {"start_batch": start_batch, "window": wait_window, "entry_ids": entry_ids}
        )
        self._ensure_scheduled_batch_progress(queue_key)
        return True

    def _ensure_scheduled_batch_progress(self, queue_key: str) -> None:
        if queue_key in self._scheduled_batch_running or queue_key in self._scheduled_batch_polling:
            return
        if not self._scheduled_batch_queues.get(queue_key):
            return
        self._scheduled_batch_polling.add(queue_key)
        self.root.after(0, lambda current_key=queue_key: self._poll_scheduled_batch_start(current_key))

    def _poll_scheduled_batch_start(self, queue_key: str) -> None:
        if queue_key in self._scheduled_batch_running:
            self._scheduled_batch_polling.discard(queue_key)
            return
        queue = self._scheduled_batch_queues.get(queue_key, [])
        if not queue:
            self._scheduled_batch_polling.discard(queue_key)
            self._scheduled_batch_queues.pop(queue_key, None)
            return
        if self._has_running_commands():
            self.root.after(500, lambda current_key=queue_key: self._poll_scheduled_batch_start(current_key))
            return

        self._scheduled_batch_polling.discard(queue_key)
        batch = queue.pop(0)
        if not queue:
            self._scheduled_batch_queues.pop(queue_key, None)
        self._scheduled_batch_running.add(queue_key)
        entry_ids = [str(entry_id) for entry_id in batch.get("entry_ids", []) if entry_id]
        self.clear_history_entries_waiting(entry_ids)

        window = batch.get("window")
        if isinstance(window, ProcessLogWindow):
            window.append_message("Current jobs finished. Starting queued scheduled jobs now.\n")
            window.finish("Queued batch started.")

        start_batch = batch.get("start_batch")
        if callable(start_batch):
            try:
                start_batch(lambda current_key=queue_key: self.finish_scheduled_batch(current_key))
            except Exception:
                self._scheduled_batch_running.discard(queue_key)
                raise

    def finish_scheduled_batch(self, queue_key: str) -> None:
        self._scheduled_batch_running.discard(queue_key)
        self._ensure_scheduled_batch_progress(queue_key)

    def open_custom_jobs_dialog(self) -> None:
        self.settings_shell().open_section("custom_job_types")

    def open_shortcuts_dialog(self) -> None:
        self.settings_shell().open_section("shortcuts")

    def open_slurm_profiles_dialog(self) -> None:
        self.settings_shell().open_section("slurm_profiles")

    def open_environments_dialog(self) -> None:
        self.settings_shell().open_section("environments")

    def settings_shell(self) -> SettingsShellWindow:
        if self._settings_shell is None or not self._settings_shell.window.winfo_exists():
            self._settings_shell = SettingsShellWindow(self)
        return self._settings_shell

    def _create_settings_section_view(self, section_key: str, host):
        if section_key == "preferences":
            return PreferencesDialog(self, host=host)
        if section_key == "viewer_defaults":
            return ViewerDefaultsDialog(self, host=host)
        if section_key == "default_parameters":
            return DefaultParametersDialog(self, host=host)
        if section_key == "slurm_profiles":
            return SlurmProfilesDialog(self, host=host)
        if section_key == "environments":
            return EnvironmentsDialog(self, host=host)
        if section_key == "custom_job_types":
            return CustomJobsDialog(self, host=host)
        if section_key == "shortcuts":
            return ManageShortcutsDialog(self, host=host)
        if section_key == "appearance":
            return AppearanceDialog(self, host=host)
        raise ValueError(f"Unsupported settings section: {section_key}")

    def export_settings_bundle_dialog(self) -> None:
        groups = exportable_settings_groups(self.project)
        selected = choose_grouped_items_dialog(
            self.root,
            "Export .cryopal.settings-file",
            "Select which settings entries should be exported. Selecting a category selects all items within it.",
            groups,
        )
        if selected is None:
            return
        if not selected:
            messagebox.showinfo("Export settings", "No settings entries were selected.")
            return
        path = filedialog.asksaveasfilename(
            title="Export .cryopal.settings-file",
            defaultextension=SETTINGS_SUFFIX,
            filetypes=[("CryoPal_tomo settings", f"*{SETTINGS_SUFFIX}"), ("JSON", "*.json")],
        )
        if not path:
            return
        export_path = export_settings_bundle(path, self.project, selected)
        self.status_var.set(f"Settings exported: {export_path.name}")

    def import_settings_bundle_dialog(self) -> None:
        path = filedialog.askopenfilename(
            title="Import .cryopal.settings-file",
            filetypes=[("CryoPal_tomo settings", f"*{SETTINGS_SUFFIX}"), ("JSON", "*.json")],
        )
        if not path:
            return
        try:
            payload = load_settings_bundle(path)
        except Exception as exc:
            messagebox.showerror("Import settings", str(exc))
            return
        groups = importable_settings_groups(payload)
        if not groups:
            messagebox.showinfo("Import settings", "No compatible settings entries were found in this file.")
            return
        selected = choose_grouped_items_dialog(
            self.root,
            "Import .cryopal.settings-file",
            "Select which detected settings entries should be imported. Selecting a category selects all items within it.",
            groups,
        )
        if selected is None:
            return
        if not selected:
            messagebox.showinfo("Import settings", "No settings entries were selected.")
            return
        selection_labels = settings_selection_label_map(groups)
        conflicts = conflicting_import_items(self.project, selected)
        overwrite_existing = True
        if conflicts:
            decision = messagebox.askyesnocancel(
                "Import settings",
                "Some selected settings entries already exist in this project.\n\n"
                + "\n".join(f"- {selection_labels.get(key, key)}" for key in conflicts)
                + "\n\nChoose 'Yes' to overwrite all conflicting entries, "
                "choose 'No' to keep existing conflicting entries and only import non-conflicting ones, "
                "or 'Cancel' to abort.",
                icon="warning",
            )
            if decision is None:
                return
            overwrite_existing = bool(decision)
        applied, skipped = apply_settings_import(
            self.project,
            payload,
            selected,
            overwrite_existing=overwrite_existing,
        )
        if not applied:
            messagebox.showinfo("Import settings", "No selected settings were imported.")
            return
        self.on_project_changed(
            "preferences",
            "defaults",
            "slurm",
            "environments",
            "custom",
            "shortcuts",
            "appearance",
            status_message="Imported settings",
        )
        suffix = ""
        if skipped:
            suffix = " | skipped: " + ", ".join(selection_labels.get(key, key) for key in skipped)
        self.status_var.set(
            "Imported settings: " + ", ".join(selection_labels.get(key, key) for key in applied) + suffix
        )

    def toggle_debug_mode(self) -> None:
        if self.debug_mode.enabled:
            self._exit_debug_mode_flow()
        else:
            self._enter_debug_mode_flow()

    def _enter_debug_mode_flow(self) -> None:
        if self._has_running_commands():
            messagebox.showinfo(
                "Debug mode",
                "Please wait until current commands have finished before entering Debug mode.",
            )
            return
        should_enter = messagebox.askyesno(
            "Enter Debug mode",
            "You are about to enter Debug mode.\n\n"
            "In Debug mode, no jobs will actually run, no project changes will be saved, "
            "and verbose output will be shown.\n\n"
            "Do you want to continue?",
            icon="warning",
        )
        if not should_enter:
            return
        workspace_dir = filedialog.askdirectory(
            title="Select a workspace for Debug mode output",
        )
        if not workspace_dir:
            return
        self.enter_debug_mode(workspace_dir)

    def enter_debug_mode(self, workspace_dir: str) -> None:
        workspace = Path(workspace_dir)
        workspace.mkdir(parents=True, exist_ok=True)
        logs_dir = workspace / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        (workspace / "slurm_scripts").mkdir(parents=True, exist_ok=True)
        (workspace / "tmp").mkdir(parents=True, exist_ok=True)

        self._debug_snapshot = deepcopy(self.project)
        self._debug_snapshot_path = self.project_path
        self._debug_snapshot_modified = self._modified
        self.debug_mode = DebugSessionState(
            enabled=True,
            workspace_dir=str(workspace),
            started_at=date.today().isoformat(),
            log_path=str(logs_dir / "cryopal_debug.log"),
        )
        Path(self.debug_mode.log_path).write_text("", encoding="utf-8")
        self._set_debug_save_state(disabled=True)
        self._show_debug_banner()
        self.open_debug_log_window()
        self.debug_log("INFO", f"Entered Debug mode. Workspace: {workspace}")
        self.status_var.set("Debug mode active")
        self._update_title()

    def _exit_debug_mode_flow(self) -> None:
        should_exit = messagebox.askyesno(
            "Leave Debug mode",
            "You are about to leave Debug mode.\n\n"
            "All progress created in Debug mode is temporary and will be discarded.\n\n"
            "Do you want to exit Debug mode?",
            icon="warning",
        )
        if not should_exit:
            return
        self.exit_debug_mode()

    def exit_debug_mode(self) -> None:
        self.debug_log("INFO", "Leaving Debug mode and discarding temporary progress")
        if self._debug_snapshot is not None:
            self.project = deepcopy(self._debug_snapshot)
        self.project_path = self._debug_snapshot_path
        self._modified = self._debug_snapshot_modified
        self._debug_snapshot = None
        self._debug_snapshot_path = None
        self._debug_snapshot_modified = False
        self.debug_mode = DebugSessionState()
        if self._debug_log_window is not None:
            try:
                self._debug_log_window.window.destroy()
            except tk.TclError:
                pass
            self._debug_log_window = None
        self._set_debug_save_state(disabled=False)
        self._hide_debug_banner()
        self._finish_loaded_project(status_message="Left Debug mode")

    def _prepare_for_context_change(self, action: str) -> bool:
        if not self.debug_mode.enabled:
            return True
        should_exit = messagebox.askyesno(
            "Leave Debug mode",
            f"To {action}, Debug mode must be left first.\n\n"
            "All progress created in Debug mode is temporary and will be discarded.\n\n"
            "Do you want to leave Debug mode?",
            icon="warning",
        )
        if not should_exit:
            return False
        self.exit_debug_mode()
        return True

    def _show_debug_banner(self) -> None:
        self.debug_banner_var.set("Debug mode active: no jobs will run and no project changes can be saved.")
        self.debug_banner.grid()

    def _hide_debug_banner(self) -> None:
        self.debug_banner.grid_remove()
        self.debug_banner_var.set("")

    def _set_debug_save_state(self, disabled: bool) -> None:
        if self._file_menu is None:
            return
        state = "disabled" if disabled else "normal"
        try:
            self._file_menu.entryconfigure("Save", state=state)
            self._file_menu.entryconfigure("Save As...", state=state)
        except tk.TclError:
            pass

    def open_debug_log_window(self) -> None:
        if not self.debug_mode.enabled:
            return
        if self._debug_log_window is not None:
            try:
                self._debug_log_window.window.lift()
                self._debug_log_window.window.focus_force()
                return
            except tk.TclError:
                self._debug_log_window = None
        self._debug_log_window = DebugLogWindow(
            self.root,
            self.debug_mode.workspace_dir,
            on_close=lambda: setattr(self, "_debug_log_window", None),
        )

    def debug_log(self, level: str, message: str) -> None:
        if not self.debug_mode.enabled:
            return
        line = timestamped_debug_line(level, message)
        log_path = self.debug_mode.log_path
        if log_path:
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        if self._debug_log_window is not None:
            def append_line() -> None:
                if self._debug_log_window is None:
                    return
                try:
                    self._debug_log_window.append(line)
                except tk.TclError:
                    self._debug_log_window = None

            try:
                self.root.after(0, append_line)
            except tk.TclError:
                self._debug_log_window = None

    def is_debug_mode_enabled(self) -> bool:
        return self.debug_mode.enabled

    def debug_workspace_dir(self) -> str:
        return self.debug_mode.workspace_dir

    def slurm_profile_names(self) -> list[str]:
        return slurm_profile_names(self.project)

    def environment_titles(self) -> list[str]:
        return environment_titles(self.project)

    def resolve_environment_activation(self, title: str) -> str:
        return resolve_environment_activation(self.project, title)

    def _local_command_with_environment(self, command: str, activation_command: str = "") -> str:
        activation = activation_command.strip()
        if not activation:
            return command

        lines = ["set -e"]
        if "conda activate" in activation.casefold():
            lines.extend(
                [
                    "if command -v conda >/dev/null 2>&1; then",
                    "  _cryopal_conda_base=\"$(conda info --base 2>/dev/null || true)\"",
                    "  if [ -n \"$_cryopal_conda_base\" ] && [ -f \"$_cryopal_conda_base/etc/profile.d/conda.sh\" ]; then",
                    "    source \"$_cryopal_conda_base/etc/profile.d/conda.sh\"",
                    "  fi",
                    "fi",
                    "for _cryopal_base in \"$HOME/miniconda3\" \"$HOME/mambaforge\" \"$HOME/anaconda3\"; do",
                    "  if [ -f \"$_cryopal_base/etc/profile.d/conda.sh\" ]; then",
                    "    source \"$_cryopal_base/etc/profile.d/conda.sh\"",
                    "    break",
                    "  fi",
                    "done",
                ]
            )
        lines.append(activation)
        lines.append(command)
        return f"bash -lc {shlex.quote(chr(10).join(lines))}"

    def submit_slurm_command(
        self,
        command: str,
        profile_name: str,
        cwd: str | None = None,
        dataset_name: str = "",
        job_name: str = "job",
        overrides: dict[str, str] | None = None,
    ):
        if self.debug_mode.enabled:
            root_dir = Path(self.debug_workspace_dir() or Path.cwd())
            profile = find_slurm_profile(self.project, profile_name)
            if profile is None:
                from cryoet_organizer.slurm import SlurmProfile

                profile = SlurmProfile(name=profile_name or "debug_profile")
            scripts_dir = root_dir / "slurm_scripts"
            scripts_dir.mkdir(parents=True, exist_ok=True)
            base_name = f"{dataset_name}_{job_name}" if dataset_name else job_name
            safe_name = "".join(char if char.isalnum() or char in "._-" else "_" for char in base_name).strip("_") or "job"
            index = 1
            while True:
                script_path = scripts_dir / f"{safe_name}_{index:03d}.sbatch"
                if not script_path.exists():
                    break
                index += 1
            script_path.write_text(
                render_sbatch_script(
                    command,
                    profile,
                    cwd,
                    dataset_name,
                    job_name,
                    overrides,
                ),
                encoding="utf-8",
            )
            fake_job_id = f"DEBUG-{int(time.time() * 1000)}"
            self.debug_log(
                "SLURM",
                f"Would submit using profile '{profile_name or '-'}': {command} "
                f"(script: {script_path}, cwd: {cwd or '-'}, job id: {fake_job_id})",
            )
            return SlurmSubmissionResult(
                job_id=fake_job_id,
                script_path=str(script_path),
                stdout="Debug mode: submission simulated",
            )

        profile = find_slurm_profile(self.project, profile_name)
        if profile is None:
            raise ValueError(f"Unknown Slurm profile: {profile_name}")
        root_dir = (
            self.project_path.parent
            if self.project_path is not None
            else Path.cwd()
        )
        script_path = write_sbatch_script(
            root_dir=root_dir,
            command=command,
            profile=profile,
            cwd=cwd,
            dataset_name=dataset_name,
            job_name=job_name,
            overrides=overrides,
        )
        return submit_sbatch_script(script_path)

    def attach_abort_button(self, button: ttk.Button | None) -> None:
        if button is None:
            return
        if button not in self.abort_buttons:
            self.abort_buttons.append(button)
        self._refresh_abort_button()

    def apply_appearance_config(self, config: AppearanceConfig) -> None:
        self._current_appearance = config
        sidebar_bg = config.sidebar_background
        sidebar_button_bg = config.sidebar_button_background
        sidebar_button_fg = config.sidebar_button_foreground
        main_bg = config.main_background
        main_fg = config.main_foreground

        active_sidebar_bg = _shift_hex_color(sidebar_button_bg, -18)

        self.root.configure(background=main_bg)
        self._style.configure("TFrame", background=main_bg)
        self._style.configure("TLabelframe", background=main_bg)
        self._style.configure("TLabelframe.Label", background=main_bg, foreground=main_fg)
        self._style.configure("TLabel", background=main_bg, foreground=main_fg)
        self._style.configure("TCheckbutton", background=main_bg, foreground=main_fg)
        self._style.configure("TRadiobutton", background=main_bg, foreground=main_fg)
        self._style.configure("Heading.TLabel", background=main_bg, foreground=main_fg)
        self._style.configure("DebugBanner.TLabel", background="#9c2f2f", foreground="#ffffff", font=("TkDefaultFont", 10, "bold"))

        self._style.configure("Sidebar.TFrame", background=sidebar_bg)
        self._style.configure(
            "Sidebar.TButton",
            anchor="w",
            padding=(12, 10),
            background=sidebar_button_bg,
            foreground=sidebar_button_fg,
        )
        self._style.map(
            "Sidebar.TButton",
            background=[("active", active_sidebar_bg), ("pressed", active_sidebar_bg)],
            foreground=[("active", sidebar_button_fg), ("pressed", sidebar_button_fg)],
        )
        self._style.configure(
            "ActiveSidebar.TButton",
            anchor="w",
            padding=(12, 10),
            background=active_sidebar_bg,
            foreground=sidebar_button_fg,
        )
        self._style.map(
            "ActiveSidebar.TButton",
            background=[("active", active_sidebar_bg), ("pressed", active_sidebar_bg)],
            foreground=[("active", sidebar_button_fg), ("pressed", sidebar_button_fg)],
        )

        if hasattr(self, "sidebar"):
            self.sidebar.configure(style="Sidebar.TFrame")
        if hasattr(self, "content"):
            self.content.configure(style="TFrame")
        if self.logo_label is not None:
            self.logo_label.configure(background=sidebar_bg)
        if self.version_label is not None:
            self.version_label.configure(background=sidebar_bg, foreground=sidebar_button_fg)

        for button in self.nav_buttons.values():
            try:
                button.configure(style=button.cget("style") or "Sidebar.TButton")
            except tk.TclError:
                continue

        active_tab = self.active_tab_id
        if active_tab is not None and active_tab in self.nav_buttons:
            self.nav_buttons[active_tab].configure(style="ActiveSidebar.TButton")

    def _refresh_abort_button(self) -> None:
        if not self.abort_buttons:
            return
        with self._managed_process_lock:
            has_running = any(process.poll() is None for process in self._managed_processes.values())
        for button in list(self.abort_buttons):
            try:
                button.config(state="normal" if has_running else "disabled")
            except tk.TclError:
                self.abort_buttons.remove(button)

    def start_managed_process(
        self,
        command: str,
        cwd: str | None = None,
        activation_command: str = "",
    ) -> subprocess.Popen:
        prepared_command = self._local_command_with_environment(command, activation_command)
        if self.debug_mode.enabled:
            process = SimulatedProcess(command, cwd=cwd)
            with self._managed_process_lock:
                self._managed_processes[process.pid] = process
                self._abort_requested = False
            if activation_command.strip():
                self.debug_log(
                    "COMMAND",
                    f"Would run locally with environment activation '{activation_command}': {command} (cwd: {cwd or '-'})",
                )
            else:
                self.debug_log("COMMAND", f"Would run locally: {command} (cwd: {cwd or '-'})")
            self.root.after(0, self._refresh_abort_button)
            return process  # type: ignore[return-value]
        process = subprocess.Popen(
            prepared_command,
            shell=True,
            cwd=cwd,
            start_new_session=True,
        )
        with self._managed_process_lock:
            self._managed_processes[process.pid] = process
            self._abort_requested = False
        self.root.after(0, self._refresh_abort_button)
        return process

    def run_shortcut_script_with_log(self, title: str, script: str) -> subprocess.Popen:
        script_body = script.strip()
        if not script_body:
            raise ValueError("Shortcut script is empty.")
        raw_lines = [line.rstrip() for line in script.splitlines() if line.strip()]
        if not raw_lines:
            raise ValueError("Shortcut script is empty.")
        setup_lines = raw_lines[:-1]
        launch_line = raw_lines[-1]
        fd, script_path = tempfile.mkstemp(prefix="cryopal_shortcut_", suffix=".sh")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write("#!/usr/bin/env bash\n")
                handle.write("set -eo pipefail\n")
                handle.write("if command -v conda >/dev/null 2>&1; then\n")
                handle.write("  _cryopal_conda_base=\"$(conda info --base 2>/dev/null || true)\"\n")
                handle.write("  if [ -n \"$_cryopal_conda_base\" ] && [ -f \"$_cryopal_conda_base/etc/profile.d/conda.sh\" ]; then\n")
                handle.write("    source \"$_cryopal_conda_base/etc/profile.d/conda.sh\"\n")
                handle.write("  fi\n")
                handle.write("else\n")
                handle.write("  for _cryopal_base in \"$HOME/miniconda3\" \"$HOME/anaconda3\" \"$HOME/mambaforge\" \"$HOME/miniforge3\"; do\n")
                handle.write("    if [ -f \"$_cryopal_base/etc/profile.d/conda.sh\" ]; then\n")
                handle.write("      source \"$_cryopal_base/etc/profile.d/conda.sh\"\n")
                handle.write("      break\n")
                handle.write("    fi\n")
                handle.write("  done\n")
                handle.write("fi\n")
                handle.write("echo \"Preparing shortcut environment...\"\n")
                for line in setup_lines:
                    handle.write(f"{line}\n")
                handle.write("echo \"Launching GUI command...\"\n")
                handle.write("launch_log=\"$(mktemp /tmp/cryopal_shortcut_gui_XXXXXX.log)\"\n")
                handle.write(
                    f"nohup bash -lc {shlex.quote(launch_line)} </dev/null >\"$launch_log\" 2>&1 &\n"
                )
                handle.write("launcher_pid=$!\n")
                handle.write("sleep 1\n")
                handle.write("if ! kill -0 \"$launcher_pid\" 2>/dev/null; then\n")
                handle.write("  wait \"$launcher_pid\"\n")
                handle.write("  rc=$?\n")
                handle.write("  echo \"Detached launch command exited early with code $rc.\"\n")
                handle.write("  if [ -s \"$launch_log\" ]; then cat \"$launch_log\"; fi\n")
                handle.write("  exit \"$rc\"\n")
                handle.write("fi\n")
                handle.write("echo \"Detached GUI command started successfully.\"\n")
            os.chmod(script_path, 0o755)
        except Exception:
            try:
                os.unlink(script_path)
            except OSError:
                pass
            raise

        def cleanup(_return_code: int) -> None:
            try:
                os.unlink(script_path)
            except OSError:
                pass

        process = subprocess.Popen(
            f"bash -lc {shlex.quote(script_path)}",
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            text=True,
            bufsize=1,
        )
        with self._managed_process_lock:
            self._managed_processes[process.pid] = process
            self._abort_requested = False
        self.root.after(0, self._refresh_abort_button)

        launch_window = ShortcutLaunchWindow(self.root, title=title)
        launch_window.attach_process(process)
        threading.Thread(
            target=lambda: self._wait_process_and_callback(process, cleanup),
            daemon=True,
        ).start()
        return process

    def run_managed_process_async(self, command: str, cwd: str | None = None) -> subprocess.Popen:
        process = self.start_managed_process(command, cwd=cwd)
        threading.Thread(target=lambda: self.wait_managed_process(process), daemon=True).start()
        return process

    def wait_managed_process(self, process: subprocess.Popen) -> int:
        returncode = process.wait()
        with self._managed_process_lock:
            self._managed_processes.pop(process.pid, None)
        self.root.after(0, self._refresh_abort_button)
        if self.debug_mode.enabled:
            if returncode == 0:
                self.debug_log("DONE", f"Simulated completion for command: {getattr(process, 'command', process.pid)}")
            else:
                self.debug_log("DONE", f"Simulated command interrupted: {getattr(process, 'command', process.pid)}")
        return returncode

    def abort_requested(self) -> bool:
        with self._managed_process_lock:
            return self._abort_requested

    def abort_running_commands(self) -> None:
        with self._managed_process_lock:
            processes = [process for process in self._managed_processes.values() if process.poll() is None]
            self._abort_requested = bool(processes)
        if not processes:
            self._refresh_abort_button()
            return
        for process in processes:
            if isinstance(process, SimulatedProcess):
                process.terminate()
                continue
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except Exception:
                try:
                    process.terminate()
                except Exception:
                    pass
            threading.Thread(
                target=self._force_kill_if_needed,
                args=(process,),
                daemon=True,
            ).start()
        self.status_var.set("Abort requested for running commands")
        if self.debug_mode.enabled:
            self.debug_log("ABORT", "Abort requested for simulated commands")

    def _force_kill_if_needed(self, process: subprocess.Popen) -> None:
        time.sleep(1.5)
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def clear_abort_request(self) -> None:
        with self._managed_process_lock:
            if not any(process.poll() is None for process in self._managed_processes.values()):
                self._abort_requested = False

    def _has_running_commands(self) -> bool:
        with self._managed_process_lock:
            return any(process.poll() is None for process in self._managed_processes.values())

    def close_app(self) -> None:
        if self.debug_mode.enabled and not self._prepare_for_context_change("close CryoPal_tomo"):
            return
        if not self._confirm_discard_changes():
            return
        self.root.withdraw()
        splash = self._show_logo_splash(
            "Putting CryoPal to sleep",
            "Saving final UI state and shutting down the application.",
        )
        try:
            self.root.update_idletasks()
            if splash is not None:
                splash.window.update_idletasks()
        except tk.TclError:
            pass
        self.root.after(120, self.root.destroy)

    def open_external_file(self, path: str) -> None:
        candidate = Path(path).expanduser()
        if not candidate.exists():
            raise FileNotFoundError(f"File not found: {candidate}")
        viewer_command = resolve_viewer_command(self.project, candidate)
        if viewer_command:
            subprocess.Popen([*shlex.split(viewer_command), str(candidate)], start_new_session=True)
            return
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(candidate)], start_new_session=True)
            return
        if os.name == "nt":
            os.startfile(str(candidate))  # type: ignore[attr-defined]
            return
        subprocess.Popen(["xdg-open", str(candidate)], start_new_session=True)


def _export_history_csv(path: str, project: ProjectData) -> None:
    fieldnames = [
        "dataset", "processing_tab", "timestamp", "action", "group",
        "job_name", "execution_mode", "slurm_job_id", "command",
    ]
    rows = []
    for owner_name, _owner_kind, entry in _iter_export_history_entries(project):
        rows.append({
            "dataset": owner_name,
            "processing_tab": _resolve_processing_tab(project, entry, _owner_kind),
            "timestamp": entry.timestamp.replace("T", " "),
            "action": entry.action,
            "group": entry.group,
            "job_name": entry.job_name,
            "execution_mode": entry.execution_mode,
            "slurm_job_id": entry.slurm_job_id,
            "command": entry.command,
        })
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _export_file_paths_csv(path: str, entries: list[PathCheckEntry]) -> None:
    fieldnames = [
        "dataset",
        "category",
        "label",
        "ts_name",
        "path",
        "status",
        "note",
    ]
    rows = [
        {
            "dataset": entry.dataset_name,
            "category": entry.category,
            "label": entry.label,
            "ts_name": entry.ts_name,
            "path": entry.path,
            "status": entry.status,
            "note": entry.note,
        }
        for entry in sorted(
            entries,
            key=lambda item: (
                item.dataset_name.casefold(),
                item.category.casefold(),
                (item.ts_name or item.label).casefold(),
                item.path.casefold(),
            ),
        )
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _export_ts_annotations_csv(path: str, project: ProjectData) -> None:
    fieldnames = [
        "TS Name",
        "Dataset",
        "Sample information",
        "Pixel size",
        "CTF resolution estimate",
        "Defocus value",
        "Total dose",
        "Rating",
        "Tags",
    ]
    rows: list[dict[str, str]] = []
    for dataset in sorted(project.datasets, key=lambda item: item.dataset_name.casefold()):
        thumbnails = sorted(
            dataset.thumbnails,
            key=lambda item: (item.ts_name.casefold(), Path(item.image_path).name.casefold()),
        )
        for thumbnail in thumbnails:
            metadata = collect_ts_metadata(
                project,
                dataset,
                thumbnail.ts_name,
                thumbnail_path=thumbnail.image_path,
                mrc_path=thumbnail.mrc_path,
            )
            rows.append(
                {
                    "TS Name": thumbnail.ts_name,
                    "Dataset": dataset.dataset_name,
                    "Sample information": dataset.sample,
                    "Pixel size": f"{metadata.pixel_size:.4f}" if metadata.pixel_size else "",
                    "CTF resolution estimate": (
                        f"{metadata.ctf_resolution_estimate:.2f}"
                        if metadata.ctf_resolution_estimate is not None
                        else ""
                    ),
                    "Defocus value": (
                        f"{metadata.defocus_value:.2f}"
                        if metadata.defocus_value is not None
                        else ""
                    ),
                    "Total dose": f"{metadata.total_dose:.2f}" if metadata.total_dose is not None else "",
                    "Rating": str(thumbnail.rating or ""),
                    "Tags": ", ".join(thumbnail.tags),
                }
            )
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _iter_export_history_entries(project: ProjectData) -> list[tuple[str, str, JobHistoryEntry]]:
    entries: list[tuple[str, str, JobHistoryEntry]] = []
    for dataset in project.datasets:
        for entry in dataset.job_history:
            entries.append((dataset.dataset_name, "dataset", entry))
    for population in project.m_populations:
        for entry in population.job_history:
            entries.append((population.name, "m_population", entry))
    return entries


def _resolve_processing_tab(project: ProjectData, entry: JobHistoryEntry, owner_kind: str) -> str:
    if entry.processing_tab:
        return entry.processing_tab
    if owner_kind == "m_population":
        return "Processing: M"
    if entry.group == "Project Overview":
        return "Project Overview"
    if entry.group == "Particles":
        return "Processing: Particle jobs"
    if entry.group == "Tomograms":
        custom_job_names = {
            str(item.get("name", "")).strip()
            for item in project.state.custom_job_types
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        }
        if entry.job_name in custom_job_names:
            return "Processing: Custom jobs"
        return "Processing: TS jobs"
    return "Processing: WARP"


def _export_history_html(path: str, project: ProjectData) -> None:
    sections: list[str] = []
    current_owner = ""
    current_rows: list[str] = []

    def flush_section() -> None:
        nonlocal current_owner, current_rows
        if not current_owner or not current_rows:
            return
        rows_html = "\n".join(current_rows)
        sections.append(
            f"<h2>{current_owner}</h2>"
            "<table><thead><tr>"
            "<th>Processing tab</th><th>Timestamp</th><th>Action</th><th>Group</th><th>Job</th>"
            "<th>Mode</th><th>SLURM Job</th><th>Command</th>"
            f"</tr></thead><tbody>{rows_html}</tbody></table>"
        )
        current_owner = ""
        current_rows = []

    for owner_name, owner_kind, entry in _iter_export_history_entries(project):
        if owner_name != current_owner:
            flush_section()
            current_owner = owner_name
        current_rows.append(
            f"<tr>"
            f"<td>{_resolve_processing_tab(project, entry, owner_kind)}</td>"
            f"<td>{entry.timestamp.replace('T', ' ')}</td>"
            f"<td>{entry.action}</td>"
            f"<td>{entry.group}</td>"
            f"<td>{entry.job_name}</td>"
            f"<td>{entry.execution_mode}</td>"
            f"<td>{entry.slurm_job_id or '—'}</td>"
            f"<td><code>{entry.command}</code></td>"
            f"</tr>"
        )
    flush_section()
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{project.name} – Job History</title>"
        "<style>"
        "body{font-family:sans-serif;padding:24px;max-width:1400px;margin:auto}"
        "table{width:100%;border-collapse:collapse;margin-bottom:32px}"
        "th,td{border:1px solid #ccc;padding:6px 10px;text-align:left}"
        "th{background:#f0f0f0} code{word-break:break-all;font-size:0.85em}"
        "</style></head><body>"
        f"<h1>{project.name} – Job History</h1>"
        + "\n".join(sections)
        + "</body></html>"
    )
    Path(path).write_text(html, encoding="utf-8")


def _shift_hex_color(value: str, delta: int) -> str:
    color = value.strip()
    if not (len(color) == 7 and color.startswith("#")):
        return value
    try:
        red = max(0, min(255, int(color[1:3], 16) + delta))
        green = max(0, min(255, int(color[3:5], 16) + delta))
        blue = max(0, min(255, int(color[5:7], 16) + delta))
    except ValueError:
        return value
    return f"#{red:02x}{green:02x}{blue:02x}"
