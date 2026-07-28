from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import tkinter as tk
from tkinter import filedialog, ttk

from cryoet_organizer.dialogs import bind_scrollable_canvas
from cryoet_organizer.environments import environment_titles
from cryoet_organizer.file_resolver import file_role_order, role_title


ENVIRONMENT_ROW_KEY = "__execution_environment__"

BASE_INPUT_TYPE_OPTIONS = (
    "text",
    "path",
    "file",
    "bool",
    "All files that: [*.mrc]",
    "All files that: [*.st]",
    "All files that: [*.star]",
    "All files that: [*.json]",
    "All files that: custom pattern",
)

INPUT_TYPE_MAP = {
    "All files that: [*.mrc]": "all_files_mrc",
    "All files that: [*.st]": "all_files_st",
    "All files that: [*.star]": "all_files_star",
    "All files that: [*.json]": "all_files_json",
    "All files that: custom pattern": "all_files_custom_pattern",
}


@dataclass
class ParameterEditorRow:
    key: str
    flag: str = ""
    input_type: str = "text"
    default: str = ""
    description: str = ""
    options: tuple[str, ...] = field(default_factory=tuple)
    extra: dict[str, str] = field(default_factory=dict)
    custom: bool = False
    removable: bool = True
    editable_flag: bool = True
    editable_input_type: bool = True
    removed: bool = False


def display_type(project, stored: str) -> str:
    if stored == "environment":
        return "environment"
    if stored == "choice":
        return "choice"
    if stored.startswith("ts_role:"):
        role = stored.split(":", 1)[1]
        return f"TS selection: {role_title(project, role)}"
    if stored in {"ts_aligned_stack", "ts_angle_file", "ts_tomogram"}:
        legacy_map = {
            "ts_aligned_stack": "aligned_stack",
            "ts_angle_file": "angle_file",
            "ts_tomogram": "tomogram",
        }
        role = legacy_map[stored]
        return f"TS selection: {role_title(project, role)}"
    return {value: key for key, value in INPUT_TYPE_MAP.items()}.get(stored, stored or "text")


def stored_type(project, displayed: str) -> str:
    if displayed in {"environment", "choice"}:
        return displayed
    if displayed.startswith("TS selection: "):
        label = displayed.removeprefix("TS selection: ").strip()
        for role in file_role_order(project):
            if role_title(project, role) == label:
                return f"ts_role:{role}"
    return INPUT_TYPE_MAP.get(displayed, displayed or "text")


def input_type_options(project, *, include_environment: bool = True, include_choice: bool = True) -> tuple[str, ...]:
    values = list(BASE_INPUT_TYPE_OPTIONS[:4])
    for role in file_role_order(project):
        values.append(f"TS selection: {role_title(project, role)}")
    values.extend(BASE_INPUT_TYPE_OPTIONS[4:])
    if include_choice and "choice" not in values:
        values.append("choice")
    if include_environment and "environment" not in values:
        values.append("environment")
    return tuple(values)


def default_display_text(row: ParameterEditorRow) -> str:
    if row.input_type == "bool":
        return "true" if str(row.default).lower() in {"1", "true", "yes", "on"} else "false"
    if row.input_type.startswith("ts_"):
        return "From TS processing list"
    return row.default or "-"


