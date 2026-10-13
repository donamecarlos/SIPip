"""Pruebas end-to-end del motor de llamadas contra el servidor falso."""

from __future__ import annotations

import time
import unittest

from sipclient.audio import DummyAudio
from sipclient.call import CallSession, CallState
from sipclient.config import AccountConfig
from sipclient.engine import SipCallEngine

from .fake_call_server import FakeCallServer
from .fake_incoming import FakeIncomingCaller


def wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def wait_state(events: list[CallSession], state: CallState, timeout: float = 5.0) -> bool:
    return wait_for(lambda: any(session.state is state for session in events), timeout)


class SipCallEngineTestCase(unittest.TestCase):
    maxDiff = None
    transport: str = "udp"

    def setUp(self) -> None:
        self.server = FakeCallServer(
            password="secreto123", ringing_delay=0.6, transport=self.transport
        )
        self.server.start()
        self.events: list[CallSession] = []
        self.audios: list[DummyAudio] = []
        self.engine = SipCallEngine(
            on_event=self.events.append,
            audio_factory=lambda: self._dummy_audio(),
            t1=0.05,
            max_sends=3,
            invite_timeout=4.0,
        )
        self.account = AccountConfig(
            server_host=self.server.host,
            server_port=self.server.port,
            extension="100",
            display_name="Pruebas",
            transport=self.transport,
        )
        self.engine.account = self.account
        self.listening_port = self.engine.start_listening()
        self.caller: FakeIncomingCaller | None = None

    def _dummy_audio(self) -> DummyAudio:
        audio = DummyAudio()
        self.audios.append(audio)
        return audio

    def tearDown(self) -> None:
        if self.caller is not None:
            self.caller.stop()
        self.engine.close()
        self.server.stop()

    def _incoming(self) -> FakeIncomingCaller:
        caller = FakeIncomingCaller(transport=self.transport)
        caller.start()
        self.caller = caller
        caller.call("100", ("127.0.0.1", self.listening_port))
        self.assertTrue(wait_state(self.events, CallState.INCOMING), "no llego el INVITE")
        return caller

    def _connected_audio(self) -> DummyAudio:
        self.assertIsNotNone(self.engine._audio, "El audio no se abrio")
        return self.engine._audio

    def test_invalid_target_fails_synchronously(self) -> None:
        session = self.engine.start_call("!!??", self.account, "secreto123")
        self.assertIs(session.state, CallState.FAILED)
        self.assertFalse(self.engine.running)

    def test_answered_call_negotiates_media_and_sends_bye(self) -> None:
        session = self.engine.start_call("100", self.account, "secreto123")
        self.assertIs(session.state, CallState.DIALING)

        self.assertTrue(wait_state(self.events, CallState.RINGING), "no llego 180")
        self.assertTrue(wait_state(self.events, CallState.CONNECTED), "no llego 200/ACK")
        self.assertEqual(self.server.ack_count, 1)
        self.assertIs(self.engine.session.state, CallState.CONNECTED)

        audio = self._connected_audio()
        self.assertTrue(wait_for(lambda: bool(self.server.rtp_received), 6.0),
                        "el motor no envio RTP")
        self.assertTrue(
            wait_for(lambda: len(audio.played) >= 2, 6.0),
            "el motor no reprodujo el eco RTP",
        )

        self.engine.hangup()
        self.assertTrue(wait_state(self.events, CallState.ENDED), "BYE no cerro la llamada")
        self.assertTrue(wait_for(lambda: self.server.byes == 1), "no llego el BYE")
        self.assertIs(self.engine.session.state, CallState.ENDED)
        self.assertTrue(any(s.duration >= 0 for s in self.events if s.state is CallState.ENDED))

    def test_cancel_while_ringing(self) -> None:
        self.engine.start_call("100", self.account, "secreto123")
        self.assertTrue(wait_state(self.events, CallState.RINGING), "no llego 180")

        self.engine.hangup()
        self.assertTrue(wait_state(self.events, CallState.ENDED), "CANCEL no cerro la llamada")
        self.assertTrue(wait_for(lambda: self.server.cancels == 1), "no llego el CANCEL")
        self.assertIs(self.engine.session.state, CallState.ENDED)

    def test_bad_password_is_forbidden(self) -> None:
        self.engine.start_call("100", self.account, "clave-incorrecta")
        self.assertTrue(
            wait_state(self.events, CallState.FAILED),
            "el digest invalido debia fallar la llamada",
        )
        self.assertGreaterEqual(self.server.bad_digest, 1)
        self.assertIn("Forbidden", self.engine.session.detail)

    def test_hold_uses_reinvite(self) -> None:
        self.engine.start_call("100", self.account, "secreto123")
        self.assertTrue(wait_state(self.events, CallState.CONNECTED), "no hubo conexion")
        invites_before = self.server.invites

        self.engine.set_hold(True)
        self.assertTrue(
            wait_state(self.events, CallState.HOLDING), "el re-INVITE no puso la llamada en espera"
        )
        self.assertTrue(wait_for(lambda: self.server.invites > invites_before),
                        "no se envio el re-INVITE")

        self.engine.set_hold(False)
        self.assertTrue(wait_state(self.events, CallState.CONNECTED),
                        "la llamada no se reanudo")
        self.engine.hangup()
        self.assertTrue(wait_state(self.events, CallState.ENDED))

    def test_mute_updates_session(self) -> None:
        self.engine.start_call("100", self.account, "secreto123")
        self.assertTrue(wait_state(self.events, CallState.CONNECTED), "no hubo conexion")
        self.engine.set_mute(True)
        self.assertTrue(self.engine.session.muting)
        self.engine.set_mute(False)
        self.assertFalse(self.engine.session.muting)

    def test_incoming_answered_and_terminated_by_caller(self) -> None:
        caller = self._incoming()
        caller.wait_for(lambda: self.engine.session.state is CallState.INCOMING)
        self.assertIn("entrante", self.engine.session.detail.lower())
        self.assertIn(caller.username, self.engine.session.target)

        self.engine.answer()
        self.assertTrue(wait_state(self.events, CallState.CONNECTED), "no se contesto")
        self.assertTrue(caller.established, "el llamante no recibio 200 OK")
        self.assertGreaterEqual(caller.acks, 1, "el llamante no envio el ACK")

        audio = self._connected_audio()
        self.assertTrue(wait_for(lambda: bool(caller.rtp_received), 6.0),
                        "el motor no envio RTP al llamante")
        self.assertTrue(
            wait_for(lambda: len(audio.played) >= 2, 6.0),
            "el motor no reprodujo el eco RTP entrante",
        )

        caller.bye()
        self.assertTrue(wait_state(self.events, CallState.ENDED), "el BYE no cerro la llamada")
        self.assertIs(self.engine.session.state, CallState.ENDED)
        self.assertIn("finalizada", self.engine.session.detail.lower())

    def test_incoming_rejected_with_486(self) -> None:
        caller = self._incoming()
        self.engine.reject_incoming()
        self.assertTrue(wait_state(self.events, CallState.ENDED), "el rechazo no cerro el ring")
        self.assertTrue(wait_for(lambda: caller.rejected), "el llamante no recibio el rechazo")
        self.assertIn(486, caller.statuses)
        self.assertIn("rechazada", self.engine.session.detail.lower())

    def test_incoming_hangup_sends_bye(self) -> None:
        caller = self._incoming()
        self.engine.answer()
        self.assertTrue(wait_state(self.events, CallState.CONNECTED), "no se contesto")
        self.assertTrue(caller.established)

        self.engine.hangup()
        self.assertTrue(wait_state(self.events, CallState.ENDED), "colgar no cerro la llamada")
        self.assertTrue(wait_for(lambda: caller.bye_confirms >= 1), "no se envio el BYE")
        self.assertIs(self.engine.session.state, CallState.ENDED)

    def test_incoming_cancelled_by_caller(self) -> None:
        caller = self._incoming()
        caller.cancel()
        self.assertTrue(wait_state(self.events, CallState.ENDED), "CANCEL no cerro el ring")
        self.assertTrue(wait_for(lambda: 487 in caller.statuses), "no llego el 487")
        self.assertIn("cancelo", self.engine.session.detail.lower())

    def test_incoming_busy_while_outgoing_active(self) -> None:
        self.engine.start_call("100", self.account, "secreto123")
        self.assertTrue(wait_state(self.events, CallState.CONNECTED), "no hubo conexion")

        responder = FakeIncomingCaller(transport=self.transport)
        responder.start()
        self.caller = responder
        responder.call("100", ("127.0.0.1", self.listening_port))
        self.assertTrue(
            wait_for(lambda: responder.rejected and 486 in responder.statuses, 6.0),
            "el llamante no recibio 486 Busy Here",
        )
        self.assertFalse(
            any(session.state is CallState.INCOMING for session in self.events),
            "no debia abrirse el ring con una llamada activa",
        )


class TcpSipCallEngineTestCase(SipCallEngineTestCase):
    """Flujo de llamadas completo sobre TCP."""

    transport: str = "tcp"


if __name__ == "__main__":
    unittest.main()