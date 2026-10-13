"""Pantalla del marcador: teclado, display, llamada y control de sesion."""

from __future__ import annotations

import queue
import re
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox
from typing import TYPE_CHECKING

from ..call import CallSession, CallState, normalize_target
from ..datastore import (
    DIRECTION_INCOMING,
    DIRECTION_OUTGOING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_LABELS,
    STATUS_MISSED,
    STATUS_NO_ANSWER,
    STATUS_ONGOING,
    STATUS_REJECTED,
)
from ..engine import SipCallEngine
from ..export_excel import write_call_summary
from ..registrar import RegistrarState
from ..tariff import charge, money, money_or_free, tariff_for
from ..tonos import TonosPlayer
from .common import (
    BORDER_SOFT,
    CARD,
    DANGER,
    DANGER_BG,
    ERR,
    FONT,
    FONT_BOLD,
    FONT_SMALL,
    FONT_TITLE,
    KEY_BG,
    MUTED,
    OK,
    OK_HOVER,
    ON_ACCENT,
    SURFACE,
    SURFACE_HOVER,
    TEXT,
    WARN,
    WARN_BG,
    ActionButton,
    Card,
    Dialpad,
)

if TYPE_CHECKING:
    from .app import MainWindow

MAX_DISPLAY_LENGTH = 40
NO_ANSWER_TIMEOUT = 8.0

_ENGINE_LABELS = {
    CallState.INCOMING: "Llamada entrante",
    CallState.DIALING: "Marcando",
    CallState.RINGING: "Recibiendo tono",
    CallState.CONNECTED: "En conversacion",
    CallState.HOLDING: "En espera",
    CallState.FAILED: "Llamada fallida",
    CallState.ENDED: "Llamada finalizada",
    CallState.IDLE: "Sin llamadas",
}
_ENGINE_COLORS = {
    CallState.INCOMING: OK,
    CallState.CONNECTED: OK,
    CallState.HOLDING: WARN,
    CallState.FAILED: ERR,
    CallState.RINGING: WARN,
}


