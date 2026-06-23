from __future__ import annotations

from copy import deepcopy
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from cryoet_organizer.dialogs import choose_items_dialog, make_copy_name
from cryoet_organizer.shortcuts import (
    SHORTCUTS_SUFFIX,
    ShortcutDefinition,
    export_shortcuts,
    get_project_shortcuts,
    import_shortcuts,
    set_project_shortcuts,
)
from cryoet_organizer.settings_shell import decorate_settings_window


class ShortcutEditorDialog:
    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str,
        confirm_label: str,
        initial: ShortcutDefinition | None = None,
    ) -> None:
        self.result: ShortcutDefinition | None = None
        self._initial = initial or ShortcutDefinition(title="", script="", color="#d9e7ff")

        self.window = tk.Toplevel(parent)
        self.window.withdraw()
        self.window.title(title)
        self.window.geometry("760x520")
        self.window.minsize(620, 420)
        self.window.transient(parent.winfo_toplevel())
        self.window.protocol("WM_DELETE_WINDOW", self._cancel)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)

        self.title_var = tk.StringVar(value=self._initial.title)
        self.color_var = tk.StringVar(value=self._initial.color)

        form = ttk.Frame(self.window, padding=12)
        form.grid(row=0, column=0, sticky="nsew")
        form.columnconfigure(1, weight=1)
        form.rowconfigure(1, weight=1)

        ttk.Label(form, text="Title").grid(row=0, column=0, sticky="w", pady=(0, 8), padx=(0, 12))
        ttk.Entry(form, textvariable=self.title_var).grid(row=0, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(form, text="Script").grid(row=1, column=0, sticky="nw", pady=(0, 8), padx=(0, 12))
        script_box = ttk.Frame(form)
        script_box.grid(row=1, column=1, sticky="nsew", pady=(0, 8))
        script_box.columnconfigure(0, weight=1)
        script_box.rowconfigure(0, weight=1)
        self.script_text = tk.Text(script_box, wrap="word", height=14)
        self.script_text.grid(row=0, column=0, sticky="nsew")
        script_scroll = ttk.Scrollbar(script_box, orient="vertical", command=self.script_text.yview)
        script_scroll.grid(row=0, column=1, sticky="ns")
        self.script_text.configure(yscrollcommand=script_scroll.set)
        self.script_text.insert("1.0", self._initial.script)

        ttk.Label(form, text="Color").grid(row=2, column=0, sticky="w", padx=(0, 12))
        color_row = ttk.Frame(form)
        color_row.grid(row=2, column=1, sticky="ew")
        color_row.columnconfigure(0, weight=1)
        ttk.Entry(color_row, textvariable=self.color_var).grid(row=0, column=0, sticky="ew")
        self.swatch = tk.Label(color_row, width=4, relief="solid", bd=1, background=self.color_var.get())
        self.swatch.grid(row=0, column=1, sticky="w", padx=(8, 8))
        ttk.Button(color_row, text="Choose...", command=self._choose_color).grid(row=0, column=2, sticky="w")
        self.color_var.trace_add("write", self._update_swatch)

        buttons = ttk.Frame(self.window, padding=(12, 0, 12, 12))
        buttons.grid(row=1, column=0, sticky="ew")
        buttons.columnconfigure(0, weight=1)
        ttk.Button(buttons, text="Cancel", command=self._cancel).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(buttons, text=confirm_label, command=self._confirm).grid(row=0, column=2, padx=(8, 0))

    def show(self) -> ShortcutDefinition | None:
        self.window.update_idletasks()
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()
        try:
            self.window.grab_set()
        except tk.TclError:
            pass
        self.script_text.focus_set()
        self.window.wait_window()
        return self.result

    def _update_swatch(self, *_args) -> None:
        try:
            self.swatch.configure(background=self.color_var.get().strip() or "#d9e7ff")
        except tk.TclError:
            pass

    def _choose_color(self) -> None:
        _rgb, color = colorchooser.askcolor(color=self.color_var.get().strip() or "#d9e7ff", parent=self.window)
        if color:
            self.color_var.set(color)

    def _cancel(self) -> None:
        self.result = None
        self.window.destroy()

    def _confirm(self) -> None:
        title = self.title_var.get().strip()
        script = self.script_text.get("1.0", "end").strip()
        color = self.color_var.get().strip() or "#d9e7ff"
        if not title:
            messagebox.showerror("Shortcut", "Please enter a title.", parent=self.window)
            return
        if not script:
            messagebox.showerror("Shortcut", "Please enter at least one script line.", parent=self.window)
            return
        try:
            self.swatch.configure(background=color)
        except tk.TclError as exc:
            messagebox.showerror("Shortcut", f"Invalid color value.\n\n{exc}", parent=self.window)
            return
        self.result = ShortcutDefinition(title=title, script=script, color=color)
        self.window.destroy()


class ManageShortcutsDialog:
    def __init__(self, app, host: tk.Misc | None = None) -> None:
        self.app = app
        self.shortcuts = deepcopy(get_project_shortcuts(app.project))
        self.saved_shortcuts = deepcopy(self.shortcuts)
        self.current_index: int | None = None
        self.embedded = host is not None

        self.window = host if host is not None else tk.Toplevel(app.root)
        if not self.embedded:
            self.window.title("Manage shortcuts")
            self.window.geometry("1080x720")
            self.window.minsize(820, 520)
            self.window.transient(app.root)
            self.window.protocol("WM_DELETE_WINDOW", self._cancel)
        content_row = 0 if self.embedded else 1
        footer_row = content_row + 1
        self.window.columnconfigure(1, weight=1)
        self.window.rowconfigure(content_row, weight=1)
        if not self.embedded:
            self.window.update_idletasks()
            self.window.deiconify()
            try:
                self.window.grab_set()
            except tk.TclError:
                pass

        if not self.embedded:
            toolbar = ttk.Frame(self.window, padding=12)
            toolbar.grid(row=0, column=0, columnspan=2, sticky="ew")
            toolbar.columnconfigure(0, weight=1)

        left = ttk.LabelFrame(self.window, text="Shortcuts", padding=12)
        left.grid(row=content_row, column=0, sticky="nsw", padx=(12, 8), pady=(0, 12))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        self.listbox = tk.Listbox(left, selectmode="extended", exportselection=False, width=30)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        left_scroll = ttk.Scrollbar(left, orient="vertical", command=self.listbox.yview)
        left_scroll.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=left_scroll.set)
        self.listbox.bind("<<ListboxSelect>>", self._on_selection_changed)
        left_buttons = ttk.Frame(left)
        left_buttons.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        left_buttons.columnconfigure(0, weight=1)
        ttk.Button(left_buttons, text="Add shortcut", command=self._add_shortcut).grid(row=0, column=0, sticky="w")
        ttk.Button(left_buttons, text="Clone entry", command=self._clone_selected).grid(row=0, column=1, sticky="e", padx=(8, 0))
        ttk.Button(left_buttons, text="Remove selected", command=self._remove_selected).grid(row=0, column=2, sticky="e", padx=(8, 0))

        right = ttk.LabelFrame(self.window, text="Edit selected shortcut", padding=12)
        right.grid(row=content_row, column=1, sticky="nsew", padx=(0, 12), pady=(0, 12))
        right.columnconfigure(1, weight=1)
        right.rowconfigure(1, weight=1)

        self.title_var = tk.StringVar()
        self.color_var = tk.StringVar(value="#d9e7ff")
        ttk.Label(right, text="Title").grid(row=0, column=0, sticky="w", pady=(0, 8), padx=(0, 12))
        self.title_entry = ttk.Entry(right, textvariable=self.title_var)
        self.title_entry.grid(row=0, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(right, text="Script").grid(row=1, column=0, sticky="nw", pady=(0, 8), padx=(0, 12))
        script_box = ttk.Frame(right)
        script_box.grid(row=1, column=1, sticky="nsew", pady=(0, 8))
        script_box.columnconfigure(0, weight=1)
        script_box.rowconfigure(0, weight=1)
        self.script_text = tk.Text(script_box, wrap="word")
        self.script_text.grid(row=0, column=0, sticky="nsew")
        script_scroll = ttk.Scrollbar(script_box, orient="vertical", command=self.script_text.yview)
        script_scroll.grid(row=0, column=1, sticky="ns")
        self.script_text.configure(yscrollcommand=script_scroll.set)

        ttk.Label(right, text="Color").grid(row=2, column=0, sticky="w", padx=(0, 12))
        color_row = ttk.Frame(right)
        color_row.grid(row=2, column=1, sticky="ew")
        color_row.columnconfigure(0, weight=1)
        self.color_entry = ttk.Entry(color_row, textvariable=self.color_var)
        self.color_entry.grid(row=0, column=0, sticky="ew")
        self.swatch = tk.Label(color_row, width=4, relief="solid", bd=1, background=self.color_var.get())
        self.swatch.grid(row=0, column=1, sticky="w", padx=(8, 8))
        self.color_button = ttk.Button(color_row, text="Choose...", command=self._choose_color)
        self.color_button.grid(row=0, column=2, sticky="w")
        self.color_var.trace_add("write", self._update_swatch)
        self.empty_hint_var = tk.StringVar(value="")
        ttk.Label(
            right,
            textvariable=self.empty_hint_var,
            style="Error.TLabel",
            wraplength=520,
            justify="left",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))

        actions = ttk.Frame(right)
        actions.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        actions.columnconfigure(0, weight=1)
        ttk.Button(actions, text="Apply edits", command=self._apply_current).grid(row=0, column=1, sticky="e")

        footer = ttk.Frame(self.window, padding=(12, 0, 12, 12))
        footer.grid(row=footer_row, column=0, columnspan=2, sticky="ew")
        footer.columnconfigure(0, weight=1)
        cancel_label = "Revert section" if self.embedded else "Cancel"
        save_label = "Save section" if self.embedded else "Save"
        ttk.Button(footer, text=cancel_label, command=self._cancel).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(footer, text=save_label, command=self._save).grid(row=0, column=2, padx=(8, 0))

        self._refresh_list()
        if not self.embedded:
            decorate_settings_window(self, "shortcuts")

    def _refresh_list(self) -> None:
        self.listbox.delete(0, "end")
        for item in self.shortcuts:
            self.listbox.insert("end", item.title)
        if self.shortcuts and self.current_index is None:
            self.listbox.selection_set(0)
            self._load_selected(0)
        elif not self.shortcuts:
            self.current_index = None
            self._clear_editor()
        self._update_editor_state()

    def _clear_editor(self) -> None:
        self.title_var.set("")
        self.color_var.set("#d9e7ff")
        self.script_text.configure(state="normal")
        self.script_text.delete("1.0", "end")

    def _choose_color(self) -> None:
        _rgb, color = colorchooser.askcolor(color=self.color_var.get().strip() or "#d9e7ff", parent=self.window)
        if color:
            self.color_var.set(color)

    def _update_swatch(self, *_args) -> None:
        try:
            self.swatch.configure(background=self.color_var.get().strip() or "#d9e7ff")
        except tk.TclError:
            pass

    def _selected_indices(self) -> list[int]:
        return sorted(self.listbox.curselection(), reverse=True)

    def _load_selected(self, index: int) -> None:
        if not (0 <= index < len(self.shortcuts)):
            return
        self.current_index = index
        item = self.shortcuts[index]
        self.title_var.set(item.title)
        self.color_var.set(item.color)
        self.script_text.delete("1.0", "end")
        self.script_text.insert("1.0", item.script)
        self._update_editor_state()

    def _on_selection_changed(self, _event=None) -> None:
        indices = self.listbox.curselection()
        if len(indices) != 1:
            self.current_index = None
            self._clear_editor()
            self._update_editor_state()
            return
        self._load_selected(indices[0])

    def _add_shortcut(self) -> None:
        created = ShortcutEditorDialog(
            self.window,
            title="Create shortcut",
            confirm_label="Create shortcut",
        ).show()
        if created is None:
            return
        self.shortcuts.append(created)
        self.current_index = len(self.shortcuts) - 1
        self._refresh_list()
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(self.current_index)
        self._load_selected(self.current_index)

    def _remove_selected(self) -> None:
        indices = self._selected_indices()
        if not indices:
            messagebox.showinfo("Remove shortcuts", "Please select one or more shortcuts first.", parent=self.window)
            return
        for index in indices:
            self.shortcuts.pop(index)
        self.current_index = None
        self._refresh_list()
        self._update_editor_state()

    def _clone_selected(self) -> None:
        indices = list(self.listbox.curselection())
        if len(indices) != 1:
            messagebox.showinfo("Clone shortcut", "Please select exactly one shortcut first.", parent=self.window)
            return
        if not self._persist_current():
            return
        index = indices[0]
        if not (0 <= index < len(self.shortcuts)):
            return
        cloned = deepcopy(self.shortcuts[index])
        cloned.title = make_copy_name([item.title for item in self.shortcuts], cloned.title)
        self.shortcuts.append(cloned)
        self.current_index = len(self.shortcuts) - 1
        self._refresh_list()
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(self.current_index)
        self._load_selected(self.current_index)

    def _update_editor_state(self) -> None:
        enabled = self.current_index is not None and 0 <= self.current_index < len(self.shortcuts)
        state = "normal" if enabled else "disabled"
        self.title_entry.configure(state=state)
        self.script_text.configure(state=state)
        self.color_entry.configure(state=state)
        self.color_button.configure(state=state)
        self.empty_hint_var.set("" if enabled else "Please select a shortcut or create a new one.")

    def _persist_current(self) -> bool:
        if self.current_index is None or not (0 <= self.current_index < len(self.shortcuts)):
            return True
        title = self.title_var.get().strip()
        script = self.script_text.get("1.0", "end").strip()
        color = self.color_var.get().strip() or "#d9e7ff"
        if not title:
            messagebox.showerror("Shortcut", "Please enter a title.", parent=self.window)
            return False
        if not script:
            messagebox.showerror("Shortcut", "Please enter at least one script line.", parent=self.window)
            return False
        try:
            self.swatch.configure(background=color)
        except tk.TclError as exc:
            messagebox.showerror("Shortcut", f"Invalid color value.\n\n{exc}", parent=self.window)
            return False
        self.shortcuts[self.current_index] = ShortcutDefinition(title=title, script=script, color=color)
        return True

    def _apply_current(self) -> None:
        if self.current_index is None:
            messagebox.showinfo("Apply edits", "Please select exactly one shortcut first.", parent=self.window)
            return
        if not self._persist_current():
            return
        current_title = self.shortcuts[self.current_index].title
        self._refresh_list()
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(self.current_index)
        self._load_selected(self.current_index)
        self.app.status_var.set(f"Applied edits to shortcut: {current_title}")

    def _import_shortcuts(self) -> None:
        path = filedialog.askopenfilename(
            title="Import shortcuts",
            filetypes=[("CryoPal_tomo shortcuts", f"*{SHORTCUTS_SUFFIX}"), ("JSON", "*.json")],
        )
        if not path:
            return
        try:
            imported = import_shortcuts(path)
        except Exception as exc:
            messagebox.showerror("Import failed", str(exc), parent=self.window)
            return
        if not imported:
            messagebox.showinfo("Import shortcuts", "No compatible shortcuts were found in this file.", parent=self.window)
            return
        selected = choose_items_dialog(
            self.window,
            "Import shortcuts",
            "Select which shortcuts should be imported.",
            [(item.title, item.title) for item in imported],
        )
        if selected is None:
            return
        imported_by_title = {item.title: item for item in imported}
        existing_by_title = {item.title.casefold(): index for index, item in enumerate(self.shortcuts)}
        for title in selected:
            item = deepcopy(imported_by_title[title])
            existing_index = existing_by_title.get(item.title.casefold())
            if existing_index is not None:
                overwrite = messagebox.askyesno(
                    "Duplicate shortcut title",
                    f"{item.title} already exists.\n\nChoose 'Yes' to overwrite it or 'No' to skip this shortcut.",
                    icon="warning",
                    parent=self.window,
                )
                if not overwrite:
                    continue
                self.shortcuts[existing_index] = item
            else:
                self.shortcuts.append(item)
                existing_by_title[item.title.casefold()] = len(self.shortcuts) - 1
        self.current_index = None
        self._refresh_list()
        self.app.status_var.set("Imported shortcuts")

    def _export_shortcuts(self) -> None:
        if not self.shortcuts:
            messagebox.showinfo("Export shortcuts", "No shortcuts available to export.", parent=self.window)
            return
        current_selection = {
            self.shortcuts[index].title
            for index in self.listbox.curselection()
            if 0 <= index < len(self.shortcuts)
        }
        selected = choose_items_dialog(
            self.window,
            "Export shortcuts",
            "Select which shortcuts should be exported.",
            [(item.title, item.title) for item in self.shortcuts],
            preselected=current_selection or None,
        )
        if selected is None:
            return
        export_items = [deepcopy(item) for item in self.shortcuts if item.title in set(selected)]
        if not export_items:
            messagebox.showinfo("Export shortcuts", "No shortcuts were selected for export.", parent=self.window)
            return
        path = filedialog.asksaveasfilename(
            title="Export shortcuts",
            defaultextension=SHORTCUTS_SUFFIX,
            filetypes=[("CryoPal_tomo shortcuts", f"*{SHORTCUTS_SUFFIX}"), ("JSON", "*.json")],
        )
        if not path:
            return
        try:
            export_shortcuts(path, export_items)
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc), parent=self.window)
            return
        self.app.status_var.set("Exported shortcuts")

    def _save(self) -> None:
        self.save_section(close_window=False)

    def save_section(self, *, close_window: bool = False) -> bool:
        if self.current_index is not None and not self._persist_current():
            return False
        set_project_shortcuts(self.app.project, self.shortcuts)
        self.saved_shortcuts = deepcopy(self.shortcuts)
        self.app.on_project_changed("shortcuts", status_message="Saved shortcuts")
        if close_window:
            self.window.destroy()
        return True

    def has_unsaved_changes(self) -> bool:
        if self.current_index is not None and not self._persist_current():
            return True
        return self.shortcuts != self.saved_shortcuts

    def _cancel(self) -> None:
        self.shortcuts = deepcopy(self.saved_shortcuts)
        self.current_index = None
        self._refresh_list()
