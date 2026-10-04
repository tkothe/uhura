"""Checks that run in the service before any call is dialled. Not enforceable by prompt."""

from __future__ import annotations

import phonenumbers
from phonenumbers import PhoneNumberType

from .config import Settings

BLOCKED_TYPES = {
    PhoneNumberType.PREMIUM_RATE: "premium-rate number",
    PhoneNumberType.SHARED_COST: "shared-cost number",
    PhoneNumberType.PAGER: "pager number",
    PhoneNumberType.UAN: "service number",
}


class GuardrailError(ValueError):
    """The call is refused; the message says why."""


def normalise_number(raw: str, region: str, settings: Settings) -> str:
    """Return the E.164 form of `raw`, or raise GuardrailError."""
    if phonenumbers.is_emergency_number(raw, region):
        raise GuardrailError("emergency numbers are never called")
    try:
        number = phonenumbers.parse(raw, region)
    except phonenumbers.NumberParseException:
        raise GuardrailError("not a phone number") from None
    if not phonenumbers.is_valid_number(number):
        raise GuardrailError("not a valid phone number")
    country = phonenumbers.region_code_for_number(number)
    if country not in settings.allowed_countries:
        raise GuardrailError(f"country {country} is not allowed (allowed: {', '.join(settings.allowed_countries)})")
    blocked = BLOCKED_TYPES.get(phonenumbers.number_type(number))
    if blocked:
        raise GuardrailError(f"{blocked}s are never called")
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def check_budget(calls_today: int, seconds_this_month: int, settings: Settings, call_secs: int = 0) -> None:
    """Refuse a new call unless the limits leave room for it, at its longest (`call_secs`)."""
    if calls_today >= settings.daily_calls:
        raise GuardrailError(f"daily limit of {settings.daily_calls} calls reached")
    limit = settings.monthly_minutes * 60
    if seconds_this_month >= limit or seconds_this_month + call_secs > limit:
        left = max(limit - seconds_this_month, 0) // 60
        raise GuardrailError(
            f"monthly budget of {settings.monthly_minutes} minutes used up: {left} left,"
            f" and a call can last up to {call_secs // 60}"
        )


def check_rehearsals(rehearsals_today: int, settings: Settings) -> None:
    if rehearsals_today >= settings.daily_rehearsals:
        raise GuardrailError(f"daily limit of {settings.daily_rehearsals} rehearsals reached")
