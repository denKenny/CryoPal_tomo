from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from cryoet_organizer.preferences import project_preference, project_preference_enabled, project_preference_int
from cryoet_organizer.settings_shell import decorate_settings_window


class PreferencesDialog:
    def __init__(self, app, host: tk.Misc | None = None) -> None:
        self.app = app
        self.embedded = host is not None
        self.window = host if host is not None else tk.Toplevel(app.root)
        if not self.embedded:
            self.window.title("Set preferences")
            self.window.geometry("720x360")
            self.window.minsize(620, 300)
            self.window.transient(app.root)
            self.window.grab_set()
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)

        container = ttk.Frame(self.window, padding=12)
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.columnconfigure(1, weight=1)

        ttk.Label(
            container,
            text="Define how CryoPal should behave for project-specific features. This section can be extended in the future.",
            wraplength=640,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        self.save_particle_plots_var = tk.BooleanVar(
            value=project_preference_enabled(app.project, "save_particle_plots", default=False)
        )
        ttk.Checkbutton(
            container,
            text="Save particle plots",
            variable=self.save_particle_plots_var,
        ).grid(row=1, column=0, columnspan=2, sticky="w")

        self.use_downscaled_thumbnails_var = tk.BooleanVar(
            value=project_preference_enabled(app.project, "use_downscaled_thumbnails", default=True)
        )
        ttk.Checkbutton(
            container,
            text="Use downscaled gallery thumbnails (recommended)",
            variable=self.use_downscaled_thumbnails_var,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(12, 0))

        ttk.Label(container, text="Thumbnail cache folder").grid(row=3, column=0, sticky="w", pady=(12, 4))
        cache_row = ttk.Frame(container)
        cache_row.grid(row=4, column=0, columnspan=2, sticky="ew")
        cache_row.columnconfigure(0, weight=1)
        self.thumbnail_cache_location_var = tk.StringVar(
            value=project_preference(
                app.project,
                "thumbnail_cache_location",
                "dataset/thumbnail-cache",
            )
        )
        ttk.Entry(cache_row, textvariable=self.thumbnail_cache_location_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(cache_row, text="Browse...", command=self._browse_cache_folder).grid(
            row=0, column=1, padx=(8, 0)
        )
        ttk.Label(
            container,
            text=(
                "Default: 'dataset/thumbnail-cache' stores cached thumbnails inside each dataset folder. "
                "If you choose an absolute folder, CryoPal_tomo creates one sub-folder per dataset there."
            ),
            wraplength=640,
            justify="left",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))

        ttk.Label(container, text="Downscaled thumbnail size (pixels)").grid(
            row=6, column=0, sticky="w", pady=(12, 4)
        )
        self.thumbnail_cache_size_var = tk.StringVar(
            value=str(project_preference_int(app.project, "thumbnail_cache_size", default=256, minimum=32, maximum=4096))
        )
        ttk.Entry(container, textvariable=self.thumbnail_cache_size_var, width=10).grid(row=7, column=0, sticky="w")

        footer = ttk.Frame(container)
        footer.grid(row=8, column=0, columnspan=2, sticky="e", pady=(16, 0))
        cancel_label = "Revert section" if self.embedded else "Cancel"
        save_label = "Save section" if self.embedded else "Save"
        ttk.Button(footer, text=cancel_label, command=self._cancel).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(footer, text=save_label, command=self._save).grid(row=0, column=1)

        if not self.embedded:
            decorate_settings_window(self, "preferences")

    def has_unsaved_changes(self) -> bool:
        current = "true" if self.save_particle_plots_var.get() else "false"
        saved = "true" if project_preference_enabled(self.app.project, "save_particle_plots", default=False) else "false"
        current_downscaled = "true" if self.use_downscaled_thumbnails_var.get() else "false"
        saved_downscaled = (
            "true" if project_preference_enabled(self.app.project, "use_downscaled_thumbnails", default=True) else "false"
        )
        current_location = self.thumbnail_cache_location_var.get().strip()
        saved_location = project_preference(
            self.app.project,
            "thumbnail_cache_location",
            "dataset/thumbnail-cache",
        ).strip()
        current_size = self.thumbnail_cache_size_var.get().strip()
        saved_size = str(
            project_preference_int(self.app.project, "thumbnail_cache_size", default=256, minimum=32, maximum=4096)
        )
        return any(
            (
                current != saved,
                current_downscaled != saved_downscaled,
                current_location != saved_location,
                current_size != saved_size,
            )
        )

    def save_section(self, *, close_window: bool = False) -> bool:
        location = self.thumbnail_cache_location_var.get().strip() or "dataset/thumbnail-cache"
        try:
            size = int(self.thumbnail_cache_size_var.get().strip() or "256")
        except ValueError:
            messagebox.showerror("Preferences", "Thumbnail cache size must be an integer.", parent=self.window)
            return False
        if size < 32 or size > 4096:
            messagebox.showerror(
                "Preferences",
                "Thumbnail cache size must be between 32 and 4096 pixels.",
                parent=self.window,
            )
            return False
        self.app.project.state.preferences["save_particle_plots"] = (
            "true" if self.save_particle_plots_var.get() else "false"
        )
        self.app.project.state.preferences["use_downscaled_thumbnails"] = (
            "true" if self.use_downscaled_thumbnails_var.get() else "false"
        )
        self.app.project.state.preferences["thumbnail_cache_location"] = location
        self.app.project.state.preferences["thumbnail_cache_size"] = str(size)
        self.app.on_project_changed("preferences")
        self.app.status_var.set("Preferences updated")
        if close_window:
            self.window.destroy()
        return True

    def _cancel(self) -> None:
        self.save_particle_plots_var.set(
            project_preference_enabled(self.app.project, "save_particle_plots", default=False)
        )
        self.use_downscaled_thumbnails_var.set(
            project_preference_enabled(self.app.project, "use_downscaled_thumbnails", default=True)
        )
        self.thumbnail_cache_location_var.set(
            project_preference(self.app.project, "thumbnail_cache_location", "dataset/thumbnail-cache")
        )
        self.thumbnail_cache_size_var.set(
            str(project_preference_int(self.app.project, "thumbnail_cache_size", default=256, minimum=32, maximum=4096))
        )

    def _save(self) -> None:
        self.save_section(close_window=False)

    def _browse_cache_folder(self) -> None:
        initial = self.thumbnail_cache_location_var.get().strip()
        path = filedialog.askdirectory(
            title="Select thumbnail cache folder",
            initialdir=initial if initial and not initial.startswith("dataset/") else "",
        )
        if path:
            self.thumbnail_cache_location_var.set(path)
