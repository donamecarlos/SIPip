"""Aplicacion Kivy para Android del softphone SIPip.

Reutiliza el nucleo de `sipclient` (engine/registrar/datastore/transport) y
solo sustituye la interfaz tkinter por Kivy y el audio por el backend Android.
"""

from __future__ import annotations

import time

from kivy.app import App
from kivy.clock import Clock
from kivy.graphics import Color, Rectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen, ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput

from sipclient.call import CallState, STATE_LABELS
from sipclient.datastore import (
    DIRECTION_INCOMING,
    DIRECTION_OUTGOING,
    STATUS_COMPLETED,
    STATUS_NO_ANSWER,
    STATUS_REJECTED,
    STATUS_FAILED,
)
from sipclient.registrar import RegistrarState

from . import __version__
from .service import SipService, is_android

GREEN = (0.16, 0.62, 0.28, 1)
RED = (0.80, 0.20, 0.20, 1)
ACCENT = (0.20, 0.42, 0.78, 1)
DARK = (0.12, 0.12, 0.15, 1)
SURFACE = (0.18, 0.19, 0.23, 1)
LIGHT = (0.94, 0.95, 0.97, 1)
MUTED = (0.60, 0.62, 0.66, 1)


def _format_duration(started: float | None) -> str:
    if not started:
        return "00:00"
    elapsed = max(0, int(time.time() - started))
    minutes, seconds = divmod(elapsed, 60)
    return f"{minutes:02d}:{seconds:02d}"


def _format_clock(stamp: float) -> str:
    return time.strftime("%d/%m %H:%M", time.localtime(stamp))


class DialpadScreen(Screen):
    KEY_ROWS = ["123", "456", "789", "*0#"]

    def __init__(self, app: "SIPipApp", **kwargs) -> None:
        super().__init__(name="dialpad", **kwargs)
        self.app = app
        self._digits = ""
        self._build()

    def _build(self) -> None:
        root = BoxLayout(orientation="vertical", padding=12, spacing=8)
        self.display = Label(
            text="", font_size=34, bold=True, color=(1, 1, 1, 1),
            halign="right", valign="middle", size_hint_y=0.16,
        )
        self.display.bind(size=lambda w, _s: setattr(w, "text_size", (w.width, w.height)))
        with self.display.canvas.before:
            Color(*SURFACE, a=1)
            self._bg = Rectangle(pos=self.display.pos, size=self.display.size)
        self.display.bind(pos=lambda w, _v: setattr(self._bg, "pos", w.pos))
        self.display.bind(size=lambda w, _s: setattr(self._bg, "size", w.size))
        root.add_widget(self.display)

        pad = GridLayout(cols=3, spacing=10)
        for row in self.KEY_ROWS:
            for char in row:
                button = Button(
                    text=char or " ",
                    font_size=30,
                    bold=True,
                    background_normal="",
                    background_color=SURFACE,
                    color=(1, 1, 1, 1),
                )
                button.bind(on_release=lambda _b, digit=char: self._press(digit))
                pad.add_widget(button)
        root.add_widget(pad, index=1)

        controls = BoxLayout(spacing=10, size_hint_y=0.18)
        for _label, _color in (("\u232B", (0.9, 0.9, 0.9, 1)), ("call", GREEN)):
            button = Button(
                text=_label, font_size=30, bold=True,
                background_normal="", background_color=_color, color=(1, 1, 1, 1),
            )
            button.bind(on_release=self._on_extra)
            controls.add_widget(button)
        root.add_widget(controls)

        self.add_widget(root)

    def _press(self, digit: str) -> None:
        self._digits += digit
        self.display.text = self._digits

    def _on_extra(self, button: Button) -> None:
        if button.text == "\u232B":
            self._digits = self._digits[:-1]
            self.display.text = self._digits
        else:
            self.app.place_call(self._digits)

    def clear(self) -> None:
        self._digits = ""
        self.display.text = ""


