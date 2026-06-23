from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from cryoet_organizer.dialogs import bind_scrollable_canvas
from cryoet_organizer.settings_shell import decorate_settings_window
from cryoet_organizer.viewer_defaults import (
    ViewerDefaultsConfig,
    ViewerException,
    default_viewer_defaults,
    format_extensions,
    get_effective_viewer_defaults,
    parse_extensions_text,
    save_global_viewer_defaults,
    set_project_viewer_defaults,
)


class ViewerDefaultsDialog:
    def __init__(self, app, host: tk.Misc | None = None) -> None:
        self.app = app
        self.saved_config = get_effective_viewer_defaults(app.project)
        self.row_state: list[tuple[tk.StringVar, tk.StringVar]] = []
        self.embedded = host is not None

        self.window = host if host is not None else tk.Toplevel(app.root)
        if not self.embedded:
            self.window.title("Configure viewer defaults")
            self.window.geometry("920x620")
            self.window.minsize(720, 460)
            self.window.transient(app.root)
            self.window.grab_set()
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(1, weight=1)

        ttk.Label(
            self.window,
            text=(
                "Files are opened using the system defaults by default. "
                "Add exceptions below to open selected file extensions with a specific command instead. "
                "Saved changes are stored in this project and also as the new global CryoPal default."
            ),
            wraplength=860,
            justify="left",
            padding=12,
        ).grid(row=0, column=0, sticky="ew")

        body = ttk.LabelFrame(self.window, text="Exceptions", padding=12)
        body.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        header = ttk.Frame(body)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="Command", style="Heading.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="File extensions", style="Heading.TLabel").grid(row=0, column=1, sticky="w", padx=(12, 0))

        self.canvas = tk.Canvas(body, highlightthickness=0)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        yscroll.grid(row=1, column=1, sticky="ns")
        xscroll = ttk.Scrollbar(body, orient="horizontal", command=self.canvas.xview)
        xscroll.grid(row=2, column=0, sticky="ew")
        self.canvas.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        self.rows = ttk.Frame(self.canvas)
        self.rows.columnconfigure(1, weight=1)
        self.rows_window = self.canvas.create_window((0, 0), window=self.rows, anchor="nw")
        bind_scrollable_canvas(self.canvas, self.rows_window, self.rows, allow_horizontal=True)

        actions = ttk.Frame(body)
        actions.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        actions.columnconfigure(0, weight=1)
        ttk.Button(actions, text="Add exception", command=self._add_row).grid(row=0, column=0, sticky="w")

        buttons = ttk.Frame(self.window, padding=(12, 0, 12, 12))
        buttons.grid(row=2, column=0, sticky="ew")
        buttons.columnconfigure(0, weight=1)
        cancel_label = "Revert section" if self.embedded else "Cancel"
        save_label = "Save section" if self.embedded else "Save changes"
        ttk.Button(buttons, text=cancel_label, command=self._cancel).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(buttons, text="Reset to default", command=self._reset).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(buttons, text=save_label, command=self._save).grid(row=0, column=3, padx=(8, 0))

        self._load_rows(self.saved_config)
        if not self.embedded:
            decorate_settings_window(self, "viewer_defaults")

    def _clear_rows(self) -> None:
        self.row_state.clear()
        for child in self.rows.winfo_children():
            child.destroy()

    def _load_rows(self, config: ViewerDefaultsConfig) -> None:
        self._clear_rows()
        for item in config.exceptions:
            self._add_row(item.command, format_extensions(item.extensions))
        if not self.row_state:
            self._add_row()
        self.canvas.yview_moveto(0)

    def _add_row(self, command: str = "", extensions: str = "") -> None:
        row_index = len(self.row_state)
        command_var = tk.StringVar(value=command)
        extensions_var = tk.StringVar(value=extensions)
        self.row_state.append((command_var, extensions_var))

        ttk.Entry(self.rows, textvariable=command_var).grid(row=row_index, column=0, sticky="ew", pady=4)
        ttk.Entry(self.rows, textvariable=extensions_var).grid(row=row_index, column=1, sticky="ew", padx=(12, 0), pady=4)
        ttk.Button(
            self.rows,
            text="Remove",
            command=lambda index=row_index: self._remove_row(index),
        ).grid(row=row_index, column=2, sticky="e", padx=(12, 0), pady=4)

    def _remove_row(self, index: int) -> None:
        if 0 <= index < len(self.row_state):
            self.row_state.pop(index)
        exceptions: list[ViewerException] = []
        for command_var, extensions_var in self.row_state:
            command = command_var.get().strip()
            extensions = parse_extensions_text(extensions_var.get())
            if not command and not extensions:
                continue
            if command and extensions:
                exceptions.append(ViewerException(command=command, extensions=extensions))
        self._load_rows(ViewerDefaultsConfig(exceptions=exceptions))

    def _current_config(self, *, allow_empty: bool = False) -> ViewerDefaultsConfig:
        exceptions: list[ViewerException] = []
        for command_var, extensions_var in self.row_state:
            command = command_var.get().strip()
            extensions = parse_extensions_text(extensions_var.get())
            if not command and not extensions:
                continue
            if not command or not extensions:
                raise ValueError("Each exception needs both a command and at least one file extension.")
            exceptions.append(ViewerException(command=command, extensions=extensions))
        if not exceptions and not allow_empty:
            raise ValueError("Please define at least one viewer exception or reset to the defaults.")
        return ViewerDefaultsConfig(exceptions=exceptions)

    def _reset(self) -> None:
        self._load_rows(default_viewer_defaults())
        self.app.status_var.set("Reset viewer defaults preview to the CryoPal defaults")

    def _save(self) -> None:
        self.save_section(close_window=False)

    def save_section(self, *, close_window: bool = False) -> bool:
        try:
            config = self._current_config()
        except ValueError as exc:
            messagebox.showerror("Viewer defaults", str(exc), parent=self.window)
            return False

        save_global_viewer_defaults(config)
        set_project_viewer_defaults(self.app.project, config)
        self.saved_config = config
        self.app._modified = True
        self.app._update_title()
        self.app.status_var.set("Saved viewer defaults for this project and as the global CryoPal default")
        if close_window:
            self.window.destroy()
        return True

    def has_unsaved_changes(self) -> bool:
        try:
            return self._current_config(allow_empty=True) != self.saved_config
        except Exception:
            return True

    def _cancel(self) -> None:
        self._load_rows(self.saved_config)
