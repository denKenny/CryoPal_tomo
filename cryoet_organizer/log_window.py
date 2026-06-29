from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import tkinter as tk
from tkinter import ttk


class ProcessLogWindow:
    """Floating window that streams stdout/stderr from a subprocess in real time."""

    def __init__(self, parent: tk.Misc, title: str = "Process output") -> None:
        self.window = tk.Toplevel(parent)
        self.window.title(title)
        self.window.geometry("860x520")
        self.window.minsize(600, 320)
        self.window.protocol("WM_DELETE_WINDOW", self._handle_close)

        self._queue: queue.Queue[str | None] = queue.Queue()
        self._process: subprocess.Popen | None = None
        self._terminate_process_on_close = False

        self._build()
        self._poll()

    def _build(self) -> None:
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)

        text_frame = ttk.Frame(self.window, padding=(8, 8, 8, 0))
        text_frame.grid(row=0, column=0, sticky="nsew")
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)

        self.text = tk.Text(
            text_frame,
            wrap="word",
            state="disabled",
            font="TkDefaultFont",
            background="#1e1e1e",
            foreground="#d4d4d4",
            insertbackground="#d4d4d4",
        )
        self.text.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(text_frame, orient="vertical", command=self.text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=scrollbar.set)

        bottom = ttk.Frame(self.window, padding=(8, 6, 8, 8))
        bottom.grid(row=1, column=0, sticky="ew")
        bottom.columnconfigure(0, weight=1)

        self.status_var = tk.StringVar(value="Running…")
        ttk.Label(bottom, textvariable=self.status_var, anchor="w").grid(
            row=0, column=0, sticky="ew"
        )
        self.close_button = ttk.Button(
            bottom,
            text="Close",
            command=self.window.destroy,
            state="disabled",
        )
        self.close_button.grid(row=0, column=1, sticky="e", padx=(8, 0))

    def attach_process(
        self,
        process: subprocess.Popen,
        *,
        terminate_process_on_close: bool = False,
    ) -> None:
        self._process = process
        self._terminate_process_on_close = terminate_process_on_close
        threading.Thread(target=self._read_output, daemon=True).start()

    def append_message(self, text: str) -> None:
        self._append_text(text)

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    def finish(self, status: str = "Finished.") -> None:
        self.status_var.set(status)
        if self.window.winfo_exists():
            try:
                self.close_button.configure(state="normal")
            except tk.TclError:
                pass

    def _read_output(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            self._queue.put(None)
            return
        try:
            for line in iter(process.stdout.readline, ""):
                self._queue.put(line)
        except Exception:
            pass
        finally:
            self._queue.put(None)

    def _poll(self) -> None:
        try:
            while True:
                item = self._queue.get_nowait()
                if item is None:
                    self._on_finished()
                    return
                self._append_text(item)
        except queue.Empty:
            pass
        if self.window.winfo_exists():
            try:
                self.window.after(50, self._poll)
            except tk.TclError:
                pass

    def _append_text(self, text: str) -> None:
        if not self.window.winfo_exists():
            return
        self.text.configure(state="normal")
        self.text.insert("end", text)
        self.text.see("end")
        self.text.configure(state="disabled")

    def _handle_close(self) -> None:
        process = self._process
        if (
            self._terminate_process_on_close
            and process is not None
            and process.poll() is None
        ):
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except Exception:
                try:
                    process.terminate()
                except Exception:
                    pass
        self.window.destroy()

    def _on_finished(self) -> None:
        rc = None
        if self._process is not None:
            rc = self._process.returncode
            if rc is None:
                rc = self._process.poll()
        if rc == 0:
            status = "Finished successfully."
        elif rc is None:
            status = "Finished."
        else:
            status = f"Finished with exit code {rc}."
        self.status_var.set(status)
        if self.window.winfo_exists():
            try:
                self.close_button.configure(state="normal")
            except tk.TclError:
                pass


class ShortcutLaunchWindow:
    """Small progress window for launching GUI-oriented shortcuts."""

    def __init__(self, parent: tk.Misc, title: str = "Launching shortcut") -> None:
        self.window = tk.Toplevel(parent)
        self.window.title(title)
        self.window.geometry("460x170")
        self.window.minsize(420, 150)
        self.window.transient(parent.winfo_toplevel())
        self.window.protocol("WM_DELETE_WINDOW", self._handle_close)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)

        self._queue: queue.Queue[str | None] = queue.Queue()
        self._process: subprocess.Popen | None = None
        self._output_lines: list[str] = []
        self._finished = False

        body = ttk.Frame(self.window, padding=16)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)

        self.message_var = tk.StringVar(
            value="CryoPal is preparing and launching the selected shortcut."
        )
        ttk.Label(
            body,
            textvariable=self.message_var,
            wraplength=400,
            justify="left",
        ).grid(row=0, column=0, sticky="w")

        self.progress = ttk.Progressbar(body, orient="horizontal", mode="indeterminate", length=320)
        self.progress.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self.progress.start(10)

        self.status_var = tk.StringVar(value="Launching…")
        ttk.Label(body, textvariable=self.status_var).grid(row=2, column=0, sticky="w", pady=(10, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=0, sticky="e", pady=(12, 0))
        self.details_button = ttk.Button(
            buttons,
            text="Show details",
            command=self._show_details,
            state="disabled",
        )
        self.details_button.grid(row=0, column=0, padx=(0, 8))
        self.close_button = ttk.Button(
            buttons,
            text="Close",
            command=self._handle_close,
        )
        self.close_button.grid(row=0, column=1)

        self.window.after(50, self._poll)

    def attach_process(self, process: subprocess.Popen) -> None:
        self._process = process
        threading.Thread(target=self._read_output, daemon=True).start()

    def _read_output(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            self._queue.put(None)
            return
        try:
            for line in iter(process.stdout.readline, ""):
                self._queue.put(line)
        except Exception:
            pass
        finally:
            self._queue.put(None)

    def _poll(self) -> None:
        try:
            while True:
                item = self._queue.get_nowait()
                if item is None:
                    self._on_finished()
                    return
                self._output_lines.append(item)
        except queue.Empty:
            pass
        if self.window.winfo_exists():
            try:
                self.window.after(50, self._poll)
            except tk.TclError:
                pass

    def _on_finished(self) -> None:
        self._finished = True
        try:
            self.progress.stop()
        except tk.TclError:
            pass
        process = self._process
        rc = process.returncode if process is not None else 0
        if rc is None and process is not None:
            rc = process.poll()
        if rc == 0:
            self.status_var.set("Shortcut launched.")
            if self.window.winfo_exists():
                self.window.after(700, self._destroy_if_exists)
        else:
            self.status_var.set(f"Shortcut failed with exit code {rc}.")
            self.message_var.set(
                "The shortcut could not be launched successfully. "
                "Use 'Show details' to inspect the shell output."
            )
            try:
                self.details_button.configure(state="normal")
            except tk.TclError:
                pass

    def _show_details(self) -> None:
        details = ProcessLogWindow(self.window, title="Shortcut details")
        details.append_message("".join(self._output_lines) or "No output was captured.\n")
        process = self._process
        rc = process.returncode if process is not None else None
        if rc is None and process is not None:
            rc = process.poll()
        if rc is None:
            details.finish("Shortcut output")
        elif rc == 0:
            details.finish("Finished successfully.")
        else:
            details.finish(f"Finished with exit code {rc}.")

    def _terminate_process(self) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except Exception:
            try:
                process.terminate()
            except Exception:
                pass

    def _destroy_if_exists(self) -> None:
        if self.window.winfo_exists():
            self.window.destroy()

    def _handle_close(self) -> None:
        if not self._finished:
            self._terminate_process()
        self._destroy_if_exists()
