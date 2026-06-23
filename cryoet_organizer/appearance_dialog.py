from __future__ import annotations

import tkinter as tk
from tkinter import colorchooser, messagebox, ttk

from cryoet_organizer.appearance import AppearanceConfig, get_project_appearance, set_project_appearance
from cryoet_organizer.settings_shell import decorate_settings_window


class AppearanceDialog:
    def __init__(self, app, host: tk.Misc | None = None) -> None:
        self.app = app
        self.saved_config = get_project_appearance(app.project)
        self.preview_config = self.saved_config
        self.embedded = host is not None

        self.window = host if host is not None else tk.Toplevel(app.root)
        if not self.embedded:
            self.window.title("Appearance")
            self.window.geometry("760x420")
            self.window.minsize(640, 360)
            self.window.transient(app.root)
            self.window.grab_set()
            self.window.protocol("WM_DELETE_WINDOW", self._cancel)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)

        self.sidebar_bg_var = tk.StringVar(value=self.saved_config.sidebar_background)
        self.sidebar_button_bg_var = tk.StringVar(value=self.saved_config.sidebar_button_background)
        self.sidebar_button_fg_var = tk.StringVar(value=self.saved_config.sidebar_button_foreground)
        self.main_bg_var = tk.StringVar(value=self.saved_config.main_background)
        self.main_fg_var = tk.StringVar(value=self.saved_config.main_foreground)

        container = ttk.Frame(self.window, padding=12)
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)

        ttk.Label(
            container,
            text=(
                "Customize this project's CryoPal_tomo appearance. Apply previews the colors immediately. "
                "Save changes stores them in the project file."
            ),
            wraplength=700,
            justify="left",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 12))

        form = ttk.LabelFrame(container, text="Project appearance", padding=12)
        form.grid(row=1, column=0, sticky="new")
        form.columnconfigure(1, weight=1)

        self._add_color_row(form, 0, "Sidebar background", self.sidebar_bg_var)
        self._add_color_row(form, 1, "Sidebar tab button color", self.sidebar_button_bg_var)
        self._add_color_row(form, 2, "Sidebar tab font color", self.sidebar_button_fg_var)
        self._add_color_row(form, 3, "Main window background", self.main_bg_var)
        self._add_color_row(form, 4, "Main window font color", self.main_fg_var)

        buttons = ttk.Frame(container)
        buttons.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        buttons.columnconfigure(0, weight=1)
        cancel_label = "Revert section" if self.embedded else "Cancel"
        save_label = "Save section" if self.embedded else "Save changes"
        ttk.Button(buttons, text=cancel_label, command=self._cancel).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(buttons, text="Reset", command=self._reset).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(buttons, text="Apply", command=self._apply).grid(row=0, column=3, padx=(8, 0))
        ttk.Button(buttons, text=save_label, command=self._save).grid(row=0, column=4, padx=(8, 0))

        if not self.embedded:
            decorate_settings_window(self, "appearance")

    def _add_color_row(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=6, padx=(0, 12))
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=6)
        swatch = tk.Label(parent, width=4, relief="solid", bd=1, background=variable.get())
        swatch.grid(row=row, column=2, sticky="w", padx=(8, 8))

        def update_swatch(*_args) -> None:
            try:
                swatch.configure(background=variable.get() or "#ffffff")
            except tk.TclError:
                pass

        variable.trace_add("write", update_swatch)
        ttk.Button(
            parent,
            text="Choose...",
            command=lambda current=variable: self._choose_color(current),
        ).grid(row=row, column=3, sticky="w")

    def _choose_color(self, variable: tk.StringVar) -> None:
        current = variable.get().strip() or "#ffffff"
        _rgb, color = colorchooser.askcolor(color=current, parent=self.window)
        if color:
            variable.set(color)

    def _current_config(self) -> AppearanceConfig:
        return AppearanceConfig(
            sidebar_background=self.sidebar_bg_var.get().strip() or self.saved_config.sidebar_background,
            sidebar_button_background=self.sidebar_button_bg_var.get().strip() or self.saved_config.sidebar_button_background,
            sidebar_button_foreground=self.sidebar_button_fg_var.get().strip() or self.saved_config.sidebar_button_foreground,
            main_background=self.main_bg_var.get().strip() or self.saved_config.main_background,
            main_foreground=self.main_fg_var.get().strip() or self.saved_config.main_foreground,
        )

    def _apply(self) -> None:
        self.preview_config = self._current_config()
        try:
            self.app.apply_appearance_config(self.preview_config)
        except tk.TclError as exc:
            messagebox.showerror("Appearance", f"Invalid color value.\n\n{exc}", parent=self.window)
            return
        self.app.status_var.set("Applied appearance preview")

    def _save(self) -> None:
        self.save_section(close_window=False)

    def save_section(self, *, close_window: bool = False) -> bool:
        self.preview_config = self._current_config()
        try:
            self.app.apply_appearance_config(self.preview_config)
        except tk.TclError as exc:
            messagebox.showerror("Appearance", f"Invalid color value.\n\n{exc}", parent=self.window)
            return False
        set_project_appearance(self.app.project, self.preview_config)
        self.saved_config = self.preview_config
        self.app._modified = True
        self.app._update_title()
        self.app.status_var.set("Saved project appearance")
        if close_window:
            self.window.destroy()
        return True

    def has_unsaved_changes(self) -> bool:
        try:
            return self._current_config() != self.saved_config
        except Exception:
            return True

    def _reset(self) -> None:
        defaults = AppearanceConfig()
        self.sidebar_bg_var.set(defaults.sidebar_background)
        self.sidebar_button_bg_var.set(defaults.sidebar_button_background)
        self.sidebar_button_fg_var.set(defaults.sidebar_button_foreground)
        self.main_bg_var.set(defaults.main_background)
        self.main_fg_var.set(defaults.main_foreground)
        self.preview_config = defaults
        try:
            self.app.apply_appearance_config(defaults)
        except tk.TclError as exc:
            messagebox.showerror("Appearance", f"Invalid color value.\n\n{exc}", parent=self.window)
            return
        self.app.status_var.set("Reset appearance preview to default colors")

    def _cancel(self) -> None:
        self.app.apply_appearance_config(self.saved_config)
        self.sidebar_bg_var.set(self.saved_config.sidebar_background)
        self.sidebar_button_bg_var.set(self.saved_config.sidebar_button_background)
        self.sidebar_button_fg_var.set(self.saved_config.sidebar_button_foreground)
        self.main_bg_var.set(self.saved_config.main_background)
        self.main_fg_var.set(self.saved_config.main_foreground)