class DialpadPage(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        app: "MainWindow",
        engine: SipCallEngine | None = None,
        tones: TonosPlayer | None = None,
    ) -> None:
        super().__init__(parent, bg=CARD)
        self.app = app
        self._digits = ""
        self._engine_events: queue.Queue[CallSession | None] = queue.Queue()
        self._call_log_id: int | None = None
        self._answered_at: float | None = None
        self._ending = False
        self._call_kind = ""
        self._call_rate = 0.0
        self._call_label = ""
        self._summary_shown = False
        self._summary_window: tk.Toplevel | None = None
        self._summary_rows: list[tuple[str, tk.Label]] = []
        self._summary_fields: list[tuple[str, str]] = []
        self._incoming = False
        self._no_answer = False
        self._ring_timer: str | None = None

        self.tones = tones or TonosPlayer()
        self.engine = engine or SipCallEngine(on_event=self._engine_event_tap)
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=4)
        self._build()

        self.after(100, self._poll_engine)
        self.after(1000, self._tick_timer)

    def _engine_event_tap(self, session: CallSession) -> None:
        self._engine_events.put(session)

    def _build(self) -> None:
        left = Card(self, "Marcador", "Escribe un numero o extension y pulsa Llamar.")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left_body = left.body()

        display_frame = tk.Frame(left_body, bg=KEY_BG, highlightbackground=BORDER_SOFT, highlightthickness=1)
        display_frame.pack(fill="x")
        self.display = tk.Label(
            display_frame, text="", bg=KEY_BG, fg=TEXT, font=("Consolas", 22), anchor="e", height=1
        )
        display_frame.pack_propagate(False)
        display_frame.configure(height=52)
        self.display.pack(fill="both", expand=True, padx=10)
        self.hint = tk.Label(left_body, text="", bg=CARD, fg=MUTED, font=FONT_SMALL, anchor="w",
                           wraplength=300)
        self.hint.pack(anchor="w", pady=(4, 6))

        Dialpad(left_body, self._append_digit, self._append_plus).pack()

        controls = tk.Frame(left_body, bg=CARD)
        controls.pack(fill="x", pady=(8, 0))
        self.backspace_button = ActionButton(
            controls, "< Borrar", self._backspace, base=SURFACE
        )
        self.backspace_button.pack(side="left")
        self.space_button = ActionButton(
            controls, "Espacio", self._append_space, base=SURFACE
        )
        self.space_button.pack(side="left", padx=(8, 0))
        ActionButton(
            controls, "Limpiar", self._clear_digits, base=SURFACE
        ).pack(side="left", padx=(8, 0))

        self.call_button = ActionButton(
            left_body, "Llamar", self._toggle_call, base=OK, hover=OK_HOVER,
            foreground=ON_ACCENT, font=("Segoe UI", 13, "bold"),
        )
        self.call_button.pack(fill="x", pady=(10, 0))
        self.call_button.set_enabled(False)

        controls_row = tk.Frame(left_body, bg=CARD)
        controls_row.pack(fill="x", pady=(6, 0))
        self.mute_button = ActionButton(
            controls_row, "Silenciar", lambda: self._toggle_flag("muting"), base=SURFACE
        )
        self.mute_button.pack(side="left", fill="x", expand=True)
        self.hold_button = ActionButton(
            controls_row, "Retener", lambda: self._toggle_flag("on_hold"), base=SURFACE
        )
        self.hold_button.pack(side="left", fill="x", expand=True, padx=(8, 0))

        right = tk.Frame(self, bg=CARD)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        state_card = Card(right, "Estado de la llamada")
        state_card.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        state_body = state_card.body()
        self.call_state = tk.Label(
            state_body, text="Sin llamadas", bg=CARD, fg=MUTED, font=FONT_TITLE, anchor="w"
        )
        self.call_state.pack(anchor="w")
        self.call_target = tk.Label(
            state_body, text="", bg=CARD, fg=MUTED, font=FONT, anchor="w", wraplength=280
        )
        self.call_target.pack(anchor="w", pady=(4, 0))
        self.call_timer = tk.Label(
            state_body, text="00:00", bg=CARD, fg=MUTED, font=("Consolas", 14), anchor="w"
        )
        self.call_timer.pack(anchor="w", pady=(8, 0))
        self.call_detail = tk.Label(
            state_body, text="", bg=CARD, fg=MUTED, font=FONT_SMALL, anchor="w",
            justify="left", wraplength=280,
        )
        self.call_detail.pack(anchor="w", fill="x", pady=(8, 0))

        self.incoming_actions = tk.Frame(state_body, bg=CARD)
        self.answer_button = ActionButton(
            self.incoming_actions, "Contestar", self._answer_incoming,
            base=OK, hover=OK_HOVER, foreground=ON_ACCENT, font=FONT_BOLD,
        )
        self.answer_button.pack(side="left", fill="x", expand=True)
        self.reject_button = ActionButton(
            self.incoming_actions, "Rechazar", self._reject_incoming,
            base=DANGER, hover=ERR, foreground=ON_ACCENT, font=FONT_BOLD,
        )
        self.reject_button.pack(side="left", fill="x", expand=True, padx=(8, 0))

        contacts_card = Card(right, "Favoritos", "Pulsa un contacto para rellenar el marcador.")
        contacts_card.grid(row=1, column=0, sticky="nsew")
        contacts_card.rowconfigure(2, weight=1)
        self.favorites_frame = tk.Frame(contacts_card, bg=CARD)
        self.favorites_frame.grid(row=2, column=0, sticky="nsew", padx=16, pady=16)

        self.rowconfigure(0, weight=1)

    def on_show(self) -> None:
        self.refresh_favorites()
        self._update_display()

    def refresh_favorites(self) -> None:
        for child in self.favorites_frame.winfo_children():
            child.destroy()
        favorites = [contact for contact in self.app.store.contacts.list() if contact.favorite]
        if not favorites:
            tk.Label(
                self.favorites_frame, text="Todavia no hay contactos favoritos.",
                bg=CARD, fg=MUTED, font=FONT_SMALL,
            ).pack(anchor="w")
            return
        for index, contact in enumerate(favorites[:8]):
            button = tk.Button(
                self.favorites_frame,
                text=f"{index + 1}. {contact.name}\n{contact.number}",
                font=FONT_SMALL,
                bg=KEY_BG,
                fg=TEXT,
                activebackground=SURFACE_HOVER,
                relief="flat",
                highlightbackground=BORDER_SOFT,
                highlightthickness=1,
                bd=0,
                anchor="w",
                cursor="hand2",
                command=lambda number=contact.number: self._set_digits(number),
            )
            button.pack(fill="x", pady=3)

    def _set_digits(self, value: str) -> None:
        self._digits = value[:MAX_DISPLAY_LENGTH]
        self._update_display()

    def _append_digit(self, digit: str) -> None:
        if len(self._digits) >= MAX_DISPLAY_LENGTH:
            return
        self._digits += digit
        self._update_display()

    def _append_plus(self) -> None:
        if not self._digits.startswith("+"):
            self._digits = "+" + self._digits.lstrip("+")
        self._update_display()

    def _append_space(self) -> None:
        if self._digits and not self._digits.endswith(" "):
            self._append_digit(" ")

    def _backspace(self) -> None:
        self._digits = self._digits[:-1]
        self._update_display()

    def _clear_digits(self) -> None:
        self._digits = ""
        self._update_display()

    def _update_display(self) -> None:
        self.display.configure(text=self._digits or "")
        normalized = normalize_target(self._digits, self.app.account.outbound_prefix)
        if self._digits and not normalized.error:
            contact = self.app.store.contacts.find_by_number(
                self._digits.strip()
            ) or self.app.store.contacts.find_by_number(normalized.number)
            peer = f"  {contact.name}" if contact else ""
            label = normalized.number
            if self.app.account.outbound_prefix and not self._digits.startswith("+"):
                label = f"{self.app.account.outbound_prefix} + {self._digits}"
            tariff = tariff_for(
                self.app.account.extension,
                normalized.number,
                strip_prefix=self.app.account.outbound_prefix,
            )
            rate_text = money_or_free(tariff.rate)
            self.hint.configure(
                text=f"-> {label}{peer}\n{tariff.label}  ·  {rate_text}/min",
                fg=OK if (contact or not tariff.rate) else MUTED,
            )
        else:
            self.hint.configure(text=normalized.error or "", fg=ERR if normalized.error else MUTED)
        self.call_button.set_enabled(
            bool(self._digits) and not normalized.error and self.app.is_registered()
        )

    def _toggle_call(self) -> None:
        state = self.engine.state
        if state is CallState.INCOMING:
            self._answer_incoming()
        elif state is CallState.IDLE:
            self._start_call()
        else:
            self.engine.hangup()

    def _answer_incoming(self) -> None:
        self.tones.stop()
        self.engine.answer()

    def _reject_incoming(self) -> None:
        self.tones.stop()
        self.engine.reject_incoming()

    def _start_call(self) -> None:
        self.tones.stop()
        normalized = normalize_target(self._digits, self.app.account.outbound_prefix)
        if normalized.error:
            messagebox.showwarning("Numero no valido", normalized.error)
            return
        if not self.app.is_registered():
            messagebox.showwarning(
                "Sin sesion", "La extension no esta registrada. Valida las credenciales primero."
            )
            return

        session = self.engine.start_call(
            normalized.number, self.app.account, self.app.account_password
        )
        tariff = tariff_for(
            self.app.account.extension,
            normalized.number,
            strip_prefix=self.app.account.outbound_prefix,
        )
        self._incoming = False
        self._call_kind = tariff.kind
        self._call_rate = tariff.rate
        self._call_label = tariff.label
        if session.state in (CallState.DIALING, CallState.RINGING):
            self._ending = False
            self._answered_at = None
            self._summary_shown = False
            self.call_state.configure(text="Marcando", fg=WARN)
            self.call_target.configure(text=session.peer or session.target)
            self.call_detail.configure(text=session.detail, fg=TEXT)
            self.app.log(session.detail)
            self._schedule_no_answer_tone()
        self._apply_session(session)

    def _poll_engine(self) -> None:
        while True:
            try:
                session = self._engine_events.get_nowait()
            except queue.Empty:
                break
            self._apply_session(session)
        self.after(100, self._poll_engine)

    def _apply_session(self, session: CallSession) -> None:
        self.call_state.configure(
            text=_ENGINE_LABELS.get(session.state, session.state.value),
            fg=_ENGINE_COLORS.get(session.state, TEXT),
        )
        self.call_target.configure(text=session.peer or session.target)
        self.call_detail.configure(text=session.detail, fg=MUTED if session.detail else TEXT)
        if session.answered_at is not None:
            self._answered_at = session.answered_at

        if session.state is CallState.INCOMING:
            self._cancel_ring_timer()
            self._incoming = True
            self._call_kind = "Entrante"
            self._call_rate = 0.0
            self._call_label = "Entrante"
            self._answered_at = None
            self._ending = False
            self._summary_shown = False
            self._call_log_id = self.app.store.calls.add(
                direction=DIRECTION_INCOMING,
                number=session.target,
                status=STATUS_ONGOING,
                peer=session.peer,
                started_at=session.started_at,
                account=self.app.account.extension,
                call_type="Entrante",
            )
            self.call_state.configure(text="Llamada entrante", fg=OK)
            self.call_detail.configure(text=session.detail, fg=TEXT)
            self.call_button.configure(text="Contestar")
            self.incoming_actions.pack(fill="x", pady=(10, 0))
            self.app.log(session.detail)
            self.tones.play("llamada_entrante", loop=True)
            return

        if session.state in (CallState.DIALING, CallState.RINGING):
            if self._call_log_id is None and not self._incoming:
                self._call_log_id = self.app.store.calls.add(
                    direction=DIRECTION_OUTGOING,
                    number=session.target,
                    status=STATUS_ONGOING,
                    peer=session.peer,
                    started_at=session.started_at,
                    account=self.app.account.extension,
                    call_type=self._call_label or "Saliente",
                )
            self._schedule_no_answer_tone()

        if session.state is CallState.CONNECTED:
            self._cancel_ring_timer()
            self._reset_call_controls()
            self._call_log_update(STATUS_ONGOING)
            self.call_button.set_enabled(True)
            self._reset_flags_after(session)
            return
        if session.state is CallState.HOLDING:
            self._cancel_ring_timer()
            self.hold_button.configure(text="Reanudar", bg=WARN_BG)
            return

        if session.state is CallState.IDLE:
            self._cancel_ring_timer()
            self.tones.stop()
            self._handle_call_end(
                STATUS_COMPLETED if self._answered_at else STATUS_NO_ANSWER, session
            )
            self._reset_call_controls()
            self.call_button.set_enabled(bool(self._digits))
            self.call_timer.configure(text="00:00")
            self.mute_button.configure(text="Silenciar", bg=SURFACE)
            self.hold_button.configure(text="Retener", bg=SURFACE)
            self._update_display()
            return

        if session.state in (CallState.ENDED, CallState.FAILED):
            self._cancel_ring_timer()
            if not self._no_answer:
                self.tones.stop()
            if session.state is CallState.FAILED:
                self.tones.play("fuera_de_servicio")
            elif not self._no_answer and not (self._incoming and self._answered_at is None):
                self.tones.play("telefono_colgado")
            summary = self._build_summary(session)
            if session.state is CallState.ENDED and self._incoming and self._answered_at is None:
                status = STATUS_REJECTED if "rechaz" in (session.detail or "") else STATUS_MISSED
            else:
                status = (
                    STATUS_FAILED if session.state is CallState.FAILED
                    else (STATUS_COMPLETED if self._answered_at else STATUS_NO_ANSWER)
                )
            self._handle_call_end(status, session)
            self._reset_call_controls()
            self.call_button.set_enabled(True)
            self.mute_button.configure(text="Silenciar", bg=SURFACE)
            self.hold_button.configure(text="Retener", bg=SURFACE)
            if summary:
                self.call_timer.configure(text=summary[0])
                self.call_detail.configure(text=summary[1], fg=TEXT)

    def _reset_call_controls(self) -> None:
        self.incoming_actions.pack_forget()
        self.call_button.configure(text="Llamar")
        self._incoming = False
        self._no_answer = False

    def _schedule_no_answer_tone(self) -> None:
        self._cancel_ring_timer()
        self._ring_timer = self.after(
            int(NO_ANSWER_TIMEOUT * 1000), self._check_no_answer
        )

    def _cancel_ring_timer(self) -> None:
        if self._ring_timer is not None:
            try:
                self.after_cancel(self._ring_timer)
            except tk.TclError:
                pass
            self._ring_timer = None

    def _check_no_answer(self) -> None:
        self._ring_timer = None
        if self.engine.state in (CallState.DIALING, CallState.RINGING):
            self.tones.play("fuera_de_servicio")
            self._no_answer = True
            self.engine.hangup()

    def _reset_flags_after(self, session: CallSession) -> None:
        if not session.muting and self.mute_button.cget("text") == "Silenciado":
            self.mute_button.configure(text="Silenciar", bg=SURFACE)
        if not session.on_hold and self.hold_button.cget("text") == "Reanudar":
            self.hold_button.configure(text="Retener", bg=SURFACE)

    def _call_log_update(self, status: str) -> None:
        record_id = self._call_log_id
        if record_id is None:
            return
        self.app.store.calls.update(record_id, status)

    def _format_elapsed(self, seconds: int) -> str:
        minutes, remaining = divmod(max(0, seconds), 60)
        return f"{minutes:02d}:{remaining:02d}"

    def _build_summary(self, session: CallSession) -> tuple[str, str] | None:
        if session.state is CallState.FAILED or self._answered_at is None:
            return None
        duration = max(0, int(time.time() - self._answered_at))
        monto = charge(self._call_rate, duration)
        timer = self._format_elapsed(duration)
        if self._call_label:
            detail = f"{self._call_label}  ·  {duration} s  ·  {money(monto)}"
        else:
            detail = f"Duracion {timer}  ·  {money(monto)}"
        return f"{timer}  ·  {money(monto)}", detail

    def _finalize_log(self, status: str) -> tuple[int, float]:
        record_id = self._call_log_id
        if record_id is None:
            return 0, 0.0
        duration = 0
        cost = 0.0
        if status is STATUS_COMPLETED and self._answered_at:
            duration = int(time.time() - self._answered_at)
            cost = charge(self._call_rate, duration)
        self.app.store.calls.update(record_id, status, duration, cost)
        self._call_log_id = None
        return duration, cost

    def _handle_call_end(self, status: str, session: CallSession) -> None:
        had_record = self._call_log_id is not None
        duration, cost = self._finalize_log(status)
        self._answered_at = None
        show_summary = (
            had_record
            and not self._summary_shown
            and not (self._incoming and status in (STATUS_REJECTED, STATUS_MISSED))
        )
        if show_summary:
            self._show_summary(session, status, duration, cost)
            self._summary_shown = True
        self._call_kind = ""
        self._call_rate = 0.0
        self._call_label = ""

    def _build_summary_fields(
        self, session: CallSession, status: str, duration: int, cost: float
    ) -> list[tuple[str, str]]:
        contact = self.app.store.contacts.find_by_number(session.target)
        name = contact.name if contact else "-"
        if session.started_at:
            fecha = datetime.fromtimestamp(session.started_at).strftime("%d/%m/%Y %H:%M:%S")
        else:
            fecha = "-"
        timer = self._format_elapsed(duration)
        return [
            ("Estado", STATUS_LABELS.get(status, status)),
            ("Nombre", name),
            ("Destino", session.target or "-"),
            ("Desde (extension)", self.app.account.extension or "-"),
            ("Fecha", fecha),
            ("Tarifa", self._call_label or "-"),
            ("Tarifa por minuto", f"{money_or_free(self._call_rate)}/min"),
            ("Duracion", f"{timer} ({duration} s)"),
            ("Monto", money(cost)),
        ]

    def _show_summary(
        self, session: CallSession, status: str, duration: int, cost: float
    ) -> None:
        fields = self._build_summary_fields(session, status, duration, cost)
        self._summary_fields = fields

        window = self._summary_window
        if window is None or not window.winfo_exists():
            window = tk.Toplevel(self)
            window.title("Resumen de llamada")
            window.configure(bg=CARD)
            window.transient(self.winfo_toplevel())
            window.resizable(False, False)
            body = tk.Frame(window, bg=CARD, padx=16, pady=14)
            body.pack(fill="both", expand=True)
            tk.Label(
                body, text="Llamada finalizada", bg=CARD, fg=TEXT,
                font=FONT_TITLE, anchor="w",
            ).pack(anchor="w")
            grid = tk.Frame(body, bg=CARD)
            grid.pack(fill="x", pady=(10, 8))
            self._summary_rows = []
            for index, (label, _value) in enumerate(fields):
                tk.Label(
                    grid, text=label + ":", bg=CARD, fg=MUTED, font=FONT_SMALL
                ).grid(row=index, column=0, sticky="ne", padx=(0, 14), pady=3)
                value = tk.Label(
                    grid, text="", bg=CARD, fg=TEXT, font=FONT_SMALL, anchor="w"
                )
                value.grid(row=index, column=1, sticky="w", pady=3)
                self._summary_rows.append((label, value))
            actions = tk.Frame(body, bg=CARD)
            actions.pack(anchor="e")
            ActionButton(
                actions, "Exportar a Excel", self._export_summary,
                base=OK, hover=OK_HOVER, foreground=ON_ACCENT,
            ).pack(side="left")
            ActionButton(
                actions, "Cerrar", window.destroy, base=SURFACE
            ).pack(side="left", padx=(8, 0))
            self._summary_window = window
        else:
            window.deiconify()
            window.lift()

        for (label, value_label), (_old_label, value) in zip(self._summary_rows, fields):
            value_label.configure(text=value, fg=TEXT)
            if label == "Monto":
                value_label.configure(font=("Segoe UI", 11, "bold"), fg=OK)

    def _export_summary(self) -> None:
        fields = self._summary_fields
        if not fields:
            return
        target = next(
            (value for label, value in fields if label == "Destino"), "llamada"
        )
        filename = re.sub(r"[^A-Za-z0-9]+", "-", target).strip("-") or "llamada"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Libro de Excel", "*.xlsx"), ("Todos", "*.*")],
            initialfile=f"{filename}_{stamp}.xlsx",
            title="Guardar resumen en Excel",
        )
        if not path:
            return
        try:
            write_call_summary(path, fields)
        except Exception as exc:  # noqa: BLE001 - el error se muestra al usuario
            self.app.log(f"No se pudo exportar el resumen: {exc}")
            messagebox.showerror("Exportar a Excel", f"No se pudo guardar el archivo:\n{exc}")
            return
        self.app.log(f"Resumen de llamada exportado a {path}")
        messagebox.showinfo("Exportar a Excel", f"Resumen guardado en:\n{path}")

    def _tick_timer(self) -> None:
        answered = self._answered_at
        if answered and self.engine.state in (CallState.CONNECTED, CallState.HOLDING):
            elapsed = max(0, int(time.time() - answered))
            minutes, seconds = divmod(elapsed, 60)
            text = f"{minutes:02d}:{seconds:02d}"
            if self._call_rate:
                text += f"  ·  {money(charge(self._call_rate, elapsed))}"
            self.call_timer.configure(text=text)
        self.after(1000, self._tick_timer)

    def _toggle_flag(self, attribute: str) -> None:
        session = self.engine.session
        if attribute == "muting":
            self.engine.set_mute(not session.muting)
            self.mute_button.configure(
                text="Silenciado" if self.engine.session.muting else "Silenciar",
                bg=DANGER_BG if self.engine.session.muting else SURFACE,
            )
        else:
            self.engine.set_hold(not session.on_hold)
            self.hold_button.configure(
                text="Reanudar" if self.engine.session.on_hold else "Retener",
                bg=WARN_BG if self.engine.session.on_hold else SURFACE,
            )

    def on_session_event(self, state: RegistrarState, _detail: str) -> None:
        if state is not RegistrarState.REGISTERED:
            self.call_button.set_enabled(False)
            if state is RegistrarState.FAILED:
                self.call_state.configure(text="Sesion no disponible", fg=ERR)
                self.call_detail.configure(
                    text="Corrige las credenciales en Ajustes para poder marcar.", fg=MUTED
                )
        else:
            self._update_display()

    def on_dial(self, number: str) -> None:
        self.app.navigate("dialpad")
        self._set_digits(number)