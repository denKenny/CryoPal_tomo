from __future__ import annotations

import re
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Any


def technical_font_name() -> str:
    try:
        tkfont.nametofont("TkDefaultFont")
        return "TkDefaultFont"
    except tk.TclError:
        return "TkFixedFont"


def technical_row_height(*, minimum: int = 24, padding: int = 10) -> int:
    try:
        font = tkfont.nametofont(technical_font_name())
        return max(minimum, font.metrics("linespace") + max(padding, font.metrics("descent") + 4))
    except tk.TclError:
        return minimum


def bind_scrollable_canvas(
    canvas: tk.Canvas,
    window_id: int,
    inner: tk.Misc,
    *,
    allow_horizontal: bool = False,
    fill_vertical: bool = False,
) -> None:
    def sync_scrollregion(_event=None) -> None:
        bbox = canvas.bbox("all")
        if bbox is not None:
            canvas.configure(scrollregion=bbox)

    def sync_window_size(event=None) -> None:
        target_width = canvas.winfo_width() if event is None else event.width
        if allow_horizontal:
            target_width = max(target_width, inner.winfo_reqwidth())
        options: dict[str, int] = {"width": target_width}
        if fill_vertical:
            target_height = canvas.winfo_height() if event is None else event.height
            options["height"] = max(target_height, inner.winfo_reqheight())
        canvas.itemconfigure(window_id, **options)

    inner.bind("<Configure>", sync_scrollregion)
    canvas.bind("<Configure>", sync_window_size)
    canvas.after_idle(sync_scrollregion)
    canvas.after_idle(sync_window_size)


def create_scrollable_frame(
    parent: tk.Misc,
    *,
    allow_horizontal: bool = False,
    fill_vertical: bool = False,
    inner_padding: int | tuple[int, int, int, int] = 0,
) -> tuple[ttk.Frame, ttk.Frame, tk.Canvas]:
    host = ttk.Frame(parent)
    host.columnconfigure(0, weight=1)
    host.rowconfigure(0, weight=1)

    canvas = tk.Canvas(host, highlightthickness=0)
    canvas.grid(row=0, column=0, sticky="nsew")
    yscroll = ttk.Scrollbar(host, orient="vertical", command=canvas.yview)
    yscroll.grid(row=0, column=1, sticky="ns")
    canvas.configure(yscrollcommand=yscroll.set)

    if allow_horizontal:
        xscroll = ttk.Scrollbar(host, orient="horizontal", command=canvas.xview)
        xscroll.grid(row=1, column=0, sticky="ew")
        canvas.configure(xscrollcommand=xscroll.set)

    inner = ttk.Frame(canvas, padding=inner_padding)
    inner.columnconfigure(0, weight=1)
    window_id = canvas.create_window((0, 0), window=inner, anchor="nw")
    bind_scrollable_canvas(
        canvas,
        window_id,
        inner,
        allow_horizontal=allow_horizontal,
        fill_vertical=fill_vertical,
    )
    return host, inner, canvas


def fit_outer_canvas_to_viewport(
    canvas: tk.Canvas,
    window_id: int,
    inner: tk.Misc,
    event,
    *,
    allow_horizontal: bool = True,
) -> None:
    inner.update_idletasks()
    viewport_width = max(1, int(getattr(event, "width", 1) or 1))
    requested_width = inner.winfo_reqwidth()
    target_width = max(viewport_width, requested_width) if allow_horizontal else viewport_width
    canvas.itemconfigure(window_id, width=target_width)
    bbox = canvas.bbox("all")
    if bbox is not None:
        canvas.configure(scrollregion=bbox)


def make_copy_name(existing_names: list[str], original_name: str) -> str:
    raw_name = original_name.strip() or "Entry"
    base_name = re.sub(r"_copy\d+$", "", raw_name, flags=re.IGNORECASE)
    pattern = re.compile(rf"^{re.escape(base_name)}_copy(\d+)$", re.IGNORECASE)
    highest = 0
    existing_lookup = {name.casefold() for name in existing_names}
    for name in existing_names:
        match = pattern.match(name.strip())
        if match:
            try:
                highest = max(highest, int(match.group(1)))
            except ValueError:
                continue
    candidate_index = highest + 1
    candidate = f"{base_name}_copy{candidate_index}"
    while candidate.casefold() in existing_lookup:
        candidate_index += 1
        candidate = f"{base_name}_copy{candidate_index}"
    return candidate


