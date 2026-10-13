"""Validacion de destinos y contrato del motor de llamadas."""

from __future__ import annotations

import unittest

from sipclient.call import CallState, PendingCallEngine, normalize_target
from sipclient.config import AccountConfig


class NormalizeTargetTest(unittest.TestCase):
    def test_extension(self) -> None:
        result = normalize_target("100")
        self.assertIsNone(result.error)
        self.assertEqual(result.number, "100")
        self.assertTrue(result.is_extension)

    def test_strips_formatting_characters(self) -> None:
        result = normalize_target("  (0034) 600-11-22-33  ")
        self.assertEqual(result.number, "0034600112233")
        self.assertFalse(result.is_extension)

    def test_international_keeps_plus(self) -> None:
        result = normalize_target("+346001112233", outbound_prefix="8")
        self.assertEqual(result.number, "+346001112233")

    def test_applies_outbound_prefix(self) -> None:
        result = normalize_target("100", outbound_prefix="8")
        self.assertEqual(result.number, "8100")

    def test_does_not_double_prefix(self) -> None:
        result = normalize_target("8100", outbound_prefix="8")
        self.assertEqual(result.number, "8100")

    def test_prefix_with_separators(self) -> None:
        result = normalize_target("100", outbound_prefix=" 9 - ")
        self.assertEqual(result.number, "9100")

    def test_star_and_hash_allowed(self) -> None:
        self.assertEqual(normalize_target("*21#").number, "*21#")

    def test_empty_input(self) -> None:
        result = normalize_target("   ")
        self.assertIsNotNone(result.error)
        self.assertEqual(result.number, "")

    def test_rejects_invalid_characters(self) -> None:
        result = normalize_target("100;rm -rf")
        self.assertIsNotNone(result.error)
        self.assertIn(";", result.error or "")

    def test_rejects_too_long(self) -> None:
        result = normalize_target("1" * 65)
        self.assertIsNotNone(result.error)

    def test_long_number_is_not_extension(self) -> None:
        self.assertFalse(normalize_target("600111223").is_extension)


class PendingCallEngineTest(unittest.TestCase):
    def account(self) -> AccountConfig:
        return AccountConfig(
            server_host="127.0.0.1", server_port=5060, extension="100", outbound_prefix="8"
        )

    def test_valid_target_reports_unsupported(self) -> None:
        engine = PendingCallEngine()
        session = engine.start_call("100", self.account(), "clave")
        self.assertEqual(session.state, CallState.UNSUPPORTED)
        self.assertEqual(session.target, "8100")
        self.assertIn("siguiente fase", session.detail)

    def test_invalid_target_fails_without_publish(self) -> None:
        events = []
        engine = PendingCallEngine(on_event=events.append)
        session = engine.start_call("abc", self.account(), "clave")
        self.assertEqual(session.state, CallState.FAILED)
        self.assertEqual(len(events), 1)

    def test_hangup_resets_to_idle(self) -> None:
        engine = PendingCallEngine()
        engine.start_call("100", self.account(), "clave")
        engine.hangup()
        self.assertEqual(engine.state, CallState.IDLE)

    def test_hangup_on_idle_is_noop(self) -> None:
        engine = PendingCallEngine()
        engine.hangup()
        self.assertEqual(engine.state, CallState.IDLE)

    def test_flags_are_published(self) -> None:
        events = []
        engine = PendingCallEngine(on_event=events.append)
        engine.set_mute(True)
        engine.set_hold(True)
        self.assertTrue(engine.session.muting)
        self.assertTrue(engine.session.on_hold)
        self.assertEqual(len(events), 2)


if __name__ == "__main__":
    unittest.main()
