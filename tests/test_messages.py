"""Analisis y construccion de mensajes SIP."""

from __future__ import annotations

import unittest

from sipclient.config import AccountConfig
from sipclient.messages import (
    SipParseError,
    contact_expires,
    parse_messages_waiting,
    parse_message,
    parse_uri,
)
from sipclient.registrar import build_register, resolve_expires


class ParseMessageTest(unittest.TestCase):
    def test_parses_response(self) -> None:
        raw = (
            b"SIP/2.0 401 Unauthorized\r\n"
            b'WWW-Authenticate: Digest realm="asterisk", nonce="n1"\r\n'
            b"CSeq: 1 REGISTER\r\n"
            b"Content-Length: 0\r\n\r\n"
        )
        message = parse_message(raw)
        self.assertFalse(message.is_request)
        self.assertEqual(message.status, 401)
        self.assertEqual(message.reason, "Unauthorized")
        self.assertIn('nonce="n1"', message.header("www-authenticate") or "")
        self.assertEqual(message.header("cseq"), "1 REGISTER")
        self.assertEqual(message.body, b"")

    def test_parses_request_and_body(self) -> None:
        raw = (
            b"INVITE sip:200@sip.local SIP/2.0\r\n"
            b"Via: SIP/2.0/UDP 10.0.0.1:5060;branch=z9hG4bK1\r\n"
            b"Content-Type: application/sdp\r\n"
            b"Content-Length: 4\r\n"
            b"\r\n"
            b"v=0\r\n"
        )
        message = parse_message(raw)
        self.assertTrue(message.is_request)
        self.assertEqual(message.method, "INVITE")
        self.assertEqual(message.body, b"v=0\r\n")

    def test_unfolds_continuation_lines(self) -> None:
        raw = (
            b"SIP/2.0 200 OK\r\n"
            b"Via: SIP/2.0/UDP 10.0.0.1:5060;\r\n"
            b" branch=z9hG4bK1\r\n"
            b"Content-Length: 0\r\n\r\n"
        )
        message = parse_message(raw)
        self.assertEqual(message.header("via"), "SIP/2.0/UDP 10.0.0.1:5060; branch=z9hG4bK1")

    def test_accepts_lf_only_separators(self) -> None:
        message = parse_message(b"SIP/2.0 200 OK\nContent-Length: 0\n\n")
        self.assertEqual(message.status, 200)

    def test_rejects_malformed(self) -> None:
        with self.assertRaises(SipParseError):
            parse_message(b"no blank line here")
        with self.assertRaises(SipParseError):
            parse_message(b"SIP/2.0 abc OK\r\nContent-Length: 0\r\n\r\n")
        with self.assertRaises(SipParseError):
            parse_message(b"INVITE sip:a SIP/2.0\r\nbadheader\r\n\r\n")

    def test_preserves_duplicate_headers(self) -> None:
        raw = (
            b"SIP/2.0 200 OK\r\n"
            b"Contact: <sip:100@a>;expires=60\r\n"
            b"Contact: <sip:100@b>;expires=90\r\n"
            b"Content-Length: 0\r\n\r\n"
        )
        message = parse_message(raw)
        self.assertEqual(len(message.headers_all("contact")), 2)


class UriTest(unittest.TestCase):
    def test_user_and_host(self) -> None:
        parsed = parse_uri("sip:100@sip.empresa.local")
        self.assertEqual(parsed["user"], "100")
        self.assertEqual(parsed["host"], "sip.empresa.local")
        self.assertEqual(parsed["port"], "")

    def test_port_and_params(self) -> None:
        parsed = parse_uri("sip:100@10.0.0.5:5080;transport=tcp")
        self.assertEqual(parsed["host"], "10.0.0.5")
        self.assertEqual(parsed["port"], "5080")
        self.assertEqual(parsed["params"], "transport=tcp")

    def test_angle_brackets_and_ipv6(self) -> None:
        self.assertEqual(parse_uri("<sip:100@a.b>")["host"], "a.b")
        parsed = parse_uri("sip:100@[2001:db8::1]:5060")
        self.assertEqual(parsed["host"], "2001:db8::1")
        self.assertEqual(parsed["port"], "5060")

    def test_no_user(self) -> None:
        self.assertEqual(parse_uri("sip:sip.local")["user"], "")

    def test_rejects_non_sip(self) -> None:
        with self.assertRaises(ValueError):
            parse_uri("tel:+34600111222")