def show_detail_dialog(
    parent: tk.Misc,
    title: str,
    sections: list[tuple[str, list[tuple[str, str]]]],
    command: str = "",
    *,
    command_height: int = 8,
) -> None:
    window = tk.Toplevel(parent)
    window.title(title)
    window.geometry("980x680")
    window.minsize(760, 480)
    window.transient(parent.winfo_toplevel())
    window.columnconfigure(0, weight=1)
    window.rowconfigure(0, weight=1)

    container = ttk.Frame(window, padding=12)
    container.grid(row=0, column=0, sticky="nsew")
    container.columnconfigure(0, weight=1)
    container.rowconfigure(0, weight=1)

    style = ttk.Style(window)
    style.configure(
        "Detail.Technical.Treeview",
        font=technical_font_name(),
        rowheight=technical_row_height(),
    )

    tree = ttk.Treeview(container, columns=("field", "value"), show="tree headings", style="Detail.Technical.Treeview")
    tree.heading("#0", text="Section")
    tree.heading("field", text="Field")
    tree.heading("value", text="Value")
    tree.column("#0", width=180, anchor="w", stretch=False)
    tree.column("field", width=240, anchor="w", stretch=False)
    tree.column("value", width=520, anchor="w", stretch=False)
    tree.grid(row=0, column=0, sticky="nsew")

    yscroll = ttk.Scrollbar(container, orient="vertical", command=tree.yview)
    yscroll.grid(row=0, column=1, sticky="ns")
    xscroll = ttk.Scrollbar(container, orient="horizontal", command=tree.xview)
    xscroll.grid(row=1, column=0, sticky="ew")
    tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

    for section_title, rows in sections:
        section_id = tree.insert("", "end", text=section_title, open=True, values=("", ""))
        for field, value in rows:
            tree.insert(section_id, "end", text="", values=(field, value))

    autosize_detail_tree_columns(tree, sections)

    next_row = 2
    if command:
        command_box = ttk.LabelFrame(container, text="Command", padding=12)
        command_box.grid(row=next_row, column=0, columnspan=2, sticky="nsew", pady=(12, 0))
        command_box.columnconfigure(0, weight=1)
        command_box.rowconfigure(0, weight=1)
        command_text = tk.Text(command_box, height=command_height, wrap="word", font=technical_font_name())
        command_text.grid(row=0, column=0, sticky="nsew")
        command_text.insert("1.0", command)
        command_scroll = ttk.Scrollbar(command_box, orient="vertical", command=command_text.yview)
        command_scroll.grid(row=0, column=1, sticky="ns")
        command_text.configure(yscrollcommand=command_scroll.set)
        next_row += 1

    footer = ttk.Frame(container)
    footer.grid(row=next_row, column=0, columnspan=2, sticky="e", pady=(12, 0))
    ttk.Button(footer, text="Close", command=window.destroy).grid(row=0, column=0)


def autosize_detail_tree_columns(
    tree: ttk.Treeview,
    sections: list[tuple[str, list[tuple[str, str]]]],
) -> None:
    try:
        font = tkfont.nametofont(technical_font_name())
    except tk.TclError:
        return

    padding = 24
    section_width = font.measure("Section") + padding
    field_width = font.measure("Field") + padding
    value_width = font.measure("Value") + padding

    for section_title, rows in sections:
        section_width = max(section_width, font.measure(str(section_title)) + padding)
        for field, value in rows:
            field_width = max(field_width, font.measure(str(field)) + padding)
            value_width = max(value_width, font.measure(str(value)) + padding)

    tree.column("#0", width=max(180, section_width), stretch=False)
    tree.column("field", width=max(240, field_width), stretch=False)
    tree.column("value", width=max(520, value_width), stretch=False)


