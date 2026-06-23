from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from cryoet_organizer.dialogs import bind_scrollable_canvas
from cryoet_organizer.shortcuts import ShortcutDefinition, get_project_shortcuts, set_project_shortcuts
from cryoet_organizer.shortcuts_dialog import ShortcutEditorDialog
from cryoet_organizer.tabs.base import SidebarTab


class ShortcutsTab(SidebarTab):
    tab_id = "shortcuts"
    title = "Shortcuts"
    refresh_domains = ("shortcuts",)

    def build(self) -> None:
        self.shortcuts: list[ShortcutDefinition] = []
        self._tile_columns = 1

        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=1)

        ttk.Label(
            self.frame,
            text="Dashboard",
            style="Heading.TLabel",
        ).grid(row=0, column=0, sticky="w")

        container = ttk.Frame(self.frame)
        container.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(container, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll = ttk.Scrollbar(container, orient="horizontal", command=self.canvas.xview)
        xscroll.grid(row=1, column=0, sticky="ew")
        self.canvas.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        self.tiles_frame = ttk.Frame(self.canvas)
        self.tiles_window = self.canvas.create_window((0, 0), window=self.tiles_frame, anchor="nw")
        bind_scrollable_canvas(self.canvas, self.tiles_window, self.tiles_frame, allow_horizontal=True)
        self.canvas.bind("<Configure>", self._on_canvas_configure, add="+")

    def on_project_loaded(self, project) -> None:
        self.shortcuts = get_project_shortcuts(project)
        self._render_tiles()

    def preload_view(self) -> None:
        self._on_canvas_configure()
        self.frame.update_idletasks()

    def sync_to_project(self, project) -> None:
        set_project_shortcuts(project, self.shortcuts)

    def _on_canvas_configure(self, event=None) -> None:
        width = self.canvas.winfo_width() if event is None else event.width
        columns = max(1, width // 220)
        if columns != self._tile_columns:
            self._tile_columns = columns
            self._render_tiles()

    def _render_tiles(self) -> None:
        for child in self.tiles_frame.winfo_children():
            child.destroy()
        for column in range(max(1, self._tile_columns)):
            self.tiles_frame.columnconfigure(column, weight=1)

        items = list(self.shortcuts)
        total_items = len(items) + 1
        for index in range(total_items):
            row = index // max(1, self._tile_columns)
            column = index % max(1, self._tile_columns)
            if index < len(items):
                tile = self._build_shortcut_tile(self.tiles_frame, items[index])
            else:
                tile = self._build_add_tile(self.tiles_frame)
            tile.grid(row=row, column=column, padx=10, pady=10, sticky="nw")

    def _build_shortcut_tile(self, parent: tk.Misc, shortcut: ShortcutDefinition) -> tk.Frame:
        tile = tk.Frame(
            parent,
            width=180,
            height=180,
            background=shortcut.color,
            highlightthickness=1,
            highlightbackground="#9aa7b2",
            bd=0,
        )
        tile.grid_propagate(False)
        tile.pack_propagate(False)

        inner = tk.Frame(tile, background="#ffffff", bd=1, relief="solid")
        inner.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.76, relheight=0.34)

        label = tk.Label(
            inner,
            text=shortcut.title,
            background="#ffffff",
            foreground="#000000",
            justify="center",
            wraplength=112,
            font=("TkDefaultFont", 11, "bold"),
        )
        label.place(relx=0.5, rely=0.5, anchor="center")

        for widget in (tile, inner, label):
            widget.bind(
                "<Double-Button-1>",
                lambda _event, current=shortcut: self._run_shortcut(current),
            )
        return tile

    def _build_add_tile(self, parent: tk.Misc) -> tk.Canvas:
        tile = tk.Canvas(
            parent,
            width=180,
            height=180,
            background=self.app._current_appearance.main_background,
            highlightthickness=0,
            bd=0,
        )
        tile.create_oval(40, 40, 140, 140, fill="#ffffff", outline="#8b97a3", width=2)
        tile.create_text(90, 90, text="+", fill="#5f6b75", font=("TkDefaultFont", 36, "bold"))
        tile.bind("<Double-Button-1>", lambda _event: self._create_shortcut())
        return tile

    def _create_shortcut(self) -> None:
        created = ShortcutEditorDialog(
            self.app.root,
            title="Create shortcut",
            confirm_label="Create shortcut",
        ).show()
        if created is None:
            return
        self.shortcuts.append(created)
        set_project_shortcuts(self.app.project, self.shortcuts)
        self.app.on_project_changed("shortcuts", status_message=f"Created shortcut: {created.title}")

    def _run_shortcut(self, shortcut: ShortcutDefinition) -> None:
        self.app.run_shortcut_script_with_log(
            shortcut.title,
            shortcut.script,
        )
