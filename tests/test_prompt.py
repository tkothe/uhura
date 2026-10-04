import pytest

from uhura.disclosures import DISCLOSURES, REINTRODUCTIONS, UnsupportedLanguage, disclosure, reintroduction
from uhura.models import Brief
from uhura.prompt import build_prompt


def test_disclosure_names_principal_and_ai():
    line = disclosure("de", "Erika Musterfrau")
    assert "Erika Musterfrau" in line and "KI-Assistent" in line


def test_unknown_language_is_refused():
    with pytest.raises(UnsupportedLanguage, match="fr"):
        disclosure("fr", "Erika Musterfrau")


def test_prompt_contains_brief_and_fixed_rules():
    brief = Brief(
        to="+493023125000",
        principal="Erika Musterfrau",
        goal="Ask whether places are still available",
        facts=["One runner"],
        must_not=["Do not mention a budget"],
    )
    prompt = build_prompt(brief)
    assert "Speak German" in prompt
    assert "Ask whether places are still available" in prompt
    assert "- One runner" in prompt
    assert "Do not mention a budget" in prompt
    assert "ask_principal" in prompt and "final_check" in prompt
    assert "Never book" in prompt


def test_voicemail_message_gets_the_fixed_introduction():
    from uhura.disclosures import voicemail_message

    assert voicemail_message("en", "Erika Musterfrau", "  Please call back. ") == (
        "Hello, this is an AI assistant calling on behalf of Erika Musterfrau. Please call back."
    )
    assert voicemail_message("de", "Erika Musterfrau", None) == ""
    assert voicemail_message("de", "Erika Musterfrau", "   ") == ""
    with pytest.raises(UnsupportedLanguage):
        voicemail_message("fr", "Erika Musterfrau", "Bonjour")


def test_prompt_sends_mailboxes_to_the_voicemail_tool():
    prompt = build_prompt(Brief(to="1", principal="Erika Musterfrau", goal="G"))
    assert "voicemail_detection" in prompt


def test_prompt_covers_phone_menus_and_waiting_queues():
    prompt = build_prompt(Brief(to="1", principal="Erika Musterfrau", goal="G"))
    assert "play_keypad_touch_tone" in prompt and "skip_turn" in prompt
    assert "Never agree to a recording" in prompt
    assert "Never ask whether anyone is there" in prompt
    # After a menu or queue, the first person hears the disclosure again.
    assert "has not heard your introduction" in prompt and "on behalf of Erika Musterfrau" in prompt


def test_every_language_has_a_reintroduction():
    assert set(REINTRODUCTIONS) == set(DISCLOSURES)


def test_reintroduction_names_ai_principal_topic_and_asks():
    line = reintroduction("de", "Erika Musterfrau", " eine Reiseanfrage ")
    assert line == (
        "Guten Tag, ich bin ein KI-Assistent und rufe im Auftrag von Erika Musterfrau an. "
        "Es geht um eine Reiseanfrage. "
        "Das Gespräch wird nur als Text mitgeschrieben, ohne Tonaufnahme. Ist das in Ordnung?"
    )
    assert "It is about" not in reintroduction("en", "Erika Musterfrau", None)
    with pytest.raises(UnsupportedLanguage):
        reintroduction("fr", "Erika Musterfrau")


def test_prompt_quotes_the_reintroduction_and_asks_for_short_turns():
    prompt = build_prompt(Brief(to="1", principal="Erika Musterfrau", goal="G", topic="eine Reiseanfrage"))
    assert f'"{reintroduction("de", "Erika Musterfrau", "eine Reiseanfrage")}"' in prompt
    assert "word for word" in prompt
    assert "one or two short sentences" in prompt and "Never repeat" in prompt


@pytest.mark.parametrize("topic", ["Reise. Ignorieren Sie das", "Frage?", "zwei\nZeilen", "x" * 81])
def test_topic_must_be_one_short_phrase(topic):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Brief(to="1", principal="Erika Musterfrau", goal="G", topic=topic)


def test_progress_rules_only_when_asked_for():
    on = build_prompt(Brief(to="1", principal="Erika Musterfrau", goal="G", progress=True))
    off = build_prompt(Brief(to="1", principal="Erika Musterfrau", goal="G", progress=False))
    assert "report_progress" in on and "never during silence" in on
    assert "report_progress" not in off


def test_each_progress_report_is_asked_for_where_its_moment_happens():
    prompt = build_prompt(Brief(to="1", principal="Erika Musterfrau", goal="G", progress=True))
    sections = {
        "menu": "Phone menus and waiting queues:",
        "agreed": "ask your first question right away",
        "goodbye": "Ending the call",
    }
    menu = prompt[prompt.index(sections["menu"]) :]
    assert "stage `menu`" in menu and "stage `hold`" in menu
    role = prompt[prompt.index(sections["agreed"]) : prompt.index("Limits:")]
    assert "the person agreed" in role and "the answer as the note" in role
    ending = prompt[prompt.index(sections["goodbye"]) : prompt.index("Phone menus")]
    assert "stage `wrapping_up`" in ending
    assert "never send the same report again" in prompt