class ParameterSummaryTable:
    def __init__(self, parent: tk.Misc, app, *, border: bool = False) -> None:
        self.app = app
        self.container = ttk.Frame(parent, padding=0)
        self.container.columnconfigure(0, weight=1)
        self.container.rowconfigure(1, weight=1)
        self._rows: list[dict[str, object]] = []

        header = ttk.Frame(self.container)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self._configure_columns(header)
        for column, heading in enumerate(("Flag", "Input type", "Default", "Description")):
            padx = (0, 8) if column < 3 else (0, 0)
            ttk.Label(header, text=heading).grid(row=0, column=column, sticky="w", padx=padx)

        self.canvas = tk.Canvas(self.container, highlightthickness=0, borderwidth=1 if border else 0)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self.container, orient="vertical", command=self.canvas.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        xscrollbar = ttk.Scrollbar(self.container, orient="horizontal", command=self.canvas.xview)
        xscrollbar.grid(row=2, column=0, sticky="ew")
        self.canvas.configure(yscrollcommand=scrollbar.set, xscrollcommand=xscrollbar.set)
        self.rows_frame = ttk.Frame(self.canvas)
        self._configure_columns(self.rows_frame)
        self.canvas_window = self.canvas.create_window((0, 0), window=self.rows_frame, anchor="nw")
        bind_scrollable_canvas(self.canvas, self.canvas_window, self.rows_frame, allow_horizontal=True)

    def grid(self, **kwargs) -> None:
        self.container.grid(**kwargs)

    def grid_remove(self) -> None:
        self.container.grid_remove()

    def _configure_columns(self, frame: ttk.Frame) -> None:
        frame.columnconfigure(0, weight=0, minsize=210)
        frame.columnconfigure(1, weight=0, minsize=170)
        frame.columnconfigure(2, weight=0, minsize=210)
        frame.columnconfigure(3, weight=1, minsize=520)

    def set_rows(self, rows: list[ParameterEditorRow]) -> None:
        visible_rows = [row for row in rows if not row.removed]
        while len(self._rows) < len(visible_rows):
            row_index = len(self._rows)
            row_frame = ttk.Frame(self.rows_frame)
            row_frame.grid(row=row_index, column=0, columnspan=4, sticky="ew")
            self._configure_columns(row_frame)
            labels = []
            for column in range(4):
                padx = (0, 8) if column < 3 else (0, 0)
                label = ttk.Label(row_frame)
                label.grid(row=0, column=column, sticky="ew", padx=padx, pady=4)
                labels.append(label)
            self._rows.append({"frame": row_frame, "labels": labels})
        for row_index, row in enumerate(visible_rows):
            state = self._rows[row_index]
            frame = state.get("frame")
            labels = state.get("labels")
            if isinstance(frame, ttk.Frame):
                frame.grid()
            if isinstance(labels, list) and len(labels) == 4:
                labels[0].configure(text=row.flag or "-")
                labels[1].configure(text=display_type(self.app.project, row.input_type))
                labels[2].configure(text=default_display_text(row))
                labels[3].configure(text=row.description or "-")
        for state in self._rows[len(visible_rows):]:
            frame = state.get("frame")
            if isinstance(frame, ttk.Frame):
                frame.grid_remove()
        self.canvas.yview_moveto(0)
        self.canvas.xview_moveto(0)
        self.canvas.after_idle(self._update_wraplengths)

    def clear(self) -> None:
        self.set_rows([])

    def _update_wraplengths(self) -> None:
        for state in self._rows:
            labels = state.get("labels")
            if isinstance(labels, list) and len(labels) == 4:
                labels[3].configure(wraplength=0)
        try:
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        except tk.TclError:
            return


