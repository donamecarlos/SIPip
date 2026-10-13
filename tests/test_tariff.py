"""Clasificacion de llamadas, tarifas y calculo del cobro."""

from __future__ import annotations

import os
import tempfile
import unittest

from sipclient.tariff import (
    RATE_AMERICA,
    RATE_CLARO,
    RATE_CROSS,
    RATE_ESPECIALES,
    RATE_EUROPA,
    RATE_RESTO,
    RATE_TIGO,
    charge,
    classify_destination,
    money,
    save_prefix_overrides,
    load_prefix_overrides,
    tariff_for,
)


class ClassifyDestinationTest(unittest.TestCase):
    def test_extension_ranges(self) -> None:
        self.assertEqual(classify_destination("1234"), "local")
        self.assertEqual(classify_destination("2000"), "local")
        self.assertEqual(classify_destination("1000"), "local")
        self.assertEqual(classify_destination("4050"), "claro")
        self.assertEqual(classify_destination("5000"), "claro")
        self.assertEqual(classify_destination("4000"), "claro")
        self.assertEqual(classify_destination("6500"), "tigo")
        self.assertEqual(classify_destination("8000"), "tigo")
        self.assertEqual(classify_destination("6000"), "tigo")

    def test_numbers_outside_ranges_are_international(self) -> None:
        self.assertEqual(classify_destination("3000").startswith("intl_"), True)
        self.assertEqual(classify_destination("9999").startswith("intl_"), True)
        self.assertEqual(classify_destination("123").startswith("intl_"), True)

    def test_international_by_country_code(self) -> None:
        self.assertEqual(classify_destination("+50581234567"), "intl_america")
        self.assertEqual(classify_destination("50588888888"), "intl_america")
        self.assertEqual(classify_destination("+52 55 1234 5678"), "intl_america")
        self.assertEqual(classify_destination("0034 600 123456"), "intl_europa")
        self.assertEqual(classify_destination("+447911123456"), "intl_europa")
        self.assertEqual(classify_destination("+8613800138000"), "intl_resto")
        self.assertEqual(classify_destination("+8816123456789"), "intl_especiales")

    def test_strip_trunk_prefix(self) -> None:
        self.assertEqual(classify_destination("81000", strip_prefix="8"), "local")
        self.assertEqual(classify_destination("05051234567"), "intl_america")

    def test_custom_prefixes_override_defaults(self) -> None:
        custom = {
            "america": ["42"],
            "europa": [],
            "especiales": [],
        }
        self.assertEqual(classify_destination("+421234567", prefixes=custom), "intl_america")
        self.assertEqual(classify_destination("+5051234567", prefixes=custom), "intl_resto")


class TariffForTest(unittest.TestCase):
    def test_same_carrier(self) -> None:
        tigo_to_tigo = tariff_for("6001", "6002")
        self.assertEqual((tigo_to_tigo.rate, tigo_to_tigo.label), (RATE_TIGO, "Tigo a Tigo"))
        claro_to_claro = tariff_for("4001", "4002")
        self.assertEqual((claro_to_claro.rate, claro_to_claro.label), (RATE_CLARO, "Claro a Claro"))

    def test_cross_carrier_symmetric(self) -> None:
        tigo_to_claro = tariff_for("6001", "4001")
        claro_to_tigo = tariff_for("4001", "6001")
        self.assertEqual(tigo_to_claro.rate, RATE_CROSS)
        self.assertEqual(claro_to_tigo.rate, RATE_CROSS)
        self.assertEqual(tigo_to_claro.label, "Tigo a Claro")
        self.assertEqual(claro_to_tigo.label, "Claro a Tigo")

    def test_local_is_free(self) -> None:
        local_to_local = tariff_for("1001", "1500")
        self.assertEqual(local_to_local.rate, 0.0)
        self.assertEqual(local_to_local.label, "Local / misma empresa")

    def test_mobile_to_local_is_free(self) -> None:
        self.assertEqual(tariff_for("6001", "1500").rate, 0.0)
        self.assertEqual(tariff_for("4001", "1500").rate, 0.0)

    def test_local_to_mobile_uses_destination_rate(self) -> None:
        self.assertEqual(tariff_for("1500", "6500").rate, RATE_TIGO)
        self.assertEqual(tariff_for("1500", "4500").rate, RATE_CLARO)

    def test_international_rates(self) -> None:
        self.assertEqual(tariff_for("1001", "+50581234567").rate, RATE_AMERICA)
        self.assertEqual(tariff_for("6001", "+34912345678").rate, RATE_EUROPA)
        self.assertEqual(tariff_for("1001", "+8613800138000").rate, RATE_RESTO)
        self.assertEqual(tariff_for("1001", "+8816123456789").rate, RATE_ESPECIALES)


class ChargeTest(unittest.TestCase):
    def test_fractional_minutes(self) -> None:
        self.assertEqual(charge(RATE_TIGO, 60), 14.65)
        self.assertEqual(charge(RATE_TIGO, 30), 7.33)
        self.assertEqual(charge(RATE_TIGO, 90), 21.98)
        self.assertEqual(charge(RATE_ESPECIALES, 60), 32.96)

    def test_zero_and_negative(self) -> None:
        self.assertEqual(charge(RATE_TIGO, 0), 0.0)
        self.assertEqual(charge(RATE_TIGO, -5), 0.0)
        self.assertEqual(money(0.0), "C$0.00")
        self.assertEqual(money(14.650000000000002), "C$14.65")


class PrefixOverridesPersistenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._appdata = tempfile.mkdtemp(prefix="sipip-tariff-")
        self._previous = os.environ.get("APPDATA")
        os.environ["APPDATA"] = self._appdata

    def tearDown(self) -> None:
        if self._previous is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self._previous

    def test_save_and_load_roundtrip(self) -> None:
        self.assertEqual(load_prefix_overrides(), {})
        save_prefix_overrides({"america": ["505 502", "52"], "europa": ["34"]})
        loaded = load_prefix_overrides()
        self.assertEqual(sorted(loaded["america"]), ["502", "505", "52"])
        self.assertEqual(loaded["europa"], ["34"])
        self.assertNotIn("especiales", loaded)


if __name__ == "__main__":
    unittest.main()