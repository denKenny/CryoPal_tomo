from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cryoet_organizer.app import CryoETOrganizerApp

from cryoet_organizer.project import ProjectData


class SidebarTab:
    tab_id = "base"
    title = "Base"
    refresh_domains: tuple[str, ...] = ()

    def __init__(self, app: "CryoETOrganizerApp", parent: ttk.Frame) -> None:
        self.app = app
        self.frame = ttk.Frame(parent, padding=16)
        self._custom_integration = None
        self.build()
        if self.tab_id in {"processing", "processing_m", "tomograms", "particles"}:
            from cryoet_organizer.tabs.custom_integration import CustomJobIntegration
            self._custom_integration = CustomJobIntegration(self)
            self.refresh_domains = (*self.refresh_domains, "custom")

    def _select_assigned_custom_job(self) -> bool:
        return self._custom_integration.select() if self._custom_integration else False

    def _refresh_assigned_custom_jobs(self) -> None:
        if self._custom_integration:
            self._custom_integration.refresh()
            if self._custom_integration.variable.get() in self._custom_integration.jobs:
                self._custom_integration.select()

    def _hide_assigned_custom_job(self) -> None:
        if self._custom_integration:
            self._custom_integration.hide()

    def _copy_assigned_custom_history(self, entry) -> bool:
        return self._custom_integration.copy_history(entry) if self._custom_integration else False

    def build(self) -> None:
        raise NotImplementedError

    def on_project_loaded(self, project: ProjectData) -> None:
        pass

    def sync_to_project(self, project: ProjectData) -> None:
        pass

    def on_project_saved(self, project: ProjectData) -> None:
        pass

    def on_tab_shown(self) -> None:
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