def choose_items_dialog(
    parent: tk.Misc,
    title: str,
    message: str,
    items: list[tuple[str, str]],
    *,
    preselected: set[str] | None = None,
    select_all_label: str = "Select all",
) -> list[str] | None:
    window = tk.Toplevel(parent)
    window.title(title)
    window.geometry("760x620")
    window.minsize(560, 420)
    window.transient(parent.winfo_toplevel())
    window.grab_set()
    window.columnconfigure(0, weight=1)
    window.rowconfigure(1, weight=1)

    ttk.Label(
        window,
        text=message,
        wraplength=700,
        justify="left",
        padding=12,
    ).grid(row=0, column=0, sticky="ew")

    container = ttk.Frame(window, padding=(12, 0, 12, 12))
    container.grid(row=1, column=0, sticky="nsew")
    container.columnconfigure(0, weight=1)
    container.rowconfigure(1, weight=1)

    preselected = preselected if preselected is not None else {key for key, _label in items}
    item_vars: dict[str, tk.BooleanVar] = {
        key: tk.BooleanVar(value=key in preselected)
        for key, _label in items
    }
    select_all_var = tk.BooleanVar(value=all(var.get() for var in item_vars.values()) if item_vars else False)
    result: list[str] | None = None

    def sync_select_all() -> None:
        values = [var.get() for var in item_vars.values()]
        select_all_var.set(bool(values) and all(values))

    def on_toggle_all() -> None:
        for var in item_vars.values():
            var.set(select_all_var.get())

    header = ttk.Frame(container)
    header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
    header.columnconfigure(1, weight=1)
    ttk.Checkbutton(header, text=select_all_label, variable=select_all_var, command=on_toggle_all).grid(
        row=0, column=0, sticky="w"
    )
    ttk.Label(header, text=f"{len(items)} item(s)").grid(row=0, column=1, sticky="e")

    canvas = tk.Canvas(container, highlightthickness=0)
    canvas.grid(row=1, column=0, sticky="nsew")
    yscroll = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
    yscroll.grid(row=1, column=1, sticky="ns")
    xscroll = ttk.Scrollbar(container, orient="horizontal", command=canvas.xview)
    xscroll.grid(row=2, column=0, sticky="ew")
    canvas.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

    rows = ttk.Frame(canvas)
    rows.columnconfigure(0, weight=1)
    window_id = canvas.create_window((0, 0), window=rows, anchor="nw")
    bind_scrollable_canvas(canvas, window_id, rows, allow_horizontal=True)

    for row_index, (key, label) in enumerate(items):
        ttk.Checkbutton(
            rows,
            text=label,
            variable=item_vars[key],
            command=sync_select_all,
        ).grid(row=row_index, column=0, sticky="w", pady=3)

    def on_cancel() -> None:
        nonlocal result
        result = None
        window.destroy()

    def on_confirm() -> None:
        nonlocal result
        result = [key for key, _label in items if item_vars[key].get()]
        window.destroy()

    footer = ttk.Frame(window, padding=(12, 0, 12, 12))
    footer.grid(row=2, column=0, sticky="ew")
    footer.columnconfigure(0, weight=1)
    ttk.Button(footer, text="Cancel", command=on_cancel).grid(row=0, column=1, padx=(8, 0))
    ttk.Button(footer, text="OK", command=on_confirm).grid(row=0, column=2, padx=(8, 0))

    window.wait_window()
    return result


