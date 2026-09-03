from __future__ import annotations

import os
import time
import tkinter as tk
from contextlib import contextmanager
from typing import Callable, Iterator, Sequence


class TkDebouncer:
    """Coalesce frequent Tk events into one callback on the UI thread."""

    def __init__(self, widget: tk.Misc, callback: Callable[[], None], *, delay_ms: int = 120) -> None:
        self.widget = widget
        self.callback = callback
        self.delay_ms = max(0, int(delay_ms))
        self._after_id: str | None = None

    def cancel(self) -> None:
        if self._after_id is None:
            return
        try:
            self.widget.after_cancel(self._after_id)
        except tk.TclError:
            pass
        self._after_id = None

    def schedule(self) -> None:
        self.cancel()
        try:
            if self.delay_ms:
                self._after_id = self.widget.after(self.delay_ms, self._run)
            else:
                self._after_id = self.widget.after_idle(self._run)
        except tk.TclError:
            self._after_id = None

    def flush(self) -> None:
        self.cancel()
        self.callback()

    def _run(self) -> None:
        self._after_id = None
        self.callback()


class TkLatestTask:
    """Guard async/UI work so only the newest scheduled result is applied."""

    def __init__(self, widget: tk.Misc) -> None:
        self.widget = widget
        self._generation = 0
        self._after_id: str | None = None

    def cancel(self) -> None:
        self._generation += 1
        if self._after_id is None:
            return
        try:
            self.widget.after_cancel(self._after_id)
        except tk.TclError:
            pass
        self._after_id = None

    def generation(self) -> int:
        self._generation += 1
        return self._generation

    def is_current(self, generation: int) -> bool:
        return generation == self._generation

    def schedule(self, callback: Callable[[int], None], *, delay_ms: int = 0) -> int:
        self.cancel()
        generation = self._generation

        def run() -> None:
            self._after_id = None
            if self.is_current(generation):
                callback(generation)

        try:
            if delay_ms:
                self._after_id = self.widget.after(delay_ms, run)
            else:
                self._after_id = self.widget.after_idle(run)
        except tk.TclError:
            self._after_id = None
        return generation


def chunked_treeview_replace(
    tree,
    rows: Sequence[tuple[str, tuple[object, ...], tuple[str, ...]]],
    *,
    widget: tk.Misc,
    generation: int,
    is_current: Callable[[int], bool],
    chunk_size: int = 150,
    on_complete: Callable[[], None] | None = None,
) -> None:
    """Replace Treeview rows without monopolizing the Tk event loop."""

    if not is_current(generation):
        return
    children = tree.get_children()
    if children:
        tree.delete(*children)

    total = len(rows)
    chunk_size = max(1, int(chunk_size))

    def insert_chunk(start: int = 0) -> None:
        if not is_current(generation):
            return
        end = min(start + chunk_size, total)
        for iid, values, tags in rows[start:end]:
            tree.insert("", "end", iid=iid, values=values, tags=tags)
        if end < total:
            try:
                widget.after(1, lambda: insert_chunk(end))
            except tk.TclError:
                return
        elif on_complete is not None:
            on_complete()

    insert_chunk()


@contextmanager
def perf_timer(label: str) -> Iterator[None]:
    """Print timing information when CRYOPAL_PERF is enabled."""

    if not os.environ.get("CRYOPAL_PERF"):
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        print(f"[CryoPal perf] {label}: {elapsed_ms:.1f} ms")
