from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from cryoet_organizer.settings_bundle import SETTINGS_CATEGORY_LABELS


SETTINGS_SHELL_ORDER: tuple[str, ...] = (
    "preferences",
    "viewer_defaults",
    "default_parameters",
    "slurm_profiles",
    "environments",
    "custom_job_types",
    "shortcuts",
    "appearance",
)


class SettingsShellWindow:
    def __init__(self, app) -> None:
        self.app = app
        self._card_target_width = 1080
        self.window = tk.Toplevel(app.root)
        self.window.title("Settings")
        self.window.geometry("1180x760")
        self.window.minsize(980, 620)
        self.window.transient(app.root)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(1, weight=1)

        header = ttk.Frame(self.window, padding=(12, 12, 12, 0))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        self.nav = ttk.Frame(header)
        self.nav.grid(row=0, column=0, sticky="ew")
        for column in range(4):
            self.nav.columnconfigure(column, weight=1)

        actions = ttk.Frame(header)
        actions.grid(row=1, column=0, sticky="e", pady=(8, 0))

        self.nav_buttons: dict[str, ttk.Button] = {}
        for index, key in enumerate(SETTINGS_SHELL_ORDER):
            button = ttk.Button(
                self.nav,
                text=SETTINGS_CATEGORY_LABELS[key],
                command=lambda current=key: self.open_section(current),
            )
            button.grid(row=index // 4, column=index % 4, padx=4, pady=4, sticky="ew")
            self.nav_buttons[key] = button

        ttk.Button(actions, text="Save all", command=self.save_all).grid(row=0, column=0, padx=(8, 0))
        ttk.Button(actions, text="Close settings", command=self.close).grid(row=0, column=1, padx=(8, 0))

        self.content = ttk.Frame(self.window, padding=(12, 6, 12, 12))
        self.content.grid(row=1, column=0, sticky="nsew")
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)

        self.card_container = tk.Frame(
            self.content,
            background="#ececec",
            highlightthickness=1,
            highlightbackground="#c7c7c7",
            bd=0,
        )
        self.card_container.place(x=0, y=0, anchor="nw")
        self.card = ttk.Frame(self.card_container, padding=10)
        self.card.grid(row=0, column=0, sticky="nsew")
        self.card_container.columnconfigure(0, weight=1)
        self.card_container.rowconfigure(0, weight=1)
        self.card.columnconfigure(0, weight=1)
        self.card.rowconfigure(0, weight=1)
        self.content.bind("<Configure>", self._layout_card, add="+")

        self.sections: dict[str, object] = {}
        self.section_frames: dict[str, ttk.Frame] = {}
        self.active_section: str | None = None
        self.window.after_idle(self._layout_card)

    def _section_exists(self, section_key: str) -> bool:
        frame = self.section_frames.get(section_key)
        try:
            return bool(frame is not None and frame.winfo_exists())
        except Exception:
            return False

    def open_section(self, section_key: str) -> None:
        if section_key not in self.sections or not self._section_exists(section_key):
            frame = ttk.Frame(self.card)
            frame.grid(row=0, column=0, sticky="nsew")
            frame.columnconfigure(0, weight=1)
            self.section_frames[section_key] = frame
            try:
                self.sections[section_key] = self.app._create_settings_section_view(section_key, frame)
            except Exception as exc:
                self.section_frames.pop(section_key, None)
                self.sections.pop(section_key, None)
                try:
                    frame.destroy()
                except Exception:
                    pass
                messagebox.showerror(
                    "Settings",
                    f"Could not open '{SETTINGS_CATEGORY_LABELS.get(section_key, section_key)}'.\n\n{exc}",
                    parent=self.window,
                )
                return
        for key, frame in self.section_frames.items():
            if key == section_key:
                frame.grid()
            else:
                frame.grid_remove()
        self.active_section = section_key
        self._refresh_header()
        self._layout_card()
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def _layout_card(self, _event=None) -> None:
        self.content.update_idletasks()
        available_width = max(320, self.content.winfo_width())
        available_height = max(240, self.content.winfo_height())
        card_width = min(self._card_target_width, max(320, available_width - 24))
        x = max(0, (available_width - card_width) // 2)
        self.card_container.place(x=x, y=0, width=card_width, height=available_height)

    def _refresh_header(self) -> None:
        for key, button in self.nav_buttons.items():
            button.configure(state="disabled" if key == self.active_section else "normal")

    def save_all(self) -> bool:
        for section_key in SETTINGS_SHELL_ORDER:
            section = self.sections.get(section_key)
            if section is None:
                continue
            save_section = getattr(section, "save_section", None)
            if callable(save_section) and not save_section(close_window=False):
                self.open_section(section_key)
                return False
        return True

    def has_unsaved_changes(self) -> bool:
        for section in self.sections.values():
            has_changes = getattr(section, "has_unsaved_changes", None)
            if callable(has_changes) and has_changes():
                return True
        return False

    def close(self) -> None:
        if self.has_unsaved_changes():
            should_save = messagebox.askyesno(
                "Unsaved settings",
                "There are unsaved settings changes.\n\nPress 'Yes' to save them before closing or 'No' to keep editing.",
                parent=self.window,
                icon="warning",
            )
            if not should_save:
                return
            if not self.save_all():
                return
        try:
            self.window.destroy()
        finally:
            self.app._settings_shell = None


def decorate_settings_window(dialog: object, section_key: str) -> None:
    window = getattr(dialog, "window", None)
    if window is None or not isinstance(window, tk.Toplevel):
        return
