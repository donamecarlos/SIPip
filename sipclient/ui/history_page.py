"""Pantalla de historial: filtro por tipo, busqueda, detalle y exportacion."""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox
from typing import TYPE_CHECKING

from ..datastore import (
    DIRECTION_INCOMING,
    DIRECTION_OUTGOING,
    STATUS_FAILED,
    STATUS_MISSED,
    STATUS_NO_ANSWER,
    CallRecord,
)
from ..tariff import money
from .common import (
    ACCENT,
    CARD,
    DANGER_BG,
    ERR,
    FONT_SMALL,
    MUTED,
    ON_ACCENT,
    SURFACE,
    SURFACE_HOVER,
    TEXT,
    ActionButton,
    Card,
    EmptyState,
    SearchBox,
    TableView,
    format_duration,
    format_full_timestamp,
    format_timestamp,
    style_tree,
)

if TYPE_CHECKING:
    from .app import MainWindow

DIRECTION_FILTERS = (
    ("Todas", None),
    ("Realizadas", DIRECTION_OUTGOING),
    ("Recibidas", DIRECTION_INCOMING),
)


class HistoryPage(tk.Frame):
    def __init__(self, parent: tk.Misc, app: "MainWindow") -> None:
        super().__init__(parent, bg=CARD)
        self.app = app
        self.records: list[CallRecord] = []
        self.filter_kind = "Todas"
        self.missed_only = False
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        card = Card(self)
        card.pack(fill="both", expand=True, padx=8, pady=8)
        self.card = card

        header = tk.Frame(card, bg=CARD)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 0))
        header.columnconfigure(1, weight=1)
        tk.Label(header, text="Historial de llamadas", bg=CARD, fg=TEXT,
                 font=("Segoe UI", 15, "bold")).grid(row=0, column=0, sticky="w")
        self.summary = tk.Label(header, text="", bg=CARD, fg=MUTED, font=FONT_SMALL, wraplength=260)
        self.summary.grid(row=0, column=1, sticky="w", padx=(10, 0))

        self.search = SearchBox(header, "Buscar por numero, contacto o cuenta...", self._apply_filter)
        self.search.grid(row=0, column=2, sticky="e")

        chips = tk.Frame(card, bg=CARD)
        chips.grid(row=1, column=0, sticky="ew", padx=16, pady=(10, 0))
        self.filter_buttons: dict[str, tk.Button] = {}
        for label, _ in DIRECTION_FILTERS:
            button = tk.Button(
                chips, text=label, font=FONT_SMALL, bg=SURFACE, fg=TEXT,
                activebackground=SURFACE_HOVER, relief="flat", bd=0, cursor="hand2",
                padx=12, pady=5, command=lambda value=label: self._set_filter(value),
            )
            button.pack(side="left", padx=(0, 6))
            self.filter_buttons[label] = button

        self.missed_button = tk.Button(
            chips, text="Solo perdidas", font=FONT_SMALL, bg=SURFACE, fg=TEXT,
            activebackground=SURFACE_HOVER, relief="flat", bd=0, cursor="hand2", padx=12, pady=5,
            command=self._toggle_missed,
        )
        self.missed_button.pack(side="left")

        actions = tk.Frame(chips, bg=CARD)
        actions.pack(side="right")
        ActionButton(actions, "Exportar CSV", self._export, base=SURFACE).pack(side="left", padx=4)
        ActionButton(actions, "Borrar seleccionada", self._delete_selected, base=SURFACE).pack(
            side="left", padx=4
        )
        ActionButton(
            actions, "Vaciar historial", self._clear_all, base=DANGER_BG, foreground=ERR
        ).pack(side="left", padx=4)

        self.table = TableView(
            card,
            columns=(
                ("when", "Fecha", 110, "w"),
                ("direction", "Tipo", 82, "center"),
                ("number", "Numero", 105, "w"),
                ("peer", "Contacto", 145, "w"),
                ("call_type", "Tarifa", 140, "w"),
                ("duration", "Duracion", 78, "e"),
                ("cost", "Costo", 88, "e"),
                ("status", "Estado", 115, "w"),
            ),
            on_double_click=self._on_double_click,
        )
        self.table.grid(row=2, column=0, sticky="nsew", padx=16, pady=(12, 8))
        style_tree(self.table.tree)

        card.rowconfigure(2, weight=1)
        card.columnconfigure(0, weight=1)

        self.empty = EmptyState(
            card, "Todavia no hay llamadas registradas",
            "El historial se rellenara cuando el motor de llamadas (INVITE + RTP) este activo.",
        )

    def on_show(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        search = self.search.value()
        store = self.app.store.calls
        records = store.list(search=search, limit=1000)
        if self.missed_only:
            records = [record for record in records if record.status == STATUS_MISSED]
        else:
            direction = dict(DIRECTION_FILTERS).get(self.filter_kind)
            if direction:
                records = [record for record in records if record.direction == direction]

        self.records = records
        self.table.clear()

        for record in self.records:
            peer = record.peer or "-"
            tags: tuple[str, ...] = ("odd",) if record.id % 2 == 0 else ()
            if record.status == STATUS_MISSED:
                tags = ("missed",)
            elif record.status in (STATUS_FAILED, STATUS_NO_ANSWER):
                tags = ("failed",)
            cost = money(record.cost) if record.direction == DIRECTION_OUTGOING else "-"
            self.table.insert(
                str(record.id),
                (
                    format_timestamp(record.started_at),
                    record.direction_label,
                    record.number,
                    peer,
                    record.call_type or "-",
                    format_duration(record.duration),
                    cost,
                    record.status_label,
                ),
                tags=tags,
            )

        if self.records:
            self.empty.grid_remove()
            self.table.grid()
        else:
            self.table.grid_remove()
            self.empty.grid(row=2, column=0, sticky="nsew", padx=16, pady=(12, 8))

        total = self.app.store.calls.total_count()
        missed = self.app.store.calls.missed_count()
        billed = sum(record.cost for record in self.records)
        billed_text = money(billed) if self.records else money(0.0)
        self.summary.configure(
            text=f"{len(self.records)} de {total} llamadas  |  {missed} perdidas"
                 f"  |  Cobro: {billed_text}"
        )

    def _apply_filter(self, _value: str = "") -> None:
        self.refresh()

    def _set_filter(self, label: str) -> None:
        self.filter_kind = label
        self.missed_only = False
        self.missed_button.configure(bg=SURFACE, fg=TEXT)
        self._paint_chips()
        self.refresh()

    def _toggle_missed(self) -> None:
        self.missed_only = not self.missed_only
        self.missed_button.configure(bg=DANGER_BG if self.missed_only else SURFACE,
                                     fg=ERR if self.missed_only else TEXT)
        self._paint_chips()
        self.refresh()

    def _paint_chips(self) -> None:
        for label, button in self.filter_buttons.items():
            active = label == self.filter_kind and not self.missed_only
            button.configure(bg=ACCENT if active else SURFACE,
                             fg=ON_ACCENT if active else TEXT)
    def _on_double_click(self, record_id: str) -> None:
        record = self._find(record_id)
        if record is None:
            return
        if record.status == STATUS_MISSED:
            messagebox.showinfo(
                "Llamada perdida",
                f"{record.direction_label} del {format_full_timestamp(record.started_at)}\n"
                f"Numero: {record.number}",
            )
            return
        self.app.dial(record.number)

    def _find(self, record_id: str) -> CallRecord | None:
        for record in self.records:
            if str(record.id) == record_id:
                return record
        return None

    def _delete_selected(self) -> None:
        record_id = self.table.selected_id()
        if record_id is None:
            messagebox.showinfo("Historial", "Selecciona primero una llamada.")
            return
        self.app.store.calls.delete(int(record_id))
        self.refresh()
        self.app.refresh_badges()

    def _clear_all(self) -> None:
        if not messagebox.askyesno(
            "Vaciar historial", "Se borraran todas las llamadas registradas. Continuar?"
        ):
            return
        removed = self.app.store.calls.clear()
        self.app.log(f"Historial vaciado ({removed} registros).")
        self.refresh()
        self.app.refresh_badges()

    def _export(self) -> None:
        if not self.records:
            messagebox.showinfo("Exportar", "No hay llamadas que exportar.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("Todos", "*.*")],
            title="Exportar historial",
        )
        if not path:
            return
        self.app.store.calls.export_csv(path, self.records)
        self.app.log(f"Historial exportado a {path}")
        messagebox.showinfo("Exportar", f"Historial exportado a:\n{path}")
