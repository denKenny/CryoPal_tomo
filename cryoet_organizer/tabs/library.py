from __future__ import annotations

from tkinter import ttk

from cryoet_organizer.project import ProjectData
from cryoet_organizer.tabs.base import SidebarTab


class LibraryTab(SidebarTab):
    tab_id = "library"
    title = "Gallery"

    def build(self) -> None:
        self.frame.columnconfigure(0, weight=1)

        ttk.Label(
            self.frame,
            text="Gallery placeholder",
            style="Heading.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        body = (
            "Hier kann spaeter eine Thumbnail-Galerie fuer Tilt-Series, "
            "Tomogramme oder Partikel eingebunden werden. "
            "Das Tab liest bereits aus dem aktuellen Projektkontext."
        )
        ttk.Label(self.frame, text=body, wraplength=700, justify="left").grid(
            row=1, column=0, sticky="w"
        )

        self.summary = ttk.Label(self.frame, text="", wraplength=700, justify="left")
        self.summary.grid(row=2, column=0, sticky="w", pady=(16, 0))

    def on_project_loaded(self, project: ProjectData) -> None:
        lines = [
            f"Project: {project.name}",
            f"Datasets: {len(project.datasets)}",
            f"Sort mode: {project.dataset_sort_mode}",
        ]
        self.summary.config(text="\n".join(lines))