class ContactsScreen(Screen):
    def __init__(self, app: "SIPipApp", **kwargs) -> None:
        super().__init__(name="contacts", **kwargs)
        self.app = app
        self._items: list = []
        self._build()

    def _build(self) -> None:
        root = BoxLayout(orientation="vertical", padding=12, spacing=8)
        top = BoxLayout(size_hint_y=None, height=54, spacing=8)
        self.search = TextInput(
            hint_text="Buscar contacto", size_hint_x=1,
            background_color=(0.25, 0.26, 0.30, 1), foreground_color=(1, 1, 1, 1),
            multiline=False,
        )
        self.search.bind(text=lambda _w, _t: self._refresh())
        add_button = Button(
            text="+", font_size=28, size_hint_x=None, width=54,
            background_normal="", background_color=ACCENT, color=(1, 1, 1, 1),
        )
        add_button.bind(on_release=self._open_add)
        top.add_widget(self.search)
        top.add_widget(add_button)
        root.add_widget(top)

        self.scroll = ScrollView()
        self.list = BoxLayout(orientation="vertical", size_hint_y=None, spacing=4)
        self.list.bind(minimum_height=self.list.setter("height"))
        self.scroll.add_widget(self.list)
        root.add_widget(self.scroll)
        self.add_widget(root)

    def on_pre_enter(self, *_args) -> None:
        self._refresh()

    def _refresh(self) -> None:
        self.list.clear_widgets()
        query = self.search.text.strip()
        for contact in self.app.service.store.contacts.list(query):
            self.list.add_widget(self._contact_row(contact))

    def _contact_row(self, contact):
        row = BoxLayout(size_hint_y=None, height=60, spacing=8, pos_hint={"center_x": 0.5, "center_y": 0.5})
        with row.canvas.before:
            Color(*SURFACE, a=1)
            rect = Rectangle(pos=row.pos, size=row.size)
            row.bind(pos=lambda w, _v: setattr(rect, "pos", w.pos))
            row.bind(size=lambda w, _s: setattr(rect, "size", w.size))
        info = BoxLayout(orientation="vertical", padding=(10, 4))
        info.add_widget(Label(text=contact.name or contact.number, font_size=16, bold=True,
                              halign="left", color=(1, 1, 1, 1),
                              size_hint_y=None, height=26))
        if contact.name and contact.number:
            info.add_widget(Label(text=contact.number, font_size=13, halign="left",
                                  color=MUTED, size_hint_y=None, height=24))
        row.add_widget(info)
        row.bind(on_touch_down=lambda r, touch: self._on_row_touch(r, touch, contact))
        return row

    def _on_row_touch(self, row, touch, contact) -> None:
        if not row.collide_point(*touch.pos):
            return
        self.app.place_call(contact.number)

    def _open_add(self, _button) -> None:
        from kivy.uix.popup import Popup

        content = BoxLayout(orientation="vertical", padding=12, spacing=8)
        name_input = TextInput(hint_text="Nombre", multiline=False)
        number_input = TextInput(hint_text="Numero", multiline=False,
                                 input_filter="int")
        content.add_widget(name_input)
        content.add_widget(number_input)
        actions = BoxLayout(size_hint_y=None, height=52, spacing=8)
        save = Button(text="Guardar", background_color=ACCENT, background_normal="")
        cancel = Button(text="Cancelar", background_normal="")
        actions.add_widget(save)
        actions.add_widget(cancel)
        content.add_widget(actions)

        popup = Popup(title="Nuevo contacto", content=content,
                      size_hint=(0.9, 0.45))
        def _save(_b=None) -> None:
            name = name_input.text.strip()
            number = number_input.text.strip()
            if number:
                self.app.service.store.contacts.add(name or number, number)
            popup.dismiss()
            self._refresh()
        def _cancel(_b=None) -> None:
            popup.dismiss()
        save.bind(on_release=_save)
        cancel.bind(on_release=_cancel)
        popup.open()


