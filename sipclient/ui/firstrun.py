"""Asistente de primer uso: firewall, microfono y configuracion inicial."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

import sipclient.permissions as permissions
from ..config import save_config
from .common import (
    ACCENT,
    ACCENT_HOVER,
    CARD,
    FONT,
    FONT_HEADING,
    FONT_SMALL,
    MUTED,
    OK,
    OK_HOVER,
    ON_ACCENT,
    SURFACE,
    TEXT,
    ActionButton,
)


class FirstRunWizard:
    """Dialogo modal que se muestra una sola vez tras la instalacion."""

    def __init__(self, app) -> None:
        self.app = app
        self.root = tk.Toplevel(app.root)
        self.root.title("Primer uso de SIPip")
        self.root.configure(bg=CARD)
        self.root.resizable(False, False)
        self.root.transient(app.root)
        self.root.grab_set()
        self._busy = False

        self.firewall_ok = permissions.firewall_rules_complete()
        self.mic_ok = permissions.microphone_enabled()
        self.var_again = tk.BooleanVar(value=False)

        self._build()
        self._center()
        self._refresh()
        self.root.protocol("WM_DELETE_WINDOW", self._finish)

    def _build(self) -> None:
        body = tk.Frame(self.root, bg=CARD, padx=24, pady=20)
        body.pack(fill="both", expand=True)

        tk.Label(
            body, text="Bienvenido a SIPip", bg=CARD, fg=TEXT,
            font=("Segoe UI", 18, "bold"),
        ).pack(anchor="w")
        tk.Label(
            body, text=(
                "Para que las llamadas entrantes y el microfono funcionen, "
                "SIPip necesita un par de permisos del sistema. "
                "Puedes concederlos ahora o hacerlo mas tarde desde Ajustes."
            ),
            bg=CARD, fg=MUTED, font=FONT, justify="left", wraplength=430,
        ).pack(anchor="w", pady=(4, 16))

        firewall_card = tk.Frame(body, bg=SURFACE)
        firewall_card.pack(fill="x", pady=(0, 10))
        self._pad(firewall_card)
        tk.Label(
            firewall_card, text="Permitir llamadas entrantes (firewall)",
            bg=SURFACE, fg=TEXT, font=FONT_HEADING,
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        tk.Label(
            firewall_card, text=(
                "Abre un dialogo de administrador (UAC) para crear las reglas "
                "que permiten que Asterisk te llame. Sin esto, no se reciben llamadas."
            ),
            bg=SURFACE, fg=MUTED, font=FONT_SMALL, justify="left", wraplength=430,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 8))
        self.firewall_action = ActionButton(
            firewall_card, "Solicitar permiso de firewall",
            self._request_firewall, base=ACCENT, hover=ACCENT_HOVER, foreground=ON_ACCENT,
        )
        self.firewall_action.grid(row=2, column=0, sticky="w")
        self.firewall_status = tk.Label(
            firewall_card, text="", bg=SURFACE, fg=MUTED, font=FONT_SMALL,
        )
        self.firewall_status.grid(row=2, column=1, sticky="e", padx=(8, 0))

        mic_card = tk.Frame(body, bg=SURFACE)
        mic_card.pack(fill="x", pady=(0, 10))
        self._pad(mic_card)
        tk.Label(
            mic_card, text="Permitir microfono", bg=SURFACE, fg=TEXT, font=FONT_HEADING,
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        tk.Label(
            mic_card, text=(
                "Windows puede bloquear el microfono por privacidad. "
                "Activalo aqui o abre el panel de microfono de Windows."
            ),
            bg=SURFACE, fg=MUTED, font=FONT_SMALL, justify="left", wraplength=430,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 8))
        mic_actions = tk.Frame(mic_card, bg=SURFACE)
        mic_actions.grid(row=2, column=0, sticky="w")
        self.mic_allow = ActionButton(
            mic_actions, "Activar microfono", self._set_mic,
            base=ACCENT, hover=ACCENT_HOVER, foreground=ON_ACCENT,
        )
        self.mic_allow.pack(side="left")
        ActionButton(
            mic_actions, "Abrir ajustes de Windows", permissions.open_microphone_settings,
            base=SURFACE, hover=SURFACE,
        ).pack(side="left", padx=(8, 0))
        self.mic_status = tk.Label(
            mic_card, text="", bg=SURFACE, fg=MUTED, font=FONT_SMALL,
        )
        self.mic_status.grid(row=2, column=1, sticky="e", padx=(8, 0))

        footer = tk.Frame(body, bg=CARD)
        footer.pack(fill="x", pady=(8, 0))
        ttk.Checkbutton(
            footer, text="No volver a preguntar (puedes cambiar esto en Ajustes)",
            variable=self.var_again,
        ).pack(side="left")
        ActionButton(
            footer, "Continuar", self._finish, base=OK, hover=OK_HOVER,
            foreground=ON_ACCENT, font=("Segoe UI", 11, "bold"),
        ).pack(side="right")

    @staticmethod
    def _pad(parent: tk.Frame) -> None:
        parent.columnconfigure(1, weight=1)
        for index in range(4):
            parent.grid_rowconfigure(index, minsize=0)

    def _center(self) -> None:
        self.root.update_idletasks()
        width, height = self.root.winfo_width(), self.root.winfo_height()
        x = self.app.root.winfo_rootx() + (self.app.root.winfo_width() - width) // 2
        y = self.app.root.winfo_rooty() + (self.app.root.winfo_height() - height) // 2
        self.root.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _refresh(self) -> None:
        text_color = OK if self.firewall_ok else MUTED
        self.firewall_status.configure(
            text="Listo" if self.firewall_ok else "Pendiente", fg=text_color
        )
        self.firewall_action.set_enabled(not self.firewall_ok and not self._busy)
        self.mic_status.configure(
            text="Listo" if self.mic_ok else "Pendiente", fg=OK if self.mic_ok else MUTED
        )
        self.mic_allow.set_enabled(not self.mic_ok and not self._busy)

    def _request_firewall(self) -> None:
        if self._busy:
            return

        def run() -> None:
            try:
                done = permissions.request_firewall_access()
            except Exception:  # noqa: BLE001 - nunca romper el asistente
                done = False
            self.app.root.after(0, lambda: self._firewall_result(done))

        self._busy = True
        self.firewall_status.configure(
            text="Abriendo dialogo de administrador...", fg=MUTED
        )
        self._refresh()
        threading.Thread(target=run, name="firstrun-firewall", daemon=True).start()

    def _firewall_result(self, done: bool) -> None:
        self._busy = False
        self.firewall_ok = done or permissions.firewall_rules_complete()
        self._refresh()
        message = (
            "Reglas de firewall creadas." if self.firewall_ok
            else "No se crearon las reglas. Puedes hacerlo mas tarde desde Ajustes."
        )
        messagebox.showinfo("Firewall", message, parent=self.root)

    def _set_mic(self) -> None:
        if permissions.allow_microphone():
            self.mic_ok = permissions.microphone_enabled()
            if self.mic_ok:
                messagebox.showinfo(
                    "Microfono", "El microfono ya esta permitido.", parent=self.root
                )
            else:
                permissions.open_microphone_settings()
                messagebox.showinfo(
                    "Microfono",
                    "Hemos abierto los ajustes de microfono de Windows. "
                    "Activalo desde el panel.",
                    parent=self.root,
                )
        else:
            permissions.open_microphone_settings()
            messagebox.showinfo(
                "Microfono",
                "Hemos abierto los ajustes de microfono de Windows. Activalo desde el panel.",
                parent=self.root,
            )
        self._refresh()

    def _finish(self) -> None:
        if self.var_again.get() or (self.firewall_ok and self.mic_ok):
            self.app.account.first_run_complete = True
            save_config(self.app.account)
        try:
            self.root.grab_release()
        except tk.TclError:
            pass
        self.root.destroy()