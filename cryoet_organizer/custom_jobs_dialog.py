from __future__ import annotations

from copy import deepcopy
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from cryoet_organizer.dialogs import bind_scrollable_canvas, choose_items_dialog, make_copy_name
from cryoet_organizer.custom_jobs import (
    CUSTOM_JOBS_SUFFIX,
    CustomJobDefinition,
    CustomJobParameter,
    export_custom_jobs,
    get_project_custom_jobs,
    import_custom_jobs,
    set_project_custom_jobs,
)
from cryoet_organizer.environments import environment_titles
from cryoet_organizer.tabs.custom import (
    runtime_input_type_options,
    display_input_type,
    stored_input_type,
)
from cryoet_organizer.settings_shell import decorate_settings_window


class CustomJobsDialog:
    def __init__(self, app, host: tk.Misc | None = None) -> None:
        self.app = app
        self.jobs = deepcopy(get_project_custom_jobs(app.project))
        self.saved_jobs = deepcopy(self.jobs)
        self.current_index: int | None = None
        self.parameter_rows: list[dict[str, tk.Variable]] = []
        self.validation_var = tk.StringVar()
        self.environment_var = tk.StringVar(value="None")
        self.embedded = host is not None

        self.window = host if host is not None else tk.Toplevel(app.root)
        if not self.embedded:
            self.window.title("Manage custom job types")
            self.window.geometry("1100x720")
            self.window.minsize(820, 520)
            self.window.transient(app.root)
            self.window.grab_set()
            self.window.protocol("WM_DELETE_WINDOW", self.window.destroy)
        content_row = 0 if self.embedded else 1
        footer_row = content_row + 1
        self.window.columnconfigure(1, weight=1)
        self.window.rowconfigure(content_row, weight=1)

        if not self.embedded:
            toolbar = ttk.Frame(self.window, padding=12)
            toolbar.grid(row=0, column=0, columnspan=2, sticky="ew")
            toolbar.columnconfigure(0, weight=1)

        left = ttk.LabelFrame(self.window, text="Custom job types", padding=12)
        left.grid(row=content_row, column=0, sticky="nsw", padx=(12, 8), pady=(0, 12))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        self.listbox = tk.Listbox(left, selectmode="extended", exportselection=False, width=34)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        left_scroll = ttk.Scrollbar(left, orient="vertical", command=self.listbox.yview)
        left_scroll.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=left_scroll.set)
        left_actions = ttk.Frame(left)
        left_actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        left_actions.columnconfigure(0, weight=1)
        ttk.Button(left_actions, text="Clone entry", command=self._clone_selected).grid(row=0, column=0, sticky="w")
        ttk.Button(left_actions, text="Remove selected", command=self._remove_selected).grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.listbox.bind("<<ListboxSelect>>", self._on_selection_changed)

        right = ttk.LabelFrame(self.window, text="Edit selected custom job type", padding=12)
        right.grid(row=content_row, column=1, sticky="nsew", padx=(0, 12), pady=(0, 12))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        self.name_var = tk.StringVar()
        top = ttk.Frame(right)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Job name").grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(top, textvariable=self.name_var).grid(row=0, column=1, sticky="ew", pady=(0, 8))
        self.name_entry = top.grid_slaves(row=0, column=1)[0]
        ttk.Label(top, text="Default local environment").grid(row=1, column=0, sticky="w", pady=(0, 4))
        self.environment_combo = ttk.Combobox(
            top,
            textvariable=self.environment_var,
            state="readonly",
            values=environment_titles(self.app.project),
        )
        self.environment_combo.grid(row=1, column=1, sticky="ew", pady=(0, 8))
        ttk.Label(top, text="Description").grid(row=2, column=0, sticky="nw", pady=(0, 4))
        self.description_text = tk.Text(top, height=4, wrap="word")
        self.description_text.grid(row=2, column=1, sticky="ew", pady=(0, 8))
        ttk.Label(top, text="Command template").grid(row=3, column=0, sticky="nw", pady=(0, 4))
        self.command_text = tk.Text(top, height=3, wrap="word")
        self.command_text.grid(row=3, column=1, sticky="ew")
        ttk.Label(
            top,
            textvariable=self.validation_var,
            style="Error.TLabel",
            wraplength=720,
            justify="left",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.empty_hint_var = tk.StringVar(value="")
        ttk.Label(
            top,
            textvariable=self.empty_hint_var,
            style="Error.TLabel",
            wraplength=720,
            justify="left",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))

        params_box = ttk.LabelFrame(right, text="Custom parameters", padding=12)
        params_box.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        params_box.columnconfigure(0, weight=1)
        params_box.rowconfigure(0, weight=1)
        self.params_canvas = tk.Canvas(params_box, highlightthickness=0)
        self.params_canvas.grid(row=0, column=0, sticky="nsew")
        params_scroll = ttk.Scrollbar(params_box, orient="vertical", command=self.params_canvas.yview)
        params_scroll.grid(row=0, column=1, sticky="ns")
        params_xscroll = ttk.Scrollbar(params_box, orient="horizontal", command=self.params_canvas.xview)
        params_xscroll.grid(row=1, column=0, sticky="ew")
        self.params_canvas.configure(yscrollcommand=params_scroll.set)
        self.params_canvas.configure(xscrollcommand=params_xscroll.set)
        self.params_frame = ttk.Frame(self.params_canvas)
        for column in range(4):
            self.params_frame.columnconfigure(column, weight=1 if column in {0, 1, 3} else 0)
        self.params_window = self.params_canvas.create_window((0, 0), window=self.params_frame, anchor="nw")
        bind_scrollable_canvas(self.params_canvas, self.params_window, self.params_frame, allow_horizontal=True)

        actions = ttk.Frame(right)
        actions.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        actions.columnconfigure(0, weight=1)
        ttk.Button(actions, text="Add parameter row", command=self._add_row).grid(row=0, column=0, sticky="w")
        ttk.Button(actions, text="Apply edits", command=self._apply_current).grid(row=0, column=1, padx=(8, 0))

        buttons = ttk.Frame(self.window, padding=(12, 0, 12, 12))
        buttons.grid(row=footer_row, column=0, columnspan=2, sticky="ew")
        buttons.columnconfigure(0, weight=1)
        cancel_label = "Revert section" if self.embedded else "Cancel"
        save_label = "Save section" if self.embedded else "Save"
        ttk.Button(buttons, text=cancel_label, command=self._cancel).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(buttons, text=save_label, command=self._save).grid(row=0, column=2, padx=(8, 0))

        self._refresh_list()
        if not self.embedded:
            decorate_settings_window(self, "custom_job_types")

    def _refresh_list(self) -> None:
        self.listbox.delete(0, "end")
        for job in self.jobs:
            self.listbox.insert("end", job.name)
        if self.jobs and self.current_index is None:
            self.listbox.selection_set(0)
            self._on_selection_changed()
        elif not self.jobs:
            self._clear_editor()
            self._update_editor_state()

    def _selected_indices(self) -> list[int]:
        return sorted(self.listbox.curselection(), reverse=True)

    def _display_input_type(self, stored: str) -> str:
        return display_input_type(self.app.project, stored)

    def _stored_input_type(self, displayed: str) -> str:
        return stored_input_type(self.app.project, displayed)

    def _new_row(self, parameter: CustomJobParameter | None = None) -> dict[str, tk.Variable]:
        parameter = parameter or CustomJobParameter(key="", label="", flag="", widget="text", default="")
        return {
            "label": tk.StringVar(value=parameter.label),
            "flag": tk.StringVar(value=parameter.flag),
            "widget": tk.StringVar(value=self._display_input_type(parameter.widget)),
            "default_text": tk.StringVar(value=parameter.default),
            "default_bool": tk.BooleanVar(value=parameter.default.lower() in {"1", "true", "yes", "on"}),
            "pattern_text": tk.StringVar(value=parameter.extra.get("pattern", "")),
        }

    def _render_rows(self) -> None:
        for child in self.params_frame.winfo_children():
            child.destroy()
        headings = ("Description", "Flag", "Input type", "Default", "")
        for column, heading in enumerate(headings):
            ttk.Label(self.params_frame, text=heading).grid(row=0, column=column, sticky="w", padx=(0, 8))
        for row_index, row in enumerate(self.parameter_rows, start=1):
            ttk.Entry(self.params_frame, textvariable=row["label"]).grid(row=row_index, column=0, sticky="ew", padx=(0, 8), pady=4)
            ttk.Entry(self.params_frame, textvariable=row["flag"]).grid(row=row_index, column=1, sticky="ew", padx=(0, 8), pady=4)
            combo = ttk.Combobox(
                self.params_frame,
                textvariable=row["widget"],
                state="readonly",
                values=runtime_input_type_options(self.app.project),
                width=34,
            )
            combo.grid(row=row_index, column=2, sticky="ew", padx=(0, 8), pady=4)
            combo.bind("<<ComboboxSelected>>", lambda _event, current=row: self._on_row_type_changed(current))
            default_cell = ttk.Frame(self.params_frame)
            default_cell.grid(row=row_index, column=3, sticky="ew", padx=(0, 8), pady=4)
            default_cell.columnconfigure(0, weight=1)
            widget = self._stored_input_type(str(row["widget"].get()))
            if widget == "text":
                ttk.Entry(default_cell, textvariable=row["default_text"]).grid(row=0, column=0, sticky="ew")
            elif widget == "path":
                ttk.Entry(default_cell, textvariable=row["default_text"]).grid(row=0, column=0, sticky="ew")
                ttk.Button(default_cell, text="Browse dir", command=lambda current=row["default_text"]: self._browse_default(current, "dir")).grid(row=0, column=1, padx=(8, 0))
            elif widget == "file":
                ttk.Entry(default_cell, textvariable=row["default_text"]).grid(row=0, column=0, sticky="ew")
                ttk.Button(default_cell, text="Browse file", command=lambda current=row["default_text"]: self._browse_default(current, "file")).grid(row=0, column=1, padx=(8, 0))
            elif widget == "bool":
                ttk.Checkbutton(default_cell, variable=row["default_bool"]).grid(row=0, column=0, sticky="w")
            elif widget.startswith("ts_"):
                ttk.Label(default_cell, text="From TS processing list").grid(row=0, column=0, sticky="w")
            elif widget == "all_files_custom_pattern":
                ttk.Entry(default_cell, textvariable=row["default_text"]).grid(row=0, column=0, sticky="ew")
                ttk.Button(default_cell, text="Browse dir", command=lambda current=row["default_text"]: self._browse_default(current, "dir")).grid(row=0, column=1, padx=(8, 0))
                ttk.Label(default_cell, text="Pattern").grid(row=0, column=2, padx=(8, 4), sticky="w")
                ttk.Entry(default_cell, textvariable=row["pattern_text"], width=18).grid(row=0, column=3, padx=(0, 0))
            else:
                ttk.Entry(default_cell, textvariable=row["default_text"]).grid(row=0, column=0, sticky="ew")
                ttk.Button(default_cell, text="Browse dir", command=lambda current=row["default_text"]: self._browse_default(current, "dir")).grid(row=0, column=1, padx=(8, 0))
            ttk.Button(self.params_frame, text="Remove", command=lambda current=row: self._remove_row(current)).grid(row=row_index, column=4, sticky="w", pady=4)
        self._update_validation()
        self._update_editor_state()

    def _browse_default(self, variable: tk.Variable, mode: str) -> None:
        path = filedialog.askdirectory(title="Select directory") if mode == "dir" else filedialog.askopenfilename(title="Select file")
        if path:
            variable.set(path)

    def _on_row_type_changed(self, _row: dict[str, tk.Variable]) -> None:
        self._render_rows()

    def _add_row(self) -> None:
        self.parameter_rows.append(self._new_row())
        self._render_rows()

    def _remove_row(self, row: dict[str, tk.Variable]) -> None:
        self.parameter_rows = [item for item in self.parameter_rows if item is not row]
        self._render_rows()

    def _validation_message(self) -> str:
        widgets = [
            self._stored_input_type(str(row["widget"].get()).strip() or "text")
            for row in self.parameter_rows
            if str(row["label"].get()).strip() or str(row["flag"].get()).strip()
        ]
        ts_count = sum(widget.startswith("ts_") for widget in widgets)
        all_files_count = sum(widget.startswith("all_files_") for widget in widgets)
        if ts_count and all_files_count:
            return "TS selection parameters cannot be combined with 'All files that...' parameters in the same custom job type."
        if all_files_count > 1:
            return "Only one 'All files that...' parameter is currently supported in a custom job type."
        return ""

    def _update_validation(self) -> None:
        self.validation_var.set(self._validation_message())

    def _persist_current_from_editor(self) -> None:
        if self.current_index is None or not (0 <= self.current_index < len(self.jobs)):
            return
        parameters: list[CustomJobParameter] = []
        for index, row in enumerate(self.parameter_rows, start=1):
            label = str(row["label"].get()).strip()
            flag = str(row["flag"].get()).strip()
            widget = self._stored_input_type(str(row["widget"].get()).strip() or "text")
            default_text = str(row["default_text"].get()).strip()
            default_bool = bool(row["default_bool"].get())
            pattern_text = str(row["pattern_text"].get()).strip()
            if not label and not flag and not default_text and not default_bool and not pattern_text and widget == "text":
                continue
            default_value = "true" if widget == "bool" and default_bool else default_text
            extra = {"pattern": pattern_text} if widget == "all_files_custom_pattern" and pattern_text else {}
            if widget.startswith("ts_"):
                default_value = ""
            parameters.append(
                CustomJobParameter(
                    key=f"param_{index}",
                    label=label,
                    flag=flag,
                    widget=widget,
                    default=default_value,
                    extra=extra,
                )
            )
        self.jobs[self.current_index] = CustomJobDefinition(
            name=self.name_var.get().strip(),
            description=self.description_text.get("1.0", "end").strip(),
            command_template=self.command_text.get("1.0", "end").strip(),
            environment_title=self.environment_var.get().strip() or "None",
            parameters=parameters,
        )

    def _load_selected_job(self, index: int) -> None:
        job = self.jobs[index]
        self.current_index = index
        self.name_var.set(job.name)
        available_environments = set(environment_titles(self.app.project))
        self.environment_var.set(
            job.environment_title if job.environment_title in available_environments else "None"
        )
        self.description_text.delete("1.0", "end")
        self.description_text.insert("1.0", job.description)
        self.command_text.delete("1.0", "end")
        self.command_text.insert("1.0", job.command_template)
        self.parameter_rows = [self._new_row(parameter) for parameter in job.parameters] or [self._new_row()]
        self._render_rows()
        self._update_editor_state()

    def _on_selection_changed(self, _event=None) -> None:
        indices = self.listbox.curselection()
        if len(indices) != 1:
            self.current_index = None
            self._clear_editor()
            self._update_editor_state()
            return
        self._load_selected_job(indices[0])

    def _remove_selected(self) -> None:
        indices = self._selected_indices()
        if not indices:
            messagebox.showinfo("Remove custom jobs", "Please select one or more custom job types first.")
            return
        for index in indices:
            self.jobs.pop(index)
        self.current_index = None
        self._refresh_list()
        self._update_editor_state()

    def _clone_selected(self) -> None:
        indices = list(self.listbox.curselection())
        if len(indices) != 1:
            messagebox.showinfo("Clone custom job", "Please select exactly one custom job type first.", parent=self.window)
            return
        if self._validation_message():
            self.validation_var.set(self._validation_message())
            return
        if self.current_index is not None:
            self._persist_current_from_editor()
        index = indices[0]
        if not (0 <= index < len(self.jobs)):
            return
        cloned = deepcopy(self.jobs[index])
        cloned.name = make_copy_name([job.name for job in self.jobs], cloned.name)
        self.jobs.append(cloned)
        self.current_index = len(self.jobs) - 1
        self._refresh_list()
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(self.current_index)
        self._load_selected_job(self.current_index)

    def _clear_editor(self) -> None:
        self.name_var.set("")
        self.environment_var.set("None")
        self.description_text.configure(state="normal")
        self.description_text.delete("1.0", "end")
        self.command_text.configure(state="normal")
        self.command_text.delete("1.0", "end")
        self.parameter_rows = []
        self._render_rows()

    def _update_editor_state(self) -> None:
        enabled = self.current_index is not None and 0 <= self.current_index < len(self.jobs)
        state = "normal" if enabled else "disabled"
        combo_state = "readonly" if enabled else "disabled"
        try:
            self.name_entry.configure(state=state)
        except Exception:
            pass
        self.environment_combo.configure(state=combo_state)
        self.description_text.configure(state=state)
        self.command_text.configure(state=state)
        for child in self.params_frame.winfo_children():
            try:
                if isinstance(child, ttk.Combobox):
                    child.configure(state=combo_state)
                elif isinstance(child, (ttk.Entry, ttk.Button, ttk.Checkbutton)):
                    child.configure(state=state)
            except Exception:
                pass
        self.empty_hint_var.set("" if enabled else "Please select a custom job type or create a new one.")

    def _import_jobs(self) -> None:
        path = filedialog.askopenfilename(
            title="Import custom job types",
            filetypes=[("CryoPal_tomo custom jobs", f"*{CUSTOM_JOBS_SUFFIX}"), ("JSON", "*.json")],
        )
        if not path:
            return
        try:
            imported = import_custom_jobs(path)
        except Exception as exc:
            messagebox.showerror("Import failed", str(exc))
            return
        if not imported:
            messagebox.showinfo("Import custom jobs", "No compatible custom jobs were found in this file.")
            return
        selected = choose_items_dialog(
            self.window,
            "Import custom jobs",
            "Select which custom jobs should be imported.",
            [(job.name, job.name) for job in imported],
        )
        if selected is None:
            return
        imported_by_name = {job.name: job for job in imported}
        existing_by_name = {job.name.casefold(): index for index, job in enumerate(self.jobs)}
        for job_name in selected:
            job = deepcopy(imported_by_name[job_name])
            existing_index = existing_by_name.get(job.name.casefold())
            if existing_index is not None:
                overwrite = messagebox.askyesno(
                    "Duplicate custom job name",
                    f"{job.name} already exists.\n\nChoose 'Yes' to overwrite it or 'No' to skip this job.",
                    icon="warning",
                    parent=self.window,
                )
                if not overwrite:
                    continue
                self.jobs[existing_index] = job
            else:
                self.jobs.append(job)
                existing_by_name[job.name.casefold()] = len(self.jobs) - 1
        self.current_index = None
        self._refresh_list()
        self.app.status_var.set("Imported custom job types")

    def _export_jobs(self) -> None:
        if not self.jobs:
            messagebox.showinfo("Export custom jobs", "No custom job types available to export.")
            return
        current_selection = {
            self.jobs[index].name
            for index in self.listbox.curselection()
            if 0 <= index < len(self.jobs)
        }
        selected = choose_items_dialog(
            self.window,
            "Export custom jobs",
            "Select which custom jobs should be exported.",
            [(job.name, job.name) for job in self.jobs],
            preselected=current_selection or None,
        )
        if selected is None:
            return
        export_items = [deepcopy(job) for job in self.jobs if job.name in set(selected)]
        if not export_items:
            messagebox.showinfo("Export custom jobs", "No custom job types were selected for export.")
            return
        path = filedialog.asksaveasfilename(
            title="Export custom job types",
            defaultextension=CUSTOM_JOBS_SUFFIX,
            filetypes=[("CryoPal_tomo custom jobs", f"*{CUSTOM_JOBS_SUFFIX}"), ("JSON", "*.json")],
        )
        if not path:
            return
        try:
            export_custom_jobs(path, export_items)
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))
            return
        self.app.status_var.set("Exported custom job types")

    def _apply_current(self) -> None:
        if self.current_index is None:
            messagebox.showinfo("Apply edits", "Please select exactly one custom job type first.")
            return
        if self._validation_message():
            self.validation_var.set(self._validation_message())
            return
        self._persist_current_from_editor()
        current_name = self.jobs[self.current_index].name
        self._refresh_list()
        self.listbox.selection_set(self.current_index)
        self._load_selected_job(self.current_index)
        self.app.status_var.set(f"Applied edits to custom job type: {current_name}")

    def _save(self) -> None:
        self.save_section(close_window=False)

    def save_section(self, *, close_window: bool = False) -> bool:
        if self.current_index is not None and self._validation_message():
            self.validation_var.set(self._validation_message())
            return False
        if self.current_index is not None:
            self._persist_current_from_editor()
        set_project_custom_jobs(self.app.project, self.jobs)
        self.saved_jobs = deepcopy(self.jobs)
        self.app._apply_project_to_tabs()
        self.app._update_title()
        self.app.status_var.set("Saved custom job types")
        if close_window:
            self.window.destroy()
        return True

    def has_unsaved_changes(self) -> bool:
        if self.current_index is not None and not self._validation_message():
            self._persist_current_from_editor()
        return self.jobs != self.saved_jobs

    def _cancel(self) -> None:
        self.jobs = deepcopy(self.saved_jobs)
        self.current_index = None
        self._refresh_list()