class HistoryScreen(Screen):
    def __init__(self, app: "SIPipApp", **kwargs) -> None:
        super().__init__(name="history", **kwargs)
        self.app = app
        self._build()

    def _build(self) -> None:
        root = BoxLayout(orientation="vertical", padding=12, spacing=8)
        title = Label(text="Historial", font_size=17, bold=True, color=(1, 1, 1, 1),
                      size_hint_y=None, height=34)
        root.add_widget(title)
        self.scroll = ScrollView()
        self.list = BoxLayout(orientation="vertical", size_hint_y=None, spacing=4)
        self.list.bind(minimum_height=self.list.setter("height"))
        self.scroll.add_widget(self.list)
        root.add_widget(self.scroll)
        self.add_widget(root)

    def on_pre_enter(self, *_args) -> None:
        self._refresh()

    def _refresh(self) -> None:
        self.list.clear_widgets()
        self.app.service.store.prune_calls(self.app.service.config.history_days)
        records = self.app.service.store.calls.list(limit=200)
        for record in records:
            self.list.add_widget(self._record_row(record))

    def _record_row(self, record):
        arrow = "\u2191" if record.direction == DIRECTION_OUTGOING else "\u2193"
        status = record.status_label
        if record.status == STATUS_NO_ANSWER:
            status = "Sin respuesta"
        if record.status == STATUS_REJECTED:
            status = "Rechazada"
        if record.status == STATUS_FAILED:
            status = "Fallida"
        text = (
            f"{arrow} {record.number or record.peer or '?'}"
            f"   {_format_clock(record.started_at)}"
        )
        row = BoxLayout(size_hint_y=None, height=50, spacing=8)
        with row.canvas.before:
            Color(*SURFACE, a=1)
            rect = Rectangle(pos=row.pos, size=row.size)
            row.bind(pos=lambda w, _v: setattr(rect, "pos", w.pos))
            row.bind(size=lambda w, _s: setattr(rect, "size", w.size))
        label = Label(text=text, font_size=15, halign="left", color=(1, 1, 1, 1),
                      size_hint_x=1)
        label.bind(size=lambda w, _s: setattr(w, "text_size", (w.width, None)))
        row.add_widget(label)
        if record.duration:
            row.add_widget(Label(text=f"{record.duration}s", font_size=13,
                                 color=MUTED, size_hint_x=None, width=64))
        row.bind(on_touch_down=lambda r, touch: self._on_row_touch(r, touch, record))
        return row

    def _on_row_touch(self, row, touch, record) -> None:
        if not row.collide_point(*touch.pos):
            return
        self.app.place_call(record.number or record.peer)


