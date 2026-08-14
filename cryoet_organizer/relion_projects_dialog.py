from __future__ import annotations

from copy import deepcopy
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from cryoet_organizer.dialogs import make_copy_name
from cryoet_organizer.relion_projects import (
    RELION_DEFAULT_LABEL,
    RelionProjectDefinition,
    get_project_relion_default,
    get_project_relion_projects,
    set_project_relion_default,
    set_project_relion_projects,
)
from cryoet_organizer.settings_shell import decorate_settings_window


class RelionProjectsDialog:
    def __init__(self, app, host: tk.Misc | None = None) -> None:
        self.app = app
        self.default_profile = deepcopy(get_project_relion_default(app.project))
        self.projects = deepcopy(get_project_relion_projects(app.project))
        self.saved_default_profile = deepcopy(self.default_profile)
        self.saved_projects = deepcopy(self.projects)
        self.current_index = 0
        self.embedded = host is not None

        self.window = host if host is not None else tk.Toplevel(app.root)
        if not self.embedded:
            self.window.title("Manage Relion projects")
            self.window.geometry("1040x660")
            self.window.minsize(880, 520)
            self.window.transient(app.root)
            self.window.grab_set()
            self.window.protocol("WM_DELETE_WINDOW", self._cancel)
        self.window.columnconfigure(0, weight=0, minsize=340)
        self.window.columnconfigure(1, weight=1)
        self.window.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(self.window, text="Relion projects", padding=12)
        left.grid(row=0, column=0, sticky="nsw", padx=(12, 8), pady=12)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)

        self.listbox = tk.Listbox(left, exportselection=False, width=40)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        left_scroll = ttk.Scrollbar(left, orient="vertical", command=self.listbox.yview)
        left_scroll.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=left_scroll.set)
        self.listbox.bind("<<ListboxSelect>>", self._on_selection_changed)

        left_actions = ttk.Frame(left)
        left_actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        left_actions.columnconfigure(0, weight=1)
        ttk.Button(left_actions, text="Add project", command=self._add_project).grid(row=0, column=0, sticky="w")
        self.remove_button = ttk.Button(left_actions, text="Remove selected", command=self._remove_selected)
        self.remove_button.grid(row=0, column=1, sticky="w", padx=(8, 0))

        right = ttk.LabelFrame(self.window, text="Relion project details", padding=12)
        right.grid(row=0, column=1, sticky="nsew", padx=(0, 12), pady=12)
        right.columnconfigure(1, weight=1)
        right.rowconfigure(4, weight=1)

        self.name_label_var = tk.StringVar(value="Relion project name")
        ttk.Label(right, textvariable=self.name_label_var).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.name_var = tk.StringVar()
        self.name_entry = ttk.Entry(right, textvariable=self.name_var)
        self.name_entry.grid(row=0, column=1, sticky="ew", pady=(0, 10))

        ttk.Label(right, text="Relion root directory").grid(row=1, column=0, sticky="w", pady=(0, 4))
        root_row = ttk.Frame(right)
        root_row.grid(row=1, column=1, sticky="ew", pady=(0, 10))
        root_row.columnconfigure(0, weight=1)
        self.root_var = tk.StringVar()
        self.root_entry = ttk.Entry(root_row, textvariable=self.root_var)
        self.root_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(root_row, text="Browse...", command=self._browse_root).grid(row=0, column=1, padx=(8, 0))

        ttk.Label(right, text="Startup command").grid(row=3, column=0, sticky="nw", pady=(0, 4))
        command_frame = ttk.Frame(right)
        command_frame.grid(row=4, column=0, columnspan=2, sticky="nsew")
        command_frame.columnconfigure(0, weight=1)
        command_frame.rowconfigure(0, weight=1)
        self.command_text = tk.Text(command_frame, height=10, wrap="word", font=self.app.ui_font("technical"))
        self.command_text.grid(row=0, column=0, sticky="nsew")
        command_scroll = ttk.Scrollbar(command_frame, orient="vertical", command=self.command_text.yview)
        command_scroll.grid(row=0, column=1, sticky="ns")
        self.command_text.configure(yscrollcommand=command_scroll.set)

        self.hint_var = tk.StringVar()
        ttk.Label(right, textvariable=self.hint_var, wraplength=620, justify="left").grid(
            row=5,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(10, 0),
        )

        buttons = ttk.Frame(self.window, padding=(12, 0, 12, 12))
        buttons.grid(row=1, column=0, columnspan=2, sticky="ew")
        buttons.columnconfigure(0, weight=1)
        cancel_label = "Revert section" if self.embedded else "Cancel"
        save_label = "Save section" if self.embedded else "Save"
        ttk.Button(buttons, text=cancel_label, command=self._cancel).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(buttons, text=save_label, command=self._save).grid(row=0, column=2, padx=(8, 0))

        self._refresh_list(select_index=0)
        if not self.embedded:
            decorate_settings_window(self, "relion_projects")

    def _entry_count(self) -> int:
        return 1 + len(self.projects)

    def _current_is_default(self) -> bool:
        return self.current_index == 0

    def _current_project_index(self) -> int | None:
        index = self.current_index - 1
        return index if 0 <= index < len(self.projects) else None

    def _refresh_list(self, *, select_index: int | None = None) -> None:
        self.listbox.delete(0, "end")
        self.listbox.insert("end", RELION_DEFAULT_LABEL)
        for project in self.projects:
            self.listbox.insert("end", project.name)
        if select_index is not None:
            self.current_index = max(0, min(select_index, self._entry_count() - 1))
        else:
            self.current_index = max(0, min(self.current_index, self._entry_count() - 1))
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(self.current_index)
        self.listbox.see(self.current_index)
        self._load_current()

    def _persist_current(self) -> None:
        value = RelionProjectDefinition(
            name=self.name_var.get().strip(),
            root_directory=self.root_var.get().strip(),
            startup_command=self.command_text.get("1.0", "end").strip(),
        )
        if self._current_is_default():
            self.default_profile = value
            return
        project_index = self._current_project_index()
        if project_index is not None:
            self.projects[project_index] = value

    def _load_current(self) -> None:
        if self._current_is_default():
            item = self.default_profile
            self.name_label_var.set("Default Relion project name")
            self.hint_var.set(
                "These values are used to pre-fill newly created Relion projects. "
                "The default entry is not shown in the Relion projects tab."
            )
            self.remove_button.configure(state="disabled")
        else:
            project_index = self._current_project_index()
            item = self.projects[project_index] if project_index is not None else RelionProjectDefinition(name="")
            self.name_label_var.set("Relion project name")
            self.hint_var.set("Startup command is optional. It is executed after changing into the Relion root directory.")
            self.remove_button.configure(state="normal")
        self.name_var.set(item.name)
        self.root_var.set(item.root_directory)
        self.command_text.delete("1.0", "end")
        self.command_text.insert("1.0", item.startup_command)

    def _on_selection_changed(self, _event=None) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        self._persist_current()
        self.current_index = selection[0]
        self._load_current()

    def _browse_root(self) -> None:
        path = filedialog.askdirectory(title="Select Relion root directory", parent=self.window)
        if path:
            self.root_var.set(path)

    def _add_project(self) -> None:
        self._persist_current()
        existing_names = [project.name for project in self.projects]
        template = self.default_profile
        base_name = template.name.strip() or "New Relion project"
        name = base_name
        if name.casefold() in {existing.casefold() for existing in existing_names}:
            name = make_copy_name(existing_names, base_name)
        self.projects.append(
            RelionProjectDefinition(
                name=name,
                root_directory=template.root_directory,
                startup_command=template.startup_command,
            )
        )
        self._refresh_list(select_index=len(self.projects))
        self.name_entry.focus_set()

    def _remove_selected(self) -> None:
        if self._current_is_default():
            messagebox.showinfo("Remove Relion project", "The default entry cannot be removed.", parent=self.window)
            return
        project_index = self._current_project_index()
        if project_index is None:
            return
        del self.projects[project_index]
        self._refresh_list(select_index=min(project_index + 1, self._entry_count() - 1))

    def _validate(self) -> str | None:
        self._persist_current()
        seen: set[str] = set()
        for project in self.projects:
            name = project.name.strip()
            if not name:
                return "Each Relion project needs a name."
            if name == RELION_DEFAULT_LABEL:
                return f"'{RELION_DEFAULT_LABEL}' is reserved for the default entry."
            key = name.casefold()
            if key in seen:
                return f"Relion project name already exists: {name}"
            if not project.root_directory.strip():
                return f"Relion project '{name}' needs a root directory."
            seen.add(key)
        return None

    def _save(self) -> None:
        self.save_section(close_window=False)

    def save_section(self, *, close_window: bool = False) -> bool:
        problem = self._validate()
        if problem:
            messagebox.showerror("Save Relion projects", problem, parent=self.window)
            return False
        set_project_relion_default(self.app.project, self.default_profile)
        set_project_relion_projects(self.app.project, self.projects)
        self.saved_default_profile = deepcopy(self.default_profile)
        self.saved_projects = deepcopy(self.projects)
        self.app.on_project_changed("relion_projects", status_message="Saved Relion projects")
        if close_window:
            self.window.destroy()
        return True

    def has_unsaved_changes(self) -> bool:
        self._persist_current()
        return self.default_profile != self.saved_default_profile or self.projects != self.saved_projects

    def _cancel(self) -> None:
        self.default_profile = deepcopy(self.saved_default_profile)
        self.projects = deepcopy(self.saved_projects)
        self.current_index = 0
        self._refresh_list(select_index=0)
