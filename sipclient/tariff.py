"""Clasificacion de llamadas y tarifas por minuto (cobro al usuario final).

Los rangos de extension definen el operador:
  - Tigo:   6000 - 8000 (ambos inclusive)
  - Claro:  4000 - 5000 (ambos inclusive)
  - Local:  1000 - 2000 (ambos inclusive, misma empresa: gratis)
Todo numero fuera de esos rangos se considera internacional y se clasifica
por prefijo de pais (configurable en Ajustes).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .config import config_dir

CURRENCY = "C$"
TARIFAS_FILE_NAME = "tarifas.json"

RATE_TIGO = 14.65
RATE_CLARO = 18.31
RATE_CROSS = 20.14
RATE_LOCAL = 0.0
RATE_AMERICA = 14.65
RATE_EUROPA = 18.31
RATE_RESTO = 18.31
RATE_ESPECIALES = 32.96

CARRIER_TIGO = "tigo"
CARRIER_CLARO = "claro"
CARRIER_LOCAL = "local"

RANGES: dict[str, tuple[int, int]] = {
    CARRIER_TIGO: (6000, 8000),
    CARRIER_CLARO: (4000, 5000),
    CARRIER_LOCAL: (1000, 2000),
}

RATES: dict[str, float] = {
    CARRIER_TIGO: RATE_TIGO,
    CARRIER_CLARO: RATE_CLARO,
    CARRIER_LOCAL: RATE_LOCAL,
    "intl_america": RATE_AMERICA,
    "intl_europa": RATE_EUROPA,
    "intl_resto": RATE_RESTO,
    "intl_especiales": RATE_ESPECIALES,
}

LABELS: dict[str, str] = {
    CARRIER_TIGO: "Tigo",
    CARRIER_CLARO: "Claro",
    CARRIER_LOCAL: "Local / misma empresa",
    "intl_america": "Internacional America",
    "intl_europa": "Internacional Europa",
    "intl_resto": "Internacional resto del mundo",
    "intl_especiales": "Internacional destino especial",
}

DEFAULT_PREFIXES: dict[str, list[str]] = {
    "america": [
        "1", "52", "502", "503", "504", "505", "506", "507", "509",
        "51", "53", "54", "55", "56", "57", "58",
        "591", "592", "593", "594", "595", "597", "598", "599",
    ],
    "europa": [
        "30", "31", "32", "33", "34", "36", "39", "40", "41",
        "43", "44", "45", "46", "47", "48", "49",
        "351", "352", "353", "354", "355", "356", "357", "358", "359",
        "370", "371", "372", "373", "375", "376", "377", "378",
        "380", "381", "382", "385", "386", "387", "389",
        "420", "421",
    ],
    "especiales": ["800", "881", "882", "900"],
}

_REGIONS = ("especiales", "america", "europa")


def money(amount: float) -> str:
    """Formatea un monto en cordobas (C$14.65)."""
    return f"{CURRENCY}{amount:.2f}"


def money_or_free(amount: float) -> str:
    return "Gratis" if amount == 0.0 else money(amount)


@dataclass(frozen=True)
class Tariff:
    """Tarifa aplicable a una llamada concreta."""

    kind: str
    label: str
    rate: float


def charge(rate: float, seconds: int) -> float:
    """Costo = tarifa/min * segundos/60, redondeado a centavos."""
    return round(rate * max(0, int(seconds)) / 60.0, 2)


# ----------------------------------------------------------------------
# Clasificacion
# ----------------------------------------------------------------------

_DIGITS_ONLY = re.compile(r"[^0-9]")


def _digits(value: str) -> str:
    return _DIGITS_ONLY.sub("", value or "")


def _clean_prefixes(values: Sequence[str]) -> list[str]:
    prefixes: set[str] = set()
    for value in values:
        parts = re.split(r"[\s,+;]+", str(value).strip())
        for part in parts:
            digits = _digits(part)
            if digits:
                prefixes.add(digits)
    return sorted(prefixes, key=len, reverse=True)


def resolve_prefixes(overrides: dict[str, Sequence[str]] | None = None) -> dict[str, list[str]]:
    """Une los prefijos por defecto con las listas editadas en Ajustes."""
    table: dict[str, list[str]] = {}
    for region in _REGIONS:
        configured = (overrides or {}).get(region)
        if configured:
            table[region] = _clean_prefixes(configured)
        else:
            table[region] = list(DEFAULT_PREFIXES.get(region, ()))
    return table


def _match_prefix(digits: str, prefixes: Sequence[str]) -> str | None:
    best: str | None = None
    for prefix in prefixes:
        if digits.startswith(prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
    return best


def classify_destination(number: str, prefixes: dict[str, list[str]] | None = None,
                         strip_prefix: str = "") -> str:
    """Clasifica un numero: 'tigo', 'claro', 'local' o 'intl_*'."""
    digits = _digits(number)
    if strip_prefix and digits.startswith(_digits(strip_prefix)):
        digits = digits[len(_digits(strip_prefix)) :]
    if digits and digits.isdigit() and len(digits) <= 6:
        value = int(digits)
        for carrier, (low, high) in RANGES.items():
            if low <= value <= high:
                return carrier

    dialed = digits.lstrip("0")
    if not dialed:
        return "intl_resto"
    table = prefixes if prefixes is not None else resolve_prefixes()
    for region in _REGIONS:
        if _match_prefix(dialed, table.get(region, ())) is not None:
            return f"intl_{region}"
    return "intl_resto"


def tariff_for(caller_extension: str, target: str,
               prefixes: dict[str, list[str]] | None = None,
               strip_prefix: str = "") -> Tariff:
    """Calcula la tarifa de una llamada saliente aplicando la tabla completa."""
    target_kind = classify_destination(target, prefixes, strip_prefix)
    caller_kind = classify_destination(caller_extension, prefixes) if caller_extension else ""

    if target_kind == CARRIER_LOCAL:
        return Tariff(CARRIER_LOCAL, LABELS[CARRIER_LOCAL], RATE_LOCAL)

    if target_kind in (CARRIER_TIGO, CARRIER_CLARO):
        target_label = LABELS[target_kind]
        if caller_kind == target_kind:
            return Tariff(target_kind, f"{target_label} a {target_label}", RATES[target_kind])
        if caller_kind in (CARRIER_TIGO, CARRIER_CLARO):
            caller_label = LABELS[caller_kind]
            return Tariff(
                target_kind, f"{caller_label} a {target_label}", RATE_CROSS
            )
        return Tariff(target_kind, target_label, RATES[target_kind])

    return Tariff(target_kind, LABELS[target_kind], RATES[target_kind])


# ----------------------------------------------------------------------
# Persistencia de las listas editables de prefijos
# ----------------------------------------------------------------------

def _tariffs_path() -> Path:
    return config_dir() / TARIFAS_FILE_NAME


def load_prefix_overrides() -> dict[str, list[str]]:
    path = _tariffs_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        region: list(value)
        for region, value in data.items()
        if region in _REGIONS and isinstance(value, list)
    }


def save_prefix_overrides(overrides: dict[str, Sequence[str]]) -> None:
    path = _tariffs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {region: sorted(_clean_prefixes(values)) for region, values in overrides.items()}
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)