class SettingsScreen(Screen):
    def __init__(self, app: "SIPipApp", **kwargs) -> None:
        super().__init__(name="settings", **kwargs)
        self.app = app
        self._build()

    def _build(self) -> None:
        root = BoxLayout(orientation="vertical", padding=12, spacing=6)
        self.status = Label(
            text="Sin iniciar", font_size=15, color=MUTED, size_hint_y=None, height=30,
        )
        root.add_widget(self.status)

        fields = [
            ("Servidor (IP):", "server_host"),
            ("Puerto:", "server_port"),
            ("Extension:", "extension"),
            ("Contrasena:", "password"),
            ("Dominio (opcional):", "domain"),
            ("Nombre mostrado:", "display_name"),
            ("Prefijo salida (opcional):", "outbound_prefix"),
        ]
        self.vars: dict[str, TextInput] = {}
        for label, key in fields:
            row = BoxLayout(size_hint_y=None, height=46, spacing=8)
            name = Label(text=label, font_size=13, color=(1, 1, 1, 1),
                         size_hint_x=None, width=170, halign="left")
            name.bind(size=lambda w, _s: setattr(w, "text_size", (w.width, None)))
            widget = TextInput(
                text=self._initial_value(key),
                multiline=False,
                password=(key == "password"),
                hint_text="" if not self._initial_value(key) else "•••",
                background_color=(0.25, 0.26, 0.30, 1), foreground_color=(1, 1, 1, 1),
            )
            self.vars[key] = widget
            row.add_widget(name)
            row.add_widget(widget)
            root.add_widget(row)

        actions = BoxLayout(size_hint_y=None, height=52, spacing=8)
        register = Button(text="Registrar", font_size=16, bold=True,
                          background_normal="", background_color=ACCENT,
                          color=(1, 1, 1, 1))
        register.bind(on_release=self._on_register)
        actions.add_widget(register)
        root.add_widget(actions)
        self.add_widget(root)

    def _initial_value(self, key: str) -> str:
        config = self.app.service.config
        if key == "server_host":
            return config.server_host
        if key == "server_port":
            return str(config.server_port)
        if key == "extension":
            return config.extension
        if key == "password":
            return self.app.service.password
        if key == "domain":
            return config.domain
        if key == "display_name":
            return config.display_name
        if key == "outbound_prefix":
            return config.outbound_prefix
        return ""

    def _on_register(self, _button) -> None:
        port_text = self.vars["server_port"].text.strip() or "5060"
        try:
            port = int(port_text)
        except ValueError:
            port = 5060
        errors = self.app.service.apply_account(
            server_host=self.vars["server_host"].text,
            server_port=port,
            extension=self.vars["extension"].text,
            password=self.vars["password"].text,
            domain=self.vars["domain"].text,
            display_name=self.vars["display_name"].text,
            transport=self.app.service.config.transport,
            outbound_prefix=self.vars["outbound_prefix"].text,
        )
        if errors:
            self.status.text = "; ".join(errors)
            return
        self.status.text = "Registrando..."
        self.app.service.start_registration()

    def on_registrar(self, state: RegistrarState, detail: str) -> None:
        if state is RegistrarState.REGISTERED:
            self.status.text = f"[color=55cc66]\u2713 Registrada ({detail})[/color]"
        elif state in (RegistrarState.FAILED, RegistrarState.EXPIRED):
            self.status.text = f"[color=cc5555]\u2717 {state.value}: {detail}[/color]"
        else:
            self.status.text = detail


