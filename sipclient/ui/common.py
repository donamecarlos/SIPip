"""Sistema visual compartido: colores, tipografias y widgets reutilizables."""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk
from typing import Callable, Iterable, Sequence

BG = "#16181d"
CARD = "#1f2228"
CARD_ALT = "#12141a"
BORDER = "#333841"
BORDER_SOFT = "#3a3f47"
SIDEBAR_BG = "#111318"
SIDEBAR_HOVER = "#1d2027"
SIDEBAR_ACTIVE = "#2563eb"
TEXT = "#e6e9ef"
MUTED = "#9aa2af"
ACCENT = "#4f8cff"
ACCENT_HOVER = "#2f5fd0"
OK = "#3fb950"
OK_HOVER = "#2ea043"
WARN = "#f0a03a"
WARN_BG = "#4a4023"
ERR = "#ff6b6b"
DANGER = "#e5534b"
DANGER_BG = "#4a2b2b"
SURFACE = "#2a2f37"
SURFACE_HOVER = "#343a44"
ENTRY_BG = "#12141a"
KEY_BG = "#2a2f37"
DISABLED_BG = "#23262d"
DISABLED_FG = "#5c626d"
ON_ACCENT = "#ffffff"
VOICEMAIL = "#fdd663"
TREE_ALT = "#23262d"
TREE_FAVORITE = "#33301f"

FONT = ("Segoe UI", 10)
FONT_SMALL = ("Segoe UI", 9)
FONT_TINY = ("Segoe UI", 8)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_TITLE = ("Segoe UI", 15, "bold")
FONT_HEADING = ("Segoe UI", 11, "bold")
FONT_MONO = ("Consolas", 10)
FONT_KEY = ("Segoe UI", 20, "bold")

STATUS_COLORS = {
    "registered": OK,
    "registering": ACCENT,
    "retrying": WARN,
    "failed": ERR,
    "idle": MUTED,
}


