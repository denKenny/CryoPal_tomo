from __future__ import annotations

from copy import copy, deepcopy
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from cryoet_organizer.dialogs import bind_scrollable_canvas, choose_items_dialog
from cryoet_organizer.environments import environment_titles
from cryoet_organizer.executables import (
    build_executable_usage_registry,
    get_project_executable_overrides,
    set_project_executable_overrides,
)
from cryoet_organizer.job_defaults import (
    CUSTOM_FIELD_PREFIX,
    build_job_default_registry,
    effective_job_default_definition,
    get_project_job_default_overrides,
    imported_file_registry_patterns,
    import_settings_payload,
    job_override_key,
    registry_lookup,
    set_project_job_default_overrides,
)
from cryoet_organizer.parameter_editor import ParameterEditorDialog, ParameterEditorRow, ParameterSummaryTable
from cryoet_organizer.performance import perf_timer
from cryoet_organizer.file_resolver import essential_file_roles, file_role_config, set_file_role_config
from cryoet_organizer.settings_shell import decorate_settings_window
from cryoet_organizer.tabs.custom import display_input_type, runtime_input_type_options, stored_input_type
import json
from pathlib import Path
from cryoet_organizer.project import SETTINGS_SUFFIX


MANAGE_EXECUTABLES_LEAF = "__manage_executables__"


