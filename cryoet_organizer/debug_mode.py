from __future__ import annotations

import itertools
import time
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, ttk


@dataclass
class DebugSessionState:
    enabled: bool = False
    workspace_dir: str = ""
    started_at: str = ""
    log_path: str = ""


class SimulatedProcess:
    _pid_counter = itertools.count(start=-1, step=-1)

    def __init__(self, command: str, cwd: str | None = None) -> None:
        self.pid = next(self._pid_counter)
        self.command = command
        self.cwd = cwd
        self.returncode: int | None = None
        self._terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self) -> int:
        if self.returncode is not None:
            return self.returncode
        time.sleep(0.08)
        self.returncode = -15 if self._terminated else 0
        return self.returncode

    def terminate(self) -> None:
        self._terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.terminate()


class DebugLogWindow:
    def __init__(self, parent: tk.Misc, workspace_dir: str, on_close) -> None:
        self.window = tk.Toplevel(parent)
        self.window.title("Debug mode verbose output")
        self.window.geometry("960x560")
        self.window.minsize(700, 360)
        self._on_close = on_close
        self._workspace_dir = workspace_dir
        self.window.protocol("WM_DELETE_WINDOW", self._handle_close)

        self._build()

    def _build(self) -> None:
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(1, weight=1)

        top = ttk.Frame(self.window, padding=(10, 10, 10, 6))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)
        ttk.Label(
            top,
            text="Verbose output for Debug mode. Commands are simulated and not executed.",
            justify="left",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(top, text=f"Workspace: {self._workspace_dir}", justify="left").grid(
            row=1, column=0, sticky="w", pady=(4, 0)
        )

        body = ttk.Frame(self.window, padding=(10, 0, 10, 0))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        self.text = tk.Text(
            body,
            wrap="word",
            state="disabled",
            font="TkDefaultFont",
            background="#11161d",
            foreground="#dce4ef",
            insertbackground="#dce4ef",
        )
        self.text.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=yscroll.set)

        bottom = ttk.Frame(self.window, padding=(10, 8, 10, 10))
        bottom.grid(row=2, column=0, sticky="ew")
        bottom.columnconfigure(0, weight=1)
        ttk.Button(bottom, text="Clear", command=self.clear).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(bottom, text="Export log", command=self.export_log).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(bottom, text="Close", command=self._handle_close).grid(row=0, column=3)

    def append(self, line: str) -> None:
        self.text.configure(state="normal")
        self.text.insert("end", line.rstrip() + "\n")
        self.text.see("end")
        self.text.configure(state="disabled")

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def export_log(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self.window,
            title="Export debug log",
            defaultextension=".log",
            initialfile="cryopal_debug.log",
            filetypes=[("Log files", "*.log"), ("Text", "*.txt"), ("All files", "*")],
        )
        if not path:
            return
        Path(path).write_text(self.text.get("1.0", "end"), encoding="utf-8")

    def _handle_close(self) -> None:
        try:
            if callable(self._on_close):
                self._on_close()
        finally:
            self.window.destroy()


def timestamped_debug_line(level: str, message: str) -> str:
    timestamp = datetime.now().strftime("%H:%M:%S")
    return f"{timestamp} | {level.upper():<8} | {message}"
