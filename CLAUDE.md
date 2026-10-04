# Uhura

AI phone-call service: FastAPI service in front of ElevenLabs Agents, with a CLI and an
MCP server. Read `README.md`, `docs/operations.md` and `docs/architecture.md` first.

## Rules

- **Never read, print or commit `.env`.** It holds live keys. To see which settings are
  filled in, print names only. `.env.example` is the template.
- **Never place a real call without the user's approval of that specific draft.** Drafts
  and rehearsals are fine; `uhura confirm` and `POST /calls/{id}/confirm` dial.
- Rehearsals and `uhura check --rehearsal` cost ElevenLabs credits. Run them when they
  answer a question, not in loops.
- Guardrails belong in code (`guardrails.py`, `service.py`), not in the prompt. The
  prompt's rules are a second line, and docs must say which is which.
- Keep the repo free of personal data; examples use Erika Musterfrau and the fictional
  number range `030 23125 000`.
- Conventional Commits with a body that says what and why.

## Commands

```sh
uv run pytest            # must pass; tests use no network
uv run ruff check .      # must pass
uv run ruff format .
uv run uhura check       # against the real account, when .env is set up
```

## Conventions

- Tests fake the voice provider (`tests/conftest.py`) and ElevenLabs' HTTP API
  (`httpx.MockTransport`). Anything learned from the real API goes into
  `docs/verified.md` with the date.
- Status lines shown to users come from `phrases.py` (Uhura's voice). Error details stay plain.
- A new language needs a reviewed line in `disclosures.py`; see `docs/setup.md`.
- The ElevenLabs API schema is at `https://api.elevenlabs.io/openapi.json`; check field
  names there instead of guessing.
