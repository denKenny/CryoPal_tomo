from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from cryoet_organizer.project import ProjectData


class SidebarTab:
    tab_id = "base"
    title = "Base"
    refresh_domains: tuple[str, ...] = ()

    def __init__(self, app: "CryoETOrganizerApp", parent: ttk.Frame) -> None:
        self.app = app
        self.frame = ttk.Frame(parent, padding=16)
        self.build()

    def build(self) -> None:
        raise NotImplementedError

    def on_project_loaded(self, project: ProjectData) -> None:
        pass

    def sync_to_project(self, project: ProjectData) -> None:
        pass

    def on_project_saved(self, project: ProjectData) -> None:
        pass


class LabeledEntry(ttk.Frame):
    def __init__(self, parent: tk.Misc, label: str) -> None:
        super().__init__(parent)
        self.columnconfigure(0, weight=1)
        ttk.Label(self, text=label).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.var = tk.StringVar()
        ttk.Entry(self, textvariable=self.var).grid(row=1, column=0, sticky="ew")

    def get(self) -> str:
        return self.var.get().strip()

    def set(self, value: str) -> None:
        self.var.set(value)


class LabeledPathEntry(ttk.Frame):
    def __init__(self, parent: tk.Misc, label: str, button_text: str, command) -> None:
        super().__init__(parent)
        self.columnconfigure(0, weight=1)
        ttk.Label(self, text=label).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))
        self.var = tk.StringVar()
        ttk.Entry(self, textvariable=self.var).grid(row=1, column=0, sticky="ew")
        ttk.Button(self, text=button_text, command=command).grid(
            row=1, column=1, sticky="ew", padx=(8, 0)
        )

    def get(self) -> str:
        return self.var.get().strip()

    def set(self, value: str) -> None:
        self.var.set(value)
