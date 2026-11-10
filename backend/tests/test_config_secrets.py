"""Secrets (OPENAI_API_KEY, JWT_SECRET, KAKAO_CLIENT_SECRET, BUSINESS_NUMBER_KEY, ORIGIN_SECRET) never show up
in a settings error, repr, model_dump or log (PM 10/12 — a key leaked into a startup error)."""

import pytest
from pydantic import ValidationError

from app.config import Settings

KEY = "sk-test-DO-NOT-PRINT-1234567890"


def test_settings_error_does_not_print_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A startup error (here: production without JWT_SECRET) must not echo the env values it was given."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    with pytest.raises(ValidationError) as info:
        Settings(_env_file=None, app_env="production")
    message = str(info.value)
    assert "JWT_SECRET" in message  # the reason is still shown
    assert KEY not in message and "DO-NOT-PRINT" not in message


def test_key_is_masked_in_repr_and_readable_when_used() -> None:
    settings = Settings(_env_file=None, openai_api_key=KEY)
    assert KEY not in repr(settings) and KEY not in str(settings.openai_api_key)
    assert str(settings.openai_api_key) == "**********"
    assert settings.openai_api_key.get_secret_value() == KEY


@pytest.mark.parametrize("value", [None, ""])
def test_missing_or_empty_key_counts_as_not_configured(value: str | None) -> None:
    from app.services.stt import STTError, transcribe

    with pytest.raises(STTError, match="not configured"):
        transcribe(b"audio", "rec.webm", "audio/webm", settings=Settings(_env_file=None, openai_api_key=value))


SECRETS = {
    "jwt_secret": "jwt-DO-NOT-PRINT-" + "j" * 32,
    "kakao_client_secret": "kakao-DO-NOT-PRINT-secret",
    "business_number_key": "QWw5Y3JJb3l3aHk0ZHRrN0tVaW1sTktxOWZpVnB0Z2g=",  # a valid Fernet key
    "origin_secret": "origin-DO-NOT-PRINT-" + "o" * 32,
}


def test_other_secrets_are_masked_in_repr_and_dump() -> None:
    settings = Settings(_env_file=None, app_env="production", **SECRETS)
    shown = " ".join([repr(settings), str(settings), str(settings.model_dump()), settings.model_dump_json()])
    for name, value in SECRETS.items():
        assert value not in shown, name
        assert getattr(settings, name).get_secret_value() == value


def test_short_jwt_secret_error_does_not_print_it() -> None:
    short = "short-DO-NOT-PRINT"
    with pytest.raises(ValidationError) as info:
        Settings(_env_file=None, app_env="production", jwt_secret=short,
                 business_number_key=SECRETS["business_number_key"])
    message = str(info.value)
    assert "JWT_SECRET" in message and short not in message


def test_bad_business_number_key_error_does_not_print_it() -> None:
    bad = "not-a-fernet-DO-NOT-PRINT"
    with pytest.raises(ValidationError) as info:
        Settings(_env_file=None, app_env="development", business_number_key=bad)
    message = str(info.value)
    assert "BUSINESS_NUMBER_KEY" in message and bad not in message