def choose_grouped_items_dialog(
    parent: tk.Misc,
    title: str,
    message: str,
    groups: list[Any],
    *,
    preselected: set[str] | None = None,
    select_all_label: str = "Select all",
) -> list[str] | None:
    window = tk.Toplevel(parent)
    window.title(title)
    window.geometry("860x680")
    window.minsize(620, 460)
    window.transient(parent.winfo_toplevel())
    window.grab_set()
    window.columnconfigure(0, weight=1)
    window.rowconfigure(1, weight=1)

    ttk.Label(
        window,
        text=message,
        wraplength=800,
        justify="left",
        padding=12,
    ).grid(row=0, column=0, sticky="ew")

    container = ttk.Frame(window, padding=(12, 0, 12, 12))
    container.grid(row=1, column=0, sticky="nsew")
    container.columnconfigure(0, weight=1)
    container.rowconfigure(1, weight=1)

    selectable_items = [
        (str(item.key), str(item.label))
        for group in groups
        for item in getattr(group, "items", ())
        if "__empty__" not in str(item.key)
    ]
    preselected = preselected if preselected is not None else {key for key, _label in selectable_items}
    item_vars: dict[str, tk.BooleanVar] = {
        key: tk.BooleanVar(value=key in preselected)
        for key, _label in selectable_items
    }
    group_vars: dict[str, tk.BooleanVar] = {}
    group_bodies: dict[str, ttk.Frame] = {}
    group_expanded: dict[str, tk.BooleanVar] = {}
    select_all_var = tk.BooleanVar(
        value=bool(item_vars) and all(variable.get() for variable in item_vars.values())
    )
    result: list[str] | None = None

    def sync_select_all() -> None:
        values = [variable.get() for variable in item_vars.values()]
        select_all_var.set(bool(values) and all(values))

    def sync_group(group_key: str) -> None:
        keys = [
            str(item.key)
            for group in groups
            if str(group.key) == group_key
            for item in getattr(group, "items", ())
            if str(item.key) in item_vars
        ]
        values = [item_vars[key].get() for key in keys]
        group_vars[group_key].set(bool(values) and all(values))
        sync_select_all()

    def on_toggle_all() -> None:
        value = select_all_var.get()
        for variable in item_vars.values():
            variable.set(value)
        for group_key in group_vars:
            sync_group(group_key)

    def on_toggle_group(group_key: str) -> None:
        value = group_vars[group_key].get()
        for group in groups:
            if str(group.key) != group_key:
                continue
            for item in getattr(group, "items", ()):
                key = str(item.key)
                if key in item_vars:
                    item_vars[key].set(value)
            break
        sync_group(group_key)

    def toggle_group_body(group_key: str) -> None:
        expanded = group_expanded[group_key]
        expanded.set(not expanded.get())
        body = group_bodies[group_key]
        if expanded.get():
            body.grid()
        else:
            body.grid_remove()

    header = ttk.Frame(container)
    header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
    header.columnconfigure(1, weight=1)
    ttk.Checkbutton(header, text=select_all_label, variable=select_all_var, command=on_toggle_all).grid(
        row=0, column=0, sticky="w"
    )
    ttk.Label(header, text=f"{len(selectable_items)} item(s)").grid(row=0, column=1, sticky="e")

    canvas = tk.Canvas(container, highlightthickness=0)
    canvas.grid(row=1, column=0, sticky="nsew")
    yscroll = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
    yscroll.grid(row=1, column=1, sticky="ns")
    xscroll = ttk.Scrollbar(container, orient="horizontal", command=canvas.xview)
    xscroll.grid(row=2, column=0, sticky="ew")
    canvas.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

    rows = ttk.Frame(canvas)
    rows.columnconfigure(0, weight=1)
    window_id = canvas.create_window((0, 0), window=rows, anchor="nw")
    bind_scrollable_canvas(canvas, window_id, rows, allow_horizontal=True)

    for row_index, group in enumerate(groups):
        group_key = str(group.key)
        item_keys = [str(item.key) for item in getattr(group, "items", ()) if str(item.key) in item_vars]
        group_vars[group_key] = tk.BooleanVar(
            value=bool(item_keys) and all(item_vars[key].get() for key in item_keys)
        )
        group_expanded[group_key] = tk.BooleanVar(value=True)

        group_frame = ttk.LabelFrame(rows, padding=8)
        group_frame.grid(row=row_index, column=0, sticky="ew", pady=6)
        group_frame.columnconfigure(1, weight=1)

        toggle_button = ttk.Button(
            group_frame,
            text="Hide" if group_expanded[group_key].get() else "Show",
            width=6,
            command=lambda current=group_key: toggle_group_body(current),
        )
        toggle_button.grid(row=0, column=0, sticky="w")

        def refresh_toggle(button: ttk.Button = toggle_button, key: str = group_key) -> None:
            button.configure(text="Hide" if group_expanded[key].get() else "Show")

        ttk.Checkbutton(
            group_frame,
            text=str(group.label),
            variable=group_vars[group_key],
            command=lambda current=group_key: on_toggle_group(current),
            state="normal" if item_keys else "disabled",
        ).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Label(
            group_frame,
            text=f"{len(item_keys)} item(s)" if item_keys else "No items available",
        ).grid(row=0, column=2, sticky="e")

        body = ttk.Frame(group_frame)
        body.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        body.columnconfigure(0, weight=1)
        group_bodies[group_key] = body

        for item_index, item in enumerate(getattr(group, "items", ())):
            key = str(item.key)
            label = str(item.label)
            if key in item_vars:
                ttk.Checkbutton(
                    body,
                    text=label,
                    variable=item_vars[key],
                    command=lambda current=group_key: sync_group(current),
                ).grid(row=item_index, column=0, sticky="w", pady=2, padx=(18, 0))
            else:
                ttk.Label(
                    body,
                    text=label,
                    foreground="#6b7280",
                ).grid(row=item_index, column=0, sticky="w", pady=2, padx=(18, 0))

        def toggle_and_refresh(current: str = group_key, button: ttk.Button = toggle_button) -> None:
            toggle_group_body(current)
            refresh_toggle(button, current)

        toggle_button.configure(command=toggle_and_refresh)

    def on_cancel() -> None:
        nonlocal result
        result = None
        window.destroy()

    def on_confirm() -> None:
        nonlocal result
        result = [key for key, _label in selectable_items if item_vars[key].get()]
        window.destroy()

    footer = ttk.Frame(window, padding=(12, 0, 12, 12))
    footer.grid(row=2, column=0, sticky="ew")
    footer.columnconfigure(0, weight=1)
    ttk.Button(footer, text="Cancel", command=on_cancel).grid(row=0, column=1, padx=(8, 0))
    ttk.Button(footer, text="OK", command=on_confirm).grid(row=0, column=2, padx=(8, 0))

    window.wait_window()
    return result
