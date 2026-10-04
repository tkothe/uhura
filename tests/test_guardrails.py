import pytest

from uhura.config import Settings
from uhura.guardrails import GuardrailError, check_budget, check_rehearsals, normalise_number

SETTINGS = Settings(allowed_countries=["DE"], monthly_minutes=60, daily_calls=3)


def test_national_number_becomes_e164():
    assert normalise_number("030 23125 000", "DE", SETTINGS) == "+493023125000"


@pytest.mark.parametrize(
    "number, reason",
    [
        ("112", "emergency"),
        ("110", "emergency"),
        ("0900 1234567", "premium-rate"),
        ("+44 20 7946 0958", "country GB"),
        ("hello", "not a"),
        ("030 1", "not a valid"),
    ],
)
def test_refused_numbers(number, reason):
    with pytest.raises(GuardrailError, match=reason):
        normalise_number(number, "DE", SETTINGS)


def test_budget():
    check_budget(2, 59 * 60, SETTINGS)
    with pytest.raises(GuardrailError, match="daily limit"):
        check_budget(3, 0, SETTINGS)
    with pytest.raises(GuardrailError, match="monthly budget"):
        check_budget(0, 60 * 60, SETTINGS)


def test_budget_leaves_room_for_a_whole_call():
    check_budget(0, 50 * 60, SETTINGS, call_secs=600)
    with pytest.raises(GuardrailError, match="9 left, and a call can last up to 10"):
        check_budget(0, 50 * 60 + 1, SETTINGS, call_secs=600)


def test_rehearsal_limit():
    settings = Settings(daily_rehearsals=2)
    check_rehearsals(1, settings)
    with pytest.raises(GuardrailError, match="daily limit of 2 rehearsals"):
        check_rehearsals(2, settings)