class DefaultParametersDialog:
    def __init__(self, app, host: tk.Misc | None = None) -> None:
        self.app = app
        self.registry = build_job_default_registry()
        self.lookup = registry_lookup()
        self.overrides = deepcopy(get_project_job_default_overrides(app.project))
        self.saved_overrides = deepcopy(self.overrides)
        self.executable_overrides = deepcopy(get_project_executable_overrides(app.project))
        self.saved_executable_overrides = deepcopy(self.executable_overrides)
        self.executable_registry = build_executable_usage_registry()
        self.file_registry_patterns = {
            role: file_role_config(app.project, role)
            for role in essential_file_roles()
        }
        self.saved_file_registry_patterns = deepcopy(self.file_registry_patterns)
        self.current_leaf: tuple[str, str, str] | str | None = None
        self.row_state: dict[str, dict[str, object]] = {}
        self.executable_row_state: dict[str, tk.StringVar] = {}
        self.environment_title_options = environment_titles(app.project)
        self._pending_leaf_after: str | None = None
        self._form_rows: list[dict[str, object]] = []
        self._executable_form_rows: list[dict[str, object]] = []
        self._summary_rows_cache: dict[tuple[str, str, str], list[ParameterEditorRow]] = {}
        self.job_edit_mode = False
        self.embedded = host is not None

        self.window = host if host is not None else tk.Toplevel(app.root)
        if not self.embedded:
            self.window.title("Set default parameters")
            self.window.geometry("1180x720")
            self.window.transient(app.root)
            self.window.grab_set()
            self.window.protocol("WM_DELETE_WINDOW", self.window.destroy)
        self.window.columnconfigure(0, weight=0, minsize=320)
        self.window.columnconfigure(1, weight=1)
        content_row = 0 if self.embedded else 1
        footer_row = content_row + 1
        self.window.rowconfigure(content_row, weight=1)

        if not self.embedded:
            toolbar = ttk.Frame(self.window, padding=12)
            toolbar.grid(row=0, column=0, columnspan=2, sticky="ew")
            toolbar.columnconfigure(0, weight=1)

        tree_box = ttk.Frame(self.window, padding=(12, 0, 0, 12))
        tree_box.grid(row=content_row, column=0, sticky="nsw")
        tree_box.columnconfigure(0, weight=1)
        tree_box.rowconfigure(1, weight=1)
        self.manage_executables_button = ttk.Button(
            tree_box,
            text="Manage Executables",
            command=self._select_manage_executables,
        )
        self.manage_executables_button.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        self.tree = ttk.Treeview(tree_box, show="tree", style="Technical.Treeview")
        self.tree.grid(row=1, column=0, sticky="nsw")
        self.tree.column("#0", width=270, minwidth=250, stretch=True)
        tree_scroll = ttk.Scrollbar(tree_box, orient="vertical", command=self.tree.yview)
        tree_scroll.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_selected)

        right = ttk.Frame(self.window, padding=(0, 0, 12, 12))
        right.grid(row=content_row, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        self.title_label = ttk.Label(right, text="Select a job on the left", style="Heading.TLabel")
        self.title_label.grid(row=0, column=0, sticky="w", pady=(0, 8))

        self.content_box = ttk.LabelFrame(right, text="Job defaults", padding=12)
        self.content_box.grid(row=1, column=0, sticky="nsew")
        self.content_box.columnconfigure(0, weight=1)
        self.content_box.rowconfigure(1, weight=1)

        self.summary_table = ParameterSummaryTable(self.content_box, self.app)
        self.summary_table.grid(row=0, column=0, rowspan=3, columnspan=2, sticky="nsew")

        self.header_frame = ttk.Frame(self.content_box)
        self.header_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.header_frame.columnconfigure(0, weight=0, minsize=210)
        self.header_frame.columnconfigure(1, weight=0, minsize=170)
        self.header_frame.columnconfigure(2, weight=0, minsize=210)
        self.header_frame.columnconfigure(3, weight=1, minsize=360)
        self.header_col0_label = ttk.Label(self.header_frame, text="Flag", width=24)
        self.header_col0_label.grid(row=0, column=0, sticky="w")
        self.header_col1_label = ttk.Label(self.header_frame, text="Input type", width=20)
        self.header_col1_label.grid(row=0, column=1, sticky="w", padx=(12, 8))
        self.header_col2_label = ttk.Label(self.header_frame, text="Default", width=24)
        self.header_col2_label.grid(row=0, column=2, sticky="w")
        self.header_col3_label = ttk.Label(self.header_frame, text="Description", width=28)
        self.header_col3_label.grid(row=0, column=3, sticky="w", padx=(8, 0))

        self.canvas = tk.Canvas(self.content_box, highlightthickness=0)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.canvas_scrollbar = ttk.Scrollbar(self.content_box, orient="vertical", command=self.canvas.yview)
        self.canvas_scrollbar.grid(row=1, column=1, sticky="ns")
        self.canvas_xscrollbar = ttk.Scrollbar(self.content_box, orient="horizontal", command=self.canvas.xview)
        self.canvas_xscrollbar.grid(row=2, column=0, sticky="ew")
        self.canvas.configure(yscrollcommand=self.canvas_scrollbar.set, xscrollcommand=self.canvas_xscrollbar.set)
        self.rows_frame = ttk.Frame(self.canvas)
        self.rows_frame.columnconfigure(2, weight=1)
        self.canvas_window = self.canvas.create_window((0, 0), window=self.rows_frame, anchor="nw")
        bind_scrollable_canvas(self.canvas, self.canvas_window, self.rows_frame, allow_horizontal=True)
        self._show_job_summary_surface()

        buttons = ttk.Frame(self.window, padding=(12, 0, 12, 12))
        buttons.grid(row=footer_row, column=0, columnspan=2, sticky="ew")
        buttons.columnconfigure(0, weight=1)
        self.edit_job_entries_button = ttk.Button(
            buttons,
            text="Edit job entries",
            command=self._enable_job_edit_mode,
            state="disabled",
        )
        self.edit_job_entries_button.grid(row=0, column=0, sticky="w")
        self.add_job_parameter_button = ttk.Button(
            buttons,
            text="Add parameter row",
            command=self._add_job_parameter_row,
            state="disabled",
        )
        cancel_label = "Revert section" if self.embedded else "Cancel"
        save_label = "Save section" if self.embedded else "Save"
        ttk.Button(buttons, text=cancel_label, command=self._cancel).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(buttons, text=save_label, command=self._save).grid(row=0, column=2, padx=(8, 0))

        self._build_tree()
        if not self.embedded:
            decorate_settings_window(self, "default_parameters")

    def _build_tree(self) -> None:
        namespace_nodes: dict[str, str] = {}
        grouped_items: dict[tuple[str, str], list] = {}
        for item in self.registry:
            grouped_items.setdefault((item.namespace, item.group), []).append(item)

        for item in self.registry:
            namespace_id = namespace_nodes.get(item.namespace)
            if namespace_id is None:
                namespace_id = self.tree.insert("", "end", text=item.namespace, open=True)
                namespace_nodes[item.namespace] = namespace_id
            leaf_id = job_override_key(item.namespace, item.group, item.job_key)
            group_items = grouped_items[(item.namespace, item.group)]
            if len(group_items) >= 2:
                group_node_id = f"group::{item.namespace}::{item.group}"
                if not self.tree.exists(group_node_id):
                    self.tree.insert(namespace_id, "end", iid=group_node_id, text=item.group, open=True)
                self.tree.insert(group_node_id, "end", iid=leaf_id, text=item.title)
            else:
                self.tree.insert(namespace_id, "end", iid=leaf_id, text=item.title)

    def _persist_current_rows(self) -> None:
        if self.current_leaf == MANAGE_EXECUTABLES_LEAF:
            self._persist_executable_rows()
            return
        if not self.job_edit_mode or not self.row_state:
            return
        if self.current_leaf is None or not isinstance(self.current_leaf, tuple):
            return
        namespace, group, job_key = self.current_leaf
        override_key = job_override_key(namespace, group, job_key)
        job_overrides: dict[str, dict[str, str]] = {}
        definition = self.lookup.get(self.current_leaf)
        fields_by_key = {field.key: field for field in definition.fields} if definition is not None else {}
        for field_key, row in self.row_state.items():
            field = fields_by_key.get(field_key)
            is_custom = bool(row.get("custom"))
            removed_var = row.get("removed_var")
            removed = isinstance(removed_var, tk.BooleanVar) and bool(removed_var.get())
            if removed and field is None:
                continue
            if removed:
                job_overrides[field_key] = {"removed": "true"}
                continue

            description_var = row.get("description_var")
            parameter_var = row.get("parameter_var")
            widget_var = row.get("widget_var")
            default_var = row.get("value_var")
            bool_var = row.get("bool_var")
            if not isinstance(description_var, tk.StringVar) or not isinstance(parameter_var, tk.StringVar):
                continue
            if not isinstance(widget_var, tk.StringVar):
                continue
            base_parameter = self._base_parameter_name(field) if field is not None else ""
            description = description_var.get().strip()
            parameter_name = parameter_var.get().strip()
            widget = self._stored_input_type(widget_var.get().strip() or "text")
            if widget == "bool" and isinstance(bool_var, tk.BooleanVar):
                default_value = "true" if bool_var.get() else ""
            elif isinstance(default_var, tk.StringVar):
                default_value = default_var.get().strip()
            else:
                default_value = ""
            settings: dict[str, str] = {}
            if is_custom:
                if not (description or parameter_name or default_value):
                    continue
                settings["custom"] = "true"
                settings["label"] = description or parameter_name or field_key
                if description:
                    settings["description"] = description
                settings["widget"] = widget
                settings["default"] = default_value
                if parameter_name:
                    settings["parameter"] = parameter_name
            elif field is not None:
                if description and description != self._field_description(field):
                    settings["description"] = description
                if widget != field.widget:
                    settings["widget"] = widget
                if default_value != field.default_value:
                    settings["enabled"] = "true"
                    settings["value"] = default_value
                if parameter_name != base_parameter:
                    settings["parameter"] = parameter_name
            elif parameter_name:
                settings["parameter"] = parameter_name
            if settings:
                job_overrides[field_key] = settings
        if job_overrides:
            self.overrides[override_key] = job_overrides
        else:
            self.overrides.pop(override_key, None)
        self._invalidate_default_summary_cache(self.current_leaf)

    def _persist_executable_rows(self) -> None:
        overrides: dict[str, str] = {}
        for item in self.executable_registry:
            variable = self.executable_row_state.get(item.executable)
            if variable is None:
                continue
            value = variable.get().strip()
            if value and value != item.executable:
                overrides[item.executable] = value
        self.executable_overrides = overrides

    def _on_tree_selected(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        selected = selection[0]
        parts = selected.split("/", 2)
        if len(parts) != 3:
            return
        leaf: tuple[str, str, str] | str = (parts[0], parts[1], parts[2])
        self._queue_leaf(leaf)

    def _select_manage_executables(self) -> None:
        selection = self.tree.selection()
        if selection:
            self.tree.selection_remove(*selection)
        self._queue_leaf(MANAGE_EXECUTABLES_LEAF)

    def _queue_leaf(self, leaf: tuple[str, str, str] | str) -> None:
        if leaf == self.current_leaf:
            return
        self._persist_current_rows()
        self.job_edit_mode = False
        self._blank_parameter_area()
        if self._pending_leaf_after is not None:
            self.window.after_cancel(self._pending_leaf_after)
        self._pending_leaf_after = self.window.after_idle(lambda current_leaf=leaf: self._show_leaf(current_leaf))

    def _blank_parameter_area(self) -> None:
        self.current_leaf = None
        self.title_label.config(text="Loading defaults...")
        self.edit_job_entries_button.configure(state="disabled")
        self.add_job_parameter_button.configure(state="disabled")
        self.summary_table.clear()
        self.canvas.yview_moveto(0)
        self.canvas.xview_moveto(0)
        self._hide_job_rows()
        self._hide_executable_rows()
        self.rows_frame.update_idletasks()

    def _show_job_summary_surface(self) -> None:
        self.header_frame.grid_remove()
        self.canvas.grid_remove()
        self.canvas_scrollbar.grid_remove()
        self.canvas_xscrollbar.grid_remove()
        self.summary_table.grid(row=0, column=0, rowspan=3, columnspan=2, sticky="nsew")

    def _show_executable_surface(self) -> None:
        self.summary_table.grid_remove()
        self.header_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.canvas_scrollbar.grid(row=1, column=1, sticky="ns")
        self.canvas_xscrollbar.grid(row=2, column=0, sticky="ew")

    def _show_leaf(self, leaf: tuple[str, str, str] | str | None) -> None:
        with perf_timer(f"default parameters leaf {leaf}"):
            self._pending_leaf_after = None
            self.current_leaf = leaf
            if leaf == MANAGE_EXECUTABLES_LEAF:
                self.job_edit_mode = False
                self._show_executables_leaf()
                return
            if not isinstance(self.current_leaf, tuple):
                return
            definition = self.lookup.get(self.current_leaf)
            if definition is None:
                return
            self.environment_title_options = environment_titles(self.app.project)
            self._hide_executable_rows()
            self._show_job_summary_surface()
            self.content_box.config(text="Job defaults")
            self.title_label.config(text=f"{definition.namespace} > {definition.group} > {definition.title}")
            self.row_state.clear()
            cached_rows = self._summary_rows_cache.get(self.current_leaf)
            if cached_rows is None:
                effective_definition = self._effective_definition_for_current_overrides(definition)
                cached_rows = self._summary_rows_from_fields(effective_definition.fields)
                self._summary_rows_cache[self.current_leaf] = cached_rows
            self._hide_job_rows()
            self.summary_table.set_rows(cached_rows)
            self.edit_job_entries_button.configure(state="disabled" if self.job_edit_mode else "normal")
            self.add_job_parameter_button.configure(state="disabled")

    def _project_view_for_current_overrides(self):
        project_view = copy(self.app.project)
        state_view = copy(self.app.project.state)
        state_view.job_default_overrides = self.overrides
        project_view.state = state_view
        return project_view

    def _effective_definition_for_current_overrides(self, definition):
        return effective_job_default_definition(self._project_view_for_current_overrides(), definition)

    def _invalidate_default_summary_cache(self, leaf: tuple[str, str, str] | None = None) -> None:
        if leaf is None:
            self._summary_rows_cache.clear()
        else:
            self._summary_rows_cache.pop(leaf, None)

    def _base_parameter_name(self, field) -> str:
        parameter_name = getattr(field, "parameter_name", "") or ""
        return parameter_name or field.label or field.key

    def _field_description(self, field) -> str:
        return (getattr(field, "description", "") or getattr(field, "label", "") or getattr(field, "key", "")).strip()

    def _summary_rows_from_fields(self, fields) -> list[ParameterEditorRow]:
        return [
            ParameterEditorRow(
                key=field.key,
                flag=self._base_parameter_name(field),
                input_type=field.widget,
                default=field.default_value,
                description=self._field_description(field),
                options=tuple(getattr(field, "options", ()) or ()),
                custom=field.key.startswith(CUSTOM_FIELD_PREFIX),
                removable=field.key != "execution_environment",
                editable_flag=field.key != "execution_environment",
                editable_input_type=field.key != "execution_environment",
            )
            for field in fields
        ]

    def _enable_job_edit_mode(self) -> None:
        if not isinstance(self.current_leaf, tuple):
            return
        rows = self._editor_rows_for_current_leaf()
        if rows is None:
            return
        definition = self.lookup.get(self.current_leaf)
        title = "Edit job entries"
        if definition is not None:
            title = f"Edit entries: {definition.namespace} > {definition.group} > {definition.title}"
        dialog = ParameterEditorDialog(self.app, self.window, title, rows)
        edited_rows = dialog.show()
        if edited_rows is None:
            return
        self._apply_editor_rows_to_current_leaf(edited_rows)
        self._show_leaf(self.current_leaf)

    def _editor_rows_for_current_leaf(self) -> list[ParameterEditorRow] | None:
        if not isinstance(self.current_leaf, tuple):
            return None
        definition = self.lookup.get(self.current_leaf)
        if definition is None:
            return None
        effective_definition = self._effective_definition_for_current_overrides(definition)
        rows: list[ParameterEditorRow] = []
        for field in effective_definition.fields:
            rows.append(
                ParameterEditorRow(
                    key=field.key,
                    flag=self._base_parameter_name(field),
                    input_type=field.widget,
                    default=field.default_value,
                    description=self._field_description(field),
                    options=tuple(getattr(field, "options", ()) or ()),
                    custom=field.key.startswith(CUSTOM_FIELD_PREFIX),
                    removable=field.key != "execution_environment",
                    editable_flag=field.key != "execution_environment",
                    editable_input_type=field.key != "execution_environment",
                )
            )
        return rows

    def _apply_editor_rows_to_current_leaf(self, rows: list[ParameterEditorRow]) -> None:
        if not isinstance(self.current_leaf, tuple):
            return
        namespace, group, job_key = self.current_leaf
        override_key = job_override_key(namespace, group, job_key)
        definition = self.lookup.get(self.current_leaf)
        fields_by_key = {field.key: field for field in definition.fields} if definition is not None else {}
        existing = deepcopy(self.overrides.get(override_key, {}))
        job_overrides: dict[str, dict[str, str]] = {
            key: {"removed": "true"}
            for key, value in existing.items()
            if isinstance(value, dict) and value.get("removed", "").lower() in {"1", "true", "yes", "on"}
        }
        for row in rows:
            field = fields_by_key.get(row.key)
            is_custom = row.custom or row.key.startswith(CUSTOM_FIELD_PREFIX) or field is None
            if row.removed:
                if not is_custom:
                    job_overrides[row.key] = {"removed": "true"}
                continue
            settings: dict[str, str] = {}
            if is_custom:
                if not (row.description or row.flag or row.default):
                    continue
                settings["custom"] = "true"
                settings["label"] = row.description or row.flag or row.key
                if row.description:
                    settings["description"] = row.description
                settings["widget"] = row.input_type or "text"
                settings["default"] = row.default
                if row.flag:
                    settings["parameter"] = row.flag
            elif field is not None:
                base_parameter = self._base_parameter_name(field)
                if row.description and row.description != self._field_description(field):
                    settings["description"] = row.description
                if row.input_type != field.widget:
                    settings["widget"] = row.input_type
                if row.default != field.default_value:
                    settings["enabled"] = "true"
                    settings["value"] = row.default
                if row.flag != base_parameter:
                    settings["parameter"] = row.flag
            if settings:
                job_overrides[row.key] = settings
            elif row.key in job_overrides and row.key not in existing:
                job_overrides.pop(row.key, None)
        if job_overrides:
            self.overrides[override_key] = job_overrides
        else:
            self.overrides.pop(override_key, None)
        self._invalidate_default_summary_cache(self.current_leaf)

    def _hide_job_rows(self) -> None:
        for row in self._form_rows:
            frame = row.get("frame")
            if isinstance(frame, ttk.Frame):
                frame.grid_remove()
        self.row_state.clear()

    def _hide_executable_rows(self) -> None:
        for row in self._executable_form_rows:
            frame = row.get("frame")
            if isinstance(frame, ttk.Frame):
                frame.grid_remove()
        self.executable_row_state.clear()

    def _show_executables_leaf(self) -> None:
        self._hide_job_rows()
        self._show_executable_surface()
        self.content_box.config(text="Executable commands")
        self.header_col0_label.config(text="Executable")
        self.header_col1_label.config(text="Execute command")
        self.header_col2_label.config(text="Application in")
        self.header_col3_label.config(text="")
        self.title_label.config(text="Manage Executables")
        self.edit_job_entries_button.configure(state="disabled")
        self.add_job_parameter_button.configure(state="disabled")
        self._ensure_executable_rows(len(self.executable_registry))
        for row_index, item in enumerate(self.executable_registry):
            value = self.executable_overrides.get(item.executable, item.executable)
            self._configure_executable_row(row_index, item.executable, value, item.applications)
        for row in self._executable_form_rows[len(self.executable_registry):]:
            frame = row.get("frame")
            if isinstance(frame, ttk.Frame):
                frame.grid_remove()
        self.window.after_idle(self._update_description_wraplengths)

    def _ensure_executable_rows(self, count: int) -> None:
        while len(self._executable_form_rows) < count:
            row_index = len(self._executable_form_rows)
            row_frame = ttk.Frame(self.rows_frame)
            row_frame.grid(row=row_index, column=0, sticky="ew")
            row_frame.columnconfigure(1, weight=1)
            row_frame.columnconfigure(2, weight=2)

            executable_label = ttk.Label(row_frame, width=24)
            executable_label.grid(row=0, column=0, sticky="nw", pady=4)
            value_var = tk.StringVar()
            command_entry = ttk.Entry(row_frame, textvariable=value_var)
            command_entry.grid(row=0, column=1, sticky="ew", padx=(12, 8), pady=4)
            applications_text = tk.Text(
                row_frame,
                height=3,
                width=52,
                wrap="word",
                font="TkDefaultFont",
            )
            applications_text.grid(row=0, column=2, sticky="ew", pady=4)
            self._executable_form_rows.append(
                {
                    "frame": row_frame,
                    "executable_label": executable_label,
                    "command_var": value_var,
                    "applications_text": applications_text,
                }
            )

    def _configure_executable_row(
        self,
        row_index: int,
        executable: str,
        command: str,
        applications: tuple[str, ...],
    ) -> None:
        row = self._executable_form_rows[row_index]
        frame = row.get("frame")
        executable_label = row.get("executable_label")
        command_var = row.get("command_var")
        applications_text = row.get("applications_text")
        if isinstance(frame, ttk.Frame):
            frame.grid()
        if isinstance(executable_label, ttk.Label):
            executable_label.config(text=executable)
        if isinstance(command_var, tk.StringVar):
            command_var.set(command)
            self.executable_row_state[executable] = command_var
        if isinstance(applications_text, tk.Text):
            applications_text.configure(state="normal")
            applications_text.delete("1.0", "end")
            applications_text.insert("1.0", "\n".join(applications))
            applications_text.configure(state="disabled")

    def _ensure_form_rows(self, count: int) -> None:
        while len(self._form_rows) < count:
            row_index = len(self._form_rows)
            row_frame = ttk.Frame(self.rows_frame)
            row_frame.grid(row=row_index, column=0, sticky="ew")
            row_frame.columnconfigure(0, weight=0, minsize=210)
            row_frame.columnconfigure(1, weight=0, minsize=170)
            row_frame.columnconfigure(2, weight=0, minsize=210)
            row_frame.columnconfigure(3, weight=1, minsize=360)
            row_frame.columnconfigure(4, weight=0)

            description_display = ttk.Label(row_frame)
            description_display.grid(row=0, column=3, sticky="ew", padx=(8, 0), pady=4)
            parameter_display = ttk.Label(row_frame)
            parameter_display.grid(row=0, column=0, sticky="ew", pady=4)
            widget_display = ttk.Label(row_frame)
            widget_display.grid(row=0, column=1, sticky="ew", padx=(12, 8), pady=4)
            value_display = ttk.Label(row_frame)
            value_display.grid(row=0, column=2, sticky="ew", pady=4)
            self._form_rows.append(
                {
                    "frame": row_frame,
                    "field_key": "",
                    "custom": False,
                    "description_display": description_display,
                    "parameter_display": parameter_display,
                    "widget_display": widget_display,
                    "value_display": value_display,
                    "description_var": None,
                    "description_entry": None,
                    "parameter_var": None,
                    "parameter_entry": None,
                    "widget_var": None,
                    "widget_combo": None,
                    "value_host": None,
                    "value_widget": None,
                    "value_var": None,
                    "bool_var": None,
                    "removed_var": None,
                    "remove_button": None,
                    "widget_kind": "",
                }
            )

    def _configure_form_row(self, row_index: int, field) -> None:
        if self.job_edit_mode:
            self._configure_edit_form_row(row_index, field)
        else:
            self._configure_readonly_form_row(row_index, field)

    def _configure_readonly_form_row(self, row_index: int, field) -> None:
        row = self._form_rows[row_index]
        frame = row.get("frame")
        description_display = row.get("description_display")
        parameter_display = row.get("parameter_display")
        widget_display = row.get("widget_display")
        value_display = row.get("value_display")
        if isinstance(frame, ttk.Frame):
            frame.grid()
        row["field_key"] = field.key
        row["custom"] = field.key.startswith(CUSTOM_FIELD_PREFIX)
        self._hide_edit_widgets(row)
        if isinstance(description_display, ttk.Label):
            description_display.config(text=self._field_description(field) or "-")
            description_display.grid(row=0, column=3, sticky="ew", padx=(8, 0), pady=4)
        if isinstance(parameter_display, ttk.Label):
            parameter_display.config(text=self._base_parameter_name(field) or "-")
            parameter_display.grid(row=0, column=0, sticky="ew", pady=4)
        if isinstance(widget_display, ttk.Label):
            widget_display.config(text=self._display_input_type(field.widget))
            widget_display.grid(row=0, column=1, sticky="ew", padx=(12, 8), pady=4)
        if isinstance(value_display, ttk.Label):
            value_text = field.default_value
            if field.widget == "bool":
                value_text = "true" if str(field.default_value).lower() in {"1", "true", "yes", "on"} else "false"
            value_display.config(text=value_text or "-")
            value_display.grid(row=0, column=2, sticky="ew", pady=4)
        self.row_state.pop(field.key, None)

    def _ensure_edit_widgets(self, row: dict[str, object]) -> None:
        if isinstance(row.get("description_entry"), ttk.Entry):
            return
        row_frame = row.get("frame")
        if not isinstance(row_frame, ttk.Frame):
            return
        description_var = tk.StringVar()
        description_entry = ttk.Entry(row_frame, textvariable=description_var, width=52)
        parameter_var = tk.StringVar()
        parameter_entry = ttk.Entry(row_frame, textvariable=parameter_var, width=26)
        widget_var = tk.StringVar()
        widget_combo = ttk.Combobox(
            row_frame,
            textvariable=widget_var,
            state="readonly",
            values=self._input_type_options(),
            width=18,
        )
        value_host = ttk.Frame(row_frame)
        value_host.columnconfigure(0, weight=1)
        removed_var = tk.BooleanVar(value=False)
        remove_button = ttk.Button(row_frame, text="-", width=3)
        row.update(
            {
                "description_var": description_var,
                "description_entry": description_entry,
                "parameter_var": parameter_var,
                "parameter_entry": parameter_entry,
                "widget_var": widget_var,
                "widget_combo": widget_combo,
                "value_host": value_host,
                "removed_var": removed_var,
                "remove_button": remove_button,
            }
        )

    def _hide_edit_widgets(self, row: dict[str, object]) -> None:
        for key in ("description_entry", "parameter_entry", "widget_combo", "value_host", "remove_button"):
            widget = row.get(key)
            if hasattr(widget, "grid_remove"):
                widget.grid_remove()

    def _configure_edit_form_row(self, row_index: int, field) -> None:
        row = self._form_rows[row_index]
        self._ensure_edit_widgets(row)
        frame = row.get("frame")
        description_var = row.get("description_var")
        description_entry = row.get("description_entry")
        description_display = row.get("description_display")
        parameter_var = row.get("parameter_var")
        parameter_entry = row.get("parameter_entry")
        parameter_display = row.get("parameter_display")
        widget_var = row.get("widget_var")
        widget_combo = row.get("widget_combo")
        widget_display = row.get("widget_display")
        value_display = row.get("value_display")
        value_host = row.get("value_host")
        removed_var = row.get("removed_var")
        remove_button = row.get("remove_button")
        is_custom = field.key.startswith(CUSTOM_FIELD_PREFIX)
        if isinstance(frame, ttk.Frame):
            frame.grid()
        row["field_key"] = field.key
        row["custom"] = is_custom
        if isinstance(removed_var, tk.BooleanVar):
            removed_var.set(False)
        if isinstance(description_var, tk.StringVar):
            description_var.set(self._field_description(field))
        if isinstance(parameter_var, tk.StringVar):
            parameter_var.set(self._base_parameter_name(field))
        if isinstance(widget_var, tk.StringVar):
            widget_var.set(self._display_input_type(field.widget))
        if isinstance(description_display, ttk.Label):
            description_display.grid_remove()
        if isinstance(parameter_display, ttk.Label):
            parameter_display.grid_remove()
        if isinstance(widget_display, ttk.Label):
            widget_display.grid_remove()
        if isinstance(value_display, ttk.Label):
            value_display.grid_remove()
        if isinstance(description_entry, ttk.Entry):
            description_entry.grid(row=0, column=0, sticky="ew", pady=4)
        if isinstance(parameter_entry, ttk.Entry):
            parameter_entry.grid(row=0, column=1, sticky="ew", padx=(12, 8), pady=4)
            parameter_entry.configure(state="disabled" if field.key == "execution_environment" else "normal")
        if isinstance(widget_combo, ttk.Combobox):
            widget_combo.grid(row=0, column=2, sticky="ew", padx=(0, 8), pady=4)
            widget_combo.configure(state="disabled" if field.key == "execution_environment" else "readonly")
            widget_combo.bind("<<ComboboxSelected>>", lambda _event, index=row_index: self._rerender_value_widget(index))
        if isinstance(value_host, ttk.Frame):
            value_host.grid(row=0, column=3, sticky="ew", pady=4)
        if isinstance(remove_button, ttk.Button):
            if field.key == "execution_environment":
                remove_button.grid_remove()
            else:
                remove_button.configure(command=lambda key=field.key: self._remove_job_parameter_row(key))
                remove_button.grid(row=0, column=4, sticky="w", padx=(8, 0), pady=4)

        desired_kind = self._stored_input_type(widget_var.get() if isinstance(widget_var, tk.StringVar) else field.widget)
        current_kind = str(row["widget_kind"])
        value_widget = row["value_widget"]
        value_var = row["value_var"]
        if not isinstance(value_host, ttk.Frame):
            return
        if desired_kind != current_kind or value_widget is None or value_var is None:
            for child in value_host.winfo_children():
                child.destroy()
            if desired_kind == "bool":
                value_var = tk.BooleanVar()
                value_widget = ttk.Checkbutton(value_host, variable=value_var)
                value_widget.grid(row=0, column=0, sticky="w")
                row["bool_var"] = value_var
            elif desired_kind == "environment":
                value_var = tk.StringVar()
                value_widget = ttk.Combobox(
                    value_host,
                    textvariable=value_var,
                    state="readonly",
                    values=self.environment_title_options,
                )
                value_widget.grid(row=0, column=0, sticky="ew")
                row["bool_var"] = None
            elif desired_kind == "choice":
                value_var = tk.StringVar()
                value_widget = ttk.Combobox(
                    value_host,
                    textvariable=value_var,
                    state="readonly",
                    values=field.options,
                )
                value_widget.grid(row=0, column=0, sticky="ew")
                row["bool_var"] = None
            else:
                value_var = tk.StringVar()
                value_widget = ttk.Entry(value_host, textvariable=value_var)
                value_widget.grid(row=0, column=0, sticky="ew")
                row["bool_var"] = None
            row["value_widget"] = value_widget
            row["value_var"] = value_var
            row["widget_kind"] = desired_kind
        elif desired_kind == "environment" and isinstance(value_widget, ttk.Combobox):
            value_widget.configure(values=self.environment_title_options)

        if desired_kind == "bool" and isinstance(value_var, tk.BooleanVar):
            value_var.set(str(field.default_value).lower() in {"1", "true", "yes", "on"})
        elif desired_kind == "environment" and isinstance(value_var, tk.StringVar):
            available = set(self.environment_title_options)
            value_var.set(field.default_value if field.default_value in available else "None")
        elif isinstance(value_var, tk.StringVar):
            value_var.set(field.default_value)
        self.row_state[field.key] = row

    def _display_input_type(self, stored: str) -> str:
        if stored == "environment":
            return "environment"
        return display_input_type(self.app.project, stored)

    def _stored_input_type(self, displayed: str) -> str:
        if displayed == "environment":
            return "environment"
        return stored_input_type(self.app.project, displayed)

    def _input_type_options(self) -> tuple[str, ...]:
        values = list(runtime_input_type_options(self.app.project))
        for value in ("choice", "environment"):
            if value not in values:
                values.append(value)
        return tuple(values)

    def _rerender_value_widget(self, row_index: int) -> None:
        if not (0 <= row_index < len(self._form_rows)):
            return
        field_key = str(self._form_rows[row_index].get("field_key", ""))
        current = self.row_state.get(field_key)
        if current is None:
            return
        description_var = current.get("description_var")
        parameter_var = current.get("parameter_var")
        widget_var = current.get("widget_var")
        default_var = current.get("value_var")
        default_text = ""
        if isinstance(default_var, tk.BooleanVar):
            default_text = "true" if default_var.get() else ""
        elif isinstance(default_var, tk.StringVar):
            default_text = default_var.get()
        field = type(
            "_Field",
            (),
            {
                "key": field_key,
                "label": "",
                "widget": self._stored_input_type(widget_var.get() if isinstance(widget_var, tk.StringVar) else "text"),
                "default_value": default_text,
                "description": description_var.get() if isinstance(description_var, tk.StringVar) else "",
                "options": (),
                "parameter_name": parameter_var.get() if isinstance(parameter_var, tk.StringVar) else "",
            },
        )()
        current["widget_kind"] = ""
        self._configure_form_row(row_index, field)

    def _next_custom_field_key(self) -> str:
        existing = set()
        if isinstance(self.current_leaf, tuple):
            existing.update(self.overrides.get(job_override_key(*self.current_leaf), {}).keys())
        existing.update(self.row_state.keys())
        index = 1
        while f"{CUSTOM_FIELD_PREFIX}{index}" in existing:
            index += 1
        return f"{CUSTOM_FIELD_PREFIX}{index}"

    def _add_job_parameter_row(self) -> None:
        if not self.job_edit_mode or not isinstance(self.current_leaf, tuple):
            return
        field = type(
            "_Field",
            (),
            {
                "key": self._next_custom_field_key(),
                "label": "",
                "widget": "text",
                "default_value": "",
                "description": "",
                "options": (),
                "parameter_name": "",
            },
        )()
        row_index = len(self.row_state)
        self._ensure_form_rows(row_index + 1)
        self._configure_form_row(row_index, field)

    def _remove_job_parameter_row(self, field_key: str) -> None:
        row = self.row_state.get(field_key)
        if row is None:
            return
        removed_var = row.get("removed_var")
        frame = row.get("frame")
        if isinstance(removed_var, tk.BooleanVar):
            removed_var.set(True)
        if isinstance(frame, ttk.Frame):
            frame.grid_remove()

    def _update_description_wraplengths(self) -> None:
        try:
            available = max(220, self.canvas.winfo_width() - 760)
        except tk.TclError:
            return
        for row in self._form_rows:
            widget = row.get("description_display")
            if isinstance(widget, ttk.Label):
                widget.configure(wraplength=available)

    def _settings_selection_items(self) -> list[tuple[str, str]]:
        items: list[tuple[str, str]] = [("executables", "Manage Executables")]
        for definition in self.registry:
            key = f"job::{job_override_key(definition.namespace, definition.group, definition.job_key)}"
            label = f"{definition.namespace} > {definition.group} > {definition.title}"
            items.append((key, label))
        for role in essential_file_roles():
            items.append((f"role::{role}", f"File registry > {role}"))
        return items

    def _payload_for_selected_items(self, selected_keys: set[str]) -> dict:
        selected_overrides: dict[str, dict[str, dict[str, str]]] = {}
        for override_key, value in self.overrides.items():
            if f"job::{override_key}" in selected_keys:
                selected_overrides[override_key] = deepcopy(value)
        selected_patterns = {
            role: config.to_dict()
            for role, config in self.file_registry_patterns.items()
            if f"role::{role}" in selected_keys
        }
        payload = {
            "version": 1,
            "job_default_overrides": selected_overrides,
            "file_registry_patterns": selected_patterns,
        }
        if "executables" in selected_keys:
            payload["executable_overrides"] = deepcopy(self.executable_overrides)
        return payload

    def _label_for_override_key(self, override_key: str) -> str:
        parts = override_key.split("/", 2)
        if len(parts) != 3:
            return override_key
        definition = self.lookup.get((parts[0], parts[1], parts[2]))
        if definition is None:
            return override_key
        return f"{definition.namespace} > {definition.group} > {definition.title}"

    def _import_settings(self) -> None:
        path = filedialog.askopenfilename(
            title="Import default parameters",
            filetypes=[("CryoPal_tomo settings", f"*{SETTINGS_SUFFIX}"), ("JSON", "*.json")],
        )
        if not path:
            return
        try:
            payload = import_settings_payload(path)
        except Exception as exc:
            messagebox.showerror("Import failed", str(exc))
            return

        raw_overrides = payload.get("job_default_overrides", {})
        overrides = raw_overrides if isinstance(raw_overrides, dict) else {}
        imported_executables = payload.get("executable_overrides", {})
        if not isinstance(imported_executables, dict):
            imported_executables = {}
        imported_patterns = imported_file_registry_patterns(payload)
        selection_items: list[tuple[str, str]] = []
        if imported_executables:
            selection_items.append(("executables", "Manage Executables"))
        for override_key in overrides:
            selection_items.append((f"job::{override_key}", self._label_for_override_key(override_key)))
        for role in imported_patterns:
            selection_items.append((f"role::{role}", f"File registry > {role}"))
        if not selection_items:
            messagebox.showinfo("Import default parameters", "No compatible job settings were found in this file.")
            return

        selected = choose_items_dialog(
            self.window,
            "Import default parameters",
            "Select which detected jobs and file-registry roles should be imported.",
            selection_items,
        )
        if selected is None:
            return
        if not selected:
            messagebox.showinfo("Import default parameters", "No jobs or file-registry roles were selected for import.")
            return
        selected_keys = set(selected)
        if "executables" in selected_keys:
            self.executable_overrides = deepcopy(imported_executables)
        for override_key, value in overrides.items():
            if f"job::{override_key}" in selected_keys:
                self.overrides[override_key] = deepcopy(value)
        for role, config in imported_patterns.items():
            if f"role::{role}" in selected_keys:
                self.file_registry_patterns[role] = deepcopy(config)
        self._invalidate_default_summary_cache()
        if self.current_leaf is not None:
            self._show_leaf(self.current_leaf)

    def _export_settings(self) -> None:
        self._persist_current_rows()
        selected = choose_items_dialog(
            self.window,
            "Export default parameters",
            "Select which jobs and file-registry roles should be exported.",
            self._settings_selection_items(),
        )
        if selected is None:
            return
        if not selected:
            messagebox.showinfo("Export default parameters", "No jobs or file-registry roles were selected for export.")
            return
        path = filedialog.asksaveasfilename(
            title="Export default parameters",
            defaultextension=SETTINGS_SUFFIX,
            filetypes=[("CryoPal_tomo settings", f"*{SETTINGS_SUFFIX}"), ("JSON", "*.json")],
        )
        if not path:
            return
        try:
            export_path = Path(path)
            if not str(export_path).endswith(SETTINGS_SUFFIX):
                export_path = export_path.with_name(f"{export_path.name}{SETTINGS_SUFFIX}")
            payload = self._payload_for_selected_items(set(selected))
            export_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))
            return
        self.app.status_var.set("Exported default parameter settings")

    def _save(self) -> None:
        self.save_section(close_window=False)

    def save_section(self, *, close_window: bool = False) -> bool:
        self._persist_current_rows()
        set_project_executable_overrides(self.app.project, self.executable_overrides)
        set_project_job_default_overrides(self.app.project, self.overrides)
        for role, config in self.file_registry_patterns.items():
            set_file_role_config(self.app.project, role, config)
        self.app.on_project_changed("defaults", "file_registry", "executables", status_message="Saved project default parameters")
        self.saved_overrides = deepcopy(self.overrides)
        self.saved_executable_overrides = deepcopy(get_project_executable_overrides(self.app.project))
        self.executable_overrides = deepcopy(self.saved_executable_overrides)
        self.saved_file_registry_patterns = deepcopy(self.file_registry_patterns)
        if close_window:
            self.window.destroy()
        return True

    def has_unsaved_changes(self) -> bool:
        self._persist_current_rows()
        return (
            self.overrides != self.saved_overrides
            or self.executable_overrides != self.saved_executable_overrides
            or self.file_registry_patterns != self.saved_file_registry_patterns
        )

    def _cancel(self) -> None:
        self.overrides = deepcopy(self.saved_overrides)
        self.executable_overrides = deepcopy(self.saved_executable_overrides)
        self.file_registry_patterns = deepcopy(self.saved_file_registry_patterns)
        self._invalidate_default_summary_cache()
        if self.current_leaf is not None:
            self._show_leaf(self.current_leaf)
        self.app.status_var.set("Reverted unsaved default parameter changes")
