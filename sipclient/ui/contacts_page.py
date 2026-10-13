"""Pantalla de contactos: alta, edicion, favoritos y marcado rapido."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from ..datastore import Contact
from .common import (
    ACCENT,
    ACCENT_HOVER,
    CARD,
    DANGER_BG,
    ERR,
    FONT,
    FONT_SMALL,
    MUTED,
    OK,
    OK_HOVER,
    ON_ACCENT,
    SURFACE,
    TEXT,
    ActionButton,
    Card,
    EmptyState,
    SearchBox,
    TableView,
    style_tree,
)

if TYPE_CHECKING:
    from .app import MainWindow


class ContactsPage(tk.Frame):
    def __init__(self, parent: tk.Misc, app: "MainWindow") -> None:
        super().__init__(parent, bg=CARD)
        self.app = app
        self.contacts: list[Contact] = []
        self.editing_id: int | None = None

        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(0, weight=1)

        list_card = Card(self, "Directorio", "Doble clic para marcar un contacto.")
        list_card.grid(row=0, column=0, sticky="nsew", padx=(8, 4))
        list_card.rowconfigure(2, weight=1)
        list_card.columnconfigure(0, weight=1)

        self.search = SearchBox(list_card, "Buscar contacto...", lambda _v: self.refresh())
        self.search.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 0))

        self.table = TableView(
            list_card,
            columns=(
                ("favorite", "*", 30, "center"),
                ("name", "Nombre", 170, "w"),
                ("number", "Numero", 120, "w"),
                ("email", "Correo", 170, "w"),
            ),
            on_double_click=self._on_double_click,
        )
        self.table.grid(row=2, column=0, sticky="nsew", padx=16, pady=(10, 6))
        style_tree(self.table.tree)

        list_actions = tk.Frame(list_card, bg=CARD)
        list_actions.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 14))
        ActionButton(list_actions, "Marcar", self._call_selected, base=OK,
                     hover=OK_HOVER, foreground=ON_ACCENT).pack(side="left")
        ActionButton(list_actions, "Editar", self._edit_selected, base=SURFACE).pack(
            side="left", padx=(8, 0)
        )
        ActionButton(list_actions, "Eliminar", self._delete_selected, base=DANGER_BG,
                     foreground=ERR).pack(side="left", padx=(8, 0))

        form_card = Card(self, "Contacto")
        form_card.grid(row=0, column=1, sticky="nsew", padx=(4, 8))
        form = form_card.body()
        form.columnconfigure(1, weight=1)

        tk.Label(form, text="Nombre:", bg=CARD, fg=MUTED, font=FONT).grid(
            row=0, column=0, sticky="e", padx=(0, 10), pady=5
        )
        self.name_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.name_var, width=26).grid(row=0, column=1, sticky="ew", pady=5)

        tk.Label(form, text="Numero:", bg=CARD, fg=MUTED, font=FONT).grid(
            row=1, column=0, sticky="e", padx=(0, 10), pady=5
        )
        self.number_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.number_var, width=26).grid(row=1, column=1, sticky="ew", pady=5)

        tk.Label(form, text="Correo:", bg=CARD, fg=MUTED, font=FONT).grid(
            row=2, column=0, sticky="e", padx=(0, 10), pady=5
        )
        self.email_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.email_var, width=26).grid(row=2, column=1, sticky="ew", pady=5)

        self.favorite_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(form, text="Marcar como favorito", variable=self.favorite_var).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )

        buttons = tk.Frame(form, bg=CARD)
        buttons.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        self.save_button = ActionButton(buttons, "Guardar", self._save, base=ACCENT,
                                        hover=ACCENT_HOVER, foreground=ON_ACCENT)
        self.save_button.pack(side="left")
        ActionButton(buttons, "Nuevo", self._reset_form, base=SURFACE).pack(
            side="left", padx=(8, 0)
        )

        self.hint = tk.Label(
            form, text="", bg=CARD, fg=MUTED, font=FONT_SMALL, justify="left", wraplength=260
        )
        self.hint.grid(row=5, column=0, columnspan=2, sticky="w", pady=(12, 0))

        self.empty = EmptyState(
            list_card, "Sin contactos", "Anade el primero con el formulario de la derecha."
        )

    def on_show(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        self.contacts = self.app.store.contacts.list(search=self.search.value())
        self.table.clear()
        for contact in self.contacts:
            tags = ("favorite",) if contact.favorite else (("odd",) if contact.id % 2 == 0 else ())
            self.table.insert(
                str(contact.id),
                ("*" if contact.favorite else "", contact.name, contact.number, contact.email or "-"),
                tags=tags,
            )

        if self.contacts:
            self.empty.grid_remove()
            self.table.grid()
        else:
            self.table.grid_remove()
            self.empty.grid(row=2, column=0, sticky="nsew", padx=16, pady=(10, 6))

    def _selected_contact(self) -> Contact | None:
        contact_id = self.table.selected_id()
        if contact_id is None:
            return None
        for contact in self.contacts:
            if str(contact.id) == contact_id:
                return contact
        return None

    def _on_double_click(self, _contact_id: str) -> None:
        self._call_selected()

    def _call_selected(self) -> None:
        contact = self._selected_contact()
        if contact is None:
            messagebox.showinfo("Contactos", "Selecciona primero un contacto.")
            return
        self.app.dial(contact.number)

    def _edit_selected(self) -> None:
        contact = self._selected_contact()
        if contact is None:
            messagebox.showinfo("Contactos", "Selecciona primero un contacto.")
            return
        self.name_var.set(contact.name)
        self.number_var.set(contact.number)
        self.email_var.set(contact.email)
        self.favorite_var.set(contact.favorite)
        self.editing_id = contact.id
        self.save_button.configure(text="Actualizar")
        self.hint.configure(text=f"Editando: {contact.name}", fg=MUTED)

    def _reset_form(self) -> None:
        self.editing_id = None
        self.name_var.set("")
        self.number_var.set("")
        self.email_var.set("")
        self.favorite_var.set(False)
        self.save_button.configure(text="Guardar")
        self.hint.configure(text="", fg=MUTED)

    def _save(self) -> None:
        name = self.name_var.get().strip()
        number = self.number_var.get().strip()
        if not name or not number:
            messagebox.showwarning("Falta datos", "El nombre y el numero son obligatorios.")
            return

        try:
            if self.editing_id is None:
                self.app.store.contacts.add(name, number, self.email_var.get(), self.favorite_var.get())
                self.app.log(f"Contacto anadido: {name} ({number})")
            else:
                self.app.store.contacts.update(
                    self.editing_id, name, number, self.email_var.get(), self.favorite_var.get()
                )
                self.app.log(f"Contacto actualizado: {name} ({number})")
        except Exception as exc:  # noqa: BLE001 - se muestra al usuario
            messagebox.showerror("No se pudo guardar", str(exc))
            return

        self._reset_form()
        self.refresh()
        self.app.refresh_badges()

    def _delete_selected(self) -> None:
        contact = self._selected_contact()
        if contact is None:
            messagebox.showinfo("Contactos", "Selecciona primero un contacto.")
            return
        if not messagebox.askyesno("Eliminar", f"Eliminar '{contact.name}' del directorio?"):
            return
        self.app.store.contacts.delete(contact.id)
        if self.editing_id == contact.id:
            self._reset_form()
        self.refresh()
        self.app.refresh_badges()
