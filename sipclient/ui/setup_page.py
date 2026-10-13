"""Pantalla de ajustes: credenciales SIP, preferencias y acceso al marcador."""

from __future__ import annotations

import re
import tkinter as tk
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from ..config import AccountConfig
from ..registrar import RegistrarState
from ..tariff import (
    RATE_AMERICA,
    RATE_CLARO,
    RATE_CROSS,
    RATE_ESPECIALES,
    RATE_EUROPA,
    RATE_LOCAL,
    RATE_RESTO,
    RATE_TIGO,
    load_prefix_overrides,
    money_or_free,
    resolve_prefixes,
    save_prefix_overrides,
)
from .common import (
    ACCENT,
    ACCENT_HOVER,
    CARD,
    ERR,
    FONT,
    FONT_SMALL,
    MUTED,
    OK,
    OK_HOVER,
    ON_ACCENT,
    SURFACE,
    TEXT,
    WARN,
    ActionButton,
    Card,
)

if TYPE_CHECKING:
    from .app import MainWindow

FIELD_LABELS = (
    ("Servidor (IP o dominio)", "server_host", False),
    ("Puerto", "server_port", False),
    ("Extension", "extension", False),
    ("Contrasena", "password", True),
    ("Dominio SIP", "domain", False),
    ("Nombre mostrado", "display_name", False),
    ("Intervalo de registro (s)", "expires", False),
)


