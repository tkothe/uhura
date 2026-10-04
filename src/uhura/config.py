"""Settings, read once from the environment (and a local .env if present)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader: KEY=VALUE lines, existing environment wins."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


# Anyone who can reach the service with a user token can place calls, and the tool secret
# lets requests pose as the voice agent. Both end up behind a public tunnel or https.
MIN_SECRET_LENGTH = 16
PLACEHOLDERS = ("change-me", "changeme")


class WeakSecretError(RuntimeError):
    pass


def weak_secrets(settings: Settings) -> list[str]:
    """Which credentials are placeholders or too short to put on a network. Names only, never values."""
    weak = [f"the token for '{name}'" for token, name in settings.tokens.items() if _weak(token)]
    if settings.tool_secret and _weak(settings.tool_secret):
        weak.append("the tool secret")
    return weak


def _weak(value: str) -> bool:
    return len(value) < MIN_SECRET_LENGTH or any(p in value.lower() for p in PLACEHOLDERS)


def check_secrets(settings: Settings) -> None:
    """Raise WeakSecretError if a token or the tool secret must not be used."""
    weak = weak_secrets(settings)
    if weak:
        raise WeakSecretError(
            f"{' and '.join(weak)} {'is' if len(weak) == 1 else 'are'} a placeholder or shorter than "
            f"{MIN_SECRET_LENGTH} characters; generate new ones with `openssl rand -hex 24`"
        )


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


@dataclass
class Settings:
    elevenlabs_api_key: str = ""
    elevenlabs_agent_id: str = ""
    elevenlabs_phone_number_id: str = ""
    voice_id: str = ""  # ElevenLabs voice of the agent; empty leaves the agent's voice alone
    language_voices: dict[str, str] = field(default_factory=dict)  # language code -> voice id
    llm: str = "gemini-3.5-flash"  # language model the agent uses; see docs/verified.md for why
    tokens: dict[str, str] = field(default_factory=dict)  # token -> user name
    tool_secret: str = ""
    db_path: str = "data/uhura.db"
    allowed_countries: list[str] = field(default_factory=lambda: ["DE"])
    monthly_minutes: int = 120
    daily_calls: int = 10
    daily_rehearsals: int = 20
    ask_timeout: float = 100.0
    poll_interval: float = 5.0
    telephony: str = "twilio"  # how the ElevenLabs number is connected: "twilio" or "sip"
    max_call_seconds: int = 1500  # stop tracking a call that runs longer than this (the agent stops at 1200)
    max_rehearsal_seconds: int = 600  # close a rehearsal that is still open after this
    progress: bool = True  # whether calls report progress unless the brief says otherwise
    retention_days: int = 30  # finished calls and transcripts are deleted after this; 0 keeps them

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        env = os.environ.get
        tokens = {}
        for pair in _csv(env("UHURA_TOKENS", "")):
            name, _, token = pair.partition(":")
            if name and token:
                tokens[token] = name
        return cls(
            elevenlabs_api_key=env("ELEVENLABS_API_KEY", ""),
            elevenlabs_agent_id=env("ELEVENLABS_AGENT_ID", ""),
            elevenlabs_phone_number_id=env("ELEVENLABS_PHONE_NUMBER_ID", ""),
            voice_id=env("UHURA_VOICE_ID", ""),
            language_voices={
                key.removeprefix("UHURA_VOICE_ID_").lower(): value
                for key, value in os.environ.items()
                if key.startswith("UHURA_VOICE_ID_") and value
            },
            llm=env("UHURA_LLM", "gemini-3.5-flash"),
            tokens=tokens,
            tool_secret=env("UHURA_TOOL_SECRET", ""),
            db_path=env("UHURA_DB", "data/uhura.db"),
            allowed_countries=[c.upper() for c in _csv(env("UHURA_ALLOWED_COUNTRIES", "DE"))],
            monthly_minutes=int(env("UHURA_MONTHLY_MINUTES", "120")),
            daily_calls=int(env("UHURA_DAILY_CALLS", "10")),
            daily_rehearsals=int(env("UHURA_DAILY_REHEARSALS", "20")),
            ask_timeout=float(env("UHURA_ASK_TIMEOUT", "100")),
            telephony=env("UHURA_TELEPHONY", "twilio").lower(),
            retention_days=int(env("UHURA_RETENTION_DAYS", "30")),
            progress=env("UHURA_PROGRESS", "on").lower() not in ("off", "false", "0", "no"),
        )
