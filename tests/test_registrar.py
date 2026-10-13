"""Prueba de extremo completo contra el servidor REGISTER falso."""

from __future__ import annotations

import threading
import time
import unittest

from sipclient.config import AccountConfig
from sipclient.registrar import Registrar, RegistrarState

from .fake_server import FakeRegistrarServer

PASSWORD = "secreto123"


def wait_for_state(registrar: Registrar, expected: RegistrarState, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if registrar.state is expected:
            return True
        time.sleep(0.02)
    return False


class RegistrarIntegrationTest(unittest.TestCase):
    transport: str = "udp"

    def setUp(self) -> None:
        self.server = FakeRegistrarServer(password=PASSWORD, expires=120, transport=self.transport)
        self.server.start()
        self.registrars: list[Registrar] = []

    def tearDown(self) -> None:
        for registrar in self.registrars:
            if registrar.running:
                registrar.stop(timeout=0.3)
        self.server.stop()

    def make_registrar(self, password: str = PASSWORD, expires: int = 120) -> Registrar:
        account = AccountConfig(
            server_host=self.server.host,
            server_port=self.server.port,
            extension="100",
            domain=self.server.host,
            expires=expires,
            transport=self.transport,
        )
        events: list[tuple[RegistrarState, str]] = []
        registrar = Registrar(account, password, on_event=lambda state, detail: events.append((state, detail)))
        registrar.events = events  # type: ignore[attr-defined]
        self.registrars.append(registrar)
        return registrar

    def test_registers_after_digest_challenge(self) -> None:
        registrar = self.make_registrar()
        registrar.start()

        self.assertTrue(wait_for_state(registrar, RegistrarState.REGISTERED), registrar.detail)
        self.assertEqual(self.server.bad_digest, 0)
        self.assertGreaterEqual(self.server.registrations, 1)

        states = [state for state, _ in registrar.events]  # type: ignore[attr-defined]
        self.assertIn(RegistrarState.CHALLENGED, states)
        self.assertEqual(states[-1], RegistrarState.REGISTERED)
        self.assertTrue(registrar.is_registered())
        self.assertGreater(registrar.expires_remaining(), 0)

        registrar.unregister()
        self.assertTrue(self.server.wait_for_registrations(1), "no se confirmo la baja")
        self.assertTrue(wait_for_state(registrar, RegistrarState.IDLE))

    def test_wrong_password_yields_forbidden(self) -> None:
        registrar = self.make_registrar(password="incorrecta")
        registrar.start()

        self.assertTrue(wait_for_state(registrar, RegistrarState.FAILED, timeout=6.0), registrar.detail)
        self.assertIn("Forbidden", registrar.detail)
        self.assertFalse(registrar.is_registered())
        self.assertEqual(self.server.registrations, 0)

    def test_unreachable_server_reports_timeout(self) -> None:
        account = AccountConfig(server_host="127.0.0.1", server_port=1, extension="100")
        registrar = Registrar(account, PASSWORD)
        self.registrars.append(registrar)
        registrar.start()

        self.assertTrue(wait_for_state(registrar, RegistrarState.RETRYING, timeout=8.0), registrar.detail)
        self.assertIn("Sin respuesta", registrar.detail)

    def test_invalid_config_is_rejected(self) -> None:
        registrar = Registrar(AccountConfig(server_host="", extension="100"), PASSWORD)
        with self.assertRaises(ValueError):
            registrar.start()

    def test_stop_terminates_thread(self) -> None:
        registrar = self.make_registrar()
        registrar.start()
        self.assertTrue(wait_for_state(registrar, RegistrarState.REGISTERED))

        registrar.stop()
        self.assertFalse(registrar.running)
        self.assertIn(registrar.state, (RegistrarState.STOPPED, RegistrarState.IDLE))

    def test_stop_sends_unregister(self) -> None:
        """stop() debe enviar expires=0 para liberar el contacto en Asterisk."""
        registrar = self.make_registrar()
        registrar.start()
        self.assertTrue(wait_for_state(registrar, RegistrarState.REGISTERED))

        registrar.stop(timeout=3.0)

        self.assertFalse(registrar.running)
        self.assertTrue(
            self.server.wait_for_deregistrations(1, timeout=2.0),
            "stop() no envio el REGISTER expires=0",
        )

    def test_unregister_from_failed_state_does_not_hang(self) -> None:
        registrar = self.make_registrar(password="mala")
        registrar.start()
        self.assertTrue(wait_for_state(registrar, RegistrarState.FAILED, timeout=6.0))

        started = time.monotonic()
        registrar.stop(timeout=3.0)
        self.assertLess(time.monotonic() - started, 3.0)
        self.assertFalse(registrar.running)

    def test_events_do_not_race_with_main_thread(self) -> None:
        registrar = self.make_registrar()
        seen: list[str] = []
        lock = threading.Lock()

        def callback(state: RegistrarState, detail: str) -> None:
            with lock:
                seen.append(state.value)

        account = AccountConfig(
            server_host=self.server.host, server_port=self.server.port, extension="100",
            expires=120, transport=self.transport,
        )
        threaded = Registrar(account, PASSWORD, on_event=callback)
        self.registrars.append(threaded)
        threaded.start()

        self.assertTrue(wait_for_state(threaded, RegistrarState.REGISTERED))
        with lock:
            self.assertIn("registered", seen)

    def test_contact_and_via_carry_transport(self) -> None:
        """El Contact y el Via del REGISTER anuncian el transporte configurado."""
        registrar = self.make_registrar()
        registrar.start()
        self.assertTrue(wait_for_state(registrar, RegistrarState.REGISTERED), registrar.detail)
        registrar.unregister()
        self.assertTrue(self.server.wait_for_registrations(1))

        any_matching = any(
            f"SIP/2.0/{self.transport.upper()} " in data.decode("utf-8", "replace")
            and f";transport={self.transport}" in data.decode("utf-8", "replace")
            for data in self.server.received
        )
        self.assertTrue(any_matching, "ningun REGISTER anuncia transport=" + self.transport)


class TcpRegistrarIntegrationTest(RegistrarIntegrationTest):
    """Los mismos flujos de registro sobre TCP."""

    transport: str = "tcp"

    def test_tcp_connect_refused_reports_failure(self) -> None:
        account = AccountConfig(
            server_host="127.0.0.1", server_port=1, extension="100", transport="tcp"
        )
        refused = Registrar(account, PASSWORD)
        self.registrars.append(refused)
        refused.start()

        self.assertTrue(wait_for_state(refused, RegistrarState.FAILED, timeout=6.0), refused.detail)
        self.assertFalse(refused.running)


class VoicemailIndicatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registrars: list[Registrar] = []

    def tearDown(self) -> None:
        for registrar in self.registrars:
            if registrar.running:
                registrar.stop(timeout=0.3)

    def _register(self, server: FakeRegistrarServer) -> Registrar:
        account = AccountConfig(
            server_host=server.host, server_port=server.port, extension="100", expires=120
        )
        registrar = Registrar(account, PASSWORD)
        self.registrars.append(registrar)
        registrar.start()
        return registrar

    def test_reads_messages_waiting_header(self) -> None:
        server = FakeRegistrarServer(password=PASSWORD, expires=120, voicemail=3)
        server.start()
        self.addCleanup(server.stop)

        registrar = self._register(server)
        self.assertTrue(wait_for_state(registrar, RegistrarState.REGISTERED), registrar.detail)
        self.assertTrue(registrar.message_waiting)
        self.assertEqual(registrar.unread_voicemail(), 3)
        self.assertIn("buzon de voz: 3", registrar.detail)

    def test_no_voicemail(self) -> None:
        server = FakeRegistrarServer(password=PASSWORD, expires=120, voicemail=0)
        server.start()
        self.addCleanup(server.stop)

        registrar = self._register(server)
        self.assertTrue(wait_for_state(registrar, RegistrarState.REGISTERED), registrar.detail)
        self.assertFalse(registrar.message_waiting)
        self.assertEqual(registrar.unread_voicemail(), 0)

    def test_absent_header_keeps_defaults(self) -> None:
        server = FakeRegistrarServer(password=PASSWORD, expires=120)
        server.start()
        self.addCleanup(server.stop)

        registrar = self._register(server)
        self.assertTrue(wait_for_state(registrar, RegistrarState.REGISTERED), registrar.detail)
        self.assertEqual(registrar.unread_voicemail(), 0)



if __name__ == "__main__":
    unittest.main()
