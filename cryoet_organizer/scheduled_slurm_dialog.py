from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from cryoet_organizer.dialogs import show_detail_dialog
from cryoet_organizer.slurm_override_ui import SlurmOverrideUI


def ask_running_scheduled_jobs_mode(parent: tk.Misc) -> str | None:
    result = {"mode": None}
    window = tk.Toplevel(parent)
    window.title("Run scheduled jobs")
    window.geometry("560x210")
    window.transient(parent.winfo_toplevel())
    window.grab_set()
    window.columnconfigure(0, weight=1)

    ttk.Label(
        window,
        text=(
            "Scheduled jobs or other commands are still running.\n\n"
            "Do you want to cancel, start the new scheduled jobs in parallel, or wait "
            "until the current work has finished and then run them afterwards?"
        ),
        wraplength=500,
        justify="left",
        padding=(16, 16, 16, 12),
    ).grid(row=0, column=0, sticky="ew")

    buttons = ttk.Frame(window, padding=(16, 0, 16, 16))
    buttons.grid(row=1, column=0, sticky="ew")
    buttons.columnconfigure(0, weight=1)
    ttk.Button(buttons, text="Cancel", command=window.destroy).grid(row=0, column=1, padx=(8, 0))
    ttk.Button(
        buttons,
        text="Run now in parallel",
        command=lambda: (result.__setitem__("mode", "parallel"), window.destroy()),
    ).grid(row=0, column=2, padx=(8, 0))
    ttk.Button(
        buttons,
        text="Wait & run afterwards",
        command=lambda: (result.__setitem__("mode", "wait"), window.destroy()),
    ).grid(row=0, column=3, padx=(8, 0))

    window.wait_window()
    return result["mode"]


def ask_scheduled_slurm_mode(parent: tk.Misc) -> str | None:
    result = {"mode": None}
    window = tk.Toplevel(parent)
    window.title("Submit scheduled jobs to Slurm")
    window.geometry("660x240")
    window.minsize(620, 220)
    window.transient(parent.winfo_toplevel())
    window.grab_set()
    window.columnconfigure(0, weight=1)
    window.rowconfigure(0, weight=1)

    body = ttk.Frame(window, padding=16)
    body.grid(row=0, column=0, sticky="nsew")
    body.columnconfigure(0, weight=1)
    body.rowconfigure(1, weight=1)

    ttk.Label(
        body,
        text="Submit scheduled jobs to Slurm",
        font=("TkDefaultFont", 12, "bold"),
    ).grid(row=0, column=0, sticky="w")
    ttk.Label(
        body,
        text=(
            "Do you want to submit each job separately in sequence, or join all scheduled jobs "
            "into one collective Slurm submission?"
        ),
        wraplength=600,
        justify="left",
    ).grid(row=1, column=0, sticky="nw", pady=(12, 0))

    buttons = ttk.Frame(body)
    buttons.grid(row=2, column=0, sticky="e", pady=(20, 0))
    buttons.columnconfigure(0, weight=1)
    ttk.Button(buttons, text="Cancel", command=window.destroy).grid(row=0, column=0, padx=(0, 8))
    ttk.Button(
        buttons,
        text="Separately",
        command=lambda: (result.__setitem__("mode", "separate"), window.destroy()),
    ).grid(row=0, column=1, padx=(0, 8))
    ttk.Button(
        buttons,
        text="Collectively",
        command=lambda: (result.__setitem__("mode", "collective"), window.destroy()),
    ).grid(row=0, column=2)

    window.wait_window()
    return result["mode"]


class CollectiveSlurmSubmissionDialog:
    def __init__(
        self,
        app,
        parent: tk.Misc,
        *,
        initial_profile: str,
        initial_overrides: dict[str, str],
        script_builder: Callable[[str, dict[str, str]], str],
    ) -> None:
        self.app = app
        self.parent = parent
        self.script_builder = script_builder
        self.profile_var = tk.StringVar(value=initial_profile)
        self.override_ui = SlurmOverrideUI(app, self.profile_var)
        self._result: tuple[str, dict[str, str]] | None = None
        self._initial_overrides = dict(initial_overrides)

        self.window = tk.Toplevel(parent)
        self.window.title("Collective Slurm submission")
        self.window.geometry("980x520")
        self.window.transient(parent.winfo_toplevel())
        self.window.grab_set()
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(1, weight=1)

        self._build()
        self.override_ui.rebuild(self._initial_overrides, preserve_existing=False)

    def _build(self) -> None:
        ttk.Label(
            self.window,
            text="Which Slurm profile should be used for the joined submission?",
            wraplength=900,
            justify="left",
            padding=(16, 16, 16, 8),
        ).grid(row=0, column=0, sticky="ew")

        content = ttk.Frame(self.window, padding=(16, 0, 16, 16))
        content.grid(row=1, column=0, sticky="nsew")
        content.columnconfigure(1, weight=1)
        content.rowconfigure(1, weight=1)

        ttk.Label(content, text="Slurm profile").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 8))
        self.profile_combo = ttk.Combobox(
            content,
            textvariable=self.profile_var,
            state="readonly",
            values=self.app.slurm_profile_names(),
            width=26,
        )
        self.profile_combo.grid(row=0, column=1, sticky="w", pady=(0, 8))
        self.profile_combo.bind("<<ComboboxSelected>>", lambda _e: self.override_ui.rebuild(preserve_existing=False))

        overrides_box = ttk.LabelFrame(content, text="Slurm parameter overrides", padding=12)
        overrides_box.grid(row=1, column=0, columnspan=2, sticky="nsew")
        overrides_box.columnconfigure(0, weight=1)
        self.overrides_frame = ttk.Frame(overrides_box)
        self.overrides_frame.grid(row=0, column=0, sticky="ew")
        self.override_ui.register_frame(self.overrides_frame)

        buttons = ttk.Frame(self.window, padding=(16, 0, 16, 16))
        buttons.grid(row=2, column=0, sticky="ew")
        buttons.columnconfigure(1, weight=1)
        ttk.Button(buttons, text="Cancel", command=self.window.destroy).grid(row=0, column=0)
        ttk.Button(buttons, text="Preview submission", command=self._preview).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Button(buttons, text="Submit job", command=self._submit).grid(row=0, column=2, sticky="e", padx=(8, 0))

    def _preview(self) -> None:
        profile_name = self.profile_var.get().strip()
        if not profile_name:
            messagebox.showerror("Slurm profile missing", "Please select a Slurm profile first.")
            return
        try:
            script = self.script_builder(profile_name, self.override_ui.metadata())
        except Exception as exc:
            messagebox.showerror("Preview failed", str(exc))
            return
        show_detail_dialog(
            self.window,
            "Submission preview",
            [("Overview", [("Profile", profile_name)])],
            command=script,
            command_height=20,
        )

    def _submit(self) -> None:
        profile_name = self.profile_var.get().strip()
        if not profile_name:
            messagebox.showerror("Slurm profile missing", "Please select a Slurm profile first.")
            return
        self._result = (profile_name, self.override_ui.metadata())
        self.window.destroy()

    def show(self) -> tuple[str, dict[str, str]] | None:
        self.window.wait_window()
        return self._result
