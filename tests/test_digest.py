"""Vectores de prueba de digest auth: RFC 2617 seccion 3.5 y casos SIP."""

from __future__ import annotations

import unittest

from sipclient.digest import (
    DigestAuthenticator,
    compute_response,
    md5_hex,
    parse_auth_header,
)


class Rfc2617VectorTest(unittest.TestCase):
    def test_known_values(self) -> None:
        self.assertEqual(
            md5_hex("Mufasa:testrealm@host.com:Circle Of Life"),
            "939e7578ed9e3c518a452acee763bce9",
        )
        self.assertEqual(
            md5_hex("GET:/dir/index.html"), "39aff3a2bab6126f332b942af96d3366"
        )

    def test_response_with_qop_auth(self) -> None:
        response = compute_response(
            username="Mufasa",
            password="Circle Of Life",
            realm="testrealm@host.com",
            nonce="dcd98b7102dd2f0e8b11d0f600bfb0c093",
            method="GET",
            uri="/dir/index.html",
            qop="auth",
            cnonce="0a4f113b",
            nc="00000001",
        )
        self.assertEqual(response, "6629fae49393a05397450978507c4ef1")

    def test_response_without_qop(self) -> None:
        response = compute_response(
            username="Mufasa",
            password="Circle Of Life",
            realm="testrealm@host.com",
            nonce="dcd98b7102dd2f0e8b11d0f600bfb0c093",
            method="GET",
            uri="/dir/index.html",
        )
        self.assertEqual(response, "670fd8c2df070c60b045671b8b24ff02")

    def test_md5_sess_differs_from_md5(self) -> None:
        common = dict(
            username="u",
            password="p",
            realm="asterisk",
            nonce="abc",
            method="REGISTER",
            uri="sip:sip.local",
        )
        md5 = compute_response(**common)
        md5_sess = compute_response(**common, algorithm="MD5-sess", cnonce="xyz", qop="auth", nc="00000001")
        self.assertNotEqual(md5, md5_sess)


class ParseAuthHeaderTest(unittest.TestCase):
    def test_plain_challenge(self) -> None:
        params = parse_auth_header('Digest realm="asterisk", nonce="1234"')
        self.assertEqual(params["realm"], "asterisk")
        self.assertEqual(params["nonce"], "1234")

    def test_challenge_with_quoted_comma_and_opaque(self) -> None:
        header = (
            'Digest realm="my, realm", nonce="n1", '
            'opaque="a,b=c", stale=false, algorithm=MD5'
        )
        params = parse_auth_header(header)
        self.assertEqual(params["realm"], "my, realm")
        self.assertEqual(params["opaque"], "a,b=c")
        self.assertEqual(params["stale"], "false")

    def test_qop_list(self) -> None:
        params = parse_auth_header('Digest realm="r", nonce="n", qop="auth,auth-int"')
        self.assertEqual(params["qop"], "auth,auth-int")

    def test_rejects_other_scheme(self) -> None:
        with self.assertRaises(ValueError):
            parse_auth_header("Basic dXNlcjpwYXNz")


class AuthenticatorTest(unittest.TestCase):
    def test_builds_expected_fields_and_increments_nc(self) -> None:
        auth = DigestAuthenticator("100", "secreto123")
        self.assertFalse(auth.challenged)

        auth.challenge_with('Digest realm="asterisk", nonce="NONCE1", qop="auth"')
        self.assertTrue(auth.challenged)

        first = auth.build_header("REGISTER", "sip:sip.local")
        second = auth.build_header("REGISTER", "sip:sip.local")

        self.assertIn('username="100"', first)
        self.assertIn('realm="asterisk"', first)
        self.assertIn("qop=auth", first)
        self.assertIn("nc=00000001", first)
        self.assertIn("nc=00000002", second)
        self.assertTrue(first.startswith("Digest "))

    def test_picks_auth_over_auth_int(self) -> None:
        auth = DigestAuthenticator("100", "p")
        auth.challenge_with('Digest realm="r", nonce="n", qop="auth-int,auth"')
        header = auth.build_header("REGISTER", "sip:s")
        self.assertIn(", qop=auth,", header)
        self.assertNotIn("auth-int", header)

    def test_challenge_resets_counter(self) -> None:
        auth = DigestAuthenticator("100", "p")
        auth.challenge_with('Digest realm="r", nonce="n1", qop="auth"')
        auth.build_header("REGISTER", "sip:s")
        auth.challenge_with('Digest realm="r", nonce="n2", qop="auth"')
        self.assertIn("nc=00000001", auth.build_header("REGISTER", "sip:s"))

    def test_requires_challenge(self) -> None:
        auth = DigestAuthenticator("100", "p")
        with self.assertRaises(RuntimeError):
            auth.build_header("REGISTER", "sip:s")

    def test_challenge_without_realm_fails(self) -> None:
        auth = DigestAuthenticator("100", "p")
        with self.assertRaises(ValueError):
            auth.challenge_with('Digest nonce="n"')


if __name__ == "__main__":
    unittest.main()
