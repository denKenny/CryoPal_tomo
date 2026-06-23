from __future__ import annotations

import tkinter as tk
from collections import defaultdict
from tkinter import ttk

from cryoet_organizer.check_paths import PathCheckEntry
from cryoet_organizer.dialogs import bind_scrollable_canvas


class ExportFilePathsDialog:
    def __init__(self, parent: tk.Misc, entries: list[PathCheckEntry]) -> None:
        self.parent = parent
        self.entries = entries
        self.result: list[PathCheckEntry] | None = None
        self.dataset_groups = self._group_entries(entries)
        self.dataset_vars: dict[str, tk.BooleanVar] = {}
        self.category_vars: dict[tuple[str, str], tk.BooleanVar] = {}
        self.category_entries: dict[tuple[str, str], list[PathCheckEntry]] = {}
        self.dataset_containers: dict[str, ttk.Frame] = {}
        self.dataset_open: dict[str, bool] = {}
        self.select_all_var = tk.BooleanVar(value=True)

        self.window = tk.Toplevel(parent)
        self.window.title("Export file paths")
        self.window.geometry("980x680")
        self.window.minsize(760, 500)
        self.window.transient(parent.winfo_toplevel())
        self.window.grab_set()
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(1, weight=1)

        self._build_header()
        self._build_body()
        self._build_footer()
        self._populate()

    def _group_entries(self, entries: list[PathCheckEntry]) -> dict[str, dict[str, list[PathCheckEntry]]]:
        grouped: dict[str, dict[str, list[PathCheckEntry]]] = defaultdict(lambda: defaultdict(list))
        for entry in entries:
            grouped[entry.dataset_name][entry.category].append(entry)
        return {dataset: dict(categories) for dataset, categories in grouped.items()}

    def _build_header(self) -> None:
        header = ttk.Frame(self.window, padding=12)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        ttk.Checkbutton(
            header,
            text="Select all",
            variable=self.select_all_var,
            command=self._toggle_select_all,
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text=(
                "Select which found file paths should be exported. "
                "Only paths and files that currently exist are listed."
            ),
            wraplength=760,
            justify="left",
        ).grid(row=0, column=1, sticky="w", padx=(12, 0))

    def _build_body(self) -> None:
        body = ttk.Frame(self.window, padding=(12, 0, 12, 12))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        canvas = tk.Canvas(body, highlightthickness=0)
        canvas.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll = ttk.Scrollbar(body, orient="horizontal", command=canvas.xview)
        xscroll.grid(row=1, column=0, sticky="ew")
        canvas.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        self.rows = ttk.Frame(canvas)
        self.rows.columnconfigure(0, weight=1)
        window_id = canvas.create_window((0, 0), window=self.rows, anchor="nw")
        bind_scrollable_canvas(canvas, window_id, self.rows, allow_horizontal=True)

    def _build_footer(self) -> None:
        footer = ttk.Frame(self.window, padding=(12, 0, 12, 12))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)

        ttk.Button(footer, text="Cancel", command=self._cancel).grid(row=0, column=0, sticky="w")
        ttk.Button(footer, text="Export paths", command=self._confirm).grid(row=0, column=1, sticky="e")

    def _populate(self) -> None:
        for row_index, dataset_name in enumerate(sorted(self.dataset_groups, key=str.casefold)):
            frame = ttk.LabelFrame(self.rows, text=dataset_name, padding=10)
            frame.grid(row=row_index, column=0, sticky="ew", pady=(0, 10))
            frame.columnconfigure(0, weight=1)
            self.dataset_containers[dataset_name] = frame
            self.dataset_open[dataset_name] = True

            categories = self.dataset_groups[dataset_name]
            dataset_var = tk.BooleanVar(value=True)
            self.dataset_vars[dataset_name] = dataset_var

            header = ttk.Frame(frame)
            header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
            header.columnconfigure(1, weight=1)

            ttk.Checkbutton(
                header,
                text="Select dataset",
                variable=dataset_var,
                command=lambda name=dataset_name: self._toggle_dataset(name),
            ).grid(row=0, column=0, sticky="w")

            toggle = ttk.Button(
                header,
                text="Hide",
                width=8,
                command=lambda name=dataset_name: self._toggle_dataset_visibility(name),
            )
            toggle.grid(row=0, column=2, sticky="e")
            frame.toggle_button = toggle  # type: ignore[attr-defined]

            inner = ttk.Frame(frame)
            inner.grid(row=1, column=0, sticky="ew")
            inner.columnconfigure(0, weight=1)
            frame.inner = inner  # type: ignore[attr-defined]

            for category_index, category in enumerate(sorted(categories, key=str.casefold)):
                key = (dataset_name, category)
                category_var = tk.BooleanVar(value=True)
                self.category_vars[key] = category_var
                entries = sorted(
                    categories[category],
                    key=lambda item: ((item.ts_name or item.label).casefold(), item.path.casefold()),
                )
                self.category_entries[key] = entries
                count = len(entries)
                description = entries[0].path if count == 1 else f"{count} path(s)"
                ttk.Checkbutton(
                    inner,
                    text=f"{category}  [{description}]",
                    variable=category_var,
                    command=lambda name=dataset_name: self._sync_dataset_state(name),
                ).grid(row=category_index, column=0, sticky="w", pady=2)

        self._sync_select_all_state()

    def _toggle_select_all(self) -> None:
        target = self.select_all_var.get()
        for dataset_var in self.dataset_vars.values():
            dataset_var.set(target)
        for category_var in self.category_vars.values():
            category_var.set(target)

    def _toggle_dataset(self, dataset_name: str) -> None:
        target = self.dataset_vars[dataset_name].get()
        for current_dataset, _category in self.category_vars:
            if current_dataset == dataset_name:
                self.category_vars[(current_dataset, _category)].set(target)
        self._sync_select_all_state()

    def _sync_dataset_state(self, dataset_name: str) -> None:
        values = [
            self.category_vars[(current_dataset, category)].get()
            for current_dataset, category in self.category_vars
            if current_dataset == dataset_name
        ]
        self.dataset_vars[dataset_name].set(bool(values) and all(values))
        self._sync_select_all_state()

    def _sync_select_all_state(self) -> None:
        values = [var.get() for var in self.category_vars.values()]
        self.select_all_var.set(bool(values) and all(values))

    def _toggle_dataset_visibility(self, dataset_name: str) -> None:
        frame = self.dataset_containers[dataset_name]
        inner = frame.inner  # type: ignore[attr-defined]
        is_open = self.dataset_open[dataset_name]
        if is_open:
            inner.grid_remove()
            frame.toggle_button.configure(text="Show")  # type: ignore[attr-defined]
        else:
            inner.grid()
            frame.toggle_button.configure(text="Hide")  # type: ignore[attr-defined]
        self.dataset_open[dataset_name] = not is_open

    def _selected_entries(self) -> list[PathCheckEntry]:
        selected: list[PathCheckEntry] = []
        for key, var in self.category_vars.items():
            if var.get():
                selected.extend(self.category_entries[key])
        return selected

    def _cancel(self) -> None:
        self.result = None
        self.window.destroy()

    def _confirm(self) -> None:
        self.result = self._selected_entries()
        self.window.destroy()

    def show(self) -> list[PathCheckEntry] | None:
        self.window.wait_window()
        return self.result