class ParameterEditorDialog:
    def __init__(
        self,
        app,
        parent: tk.Misc,
        title: str,
        rows: list[ParameterEditorRow],
        *,
        allow_add: bool = True,
        validation_callback=None,
    ) -> None:
        self.app = app
        self.parent = parent.winfo_toplevel()
        self.result: list[ParameterEditorRow] | None = None
        self._source_rows = deepcopy(rows)
        self._rows = deepcopy(rows)
        self._row_state: list[dict[str, object]] = []
        self._validation_callback = validation_callback
        self.environment_options = environment_titles(app.project)
        self.type_options = input_type_options(app.project)

        self.window = tk.Toplevel(self.parent)
        self.window.title(title)
        self.window.geometry("1180x680")
        self.window.minsize(880, 520)
        self.window.transient(self.parent)
        self.window.grab_set()
        self.window.protocol("WM_DELETE_WINDOW", self._cancel)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)

        box = ttk.LabelFrame(self.window, text="Job entries", padding=12)
        box.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(1, weight=1)

        header = ttk.Frame(box)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self._configure_columns(header)
        headings = ("Flag", "Input type", "Default", "Description", "")
        for column, heading in enumerate(headings):
            padx = (0, 8) if column < 4 else (0, 0)
            ttk.Label(header, text=heading).grid(row=0, column=column, sticky="w", padx=padx)

        self.canvas = tk.Canvas(box, highlightthickness=0)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(box, orient="vertical", command=self.canvas.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        xscrollbar = ttk.Scrollbar(box, orient="horizontal", command=self.canvas.xview)
        xscrollbar.grid(row=2, column=0, sticky="ew")
        self.canvas.configure(yscrollcommand=scrollbar.set, xscrollcommand=xscrollbar.set)
        self.rows_frame = ttk.Frame(self.canvas)
        self._configure_columns(self.rows_frame)
        self.canvas_window = self.canvas.create_window((0, 0), window=self.rows_frame, anchor="nw")
        bind_scrollable_canvas(self.canvas, self.canvas_window, self.rows_frame, allow_horizontal=True)

        self.validation_var = tk.StringVar()
        ttk.Label(box, textvariable=self.validation_var, style="Error.TLabel").grid(row=3, column=0, sticky="w", pady=(8, 0))

        actions = ttk.Frame(self.window, padding=(12, 0, 12, 12))
        actions.grid(row=1, column=0, sticky="ew")
        actions.columnconfigure(0, weight=1)
        self.add_button = ttk.Button(actions, text="Add parameter row", command=self._add_row)
        if allow_add:
            self.add_button.grid(row=0, column=0, sticky="w")
        ttk.Button(actions, text="Cancel", command=self._cancel).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(actions, text="Save", command=self._save).grid(row=0, column=2, padx=(8, 0))

        self._render_rows()

    def show(self) -> list[ParameterEditorRow] | None:
        self.window.wait_window()
        return self.result

    def _configure_columns(self, frame: ttk.Frame) -> None:
        frame.columnconfigure(0, weight=0, minsize=220)
        frame.columnconfigure(1, weight=0, minsize=190)
        frame.columnconfigure(2, weight=0, minsize=260)
        frame.columnconfigure(3, weight=1, minsize=420)
        frame.columnconfigure(4, weight=0, minsize=42)

    def _new_empty_row(self) -> ParameterEditorRow:
        existing = {row.key for row in self._rows}
        index = 1
        while f"custom__{index}" in existing:
            index += 1
        return ParameterEditorRow(key=f"custom__{index}", custom=True)

    def _add_row(self) -> None:
        self._collect_rows()
        self._rows.append(self._new_empty_row())
        self._render_rows()
        self.canvas.yview_moveto(1)

    def _remove_row(self, index: int) -> None:
        self._collect_rows()
        if not (0 <= index < len(self._rows)):
            return
        if self._rows[index].custom:
            self._rows.pop(index)
        else:
            self._rows[index].removed = True
        self._render_rows()

    def _render_rows(self) -> None:
        for child in self.rows_frame.winfo_children():
            child.destroy()
        self._row_state.clear()
        visible_index = 0
        for source_index, row in enumerate(self._rows):
            if row.removed:
                continue
            row_frame = ttk.Frame(self.rows_frame)
            row_frame.grid(row=visible_index, column=0, columnspan=5, sticky="ew")
            self._configure_columns(row_frame)

            flag_var = tk.StringVar(value=row.flag)
            type_var = tk.StringVar(value=display_type(self.app.project, row.input_type))
            default_text_var = tk.StringVar(value=row.default)
            default_bool_var = tk.BooleanVar(value=str(row.default).lower() in {"1", "true", "yes", "on"})
            pattern_var = tk.StringVar(value=row.extra.get("pattern", ""))
            description_var = tk.StringVar(value=row.description)

            flag_entry = ttk.Entry(row_frame, textvariable=flag_var, width=26)
            flag_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8), pady=4)
            if not row.editable_flag:
                flag_entry.configure(state="disabled")

            type_combo = ttk.Combobox(
                row_frame,
                textvariable=type_var,
                state="readonly",
                values=self.type_options,
                width=20,
            )
            type_combo.grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=4)
            if not row.editable_input_type:
                type_combo.configure(state="disabled")

            default_cell = ttk.Frame(row_frame)
            default_cell.grid(row=0, column=2, sticky="ew", padx=(0, 8), pady=4)
            default_cell.columnconfigure(0, weight=1)
            self._build_default_widget(default_cell, row, type_var, default_text_var, default_bool_var, pattern_var)
            type_combo.bind(
                "<<ComboboxSelected>>",
                lambda _event, current_index=source_index: self._on_type_changed(current_index),
            )

            ttk.Entry(row_frame, textvariable=description_var, width=56).grid(row=0, column=3, sticky="ew", padx=(0, 8), pady=4)
            if row.removable:
                ttk.Button(row_frame, text="-", width=3, command=lambda index=source_index: self._remove_row(index)).grid(
                    row=0,
                    column=4,
                    sticky="w",
                    pady=4,
                )
            self._row_state.append(
                {
                    "source_index": source_index,
                    "flag_var": flag_var,
                    "type_var": type_var,
                    "default_text_var": default_text_var,
                    "default_bool_var": default_bool_var,
                    "pattern_var": pattern_var,
                    "description_var": description_var,
                }
            )
            visible_index += 1
        self._update_validation()

    def _build_default_widget(
        self,
        parent: ttk.Frame,
        row: ParameterEditorRow,
        type_var: tk.StringVar,
        default_text_var: tk.StringVar,
        default_bool_var: tk.BooleanVar,
        pattern_var: tk.StringVar,
    ) -> None:
        input_type = stored_type(self.app.project, type_var.get())
        if input_type == "bool":
            ttk.Checkbutton(parent, variable=default_bool_var).grid(row=0, column=0, sticky="w")
        elif input_type == "environment":
            value = default_text_var.get()
            if value not in set(self.environment_options):
                default_text_var.set("None")
            ttk.Combobox(parent, textvariable=default_text_var, state="readonly", values=self.environment_options).grid(
                row=0,
                column=0,
                sticky="ew",
            )
        elif input_type == "choice":
            ttk.Combobox(parent, textvariable=default_text_var, state="readonly", values=row.options).grid(
                row=0,
                column=0,
                sticky="ew",
            )
        elif input_type.startswith("ts_"):
            ttk.Label(parent, text="From TS processing list").grid(row=0, column=0, sticky="w")
            default_text_var.set("")
        else:
            ttk.Entry(parent, textvariable=default_text_var).grid(row=0, column=0, sticky="ew")
            if input_type in {"path"} or input_type.startswith("all_files_"):
                ttk.Button(parent, text="Browse dir", command=lambda: self._browse(default_text_var, "dir")).grid(
                    row=0,
                    column=1,
                    padx=(8, 0),
                )
                if input_type == "all_files_custom_pattern":
                    ttk.Label(parent, text="Pattern").grid(row=0, column=2, padx=(8, 4), sticky="w")
                    ttk.Entry(parent, textvariable=pattern_var, width=18).grid(row=0, column=3, sticky="ew")
            elif input_type == "file":
                ttk.Button(parent, text="Browse file", command=lambda: self._browse(default_text_var, "file")).grid(
                    row=0,
                    column=1,
                    padx=(8, 0),
                )

    def _browse(self, variable: tk.StringVar, mode: str) -> None:
        path = filedialog.askdirectory(title="Select directory") if mode == "dir" else filedialog.askopenfilename(title="Select file")
        if path:
            variable.set(path)

    def _on_type_changed(self, _source_index: int) -> None:
        self._collect_rows()
        self._render_rows()

    def _collect_rows(self) -> None:
        for state in self._row_state:
            source_index = state.get("source_index")
            if not isinstance(source_index, int) or not (0 <= source_index < len(self._rows)):
                continue
            row = self._rows[source_index]
            flag_var = state.get("flag_var")
            type_var = state.get("type_var")
            default_text_var = state.get("default_text_var")
            default_bool_var = state.get("default_bool_var")
            pattern_var = state.get("pattern_var")
            description_var = state.get("description_var")
            if isinstance(flag_var, tk.StringVar) and row.editable_flag:
                row.flag = flag_var.get().strip()
            if isinstance(type_var, tk.StringVar) and row.editable_input_type:
                row.input_type = stored_type(self.app.project, type_var.get().strip() or "text")
            if row.input_type == "bool" and isinstance(default_bool_var, tk.BooleanVar):
                row.default = "true" if default_bool_var.get() else ""
            elif isinstance(default_text_var, tk.StringVar):
                row.default = default_text_var.get().strip()
            if row.input_type == "all_files_custom_pattern" and isinstance(pattern_var, tk.StringVar):
                pattern = pattern_var.get().strip()
                row.extra = {"pattern": pattern} if pattern else {}
            elif row.input_type != "all_files_custom_pattern":
                row.extra = {}
            if isinstance(description_var, tk.StringVar):
                row.description = description_var.get().strip()

    def _validation_message(self) -> str:
        if self._validation_callback is None:
            return ""
        return str(self._validation_callback([row for row in self._rows if not row.removed]) or "")

    def _update_validation(self) -> None:
        self.validation_var.set(self._validation_message())

    def _save(self) -> None:
        self._collect_rows()
        message = self._validation_message()
        if message:
            self.validation_var.set(message)
            return
        self.result = deepcopy(self._rows)
        self.window.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.window.destroy()
