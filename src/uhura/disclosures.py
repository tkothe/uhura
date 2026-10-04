"""The fixed opening line per language.

The agent always opens with one of these; a brief cannot override it. To support
a new language, add its line here (reviewed by someone who speaks it).
"""

from __future__ import annotations

DISCLOSURES: dict[str, str] = {
    "de": (
        "Guten Tag, hier spricht ein KI-Assistent im Auftrag von {principal}. "
        "Dieses Gespräch wird verschriftlicht, eine Tonaufnahme wird nicht gespeichert. "
        "Ist das für Sie in Ordnung?"
    ),
    "en": (
        "Hello, this is an AI assistant calling on behalf of {principal}. "
        "This call is being transcribed; no audio recording is kept. "
        "Is that okay with you?"
    ),
}


# Said word for word to the first person reached after a phone menu or a waiting queue,
# who has not heard the opening line. Short on purpose: a person who has just picked up
# hears who is calling, for whom, what about, and is asked for agreement in one go.
REINTRODUCTIONS: dict[str, str] = {
    "de": (
        "Guten Tag, ich bin ein KI-Assistent und rufe im Auftrag von {principal} an.{topic} "
        "Das Gespräch wird nur als Text mitgeschrieben, ohne Tonaufnahme. Ist das in Ordnung?"
    ),
    "en": (
        "Hello, I am an AI assistant calling on behalf of {principal}.{topic} "
        "The call is transcribed as text only, no audio is kept. Is that okay?"
    ),
}
# The brief's topic, if any, completes this sentence.
TOPICS: dict[str, str] = {"de": " Es geht um {topic}.", "en": " It is about {topic}."}

# Spoken before a voicemail message, so a recorded message also says who is speaking.
VOICEMAIL_INTROS: dict[str, str] = {
    "de": "Hallo, hier spricht ein KI-Assistent im Auftrag von {principal}.",
    "en": "Hello, this is an AI assistant calling on behalf of {principal}.",
}


class UnsupportedLanguage(ValueError):
    pass


def voicemail_message(language: str, principal: str, text: str | None) -> str:
    """The message the agent leaves on a mailbox: fixed introduction plus the brief's text.

    Empty when the brief has no voicemail text; the agent then hangs up without a message.
    """
    if not text or not text.strip():
        return ""
    intro = VOICEMAIL_INTROS.get(language.lower())
    if intro is None:
        raise UnsupportedLanguage(f"no voicemail introduction for language '{language}'")
    return f"{intro.format(principal=principal)} {text.strip()}"


def reintroduction(language: str, principal: str, topic: str | None = None) -> str:
    """The line the agent says to the first person it reaches after a menu or queue."""
    lang = language.lower()
    if lang not in REINTRODUCTIONS:
        raise UnsupportedLanguage(f"no re-introduction for language '{language}'")
    about = TOPICS[lang].format(topic=topic.strip()) if topic and topic.strip() else ""
    return REINTRODUCTIONS[lang].format(principal=principal, topic=about)


def disclosure(language: str, principal: str) -> str:
    try:
        return DISCLOSURES[language.lower()].format(principal=principal)
    except KeyError:
        raise UnsupportedLanguage(
            f"no disclosure line for language '{language}'; supported: {', '.join(sorted(DISCLOSURES))}"
        ) from None