def apply_ttk_theme(root: tk.Tk) -> None:
    """Tema oscuro para los widgets ttk (Treeview, Entry, Combobox, ...)."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=BG, foreground=TEXT, fieldbackground=ENTRY_BG)
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure(
        "TEntry",
        fieldbackground=ENTRY_BG,
        foreground=TEXT,
        insertcolor=TEXT,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        padding=(6, 7),
    )
    style.map(
        "TEntry",
        fieldbackground=[("disabled", DISABLED_BG), ("readonly", ENTRY_BG)],
        foreground=[("disabled", DISABLED_FG)],
    )
    style.configure(
        "TCombobox",
        fieldbackground=ENTRY_BG,
        background=SURFACE,
        foreground=TEXT,
        arrowcolor=TEXT,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        padding=(6, 7),
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", ENTRY_BG), ("disabled", DISABLED_BG)],
        foreground=[("readonly", TEXT), ("disabled", DISABLED_FG)],
        selectbackground=[("readonly", ENTRY_BG)],
        selectforeground=[("readonly", TEXT)],
        arrowcolor=[("disabled", DISABLED_FG)],
    )
    style.configure(
        "TCheckbutton",
        background=BG,
        foreground=TEXT,
        focuscolor=BG,
        padding=(0, 4),
        indicatorcolor=SURFACE,
    )
    style.map(
        "TCheckbutton",
        background=[("active", BG), ("disabled", BG)],
        foreground=[("active", ACCENT), ("disabled", DISABLED_FG)],
        indicatorcolor=[("selected", ACCENT), ("disabled", DISABLED_BG)],
    )
    style.configure(
        "TButton",
        background=SURFACE,
        foreground=TEXT,
        focuscolor=BG,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        padding=(10, 7),
    )
    style.map(
        "TButton",
        background=[("active", SURFACE_HOVER), ("disabled", DISABLED_BG)],
        foreground=[("disabled", DISABLED_FG)],
    )
    style.configure(
        "Treeview",
        background=ENTRY_BG,
        fieldbackground=ENTRY_BG,
        foreground=TEXT,
        bordercolor=BORDER,
        rowheight=28,
    )
    style.map(
        "Treeview",
        background=[("selected", SIDEBAR_ACTIVE)],
        foreground=[("selected", ON_ACCENT)],
    )
    style.configure(
        "Treeview.Heading",
        background=SURFACE,
        foreground=TEXT,
        relief="flat",
        font=FONT_SMALL,
        padding=(8, 8),
    )
    style.map(
        "Treeview.Heading",
        background=[("active", SURFACE_HOVER)],
        foreground=[("active", TEXT)],
    )
    style.configure(
        "Vertical.TScrollbar",
        background=SURFACE,
        troughcolor=BG,
        bordercolor=BG,
        arrowcolor=MUTED,
        gripcolor=SURFACE,
    )
    style.map(
        "Vertical.TScrollbar",
        background=[("active", SURFACE_HOVER), ("pressed", SURFACE_HOVER)],
        arrowcolor=[("active", TEXT)],
    )
    style.configure(
        "TScale",
        background=BG,
        troughcolor=SURFACE,
        slidercolor=ACCENT,
        bordercolor=BG,
        lightcolor=BG,
        darkcolor=BG,
    )


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_timestamp(epoch: float, with_date: bool = True) -> str:
    stamp = time.localtime(epoch)
    if with_date and time.localtime().tm_yday != stamp.tm_yday:
        return time.strftime("%d/%m %H:%M", stamp)
    return time.strftime("%H:%M:%S", stamp)


def format_full_timestamp(epoch: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch))


class Card(tk.Frame):
    """Tarjeta blanca con borde y titulo opcional."""

    def __init__(
        self,
        parent: tk.Misc,
        title: str = "",
        subtitle: str = "",
        subtitle_wrap: int = 260,
        **kwargs: object,
    ) -> None:
        super().__init__(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1, **kwargs)
        self.columnconfigure(0, weight=1)
        if title:
            tk.Label(
                self, text=title, bg=CARD, fg=TEXT, font=FONT_HEADING, anchor="w"
            ).grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 0))
        if subtitle:
            tk.Label(
                self, text=subtitle, bg=CARD, fg=MUTED, font=FONT_SMALL, anchor="w", justify="left",
                wraplength=subtitle_wrap,
            ).grid(row=1, column=0, sticky="ew", padx=16, pady=(2, 0))
        self.body_row = 2 if subtitle else (1 if title else 0)

    def body(self, **kwargs: object) -> tk.Frame:
        frame = tk.Frame(self, bg=CARD, **kwargs)
        frame.grid(row=self.body_row, column=0, sticky="nsew", padx=16, pady=16)
        return frame


class StatusDot(tk.Frame):
    """Circulo de color que refleja el estado de la sesion."""

    def __init__(self, parent: tk.Misc, diameter: int = 10) -> None:
        super().__init__(parent, bg=parent.cget("bg") if isinstance(parent, (tk.Frame, tk.Label)) else BG)
        self._diameter = diameter
        self._canvas = tk.Canvas(
            self, width=diameter, height=diameter, highlightthickness=0, bg=self.cget("bg")
        )
        self._canvas.pack()
        self._oval = self._canvas.create_oval(0, 0, diameter, diameter, fill=MUTED, outline="")

    def set_color(self, color: str) -> None:
        self._canvas.itemconfigure(self._oval, fill=color)


class SectionTitle(tk.Frame):
    def __init__(self, parent: tk.Misc, title: str, subtitle: str = "") -> None:
        super().__init__(parent, bg=BG)
        tk.Label(self, text=title, bg=BG, fg=TEXT, font=FONT_TITLE, anchor="w").pack(anchor="w")
        if subtitle:
            tk.Label(self, text=subtitle, bg=BG, fg=MUTED, font=FONT_SMALL, anchor="w",
                     wraplength=420).pack(
                anchor="w", pady=(2, 0)
            )


class Dialpad(tk.Frame):
    """Teclado telefonico estandar de 12 teclas."""

    KEYS = ("1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "0", "#")
    KEY_FONT = ("Segoe UI", 18, "bold")

    def __init__(self, parent: tk.Misc, on_key: Callable[[str], None], on_long_zero: Callable[[], None]) -> None:
        super().__init__(parent, bg=CARD)
        self._on_key = on_key
        self._on_long_zero = on_long_zero
        self._hold_job: str | None = None

        for index, key in enumerate(self.KEYS):
            row, column = divmod(index, 3)
            button = tk.Button(
                self,
                text=key,
                font=self.KEY_FONT,
                bg=KEY_BG,
                fg=TEXT,
                activebackground=SURFACE_HOVER,
                activeforeground=TEXT,
                relief="flat",
                highlightbackground=BORDER,
                highlightthickness=1,
                bd=0,
                cursor="hand2",
                width=3,
                command=lambda value=key: self._press(value),
            )
            button.grid(row=row, column=column, padx=5, pady=4, sticky="nsew")
            if key == "0":
                self._bind_long_press(button)

        for column in range(3):
            self.grid_columnconfigure(column, weight=1)
        for row in range(4):
            self.grid_rowconfigure(row, weight=1)

    def _press(self, value: str) -> None:
        self._on_key(value)

    def _bind_long_press(self, button: tk.Button) -> None:
        button.bind("<ButtonPress-1>", self._start_hold)
        button.bind("<ButtonRelease-1>", self._end_hold)

    def _start_hold(self, _event: tk.Event) -> None:
        self._hold_job = self.after(600, self._on_long_zero)

    def _end_hold(self, _event: tk.Event) -> None:
        if self._hold_job is not None:
            try:
                self.after_cancel(self._hold_job)
            except tk.TclError:
                pass
            self._hold_job = None


class TableView(tk.Frame):
    """Tabla basada en ttk.Treeview con desplazamiento vertical."""

    def __init__(
        self,
        parent: tk.Misc,
        columns: Sequence[tuple[str, str, int, str]],
        on_double_click: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent, bg=CARD)
        self._on_double_click = on_double_click
        keys = [column[0] for column in columns]

        self.tree = ttk.Treeview(self, columns=keys, show="headings", selectmode="browse")
        for key, label, width, anchor in columns:
            self.tree.heading(key, text=label, anchor=anchor)
            self.tree.column(key, width=width, anchor=anchor, stretch=True)

        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

        if on_double_click is not None:
            self.tree.bind("<Double-1>", self._handle_double_click)

    def _handle_double_click(self, _event: tk.Event) -> None:
        selection = self.tree.selection()
        if selection:
            self._on_double_click(selection[0])  # type: ignore[misc]

    def clear(self) -> None:
        self.tree.delete(*self.tree.get_children())

    def insert(self, iid: str, values: Iterable[object], tags: tuple[str, ...] = ()) -> None:
        self.tree.insert("", "end", iid=iid, values=list(values), tags=tags)

    def selected_id(self) -> str | None:
        selection = self.tree.selection()
        return selection[0] if selection else None


class SearchBox(tk.Frame):
    def __init__(self, parent: tk.Misc, placeholder: str, on_change: Callable[[str], None]) -> None:
        super().__init__(parent, bg=CARD)
        self._on_change = on_change
        self.variable = tk.StringVar()
        self._placeholder = placeholder
        self._showing_placeholder = True

        self.entry = tk.Entry(
            self,
            textvariable=self.variable,
            font=FONT,
            bg=ENTRY_BG,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            highlightbackground=BORDER,
            highlightthickness=1,
            bd=0,
        )
        self.entry.pack(side="left", fill="both", expand=True, ipady=6, ipadx=8)
        self.entry.insert(0, placeholder)
        self.entry.configure(fg=MUTED)
        self.entry.bind("<FocusIn>", self._on_focus_in)
        self.entry.bind("<FocusOut>", self._on_focus_out)
        self.entry.bind("<KeyRelease>", self._on_key_release)
        self.entry.bind("<Escape>", lambda _event: self._clear())

    def _on_focus_in(self, _event: tk.Event) -> None:
        if self._showing_placeholder:
            self.entry.delete(0, "end")
            self.entry.configure(fg=TEXT)
            self._showing_placeholder = False

    def _on_focus_out(self, _event: tk.Event) -> None:
        if not self.variable.get():
            self._restore_placeholder()

    def _on_key_release(self, event: tk.Event) -> None:
        if event.keysym in ("BackSpace", "Delete") and not self._showing_placeholder:
            if not self.variable.get():
                self._restore_placeholder()
                self._on_change("")
                return
        self._on_change(self.value())

    def _restore_placeholder(self) -> None:
        self.entry.delete(0, "end")
        self.entry.insert(0, self._placeholder)
        self.entry.configure(fg=MUTED)
        self._showing_placeholder = True

    def _clear(self) -> None:
        self.variable.set("")
        self.entry.delete(0, "end")
        self._restore_placeholder()
        self._on_change("")
        self.entry.focus_set()

    def value(self) -> str:
        return "" if self._showing_placeholder else self.variable.get().strip()

    def set_value(self, text: str) -> None:
        """Fija el texto desde codigo y dispara el filtro."""
        self.entry.delete(0, "end")
        if text:
            self.variable.set(text)
            self.entry.configure(fg=TEXT)
            self._showing_placeholder = False
        else:
            self._restore_placeholder()
        self._on_change(self.value())


class EmptyState(tk.Frame):
    def __init__(self, parent: tk.Misc, text: str, hint: str = "") -> None:
        super().__init__(parent, bg=CARD)
        tk.Label(self, text=text, bg=CARD, fg=MUTED, font=FONT, justify="center").pack(pady=(28, 4))
        if hint:
            tk.Label(self, text=hint, bg=CARD, fg=MUTED, font=FONT_TINY, justify="center").pack()


class ActionButton(tk.Button):
    """Boton plano con estados de color explicitos."""

    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        command: Callable[[], None],
        base: str = SURFACE,
        hover: str = SURFACE_HOVER,
        foreground: str = TEXT,
        font: tuple = FONT,
        disabled_bg: str = DISABLED_BG,
        disabled_fg: str = DISABLED_FG,
        **kwargs: object,
    ) -> None:
        self._palette = (base, hover, foreground, disabled_bg, disabled_fg)
        super().__init__(
            parent,
            text=text,
            command=command,
            font=font,
            bg=base,
            fg=foreground,
            activebackground=hover,
            activeforeground=foreground,
            disabledforeground=disabled_fg,
            relief="flat",
            highlightthickness=0,
            bd=0,
            cursor="hand2",
            padx=12,
            pady=7,
            **kwargs,
        )

    def set_enabled(self, enabled: bool) -> None:
        base, hover, foreground, disabled_bg, disabled_fg = self._palette
        if enabled:
            self.configure(
                state="normal",
                bg=base,
                fg=foreground,
                activebackground=hover,
                activeforeground=foreground,
                cursor="hand2",
            )
        else:
            self.configure(
                state="disabled",
                bg=disabled_bg,
                fg=disabled_fg,
                activebackground=disabled_bg,
                activeforeground=disabled_fg,
                cursor="arrow",
            )


def style_tree(tree: ttk.Treeview) -> None:
    tree.tag_configure("odd", background=TREE_ALT)
    tree.tag_configure("missed", foreground=ERR)
    tree.tag_configure("failed", foreground=WARN)
    tree.tag_configure("favorite", background=TREE_FAVORITE)
