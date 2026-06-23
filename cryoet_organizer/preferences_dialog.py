from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from cryoet_organizer.preferences import project_preference_enabled
from cryoet_organizer.settings_shell import decorate_settings_window


class PreferencesDialog:
    def __init__(self, app, host: tk.Misc | None = None) -> None:
        self.app = app
        self.embedded = host is not None
        self.window = host if host is not None else tk.Toplevel(app.root)
        if not self.embedded:
            self.window.title("Set preferences")
            self.window.geometry("520x220")
            self.window.minsize(440, 180)
            self.window.transient(app.root)
            self.window.grab_set()
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)

        container = ttk.Frame(self.window, padding=12)
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)

        ttk.Label(
            container,
            text="Define how CryoPal should behave for project-specific features. This section can be extended in the future.",
            wraplength=480,
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 12))

        self.save_particle_plots_var = tk.BooleanVar(
            value=project_preference_enabled(app.project, "save_particle_plots", default=False)
        )
        ttk.Checkbutton(
            container,
            text="Save particle plots",
            variable=self.save_particle_plots_var,
        ).grid(row=1, column=0, sticky="w")

        footer = ttk.Frame(container)
        footer.grid(row=2, column=0, sticky="e", pady=(16, 0))
        cancel_label = "Revert section" if self.embedded else "Cancel"
        save_label = "Save section" if self.embedded else "Save"
        ttk.Button(footer, text=cancel_label, command=self._cancel).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(footer, text=save_label, command=self._save).grid(row=0, column=1)

        if not self.embedded:
            decorate_settings_window(self, "preferences")

    def has_unsaved_changes(self) -> bool:
        current = "true" if self.save_particle_plots_var.get() else "false"
        saved = "true" if project_preference_enabled(self.app.project, "save_particle_plots", default=False) else "false"
        return current != saved

    def save_section(self, *, close_window: bool = False) -> bool:
        self.app.project.state.preferences["save_particle_plots"] = (
            "true" if self.save_particle_plots_var.get() else "false"
        )
        self.app.on_project_changed("preferences")
        self.app.status_var.set("Preferences updated")
        if close_window:
            self.window.destroy()
        return True

    def _cancel(self) -> None:
        self.save_particle_plots_var.set(
            project_preference_enabled(self.app.project, "save_particle_plots", default=False)
        )

    def _save(self) -> None:
        self.save_section(close_window=False)
