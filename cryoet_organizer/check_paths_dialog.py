from __future__ import annotations

import tkinter as tk
from collections import defaultdict
from tkinter import ttk

from cryoet_organizer.check_paths import PathCheckEntry, collect_project_path_report


class CheckPathsDialog:
    def __init__(self, app) -> None:
        self.app = app
        self.report = collect_project_path_report(app.project)
        self.details_visible = False

        self.window = tk.Toplevel(app.root)
        self.window.title("Check paths")
        self.window.transient(app.root)
        self.window.grab_set()
        self.window.geometry("980x620")
        self.window.minsize(820, 460)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(2, weight=1)

        self._build_summary()
        self._build_missing_summary()
        self._build_details()
        self._build_buttons()
        self._populate()

    def _build_summary(self) -> None:
        summary = ttk.Frame(self.window, padding=16)
        summary.grid(row=0, column=0, sticky="ew")
        summary.columnconfigure(1, weight=1)

        success = self.report.all_found
        icon = "✓" if success else "✗"
        icon_color = "#1d7f3b" if success else "#b32222"
        message = (
            "All paths are found!"
            if success
            else "Following paths and files seem to be missing:"
        )

        tk.Label(
            summary,
            text=icon,
            fg=icon_color,
            font=("TkDefaultFont", 26, "bold"),
        ).grid(row=0, column=0, sticky="nw", padx=(0, 12))
        ttk.Label(
            summary,
            text=message,
            wraplength=860,
            justify="left",
        ).grid(row=0, column=1, sticky="w")

    def _build_missing_summary(self) -> None:
        self.summary_frame = ttk.Frame(self.window, padding=(16, 0, 16, 0))
        self.summary_frame.grid(row=1, column=0, sticky="nsew")
        self.summary_frame.columnconfigure(0, weight=1)
        self.summary_frame.rowconfigure(0, weight=1)

        columns = ("dataset", "item", "note")
        self.summary_tree = ttk.Treeview(self.summary_frame, columns=columns, show="headings", height=8)
        self.summary_tree.heading("dataset", text="Dataset")
        self.summary_tree.heading("item", text="Missing item")
        self.summary_tree.heading("note", text="Note")
        self.summary_tree.column("dataset", width=180, anchor="w")
        self.summary_tree.column("item", width=260, anchor="w")
        self.summary_tree.column("note", width=440, anchor="w")
        self.summary_tree.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(self.summary_frame, orient="vertical", command=self.summary_tree.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(self.summary_frame, orient="horizontal", command=self.summary_tree.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.summary_tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.summary_tree.tag_configure("missing", foreground="#9c1c1c")

        if self.report.all_found:
            self.summary_frame.grid_remove()

    def _build_details(self) -> None:
        self.details_frame = ttk.Frame(self.window, padding=(16, 12, 16, 0))
        self.details_frame.grid(row=2, column=0, sticky="nsew")
        self.details_frame.columnconfigure(0, weight=1)
        self.details_frame.rowconfigure(0, weight=1)

        self.details_tree = ttk.Treeview(
            self.details_frame,
            columns=("status", "path", "note"),
            show="tree headings",
        )
        self.details_tree.heading("#0", text="Item")
        self.details_tree.heading("status", text="Status")
        self.details_tree.heading("path", text="Path")
        self.details_tree.heading("note", text="Note")
        self.details_tree.column("#0", width=260, anchor="w")
        self.details_tree.column("status", width=90, anchor="w")
        self.details_tree.column("path", width=360, anchor="w")
        self.details_tree.column("note", width=220, anchor="w")
        self.details_tree.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(self.details_frame, orient="vertical", command=self.details_tree.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(self.details_frame, orient="horizontal", command=self.details_tree.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.details_tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.details_tree.tag_configure("found", foreground="#1d7f3b")
        self.details_tree.tag_configure("missing", foreground="#9c1c1c")
        self.details_tree.tag_configure("dataset", foreground="#202020")

        self.details_frame.grid_remove()

    def _build_buttons(self) -> None:
        buttons = ttk.Frame(self.window, padding=16)
        buttons.grid(row=3, column=0, sticky="ew")
        buttons.columnconfigure(0, weight=1)

        self.toggle_button = ttk.Button(buttons, text="Show details", command=self._toggle_details)
        self.toggle_button.grid(row=0, column=0, sticky="w")
        ttk.Button(buttons, text="OK", command=self.window.destroy).grid(row=0, column=1, sticky="e")

    def _populate(self) -> None:
        for entry in self.report.summary_missing:
            self.summary_tree.insert(
                "",
                "end",
                values=(entry.dataset_name, entry.label, entry.note or "-"),
                tags=("missing",),
            )

        dataset_groups: dict[str, list[PathCheckEntry]] = defaultdict(list)
        for entry in self.report.entries:
            dataset_groups[entry.dataset_name].append(entry)

        for dataset_name in sorted(dataset_groups):
            dataset_id = self.details_tree.insert("", "end", text=dataset_name, tags=("dataset",), open=False)
            category_groups: dict[str, list[PathCheckEntry]] = defaultdict(list)
            for entry in dataset_groups[dataset_name]:
                category_groups[entry.category].append(entry)
            for category in sorted(category_groups):
                items = category_groups[category]
                found_count = sum(1 for entry in items if entry.status == "found")
                missing_count = len(items) - found_count
                if len(items) == 1 and not items[0].ts_name:
                    item = items[0]
                    self.details_tree.insert(
                        dataset_id,
                        "end",
                        text=category,
                        values=(item.status, item.path or "-", item.note or "-"),
                        tags=(item.status,),
                    )
                    continue

                category_status = "found" if missing_count == 0 else "missing"
                category_note = (
                    f"{found_count}/{len(items)} found"
                    if len(items) > 1
                    else items[0].note or "-"
                )
                category_id = self.details_tree.insert(
                    dataset_id,
                    "end",
                    text=category,
                    values=(category_status, "", category_note),
                    tags=(category_status,),
                    open=False,
                )
                sorted_items = sorted(items, key=lambda entry: (entry.ts_name or entry.label).casefold())
                for item in sorted_items:
                    self.details_tree.insert(
                        category_id,
                        "end",
                        text=item.ts_name or item.label,
                        values=(item.status, item.path or "-", item.note or "-"),
                        tags=(item.status,),
                    )

    def _toggle_details(self) -> None:
        self.details_visible = not self.details_visible
        if self.details_visible:
            self.details_frame.grid()
            self.toggle_button.configure(text="Hide details")
        else:
            self.details_frame.grid_remove()
            self.toggle_button.configure(text="Show details")
