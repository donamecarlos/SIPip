"""Ventana principal: menu lateral, barra de estado y gestion de la sesion SIP."""

from __future__ import annotations

import queue
import sys
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from .. import __version__, storage
from ..config import AccountConfig, load_config, save_config
from ..datastore import Database
from ..registrar import Registrar, RegistrarState
from .common import (
    BG,
    BORDER_SOFT,
    CARD,
    ERR,
    FONT_SMALL,
    FONT_TINY,
    MUTED,
    OK,
    ON_ACCENT,
    SIDEBAR_ACTIVE,
    SIDEBAR_BG,
    SIDEBAR_HOVER,
    TEXT,
    VOICEMAIL,
    WARN,
    apply_ttk_theme,
)
from .contacts_page import ContactsPage
from .dialpad_page import DialpadPage
from .history_page import HistoryPage
from .setup_page import SetupPage

PAGES: tuple[tuple[str, str, str], ...] = (
    ("dialpad", "Marcador", "Teclado y marcado"),
    ("history", "Historial", "Llamadas registradas"),
    ("contacts", "Contactos", "Directorio y favoritos"),
    ("settings", "Ajustes", "Credenciales SIP"),
)


class MainWindow:
    def __init__(self, root: tk.Tk, tones: object | None = None) -> None:
        self.root = root
        self._tones = tones
        self.root.title(f"SIPip {__version__} - Telefonia IP")
        self.root.configure(bg=BG)
        self.root.minsize(980, 700)
        apply_ttk_theme(root)

        self.account: AccountConfig = load_config()
        self.account_password = ""
        self.registrar: Registrar | None = None
        self.events: queue.Queue[tuple[RegistrarState, str]] = queue.Queue()
        self._generation = 0
        self._after_ids: list[str] = []
        self._current_page = ""
        self.store = Database()

        self._build_shell()
        self._build_pages()

        if self.account.window_geometry:
            try:
                self.root.geometry(self.account.window_geometry)
            except tk.TclError:
                pass

        start_page = "settings" if not self._has_session() else "dialpad"
        self.navigate(start_page)
        self._after_ids.append(self.root.after(100, self._poll_events))
        self._after_ids.append(self.root.after(1000, self._tick))
        self.refresh_badges()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        if self.account.auto_register and self._has_session():
            self.root.after(400, self.register)

        if not self.account.first_run_complete and getattr(sys, "frozen", False):
            self._after_ids.append(self.root.after(1500, self._open_first_run))

        removed = self.store.prune_calls(self.account.history_days)
        if removed:
            self.log(f"Historial podado: {removed} registros de mas de {self.account.history_days} dias.")

    def _open_first_run(self) -> None:
        if self.account.first_run_complete or getattr(self, "_first_run_open", False):
            return
        self._first_run_open = True
        try:
            from .firstrun import FirstRunWizard

            FirstRunWizard(self)
        except Exception as exc:  # noqa: BLE001 - el asistente nunca rompe el arranque
            self.log(f"No se pudo abrir el asistente de primer uso: {exc}")
            self._first_run_open = False

    def _has_session(self) -> bool:
        return bool(self.account.server_host and self.account.extension)

    def _build_shell(self) -> None:
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(1, weight=1)

        sidebar = tk.Frame(self.root, bg=SIDEBAR_BG, width=210)
        sidebar.grid(row=0, column=0, sticky="nsw")
        sidebar.pack_propagate(False)
        self.sidebar_buttons: dict[str, tk.Button] = {}

        brand = tk.Frame(sidebar, bg=SIDEBAR_BG)
        brand.pack(fill="x", padx=16, pady=(20, 4))
        tk.Label(brand, text="SIPip", bg=SIDEBAR_BG, fg=ON_ACCENT,
                 font=("Segoe UI", 20, "bold")).pack(anchor="w")
        tk.Label(brand, text="Telefonia IP", bg=SIDEBAR_BG, fg=MUTED,
                 font=FONT_TINY).pack(anchor="w")

        self.sidebar_account = tk.Label(sidebar, text="", bg=SIDEBAR_BG, fg=MUTED,
                                        font=FONT_SMALL, justify="left", anchor="w",
                                        wraplength=178)
        self.sidebar_account.pack(fill="x", padx=16, pady=(0, 14))

        for key, label, subtitle in PAGES:
            button = tk.Button(
                sidebar,
                text=f"  {label}\n  {subtitle}",
                font=("Segoe UI", 9),
                bg=SIDEBAR_BG,
                fg=TEXT,
                activebackground=SIDEBAR_HOVER,
                activeforeground=ON_ACCENT,
                relief="flat",
                bd=0,
                anchor="w",
                justify="left",
                cursor="hand2",
                padx=8,
                pady=7,
                command=lambda name=key: self.navigate(name),
            )
            button.pack(fill="x", padx=8, pady=1)
            self.sidebar_buttons[key] = button

        spacer = tk.Frame(sidebar, bg=SIDEBAR_BG)
        spacer.pack(fill="both", expand=True)

        self.voicemail_badge = tk.Label(
            sidebar, text="", bg=SIDEBAR_BG, fg=VOICEMAIL, font=FONT_SMALL, justify="left",
            anchor="w", wraplength=178,
        )
        self.voicemail_badge.pack(fill="x", padx=16, pady=(0, 6))
        self.sidebar_status = tk.Label(
            sidebar, text="Sin sesion", bg=SIDEBAR_BG, fg=MUTED, font=FONT_SMALL,
            justify="left", anchor="w", wraplength=178,
        )
        self.sidebar_status.pack(fill="x", padx=16, pady=(0, 16))

        content = tk.Frame(self.root, bg=BG)
        content.grid(row=0, column=1, sticky="nsew")
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)
        self.content = content
        self.pages_frame = tk.Frame(content, bg=BG)
        self.pages_frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self.pages_frame.rowconfigure(0, weight=1)
        self.pages_frame.columnconfigure(0, weight=1)

        self._build_statusbar(content)

    def _build_statusbar(self, parent: tk.Frame) -> None:
        bar = tk.Frame(parent, bg=CARD, height=34, highlightbackground=BORDER_SOFT, highlightthickness=1)
        bar.grid(row=1, column=0, sticky="ew")
        bar.pack_propagate(False)

        self.status_dot = tk.Frame(bar, bg=CARD, width=10, height=10)
        self.status_dot.pack(side="left", padx=(12, 6))
        self._dot = tk.Canvas(self.status_dot, width=10, height=10, highlightthickness=0, bg=CARD)
        self._dot.pack()
        self._dot_oval = self._dot.create_oval(0, 0, 10, 10, fill=MUTED, outline="")

        self.status_label = tk.Label(bar, text="Listo", bg=CARD, fg=MUTED, font=FONT_SMALL)
        self.status_label.pack(side="left")

        self.status_account = tk.Label(bar, text="", bg=CARD, fg=MUTED, font=FONT_SMALL)
        self.status_account.pack(side="right", padx=12)

    def _build_pages(self) -> None:
        self.pages: dict[str, tk.Frame] = {}
        self.setup_page = SetupPage(self.pages_frame, self)
        self.dialpad_page = DialpadPage(self.pages_frame, self, tones=self._tones)
        self.history_page = HistoryPage(self.pages_frame, self)
        self.contacts_page = ContactsPage(self.pages_frame, self)

        for key, page in (
            ("settings", self.setup_page),
            ("dialpad", self.dialpad_page),
            ("history", self.history_page),
            ("contacts", self.contacts_page),
        ):
            self.pages[key] = page

    def navigate(self, key: str) -> None:
        if key not in self.pages:
            return
        page = self.pages[key]
        if self._current_page and self._current_page in self.pages:
            self.pages[self._current_page].grid_forget()
        page.grid(row=0, column=0, sticky="nsew")
        self._current_page = key

        for name, button in self.sidebar_buttons.items():
            active = name == key
            button.configure(
                bg=SIDEBAR_ACTIVE if active else SIDEBAR_BG,
                fg=ON_ACCENT if active else TEXT,
            )

        on_show = getattr(page, "on_show", None)
        if callable(on_show):
            on_show()

    def dial(self, number: str) -> None:
        self.navigate("dialpad")
        self.dialpad_page.on_dial(number)

    def log(self, message: str) -> None:
        clean = " ".join(str(message).split())
        self.status_label.configure(text=clean if len(clean) <= 110 else clean[:109] + "\u2026")

    def is_registered(self) -> bool:
        return self.registrar is not None and self.registrar.is_registered()

    def load_password(self, server_host: str, extension: str) -> str | None:
        return storage.load_password(server_host, extension)

    def _discard_pending_events(self) -> None:
        while True:
            try:
                self.events.get_nowait()
            except queue.Empty:
                return

    def _make_event_filter(self, generation: int) -> Callable[[RegistrarState, str], None]:
        def handler(state: RegistrarState, detail: str) -> None:
            if generation == self._generation:
                self.events.put((state, detail))

        return handler

    def register(self) -> None:
        page = self.setup_page
        if self.registrar is not None and self.registrar.running:
            if self.registrar.state is not RegistrarState.FAILED:
                return
            self._generation += 1
            self._discard_pending_events()
            self.registrar.stop(timeout=1.5)

        password = page.vars["password"].get()
        if not password:
            messagebox.showwarning("Falta contrasena", "Introduce la contrasena de la extension.")
            page.focus_password()
            return

        try:
            account = page.build_account()
        except ValueError as exc:
            page.show_error(str(exc))
            return

        errors = account.validate()
        if errors:
            page.show_error("\n".join(errors))
            return

        self.account = account
        self.account_password = password
        page._auto_domain = account.domain
        page.vars["domain"].set(account.domain)
        self._persist(account, password)
        self._start_registrar(account, password)

    def _start_registrar(self, account: AccountConfig, password: str) -> None:
        engine = self.dialpad_page.engine
        engine.account = account
        try:
            listening_port = engine.start_listening(account.sip_local_port)
        except OSError as exc:
            self.log(f"No se pudo abrir el punto de escucha: {exc}")
            messagebox.showerror("No se pudo registrar", str(exc))
            return
        self._generation += 1
        self._discard_pending_events()
        self.registrar = Registrar(
            account,
            password,
            on_event=self._make_event_filter(self._generation),
            contact_port=listening_port,
        )
        try:
            self.registrar.start()
        except (ValueError, OSError) as exc:
            self.log(f"No se pudo iniciar: {exc}")
            messagebox.showerror("No se pudo registrar", str(exc))
            self.registrar = None
            return
        self.log(f"Conectando a {account.server_host}:{account.server_port}...")

    def unregister(self) -> None:
        if self.registrar is not None and self.registrar.running:
            self.log("Solicitando baja de la extension...")
            self.registrar.unregister()

    def clear_credentials(self) -> None:
        """Limpia contrasena y campos derivados; conserva servidor y extension."""
        self._generation += 1
        self._discard_pending_events()
        if self.registrar is not None and self.registrar.running:
            self.registrar.stop(timeout=1.5)

        previous = self.account
        try:
            if previous.server_host and previous.extension:
                storage.delete_password(previous.server_host, previous.extension)
                self.log("Contrasena eliminada del Administrador de credenciales.")
        except storage.CredentialError as exc:
            self.log(f"No se pudo borrar la contrasena guardada: {exc}")

        self.account = AccountConfig(
            server_host=previous.server_host,
            server_port=previous.server_port,
            extension=previous.extension,
            display_name="",
            expires=previous.expires,
            keep_password=previous.keep_password,
            auto_register=previous.auto_register,
            outbound_prefix=previous.outbound_prefix,
            history_days=previous.history_days,
            window_geometry=previous.window_geometry,
        )
        save_config(self.account)

        page = self.setup_page
        page.clear_fields()
        page.sync_from_account(self.account)
        page._auto_domain = self.account.server_host
        page.vars["password"].set("")
        page.on_session_event(RegistrarState.IDLE, "Campos limpiados. Introduce la contrasena.")
        self.registrar = None
        self._paint_session(RegistrarState.IDLE, "Campos limpiados. Introduce la contrasena.")
        self.log("Campos limpiados. Introduce la contrasena correcta.")
        page.focus_password()


    def _persist(self, account: AccountConfig, password: str) -> None:
        account.window_geometry = self.account.window_geometry
        save_config(account)
        try:
            if account.keep_password and password:
                storage.store_password(account.server_host, account.extension, password)
            else:
                storage.delete_password(account.server_host, account.extension)
        except storage.CredentialError as exc:
            self.log(f"No se pudo guardar la contrasena: {exc}")
        self._paint_account()

    def _poll_events(self) -> None:
        try:
            while True:
                state, detail = self.events.get_nowait()
                self._paint_session(state, detail)
                self.setup_page.on_session_event(state, detail)
                self.dialpad_page.on_session_event(state, detail)
                self._paint_voicemail()
        except queue.Empty:
            pass
        self._after_ids.append(self.root.after(100, self._poll_events))

    def _paint_session(self, state: RegistrarState, detail: str) -> None:
        colors = {
            RegistrarState.REGISTERED: OK,
            RegistrarState.FAILED: ERR,
            RegistrarState.RETRYING: WARN,
        }
        color = colors.get(state, MUTED)
        self._dot.itemconfigure(self._dot_oval, fill=color)
        self.sidebar_status.configure(text=detail[:160], fg=color)
        self.log(detail)
        if state is RegistrarState.FAILED:
            self.setup_page.focus_password()
        if state in (RegistrarState.REGISTERED, RegistrarState.IDLE, RegistrarState.STOPPED):
            self._paint_account()

    def _paint_account(self) -> None:
        account = self.account
        if account.extension:
            target = f"@{account.domain or account.server_host}"
            self.sidebar_account.configure(text=f"Extension {account.extension}{target}")
            self.status_account.configure(
                text=f"{account.extension} @ {account.server_host}:{account.server_port}"
            )
        else:
            self.sidebar_account.configure(text="Sin extension configurada")
            self.status_account.configure(text="")

    def _paint_voicemail(self) -> None:
        if self.registrar is None:
            self.voicemail_badge.configure(text="")
            return
        unread = self.registrar.unread_voicemail()
        self.voicemail_badge.configure(
            text=f"Buzon de voz: {unread} pendiente(s)" if unread else ""
        )

    def refresh_badges(self) -> None:
        self._paint_voicemail()

    def _tick(self) -> None:
        if self.registrar is not None and self.registrar.is_registered():
            remaining = self.registrar.expires_remaining()
            expires_at = self.registrar.expires_at
            stamp = time.strftime("%H:%M:%S", time.localtime(expires_at)) if expires_at else "--:--:--"
            self.status_account.configure(
                text=f"{self.account.extension} @ {self.account.server_host}:"
                     f"{self.account.server_port}  |  refresco en {remaining}s ({stamp})"
            )
        self._after_ids.append(self.root.after(1000, self._tick))

    def _cancel_scheduled(self) -> None:
        for after_id in self._after_ids:
            try:
                self.root.after_cancel(after_id)
            except tk.TclError:
                pass
        self._after_ids.clear()

    def on_close(self) -> None:
        try:
            self.dialpad_page.engine.close()
        except Exception:  # noqa: BLE001 - cerrar la app no debe fallar
            pass
        if self.registrar is not None and self.registrar.running:
            self.registrar.stop(timeout=2.0)
        try:
            self.account.window_geometry = self.root.geometry()
            save_config(self.account)
        except (ValueError, OSError):
            pass
        self._cancel_scheduled()
        self.store.close()
        self.root.destroy()


def run() -> None:
    root = tk.Tk()
    MainWindow(root)
    root.mainloop()
