from __future__ import annotations

from copy import deepcopy
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from cryoet_organizer.dialogs import bind_scrollable_canvas, choose_items_dialog
from cryoet_organizer.environments import environment_titles
from cryoet_organizer.job_defaults import (
    build_job_default_registry,
    get_project_job_default_overrides,
    imported_file_registry_patterns,
    import_settings_payload,
    job_override_key,
    registry_lookup,
    set_project_job_default_overrides,
)
from cryoet_organizer.file_resolver import essential_file_roles, file_role_config, set_file_role_config
from cryoet_organizer.settings_shell import decorate_settings_window
import json
from pathlib import Path
from cryoet_organizer.project import SETTINGS_SUFFIX


class DefaultParametersDialog:
    def __init__(self, app, host: tk.Misc | None = None) -> None:
        self.app = app
        self.registry = build_job_default_registry()
        self.lookup = registry_lookup()
        self.overrides = deepcopy(get_project_job_default_overrides(app.project))
        self.saved_overrides = deepcopy(self.overrides)
        self.file_registry_patterns = {
            role: file_role_config(app.project, role)
            for role in essential_file_roles()
        }
        self.saved_file_registry_patterns = deepcopy(self.file_registry_patterns)
        self.current_leaf: tuple[str, str, str] | None = None
        self.row_state: dict[str, tuple[tk.BooleanVar, tk.Variable]] = {}
        self._pending_leaf_after: str | None = None
        self._form_rows: list[dict[str, object]] = []
        self.embedded = host is not None

        self.window = host if host is not None else tk.Toplevel(app.root)
        if not self.embedded:
            self.window.title("Set default parameters")
            self.window.geometry("1180x720")
            self.window.transient(app.root)
            self.window.grab_set()
            self.window.protocol("WM_DELETE_WINDOW", self.window.destroy)
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
        tree_box.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(tree_box, show="tree")
        self.tree.grid(row=0, column=0, sticky="nsw")
        tree_scroll = ttk.Scrollbar(tree_box, orient="vertical", command=self.tree.yview)
        tree_scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_selected)

        right = ttk.Frame(self.window, padding=(0, 0, 12, 12))
        right.grid(row=content_row, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        self.title_label = ttk.Label(right, text="Select a job on the left", style="Heading.TLabel")
        self.title_label.grid(row=0, column=0, sticky="w", pady=(0, 8))

        content_box = ttk.LabelFrame(right, text="Job defaults", padding=12)
        content_box.grid(row=1, column=0, sticky="nsew")
        content_box.columnconfigure(0, weight=1)
        content_box.rowconfigure(1, weight=1)

        header = ttk.Frame(content_box)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(2, weight=1)
        ttk.Label(header, text="Overwrite default", width=18).grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="Parameter", width=28).grid(row=0, column=1, sticky="w", padx=(12, 8))
        ttk.Label(header, text="Value / default", width=42).grid(row=0, column=2, sticky="w")

        self.canvas = tk.Canvas(content_box, highlightthickness=0)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(content_box, orient="vertical", command=self.canvas.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        xscrollbar = ttk.Scrollbar(content_box, orient="horizontal", command=self.canvas.xview)
        xscrollbar.grid(row=2, column=0, sticky="ew")
        self.canvas.configure(yscrollcommand=scrollbar.set, xscrollcommand=xscrollbar.set)
        self.rows_frame = ttk.Frame(self.canvas)
        self.rows_frame.columnconfigure(2, weight=1)
        self.canvas_window = self.canvas.create_window((0, 0), window=self.rows_frame, anchor="nw")
        bind_scrollable_canvas(self.canvas, self.canvas_window, self.rows_frame, allow_horizontal=True)

        buttons = ttk.Frame(self.window, padding=(12, 0, 12, 12))
        buttons.grid(row=footer_row, column=0, columnspan=2, sticky="ew")
        buttons.columnconfigure(0, weight=1)
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
        if self.current_leaf is None:
            return
        namespace, group, job_key = self.current_leaf
        override_key = job_override_key(namespace, group, job_key)
        job_overrides: dict[str, dict[str, str]] = {}
        for field_key, (enabled_var, value_var) in self.row_state.items():
            if enabled_var.get():
                job_overrides[field_key] = {
                    "enabled": "true",
                    "value": str(value_var.get()),
                }
        if job_overrides:
            self.overrides[override_key] = job_overrides
        else:
            self.overrides.pop(override_key, None)

    def _on_tree_selected(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        selected = selection[0]
        parts = selected.split("/", 2)
        if len(parts) != 3:
            return
        leaf = (parts[0], parts[1], parts[2])
        if leaf == self.current_leaf:
            return
        self._persist_current_rows()
        if self._pending_leaf_after is not None:
            self.window.after_cancel(self._pending_leaf_after)
        self._pending_leaf_after = self.window.after_idle(lambda current_leaf=leaf: self._show_leaf(current_leaf))

    def _show_leaf(self, leaf: tuple[str, str, str] | None) -> None:
        self._pending_leaf_after = None
        self.current_leaf = leaf
        definition = self.lookup.get(self.current_leaf)
        if definition is None:
            return
        self.title_label.config(text=f"{definition.namespace} > {definition.group} > {definition.title}")
        self.row_state.clear()
        current_overrides = self.overrides.get(job_override_key(*self.current_leaf), {})
        self._ensure_form_rows(len(definition.fields))
        for row_index, field in enumerate(definition.fields):
            enabled = field.key in current_overrides
            stored_value = current_overrides.get(field.key, {}).get("value", field.default_value)
            self._configure_form_row(row_index, field, enabled, stored_value)
        for row in self._form_rows[len(definition.fields):]:
            frame = row["frame"]
            if isinstance(frame, ttk.Frame):
                frame.grid_remove()
        self.window.after_idle(self._update_description_wraplengths)

    def _ensure_form_rows(self, count: int) -> None:
        while len(self._form_rows) < count:
            row_index = len(self._form_rows)
            row_frame = ttk.Frame(self.rows_frame)
            row_frame.grid(row=row_index, column=0, sticky="ew")
            row_frame.columnconfigure(2, weight=1)
            row_frame.columnconfigure(3, weight=1)
            enabled_var = tk.BooleanVar(value=False)
            enabled_button = ttk.Checkbutton(row_frame, variable=enabled_var)
            enabled_button.grid(row=0, column=0, sticky="w", pady=4)
            label_widget = ttk.Label(row_frame, width=28)
            label_widget.grid(row=0, column=1, sticky="w", padx=(12, 8), pady=4)
            value_host = ttk.Frame(row_frame)
            value_host.grid(row=0, column=2, sticky="ew", pady=4)
            value_host.columnconfigure(0, weight=1)
            description_widget = ttk.Label(row_frame, wraplength=560, justify="left")
            description_widget.grid(row=0, column=3, sticky="ew", padx=(8, 0), pady=4)
            self._form_rows.append(
                {
                    "frame": row_frame,
                    "enabled_var": enabled_var,
                    "label": label_widget,
                    "value_host": value_host,
                    "description": description_widget,
                    "value_widget": None,
                    "value_var": None,
                    "widget_kind": "",
                }
            )

    def _configure_form_row(self, row_index: int, field, enabled: bool, stored_value: str) -> None:
        row = self._form_rows[row_index]
        frame = row["frame"]
        label_widget = row["label"]
        description_widget = row["description"]
        value_host = row["value_host"]
        enabled_var = row["enabled_var"]
        if isinstance(frame, ttk.Frame):
            frame.grid()
        if isinstance(enabled_var, tk.BooleanVar):
            enabled_var.set(enabled)
        if isinstance(label_widget, ttk.Label):
            label_widget.config(text=field.label)
        if isinstance(description_widget, ttk.Label):
            if field.description:
                description_widget.config(text=field.description)
                description_widget.grid()
            else:
                description_widget.config(text="")
                description_widget.grid_remove()

        desired_kind = field.widget
        current_kind = str(row["widget_kind"])
        value_widget = row["value_widget"]
        value_var = row["value_var"]
        if desired_kind != current_kind or value_widget is None or value_var is None:
            for child in value_host.winfo_children():
                child.destroy()
            if desired_kind == "bool":
                value_var = tk.BooleanVar()
                value_widget = ttk.Checkbutton(value_host, variable=value_var)
                value_widget.grid(row=0, column=0, sticky="w")
            elif desired_kind == "environment":
                value_var = tk.StringVar()
                value_widget = ttk.Combobox(
                    value_host,
                    textvariable=value_var,
                    state="readonly",
                    values=environment_titles(self.app.project),
                )
                value_widget.grid(row=0, column=0, sticky="ew")
            elif desired_kind == "choice":
                value_var = tk.StringVar()
                value_widget = ttk.Combobox(
                    value_host,
                    textvariable=value_var,
                    state="readonly",
                    values=field.options,
                )
                value_widget.grid(row=0, column=0, sticky="ew")
            else:
                value_var = tk.StringVar()
                value_widget = ttk.Entry(value_host, textvariable=value_var)
                value_widget.grid(row=0, column=0, sticky="ew")
            row["value_widget"] = value_widget
            row["value_var"] = value_var
            row["widget_kind"] = desired_kind

        if desired_kind == "bool" and isinstance(value_var, tk.BooleanVar):
            value_var.set(str(stored_value).lower() in {"1", "true", "yes", "on"})
        elif desired_kind == "environment" and isinstance(value_var, tk.StringVar):
            available = set(environment_titles(self.app.project))
            value_var.set(stored_value if stored_value in available else "None")
        elif isinstance(value_var, tk.StringVar):
            value_var.set(stored_value)
        self.row_state[field.key] = (enabled_var, value_var)

    def _update_description_wraplengths(self) -> None:
        try:
            available = max(220, self.canvas.winfo_width() - 760)
        except tk.TclError:
            return
        for row in self._form_rows:
            description_widget = row.get("description")
            if isinstance(description_widget, ttk.Label):
                description_widget.configure(wraplength=available)

    def _settings_selection_items(self) -> list[tuple[str, str]]:
        items: list[tuple[str, str]] = []
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
        return {
            "version": 1,
            "job_default_overrides": selected_overrides,
            "file_registry_patterns": selected_patterns,
        }

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
        imported_patterns = imported_file_registry_patterns(payload)
        selection_items: list[tuple[str, str]] = []
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
        for override_key, value in overrides.items():
            if f"job::{override_key}" in selected_keys:
                self.overrides[override_key] = deepcopy(value)
        for role, config in imported_patterns.items():
            if f"role::{role}" in selected_keys:
                self.file_registry_patterns[role] = deepcopy(config)
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
        set_project_job_default_overrides(self.app.project, self.overrides)
        for role, config in self.file_registry_patterns.items():
            set_file_role_config(self.app.project, role, config)
        self.app.on_project_changed("defaults", "file_registry", status_message="Saved project default parameters")
        processing_tab = self.app.tabs.get("processing")
        if processing_tab is not None and hasattr(processing_tab, "_build_parameter_form"):
            processing_tab._build_parameter_form()
        particles_tab = self.app.tabs.get("particles")
        if particles_tab is not None:
            if hasattr(particles_tab, "_apply_particle_custom_defaults"):
                particles_tab._apply_particle_custom_defaults()
            if hasattr(particles_tab, "_build_parameter_form"):
                particles_tab._build_parameter_form()
            if hasattr(particles_tab, "_on_intersect_identification_changed"):
                particles_tab._on_intersect_identification_changed()
            if hasattr(particles_tab, "_mark_abundance_dirty"):
                particles_tab._mark_abundance_dirty()
        project_tab = self.app.tabs.get("project_overview")
        if project_tab is not None and hasattr(project_tab, "_apply_custom_defaults"):
            project_tab._apply_custom_defaults()
        tomograms_tab = self.app.tabs.get("tomograms")
        if tomograms_tab is not None and hasattr(tomograms_tab, "_apply_custom_defaults"):
            tomograms_tab._apply_custom_defaults()
        self.saved_overrides = deepcopy(self.overrides)
        self.saved_file_registry_patterns = deepcopy(self.file_registry_patterns)
        if close_window:
            self.window.destroy()
        return True

    def has_unsaved_changes(self) -> bool:
        self._persist_current_rows()
        return (
            self.overrides != self.saved_overrides
            or self.file_registry_patterns != self.saved_file_registry_patterns
        )

    def _cancel(self) -> None:
        self.overrides = deepcopy(self.saved_overrides)
        self.file_registry_patterns = deepcopy(self.saved_file_registry_patterns)
        if self.current_leaf is not None:
            self._show_leaf(self.current_leaf)
        self.app.status_var.set("Reverted unsaved default parameter changes")
