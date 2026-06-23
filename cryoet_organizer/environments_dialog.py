from __future__ import annotations

from copy import deepcopy
import tkinter as tk
from tkinter import messagebox, ttk

from cryoet_organizer.dialogs import make_copy_name
from cryoet_organizer.environments import (
    EnvironmentDefinition,
    get_project_environments,
    set_project_environments,
)
from cryoet_organizer.settings_shell import decorate_settings_window


class EnvironmentsDialog:
    def __init__(self, app, host: tk.Misc | None = None) -> None:
        self.app = app
        self.environments = deepcopy(get_project_environments(app.project))
        self.saved_environments = deepcopy(self.environments)
        self.current_index: int | None = None
        self.embedded = host is not None

        self.window = host if host is not None else tk.Toplevel(app.root)
        if not self.embedded:
            self.window.title("Manage environments")
            self.window.geometry("980x620")
            self.window.minsize(760, 440)
            self.window.transient(app.root)
            self.window.grab_set()
            self.window.protocol("WM_DELETE_WINDOW", self._cancel)
        self.window.columnconfigure(1, weight=1)
        self.window.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(self.window, text="Environments", padding=12)
        left.grid(row=0, column=0, sticky="nsw", padx=(12, 8), pady=12)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)

        self.listbox = tk.Listbox(left, exportselection=False, width=30)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        left_scroll = ttk.Scrollbar(left, orient="vertical", command=self.listbox.yview)
        left_scroll.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=left_scroll.set)
        self.listbox.bind("<<ListboxSelect>>", self._on_selection_changed)

        left_actions = ttk.Frame(left)
        left_actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        left_actions.columnconfigure(0, weight=1)
        ttk.Button(left_actions, text="Add environment", command=self._add_environment).grid(row=0, column=0, sticky="w")
        ttk.Button(left_actions, text="Clone entry", command=self._clone_selected).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(left_actions, text="Remove selected", command=self._remove_selected).grid(row=0, column=2, padx=(8, 0))

        right = ttk.LabelFrame(self.window, text="Environment details", padding=12)
        right.grid(row=0, column=1, sticky="nsew", padx=(0, 12), pady=12)
        right.columnconfigure(1, weight=1)
        right.rowconfigure(1, weight=1)
        self.right_panel = right

        self.title_var = tk.StringVar()
        ttk.Label(right, text="Title").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.title_entry = ttk.Entry(right, textvariable=self.title_var)
        self.title_entry.grid(row=0, column=1, sticky="ew", pady=(0, 12))

        ttk.Label(right, text="Activation command").grid(row=1, column=0, sticky="nw", pady=(0, 4))
        self.command_text = tk.Text(right, height=8, wrap="word")
        self.command_text.grid(row=1, column=1, sticky="nsew")

        help_label = ttk.Label(
            right,
            text=(
                "This command is executed before a local job starts. "
                "Examples: 'conda activate warp3' or 'source /path/to/venv/bin/activate'."
            ),
            wraplength=560,
            justify="left",
        )
        help_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=(12, 0))
        self.empty_hint_var = tk.StringVar(value="")
        self.empty_hint_label = ttk.Label(
            right,
            textvariable=self.empty_hint_var,
            style="Error.TLabel",
            wraplength=560,
            justify="left",
        )
        self.empty_hint_label.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

        buttons = ttk.Frame(self.window, padding=(12, 0, 12, 12))
        buttons.grid(row=1, column=0, columnspan=2, sticky="ew")
        buttons.columnconfigure(0, weight=1)
        cancel_label = "Revert section" if self.embedded else "Cancel"
        save_label = "Save section" if self.embedded else "Save"
        ttk.Button(buttons, text=cancel_label, command=self._cancel).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(buttons, text=save_label, command=self._save).grid(row=0, column=2, padx=(8, 0))

        self._refresh_list()
        if not self.embedded:
            decorate_settings_window(self, "environments")

    def _refresh_list(self) -> None:
        self.listbox.delete(0, "end")
        for item in self.environments:
            self.listbox.insert("end", item.title)
        if self.environments and self.current_index is None:
            self.current_index = 0
            self.listbox.selection_set(0)
            self._load_current()
        elif not self.environments:
            self.current_index = None
            self._clear_editor()
        self._update_editor_state()

    def _persist_current(self) -> None:
        if self.current_index is None or not (0 <= self.current_index < len(self.environments)):
            return
        self.environments[self.current_index] = EnvironmentDefinition(
            title=self.title_var.get().strip(),
            activation_command=self.command_text.get("1.0", "end").strip(),
        )

    def _load_current(self) -> None:
        if self.current_index is None or not (0 <= self.current_index < len(self.environments)):
            self._clear_editor()
            self._update_editor_state()
            return
        item = self.environments[self.current_index]
        self.title_var.set(item.title)
        self.command_text.delete("1.0", "end")
        self.command_text.insert("1.0", item.activation_command)
        self._update_editor_state()

    def _on_selection_changed(self, _event=None) -> None:
        selection = self.listbox.curselection()
        if not selection:
            self.current_index = None
            self._clear_editor()
            self._update_editor_state()
            return
        self._persist_current()
        self.current_index = selection[0]
        self._load_current()

    def _add_environment(self) -> None:
        self._persist_current()
        self.environments.append(EnvironmentDefinition(title="New environment", activation_command=""))
        self.current_index = len(self.environments) - 1
        self._refresh_list()
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(self.current_index)
        self._load_current()
        self.title_entry.focus_set()

    def _remove_selected(self) -> None:
        selection = list(self.listbox.curselection())
        if not selection:
            messagebox.showinfo("Remove environment", "Please select an environment first.", parent=self.window)
            return
        for index in reversed(selection):
            if 0 <= index < len(self.environments):
                self.environments.pop(index)
        self.current_index = None
        self._refresh_list()
        self._update_editor_state()

    def _clone_selected(self) -> None:
        selection = list(self.listbox.curselection())
        if len(selection) != 1:
            messagebox.showinfo("Clone environment", "Please select exactly one environment first.", parent=self.window)
            return
        self._persist_current()
        index = selection[0]
        if not (0 <= index < len(self.environments)):
            return
        cloned = deepcopy(self.environments[index])
        cloned.title = make_copy_name([item.title for item in self.environments], cloned.title)
        self.environments.append(cloned)
        self.current_index = len(self.environments) - 1
        self._refresh_list()
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(self.current_index)
        self._load_current()
        self.title_entry.focus_set()

    def _clear_editor(self) -> None:
        self.title_var.set("")
        self.command_text.configure(state="normal")
        self.command_text.delete("1.0", "end")

    def _update_editor_state(self) -> None:
        enabled = self.current_index is not None and 0 <= self.current_index < len(self.environments)
        self.title_entry.configure(state="normal" if enabled else "disabled")
        self.command_text.configure(state="normal" if enabled else "disabled")
        self.empty_hint_var.set("" if enabled else "Please select an environment or create a new one.")

    def _validate(self) -> str | None:
        self._persist_current()
        seen: set[str] = set()
        for item in self.environments:
            title = item.title.strip()
            command = item.activation_command.strip()
            if not title:
                return "Each environment needs a title."
            if title.casefold() == "none":
                return "'None' is reserved and cannot be used as an environment title."
            if title.casefold() in seen:
                return f"Environment title already exists: {title}"
            if not command:
                return f"Environment '{title}' needs an activation command."
            seen.add(title.casefold())
        return None

    def _save(self) -> None:
        self.save_section(close_window=False)

    def save_section(self, *, close_window: bool = False) -> bool:
        problem = self._validate()
        if problem:
            messagebox.showerror("Save environments", problem, parent=self.window)
            return False
        set_project_environments(self.app.project, self.environments)
        self.saved_environments = deepcopy(self.environments)
        self.app.on_project_changed("environments", status_message="Saved environments")
        if close_window:
            self.window.destroy()
        return True

    def has_unsaved_changes(self) -> bool:
        self._persist_current()
        return self.environments != self.saved_environments

    def _cancel(self) -> None:
        self.environments = deepcopy(self.saved_environments)
        self.current_index = None
        self._refresh_list()