class SetupPage(tk.Frame):
    """Formulario de credenciales. Al validar con exito habilita 'Siguiente'."""

    def __init__(self, parent: tk.Misc, app: "MainWindow") -> None:
        super().__init__(parent, bg=CARD)
        self.app = app
        self._auto_domain = app.account.domain or app.account.server_host
        self.entries: dict[str, ttk.Entry] = {}
        self.password_visible = False

        self.vars = {
            "server_host": tk.StringVar(value=app.account.server_host),
            "server_port": tk.StringVar(value=str(app.account.server_port)),
            "extension": tk.StringVar(value=app.account.extension),
            "password": tk.StringVar(),
            "domain": tk.StringVar(value=app.account.domain),
            "display_name": tk.StringVar(value=app.account.display_name),
            "expires": tk.StringVar(value=str(app.account.expires)),
            "transport": tk.StringVar(value=app.account.transport),
            "outbound_prefix": tk.StringVar(value=app.account.outbound_prefix),
            "history_days": tk.StringVar(value=str(app.account.history_days)),
            "keep_password": tk.BooleanVar(value=app.account.keep_password),
            "auto_register": tk.BooleanVar(value=app.account.auto_register),
        }

        self._build()
        self.load_stored_password()

    def _build(self) -> None:
        self.columnconfigure(0, weight=2)
        self.columnconfigure(1, weight=2)

        left = Card(self, "Credenciales de la extension",
                    "Registros tu extension contra el servidor Issabel/Asterisk.")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        body = left.body()
        body.columnconfigure(1, weight=1)

        row = 0
        for label, key, secret in FIELD_LABELS:
            tk.Label(body, text=f"{label}:", bg=CARD, fg=MUTED, font=FONT).grid(
                row=row, column=0, sticky="e", padx=(0, 10), pady=4
            )
            entry = ttk.Entry(body, textvariable=self.vars[key], width=26, show="*" if secret else "")
            entry.grid(row=row, column=1, sticky="ew", pady=4)
            self.entries[key] = entry
            row += 1

        tk.Label(body, text="Transporte:", bg=CARD, fg=MUTED, font=FONT).grid(
            row=row, column=0, sticky="e", padx=(0, 10), pady=4
        )
        ttk.Combobox(
            body, textvariable=self.vars["transport"], values=("udp", "tcp"),
            state="readonly", width=29,
        ).grid(row=row, column=1, sticky="ew", pady=4)
        row += 1

        toggles = tk.Frame(body, bg=CARD)
        toggles.grid(row=row, column=1, sticky="w", pady=(6, 0))
        ttk.Checkbutton(
            toggles, text="Recordar contrasena", variable=self.vars["keep_password"]
        ).pack(side="left")
        ttk.Checkbutton(
            toggles, text="Registrar al iniciar", variable=self.vars["auto_register"]
        ).pack(side="left", padx=(10, 0))
        row += 1

        self.password_row = row
        show = tk.Checkbutton(
            body, text="Mostrar contrasena", command=self._toggle_password,
            bg=CARD, fg=MUTED, font=FONT_SMALL, activebackground=CARD, selectcolor=CARD,
            highlightthickness=0, bd=0, cursor="hand2",
        )
        show.grid(row=row, column=1, sticky="w", pady=(4, 0))
        row += 1

        actions = tk.Frame(body, bg=CARD)
        actions.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        self.register_button = ActionButton(
            actions, "Validar credenciales", self.app.register, base=ACCENT, hover=ACCENT_HOVER,
            foreground=ON_ACCENT, font=FONT,
        )
        self.register_button.pack(side="left")
        self.unregister_button = ActionButton(
            actions, "Desregistrar", self.app.unregister, base=SURFACE
        )
        self.unregister_button.pack(side="left", padx=(8, 0))
        self.unregister_button.set_enabled(False)
        self.clear_button = ActionButton(
            actions, "Limpiar campos", self.app.clear_credentials, base=SURFACE
        )
        self.clear_button.pack(side="left", padx=(8, 0))
        row += 1

        permissions = tk.Frame(body, bg=CARD)
        permissions.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        permissions_row = tk.Frame(permissions, bg=CARD)
        permissions_row.pack(fill="x")
        self.permissions_status = tk.Label(
            permissions_row, text="", bg=CARD, fg=MUTED, font=FONT_SMALL,
        )
        self.permissions_status.pack(side="right")
        ActionButton(
            permissions_row, "Revisar firewall y microfono",
            self._open_permissions, base=SURFACE,
        ).pack(side="left")
        row += 1

        right_column = tk.Frame(self, bg=CARD)
        right_column.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        next_card = Card(right_column, "Siguiente paso",
                         "Cuando el registro sea valido podras abrir el marcador.")
        next_card.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        next_body = next_card.body()
        self.next_label = tk.Label(
            next_body, text="Aun no hay una sesion valida.", bg=CARD, fg=MUTED,
            font=FONT_SMALL, justify="left", wraplength=250, anchor="w",
        )
        self.next_label.pack(anchor="w")
        self.next_button = ActionButton(
            next_body, "Siguiente  >", self._go_next, base=OK, hover=OK_HOVER,
            foreground=ON_ACCENT, font=("Segoe UI", 11, "bold"),
        )
        self.next_button.pack(anchor="w", pady=(12, 0))
        self.next_button.set_enabled(False)

        prefs = Card(right_column, "Preferencias")
        prefs.grid(row=1, column=0, sticky="ew")
        prefs_body = prefs.body()
        prefs_body.columnconfigure(1, weight=1)

        tk.Label(prefs_body, text="Prefijo de salida:", bg=CARD, fg=MUTED, font=FONT).grid(
            row=0, column=0, sticky="e", padx=(0, 10), pady=4
        )
        ttk.Entry(prefs_body, textvariable=self.vars["outbound_prefix"], width=18).grid(
            row=0, column=1, sticky="w", pady=4
        )
        tk.Label(prefs_body, text="Historial (dias):", bg=CARD, fg=MUTED, font=FONT).grid(
            row=1, column=0, sticky="e", padx=(0, 10), pady=4
        )
        ttk.Entry(prefs_body, textvariable=self.vars["history_days"], width=18).grid(
            row=1, column=1, sticky="w", pady=4
        )
        tk.Label(
            prefs_body,
            text="Ej.: prefijo 8 + 100 marca 8100.",
            bg=CARD, fg=MUTED, font=FONT_SMALL, justify="left", wraplength=250,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

        session = Card(right_column, "Sesion actual")
        session.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        session_body = session.body()
        self.session_label = tk.Label(
            session_body, text="Sin sesion", bg=CARD, fg=MUTED, font=FONT,
            justify="left", anchor="w", wraplength=270,
        )
        self.session_label.pack(anchor="w", fill="x")

        tariff = Card(right_column, "Tarifas (C$/min)")
        tariff.grid(row=3, column=0, sticky="ew", pady=(2, 0))
        tariff_body = tariff.body()
        ActionButton(
            tariff_body, "Ver tarifas y destinos internacionales",
            self._open_tariff_editor, base=SURFACE,
        ).pack(anchor="w")

        self.rowconfigure(0, weight=1)

    def _open_permissions(self) -> None:
        try:
            from .firstrun import FirstRunWizard

            FirstRunWizard(self.app)
        except Exception as exc:  # noqa: BLE001 - el asistente nunca rompe la app
            self.app.log(f"No se pudo abrir el asistente de permisos: {exc}")

    def _refresh_permissions(self) -> None:
        try:
            import sipclient.permissions as permissions

            firewall = permissions.firewall_rules_complete()
            microphone = permissions.microphone_enabled()
        except Exception:  # noqa: BLE001 - estado informativo
            return
        parts = [
            ("firewall", firewall),
            ("microfono", microphone),
        ]
        text = " ".join(
            f"{label}: {'OK' if ok else 'pendiente'}" for label, ok in parts
        )
        self.permissions_status.configure(text=text)

    def on_show(self) -> None:
        self._refresh_permissions()
        self.load_stored_password()

    def _open_tariff_editor(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Destinos internacionales")
        dialog.configure(bg=CARD)
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)

        body = tk.Frame(dialog, bg=CARD, padx=16, pady=14)
        body.pack(fill="both", expand=True)
        rates = (
            ("Tigo a Tigo", RATE_TIGO),
            ("Claro a Claro", RATE_CLARO),
            ("Entre operadores", RATE_CROSS),
            ("Local / misma empresa", RATE_LOCAL),
            ("Internacional America", RATE_AMERICA),
            ("Internacional Europa", RATE_EUROPA),
            ("Internacional resto", RATE_RESTO),
            ("Internacional especiales", RATE_ESPECIALES),
        )
        tk.Label(
            body, text="Costo por minuto (C$):", bg=CARD, fg=TEXT,
            font=FONT, justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        for index, (label, rate) in enumerate(rates):
            tk.Label(body, text=f"{label}:", bg=CARD, fg=MUTED, font=FONT_SMALL).grid(
                row=1 + index, column=0, sticky="e", padx=(0, 8), pady=1
            )
            tk.Label(body, text=money_or_free(rate), bg=CARD, fg=TEXT, font=FONT_SMALL).grid(
                row=1 + index, column=1, sticky="w", pady=1
            )
        range_row = 1 + len(rates)
        tk.Frame(body, bg=SURFACE, height=1).grid(
            row=range_row, column=0, columnspan=2, sticky="ew", pady=6
        )
        tk.Label(
            body, text="Rangos de extension: local 1000-2000, Claro 4000-5000, Tigo 6000-8000.",
            bg=CARD, fg=MUTED, font=FONT_SMALL, justify="left",
        ).grid(row=range_row + 1, column=0, columnspan=2, sticky="w", pady=(6, 4))
        top_row = range_row + 2

        effective = resolve_prefixes(load_prefix_overrides())
        tk.Label(
            body, text="Prefijos de pais (sin el +, separados por comas o espacios).",
            bg=CARD, fg=MUTED, font=FONT_SMALL,
        ).grid(row=top_row, column=0, columnspan=2, sticky="w", pady=(0, 8))

        entries: dict[str, ttk.Entry] = {}
        row = top_row + 1
        for region, title in (
            ("america", "America"),
            ("europa", "Europa"),
            ("especiales", "Especiales"),
        ):
            tk.Label(body, text=f"{title}:", bg=CARD, fg=TEXT, font=FONT).grid(
                row=row, column=0, sticky="ne", padx=(0, 8), pady=4
            )
            entry = ttk.Entry(body, width=46)
            entry.insert(0, " ".join(effective[region]))
            entry.grid(row=row, column=1, sticky="ew", pady=4)
            entries[region] = entry
            row += 1
        tk.Label(
            body, text="Lo no listado se cobra como resto del mundo (C$18.31/min).",
            bg=CARD, fg=MUTED, font=FONT_SMALL,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(6, 8))
        row += 1

        actions = tk.Frame(body, bg=CARD)
        actions.grid(row=row, column=0, columnspan=2, sticky="e")

        def _save() -> None:
            payload: dict[str, list[str]] = {}
            for region, entry in entries.items():
                payload[region] = re.split(r"[\s,;]+", entry.get().strip())
            save_prefix_overrides(payload)
            self.app.log("Destinos internacionales actualizados.")
            dialog.destroy()

        ActionButton(actions, "Guardar", _save, base=OK, hover=OK_HOVER,
                     foreground=ON_ACCENT).pack(side="right")
        ActionButton(actions, "Cancelar", dialog.destroy, base=SURFACE).pack(
            side="right", padx=(0, 8)
        )

    def _toggle_password(self) -> None:
        self.password_visible = not self.password_visible
        self.entries["password"].configure(show="" if self.password_visible else "*")

    def _go_next(self) -> None:
        self.app.navigate("dialpad")

    def load_stored_password(self) -> None:
        account = self.app.account
        if not (account.server_host and account.extension):
            return
        try:
            password = self.app.load_password(account.server_host, account.extension)
        except Exception as exc:  # noqa: BLE001 - el mensaje va al registro de la app
            self.app.log(f"No se pudo leer la contrasena guardada: {exc}")
            return
        if password:
            self.vars["password"].set(password)
            self.app.log("Contrasena cargada del Administrador de credenciales de Windows.")

    def build_account(self) -> AccountConfig:
        domain = self.vars["domain"].get().strip()
        if domain == self._auto_domain:
            domain = ""
        return AccountConfig(
            server_host=self.vars["server_host"].get().strip(),
            server_port=int(self.vars["server_port"].get() or 5060),
            extension=self.vars["extension"].get().strip(),
            domain=domain,
            display_name=self.vars["display_name"].get().strip(),
            transport=self.vars["transport"].get().strip().lower(),
            expires=int(self.vars["expires"].get() or 3600),
            auto_register=bool(self.vars["auto_register"].get()),
            keep_password=bool(self.vars["keep_password"].get()),
            outbound_prefix=self.vars["outbound_prefix"].get().strip(),
            history_days=int(self.vars["history_days"].get() or 90),
        )

    def show_error(self, message: str) -> None:
        messagebox.showerror("Datos invalidos", message)

    def focus_password(self) -> None:
        self.entries["password"].focus_set()

    def focus_extension(self) -> None:
        self.entries["extension"].focus_set()

    def clear_fields(self) -> None:
        for key in ("extension", "password", "display_name", "domain"):
            self.vars[key].set("")
        self._auto_domain = ""

    def sync_from_account(self, account: AccountConfig) -> None:
        self.vars["server_host"].set(account.server_host)
        self.vars["server_port"].set(str(account.server_port))
        self.vars["extension"].set(account.extension)
        self.vars["domain"].set(account.domain)
        self.vars["display_name"].set(account.display_name)
        self.vars["expires"].set(str(account.expires))
        self.vars["outbound_prefix"].set(account.outbound_prefix)
        self.vars["history_days"].set(str(account.history_days))
        self.vars["transport"].set(account.transport)
        self._auto_domain = account.domain

    def on_session_event(self, state: RegistrarState, detail: str) -> None:
        registered = state is RegistrarState.REGISTERED
        self.next_button.set_enabled(registered)
        self.register_button.set_enabled(
            state not in (RegistrarState.REGISTERING, RegistrarState.REGISTERED)
        )
        self.unregister_button.set_enabled(
            state in (RegistrarState.REGISTERED, RegistrarState.RETRYING)
        )

        if registered:
            self.next_label.configure(
                text=f"Extension {self.app.account.extension} validada en "
                     f"{self.app.account.server_host}. Ya puedes marcar.",
                fg=OK,
            )
        else:
            self.next_label.configure(
                text="Aun no hay una sesion valida. Corrige los datos y pulsa validar.", fg=MUTED
            )

        colors = {
            RegistrarState.REGISTERED: OK,
            RegistrarState.FAILED: ERR,
            RegistrarState.RETRYING: WARN,
        }
        self.session_label.configure(text=detail, fg=colors.get(state, MUTED))
