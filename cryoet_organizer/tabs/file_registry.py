from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from cryoet_organizer.file_resolver import (
    FileRoleConfig,
    add_custom_file_role,
    all_file_role_configs,
    clear_dataset_overrides,
    clear_dataset_role_overrides,
    essential_file_roles,
    file_role_config,
    file_role_order,
    remove_custom_file_role,
    resolve_dataset_role_map,
    role_title,
    role_titles,
    set_file_override,
    set_file_role_config,
)
from cryoet_organizer.project import ProjectData
from cryoet_organizer.tabs.base import SidebarTab


class FileRegistryTab(SidebarTab):
    tab_id = "file_registry"
    title = "File registry"
    refresh_domains = ("file_registry", "datasets")

    def build(self) -> None:
        self.current_project: ProjectData | None = None
        self._suspend_role_table_callback = False
        self.current_role_var = tk.StringVar(value=file_role_order()[0])
        self.open_dataset_nodes: set[str] = set()
        self.base_dir_var = tk.StringVar()
        self.filename_pattern_var = tk.StringVar()
        self.exclude_patterns_var = tk.StringVar()
        self.recursive_var = tk.BooleanVar(value=False)
        self.selection_mode_var = tk.StringVar(value="unique")
        self.apply_ts_matching_var = tk.BooleanVar(value=True)

        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(3, weight=1)

        intro = ttk.LabelFrame(self.frame, text="File Registry", padding=12)
        intro.grid(row=0, column=0, sticky="ew")
        intro.columnconfigure(0, weight=1)
        ttk.Label(
            intro,
            text=(
                "This section defines how CryoPal resolves file roles such as tomograms, angle files, "
                "aligned stacks, tomostar files, and MDOCs. Other tabs can use these shared rules instead "
                "of maintaining separate search logic."
            ),
            wraplength=980,
            justify="left",
        ).grid(row=0, column=0, sticky="w")

        role_box = ttk.LabelFrame(self.frame, text="File roles overview", padding=12)
        role_box.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        role_box.columnconfigure(0, weight=1)
        role_box.columnconfigure(1, weight=1)
        ttk.Label(role_box, text="Selected file role").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.role_combo = ttk.Combobox(
            role_box,
            textvariable=self.current_role_var,
            state="readonly",
            values=file_role_order(self.app.project),
        )
        self.role_combo.grid(row=0, column=1, sticky="ew")
        self.role_combo.bind("<<ComboboxSelected>>", self._on_role_changed)
        self.roles_table = ttk.Treeview(
            role_box,
            columns=("title", "base_dir", "pattern"),
            show="headings",
            height=5,
        )
        self.roles_table.heading("title", text="Role")
        self.roles_table.heading("base_dir", text="Base directory")
        self.roles_table.heading("pattern", text="Pattern")
        self.roles_table.column("title", width=180, anchor="w")
        self.roles_table.column("base_dir", width=420, anchor="w")
        self.roles_table.column("pattern", width=220, anchor="w")
        self.roles_table.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self.roles_table.bind("<<TreeviewSelect>>", self._on_role_table_selected)
        roles_xscroll = ttk.Scrollbar(role_box, orient="horizontal", command=self.roles_table.xview)
        roles_xscroll.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.roles_table.configure(xscrollcommand=roles_xscroll.set)
        role_actions = ttk.Frame(role_box)
        role_actions.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        role_actions.columnconfigure(0, weight=1)
        ttk.Button(role_actions, text="Add file role", command=self._add_file_role).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(role_actions, text="Remove selected role", command=self._remove_selected_role).grid(row=0, column=2, padx=(8, 0))

        pattern_box = ttk.LabelFrame(self.frame, text="Default pattern", padding=12)
        pattern_box.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        pattern_box.columnconfigure(1, weight=1)

        ttk.Label(pattern_box, text="Base directory template").grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(pattern_box, textvariable=self.base_dir_var).grid(row=0, column=1, sticky="ew", pady=(0, 8))
        ttk.Label(pattern_box, text="Filename pattern").grid(row=1, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(pattern_box, textvariable=self.filename_pattern_var).grid(row=1, column=1, sticky="ew", pady=(0, 8))
        ttk.Label(pattern_box, text="Exclude patterns").grid(row=2, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(pattern_box, textvariable=self.exclude_patterns_var).grid(row=2, column=1, sticky="ew", pady=(0, 8))

        options_row = ttk.Frame(pattern_box)
        options_row.grid(row=3, column=0, columnspan=2, sticky="ew")
        ttk.Checkbutton(options_row, text="Recursive search", variable=self.recursive_var).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(options_row, text="Apply TS matching", variable=self.apply_ts_matching_var).grid(row=0, column=1, sticky="w", padx=(16, 0))
        ttk.Label(options_row, text="Selection mode").grid(row=0, column=2, sticky="w", padx=(16, 8))
        self.selection_mode_combo = ttk.Combobox(
            options_row,
            textvariable=self.selection_mode_var,
            state="readonly",
            values=("unique", "newest"),
            width=12,
        )
        self.selection_mode_combo.grid(row=0, column=3, sticky="w")
        ttk.Button(pattern_box, text="Save pattern", command=self._save_current_pattern).grid(
            row=4, column=1, sticky="e", pady=(12, 0)
        )

        registry_box = ttk.LabelFrame(self.frame, text="Dataset and TS associations", padding=12)
        registry_box.grid(row=3, column=0, sticky="nsew", pady=(12, 0))
        registry_box.columnconfigure(0, weight=1)
        registry_box.rowconfigure(0, weight=1)

        self.registry_tree = ttk.Treeview(
            registry_box,
            columns=("ts_name", "path", "source", "note"),
            show="tree headings",
            height=18,
        )
        self.registry_tree.heading("#0", text="Dataset / TS")
        self.registry_tree.heading("ts_name", text="TS name")
        self.registry_tree.heading("path", text="Resolved file")
        self.registry_tree.heading("source", text="Source")
        self.registry_tree.heading("note", text="Note")
        self.registry_tree.column("#0", width=220, anchor="w")
        self.registry_tree.column("ts_name", width=160, anchor="w")
        self.registry_tree.column("path", width=420, anchor="w")
        self.registry_tree.column("source", width=120, anchor="w")
        self.registry_tree.column("note", width=220, anchor="w")
        self.registry_tree.grid(row=0, column=0, sticky="nsew")
        self.registry_tree.bind("<<TreeviewOpen>>", self._on_tree_open)
        self.registry_tree.bind("<<TreeviewClose>>", self._on_tree_close)

        tree_scroll = ttk.Scrollbar(registry_box, orient="vertical", command=self.registry_tree.yview)
        tree_scroll.grid(row=0, column=1, sticky="ns")
        self.registry_tree.configure(yscrollcommand=tree_scroll.set)
        tree_xscroll = ttk.Scrollbar(registry_box, orient="horizontal", command=self.registry_tree.xview)
        tree_xscroll.grid(row=1, column=0, sticky="ew")
        self.registry_tree.configure(xscrollcommand=tree_xscroll.set)

        actions = ttk.Frame(registry_box)
        actions.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        actions.columnconfigure(0, weight=1)
        ttk.Button(actions, text="Browse override for selected TS", command=self._browse_override).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(actions, text="Reset overrides for selected dataset", command=self._reset_dataset_overrides).grid(row=0, column=2, padx=(8, 0))

        self._load_current_role()

    def _role_config(self) -> FileRoleConfig:
        project = self.current_project or self.app.project
        return file_role_config(project, self.current_role_var.get())

    def _load_current_role(self) -> None:
        config = self._role_config()
        self.base_dir_var.set(config.base_dir_template)
        self.filename_pattern_var.set(config.filename_pattern)
        self.exclude_patterns_var.set(config.exclude_patterns)
        self.recursive_var.set(config.recursive)
        self.selection_mode_var.set(config.selection_mode)
        self.apply_ts_matching_var.set(config.apply_ts_matching)
        self._refresh_roles_table()
        self._refresh_registry_tree()

    def _on_role_changed(self, _event=None) -> None:
        self._load_current_role()

    def _on_role_table_selected(self, _event=None) -> None:
        if self._suspend_role_table_callback:
            return
        selection = self.roles_table.selection()
        if not selection:
            return
        selected_role = selection[0]
        if self.current_role_var.get() == selected_role:
            return
        self.current_role_var.set(selected_role)
        self._load_current_role()

    def _save_current_pattern(self) -> None:
        project = self.current_project or self.app.project
        role = self.current_role_var.get()
        fallback = file_role_config(project, role)
        config = FileRoleConfig(
            role=role,
            title=fallback.title,
            description=fallback.description,
            base_dir_template=self.base_dir_var.get().strip(),
            filename_pattern=self.filename_pattern_var.get().strip(),
            exclude_patterns=self.exclude_patterns_var.get().strip(),
            recursive=self.recursive_var.get(),
            selection_mode=self.selection_mode_var.get().strip() or fallback.selection_mode,
            apply_ts_matching=self.apply_ts_matching_var.get(),
        )
        set_file_role_config(project, role, config)
        self.app.on_project_changed("file_registry")
        self._refresh_roles_table()
        self._refresh_registry_tree()
        self.app.status_var.set(f"Saved File Registry pattern for {role_title(project, role)}")

    def _add_file_role(self) -> None:
        dialog = tk.Toplevel(self.frame)
        dialog.title("Add file role")
        dialog.transient(self.frame.winfo_toplevel())
        dialog.grab_set()
        dialog.columnconfigure(1, weight=1)
        role_key_var = tk.StringVar()
        description_var = tk.StringVar()
        ttk.Label(dialog, text="Role").grid(row=0, column=0, sticky="w", padx=12, pady=(12, 4))
        ttk.Entry(dialog, textvariable=role_key_var).grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=(12, 4))
        ttk.Label(dialog, text="Description").grid(row=1, column=0, sticky="w", padx=12, pady=4)
        ttk.Entry(dialog, textvariable=description_var).grid(row=1, column=1, sticky="ew", padx=(0, 12), pady=4)

        def confirm() -> None:
            role_key = role_key_var.get().strip().lower().replace(" ", "_")
            if not role_key:
                messagebox.showerror("Add file role", "Please provide a role.", parent=dialog)
                return
            project = self.current_project or self.app.project
            if role_key in file_role_order(project):
                messagebox.showerror("Add file role", "That role already exists.", parent=dialog)
                return
            add_custom_file_role(
                project,
                FileRoleConfig(
                    role=role_key,
                    title=role_key.replace("_", " ").title(),
                    description=description_var.get().strip() or "Custom file role.",
                    base_dir_template="",
                    filename_pattern="*",
                ),
            )
            self.current_role_var.set(role_key)
            self.app.on_project_changed("file_registry")
            self._load_current_role()
            dialog.destroy()

        buttons = ttk.Frame(dialog)
        buttons.grid(row=2, column=0, columnspan=2, sticky="e", padx=12, pady=12)
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(buttons, text="Add", command=confirm).grid(row=0, column=1)

    def _remove_selected_role(self) -> None:
        role = self.current_role_var.get()
        if role in essential_file_roles():
            messagebox.showinfo("Remove file role", "Essential file roles cannot be removed.")
            return
        project = self.current_project or self.app.project
        remove_custom_file_role(project, role)
        remaining = file_role_order(project)
        self.current_role_var.set(remaining[0] if remaining else "")
        self.app.on_project_changed("file_registry")
        self._load_current_role()

    def _refresh_roles_table(self) -> None:
        self._suspend_role_table_callback = True
        try:
            self.roles_table.delete(*self.roles_table.get_children())
            project = self.current_project or self.app.project
            self.role_combo.configure(values=file_role_order(project))
            current_role = self.current_role_var.get()
            for role in file_role_order(project):
                config = file_role_config(project, role)
                self.roles_table.insert(
                    "",
                    "end",
                    iid=role,
                    values=(config.title, config.base_dir_template, config.filename_pattern),
                )
            if current_role in self.roles_table.get_children():
                self.roles_table.selection_set(current_role)
        finally:
            self._suspend_role_table_callback = False

    def _refresh_registry_tree(self) -> None:
        self.registry_tree.delete(*self.registry_tree.get_children())
        project = self.current_project or self.app.project
        role = self.current_role_var.get()
        for dataset in project.datasets:
            is_open = dataset.dataset_name in self.open_dataset_nodes
            parent = self.registry_tree.insert(
                "",
                "end",
                iid=f"dataset::{dataset.dataset_name}",
                text=dataset.dataset_name,
                values=("", "", "", ""),
                open=is_open,
            )
            for item in resolve_dataset_role_map(project, dataset, role):
                self.registry_tree.insert(
                    parent,
                    "end",
                    iid=f"ts::{dataset.dataset_name}::{item.ts_name}",
                    text="",
                    values=(item.ts_name, item.path or "-", item.source, item.note or "-"),
                )

    def _on_tree_open(self, _event=None) -> None:
        selection = self.registry_tree.focus()
        if selection.startswith("dataset::"):
            self.open_dataset_nodes.add(selection.split("::", 1)[1])

    def _on_tree_close(self, _event=None) -> None:
        selection = self.registry_tree.focus()
        if selection.startswith("dataset::"):
            self.open_dataset_nodes.discard(selection.split("::", 1)[1])

    def _selected_ts_context(self) -> tuple[str, str] | None:
        selection = self.registry_tree.selection()
        if not selection:
            return None
        item_id = selection[0]
        if not item_id.startswith("ts::"):
            return None
        _, dataset_name, ts_name = item_id.split("::", 2)
        return dataset_name, ts_name

    def _selected_dataset_name(self) -> str | None:
        selection = self.registry_tree.selection()
        if not selection:
            return None
        item_id = selection[0]
        if item_id.startswith("dataset::"):
            return item_id.split("::", 1)[1]
        if item_id.startswith("ts::"):
            return item_id.split("::", 2)[1]
        return None

    def _browse_override(self) -> None:
        context = self._selected_ts_context()
        if context is None:
            messagebox.showinfo("Browse override", "Please select a TS entry first.")
            return
        dataset_name, ts_name = context
        path = filedialog.askopenfilename(title=f"Select override file for {ts_name}")
        if not path:
            return
        project = self.current_project or self.app.project
        set_file_override(project, dataset_name, self.current_role_var.get(), ts_name, path)
        self.app.on_project_changed("file_registry")
        self._refresh_registry_tree()
        self.app.status_var.set(f"Saved override for {dataset_name} / {ts_name}")

    def _reset_dataset_overrides(self) -> None:
        dataset_name = self._selected_dataset_name()
        if not dataset_name:
            messagebox.showinfo("Reset overrides", "Please select a dataset or one of its TS entries first.")
            return
        project = self.current_project or self.app.project
        clear_dataset_overrides(project, dataset_name)
        self.app.on_project_changed("file_registry")
        self._refresh_registry_tree()
        self.app.status_var.set(f"Reset overrides for {dataset_name}")

    def on_project_loaded(self, project: ProjectData) -> None:
        self.current_project = project
        self._load_current_role()