class MessagesWaitingTest(unittest.TestCase):
    def test_yes_with_count(self) -> None:
        self.assertEqual(parse_messages_waiting("yes;voicemail=3"), (True, 3))

    def test_no(self) -> None:
        self.assertEqual(parse_messages_waiting("no"), (False, 0))

    def test_yes_without_count(self) -> None:
        self.assertEqual(parse_messages_waiting("yes"), (True, 0))

    def test_case_insensitive_and_extra_params(self) -> None:
        self.assertEqual(parse_messages_waiting("YES; Voicemail=1; X=2"), (True, 1))

    def test_missing_or_invalid(self) -> None:
        self.assertIsNone(parse_messages_waiting(None))
        self.assertIsNone(parse_messages_waiting("maybe"))
        self.assertIsNone(parse_messages_waiting(""))

    def test_non_numeric_count(self) -> None:
        self.assertEqual(parse_messages_waiting("yes;voicemail=abc"), (True, 0))


class ContactExpiresTest(unittest.TestCase):
    def test_reads_expires(self) -> None:
        self.assertEqual(contact_expires("<sip:100@1.2.3.4:5060>;expires=3600"), 3600)
        self.assertEqual(contact_expires('<sip:100@1.2.3.4>;expires="120"'), 120)

    def test_missing_or_invalid(self) -> None:
        self.assertIsNone(contact_expires("<sip:100@1.2.3.4:5060>"))
        self.assertIsNone(contact_expires("<sip:100@1.2.3.4>;expires=abc"))


class BuildRegisterTest(unittest.TestCase):
    def account(self) -> AccountConfig:
        return AccountConfig(
            server_host="sip.empresa.local",
            server_port=5060,
            extension="100",
            domain="sip.empresa.local",
            display_name="Ana",
            expires=1800,
        )

    def test_wire_format(self) -> None:
        payload, uri = build_register(
            self.account(), "192.168.1.20", 5062, "callid123", 1, 1800
        )
        text = payload.decode("utf-8")
        self.assertTrue(text.startswith("REGISTER sip:sip.empresa.local SIP/2.0\r\n"))
        self.assertTrue(text.endswith("Content-Length: 0\r\n\r\n"))
        self.assertEqual(uri, "sip:sip.empresa.local")

        message = parse_message(payload)
        self.assertTrue(message.is_request)
        self.assertEqual(message.method, "REGISTER")
        self.assertEqual(message.header("cseq"), "1 REGISTER")
        self.assertEqual(message.header("expires"), "1800")
        self.assertEqual(message.header("contact"), "<sip:100@192.168.1.20:5062;transport=udp>")
        self.assertIn("tag=", message.header("from") or "")
        self.assertIn("branch=z9hG4bK", message.header("via") or "")
        self.assertEqual(message.header("call-id"), "callid123")
        self.assertEqual(int(message.header("content-length") or "-1"), 0)

    def test_unregister_uses_zero_expires(self) -> None:
        payload, _ = build_register(self.account(), "10.0.0.5", 5060, "c", 9, 0)
        self.assertEqual(parse_message(payload).header("expires"), "0")

    def test_authorization_header_included_only_when_given(self) -> None:
        payload, _ = build_register(self.account(), "10.0.0.5", 5060, "c", 2, 3600)
        self.assertIsNone(parse_message(payload).header("authorization"))

        payload, _ = build_register(
            self.account(), "10.0.0.5", 5060, "c", 2, 3600, 'Digest realm="r"'
        )
        self.assertEqual(parse_message(payload).header("authorization"), 'Digest realm="r"')

    def test_domain_defaults_to_server_host(self) -> None:
        account = AccountConfig(server_host="10.0.0.1", extension="200")
        self.assertEqual(account.domain, "10.0.0.1")
        payload, uri = build_register(account, "10.0.0.5", 5060, "c", 1, 3600)
        message = parse_message(payload)
        self.assertEqual(uri, "sip:10.0.0.1")
        self.assertIn("<sip:200@10.0.0.1>", message.header("to") or "")
        self.assertIn("<sip:200@10.0.0.1>", message.header("from") or "")
        self.assertEqual(message.header("contact"), "<sip:200@10.0.0.5:5060;transport=udp>")


class ResolveExpiresTest(unittest.TestCase):
    def test_prefers_expires_header(self) -> None:
        response = parse_message(
            b"SIP/2.0 200 OK\r\nExpires: 120\r\n"
            b"Contact: <sip:100@a>;expires=9999\r\nContent-Length: 0\r\n\r\n"
        )
        self.assertEqual(resolve_expires(response, 3600), 120)

    def test_falls_back_to_contact(self) -> None:
        response = parse_message(
            b"SIP/2.0 200 OK\r\nContact: <sip:100@a>;expires=900\r\n"
            b"Content-Length: 0\r\n\r\n"
        )
        self.assertEqual(resolve_expires(response, 3600), 900)

    def test_falls_back_to_request(self) -> None:
        response = parse_message(b"SIP/2.0 200 OK\r\nContent-Length: 0\r\n\r\n")
        self.assertEqual(resolve_expires(response, 3600), 3600)


if __name__ == "__main__":
    unittest.main()
