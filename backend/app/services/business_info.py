"""Business registration number — checksum, Fernet encryption, masking (SPEC §5.4).

The plain number never leaves this module except into the Mock issuer payload:
responses show the masked form only, and nothing here logs it.
"""

import hashlib
import hmac
import re

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings, get_settings

_WEIGHTS = (1, 3, 7, 1, 3, 7, 1, 3, 5)


def normalize(raw: str) -> str:
    """Sanitize before validation: drop hyphens/spaces ("123-45-67890" → "1234567890")."""
    return re.sub(r"[\s-]", "", raw or "")


def is_valid(number: str) -> bool:
    """10 digits and the NTS check digit."""
    if not re.fullmatch(r"\d{10}", number):
        return False
    digits = [int(c) for c in number]
    total = sum(d * w for d, w in zip(digits, _WEIGHTS))
    total += (digits[8] * 5) // 10
    return (10 - total % 10) % 10 == digits[9]


def mask(number: str) -> str:
    """123-45-***** — enough for the owner to recognise it."""
    return f"{number[:3]}-{number[3:5]}-*****"


def _fernet(settings: Settings | None) -> Fernet:
    return Fernet((settings or get_settings()).business_number_key.get_secret_value().encode())


def encrypt(number: str, settings: Settings | None = None) -> str:
    return _fernet(settings).encrypt(number.encode()).decode()


def fingerprint(number: str, settings: Settings | None = None) -> str:
    """Keyed hash for idempotency comparison — a plain hash of 10 digits is brute-forceable."""
    key = (settings or get_settings()).business_number_key.get_secret_value().encode()
    return hmac.new(key, number.encode(), hashlib.sha256).hexdigest()


def decrypt(token: str, settings: Settings | None = None) -> str:
    """Raises InvalidToken if the key changed — callers treat that as a server error (500)."""
    return _fernet(settings).decrypt(token.encode()).decode()


__all__ = ["InvalidToken", "decrypt", "encrypt", "fingerprint", "is_valid", "mask", "normalize"]