class CallScreen(Screen):
    def __init__(self, app: "SIPipApp", **kwargs) -> None:
        super().__init__(name="call", **kwargs)
        self.app = app
        self._timer_event = None
        self._build()

    def _build(self) -> None:
        root = BoxLayout(orientation="vertical", padding=12, spacing=8)
        self.peer = Label(text="", font_size=28, bold=True, color=(1, 1, 1, 1))
        self.state_label = Label(text="", font_size=17, color=MUTED)
        self.timer = Label(text="00:00", font_size=48, bold=True, color=(1, 1, 1, 1))
        root.add_widget(self.peer)
        root.add_widget(self.state_label)
        root.add_widget(self.timer)

        self.dtmf_pad = self._build_pad()
        root.add_widget(self.dtmf_pad)

        controls = BoxLayout(size_hint_y=None, height=64, spacing=10)
        self.mute_button = Button(text="Mudo", font_size=15,
                                  background_normal="", background_color=SURFACE,
                                  color=(1, 1, 1, 1))
        self.mute_button.bind(on_release=lambda _b: self.app.toggle_mute())
        self.hold_button = Button(text="Espera", font_size=15,
                                  background_normal="", background_color=SURFACE,
                                  color=(1, 1, 1, 1))
        self.hold_button.bind(on_release=lambda _b: self.app.toggle_hold())
        self.hangup_button = Button(text="\u25A0 Colgar", font_size=18, bold=True,
                                    background_normal="", background_color=RED,
                                    color=(1, 1, 1, 1))
        self.hangup_button.bind(on_release=lambda _b: self.app.service.hangup())
        controls.add_widget(self.mute_button)
        controls.add_widget(self.hold_button)
        controls.add_widget(self.hangup_button)
        root.add_widget(controls)
        self.add_widget(root)

    def _build_pad(self) -> GridLayout:
        pad = GridLayout(cols=3, row_default_height=52, spacing=8,
                         size_hint_y=0.5)
        for digit in "123456789*0#":
            button = Button(
                text=digit, font_size=24, bold=True,
                background_normal="", background_color=SURFACE, color=(1, 1, 1, 1),
            )
            button.bind(on_release=lambda _b, d=digit: self.app.service.send_dtmf(d))
            pad.add_widget(button)
        return pad

    def on_enter(self, *_args) -> None:
        self._timer_event = Clock.schedule_interval(lambda _dt: self._refresh_timer(), 0.5)
        self._render()

    def on_leave(self, *_args) -> None:
        if self._timer_event is not None:
            self._timer_event.cancel()
            self._timer_event = None

    def _refresh_timer(self) -> None:
        session = self.app.service.engine.session
        if session.state in (CallState.CONNECTED, CallState.HOLDING):
            self.timer.text = _format_duration(session.answered_at)
        elif session.state in (CallState.INCOMING, CallState.DIALING,
                               CallState.RINGING, CallState.ANSWERED):
            self.timer.text = _format_duration(session.started_at)

    def _render(self) -> None:
        session = self.app.service.engine.session
        if session.state is CallState.INCOMING:
            self.peer.text = session.peer or session.target or "?"
            self.state_label.text = "Llamada entrante"
            self.timer.text = "Invocando Tono"
            self.hangup_button.text = "Rechazar"
            self.hangup_button.background_color = RED
            self.dtmf_pad.opacity = 0
        else:
            self.peer.text = session.peer or session.target or ""
            self.state_label.text = STATE_LABELS.get(session.state, "")
            if session.state in (CallState.CONNECTED, CallState.HOLDING):
                self.timer.text = _format_duration(session.answered_at)
            else:
                self.timer.text = _format_duration(session.started_at)
            self.hangup_button.text = "\u25A0 Colgar"
            self.hangup_button.background_color = RED
            self.dtmf_pad.opacity = 1 if session.state in (
                CallState.CONNECTED, CallState.HOLDING) else 0.6

    def update_controls(self, muting: bool, hold: bool) -> None:
        self.mute_button.text = "Mudo ON" if muting else "Mudo"
        self.mute_button.background_color = (0.55, 0.4, 0.0, 1) if muting else SURFACE
        self.hold_button.text = "En espera" if hold else "Espera"
        self.hold_button.background_color = (0.4, 0.3, 0.0, 1) if hold else SURFACE


class SIPipApp(App):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.title = "SIPip"
        self.service = SipService()
        self._previous_screen = "dialpad"
        self._wake_lock = None

    # ------------------------------------------------------------------
    # Arranque
    # ------------------------------------------------------------------

    def build(self) -> BoxLayout:
        self._request_android_permissions()
        self.manager = ScreenManager()
        self.nav = self._build_navigation()

        self.dialpad = DialpadScreen(self)
        self.contacts = ContactsScreen(self)
        self.history = HistoryScreen(self)
        self.settings = SettingsScreen(self)
        self.call_screen = CallScreen(self)
        for screen in (self.dialpad, self.contacts, self.history,
                       self.settings, self.call_screen):
            self.manager.add_widget(screen)

        header = BoxLayout(size_hint_y=None, height=38, padding=(12, 0))
        header_label = Label(
            text=f"SIPip {__version__}", font_size=14, color=MUTED,
            halign="left",
        )
        header_label.bind(size=lambda w, _s: setattr(w, "text_size", (w.width, None)))
        header.add_widget(header_label)
        self.status_label = Label(text="", font_size=13, color=MUTED)
        header.add_widget(self.status_label)

        root = BoxLayout(orientation="vertical")
        root.add_widget(header)
        root.add_widget(self.manager)
        root.add_widget(self.nav)
        return root

    def _build_navigation(self) -> BoxLayout:
        bar = BoxLayout(size_hint_y=None, height=58, spacing=2)
        for key, label in (("dialpad", "Marcar"), ("contacts", "Contactos"),
                           ("history", "Historial"), ("settings", "Ajustes")):
            button = Button(
                text=label, font_size=14, bold=False,
                background_normal="", background_color=SURFACE, color=(1, 1, 1, 1),
            )
            button.bind(on_release=lambda _b, name=key: self.go(name))
            bar.add_widget(button)
        return bar

    def _request_android_permissions(self) -> None:
        if is_android():
            try:
                from android import permissions  # type: ignore[attr-defined]

                permissions.request_permissions(
                    [permissions.Permission.RECORD_AUDIO]
                )
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # Navegacion
    # ------------------------------------------------------------------

    def go(self, name: str) -> None:
        self._previous_screen = self.manager.current
        self.manager.current = name

    def on_start(self) -> None:
        self.service.on_registrar = self._on_registrar
        self.service.on_call = self._on_call
        if self.service.config.is_complete:
            self.settings.status.text = "Ajustes guardados. Pulsa Registrar."

    def on_stop(self) -> None:
        self.service.shutdown()
        self._release_wake_lock()

    # ------------------------------------------------------------------
    # Eventos del servicio
    # ------------------------------------------------------------------

    def _on_registrar(self, state: RegistrarState, detail: str) -> None:
        if self.service.is_registered:
            self.status_label.text = "[color=55cc66]\u2713 Registrada[/color]"
        else:
            self.status_label.text = "[color=cc5555]\u2717 Sin registrar[/color]"
        self.settings.on_registrar(state, detail)

    def _on_call(self, session) -> None:
        state = session.state
        if state is CallState.IDLE:
            self._release_wake_lock()
            self.manager.current = self._previous_screen
            self.dialpad.clear()
            self._append_history(session)
            return
        self._acquire_wake_lock()
        if state in (CallState.INCOMING, CallState.DIALING, CallState.RINGING,
                     CallState.ANSWERED, CallState.CONNECTED, CallState.HOLDING):
            self.manager.current = "call"
            self.call_screen._render()
            self.call_screen.update_controls(session.muting, session.on_hold)
        elif state is CallState.FAILED:
            self._release_wake_lock()
            self.manager.current = self._previous_screen
            self.dialpad.clear()
            self._append_history(session)

    def _append_history(self, session) -> None:
        if not session.target:
            return
        status = STATUS_COMPLETED
        if session.state is CallState.FAILED:
            status = STATUS_FAILED
        elif session.answered_at is None:
            status = STATUS_NO_ANSWER
        self.service.store.calls.add(
            direction=DIRECTION_OUTGOING,
            number=session.target,
            status=status,
            peer=session.peer,
            duration=session.duration,
            account=self.service.config.extension,
            started_at=session.started_at,
        )

    # ------------------------------------------------------------------
    # Acciones de llamada
    # ------------------------------------------------------------------

    def place_call(self, number: str) -> None:
        if not number:
            return
        self.service.start_call(number)

    def toggle_mute(self) -> None:
        enabled = self.service.toggle_mute()
        self.call_screen.update_controls(enabled, self.service.engine.session.on_hold)

    def toggle_hold(self) -> None:
        enabled = self.service.toggle_hold()
        self.call_screen.update_controls(self.service.engine.session.muting, enabled)

    # ------------------------------------------------------------------
    # Wake lock (mantener la CPU despierta durante la llamada)
    # ------------------------------------------------------------------

    def _acquire_wake_lock(self) -> None:
        if self._wake_lock is not None or not is_android():
            return
        try:
            from jnius import autoclass  # type: ignore[attr-defined]

            python_activity = autoclass("org.kivy.android.PythonActivity")
            power_manager = python_activity.mActivity.getSystemService(
                python_activity.mActivity.POWER_SERVICE
            )
            wake_lock = power_manager.newWakeLock(
                power_manager.PARTIAL_WAKE_LOCK, "SIPip:Call"
            )
            wake_lock.acquire()
            self._wake_lock = wake_lock
        except Exception:  # noqa: BLE001
            self._wake_lock = None

    def _release_wake_lock(self) -> None:
        if self._wake_lock is None:
            return
        try:
            self._wake_lock.release()
        except Exception:  # noqa: BLE001
            pass
        self._wake_lock = None