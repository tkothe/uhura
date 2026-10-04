# Security policy

Uhura places real phone calls and holds API keys for ElevenLabs, so security problems
matter here even though it is a small project.

## Reporting a vulnerability

Please do not open a public issue. Report it privately through GitHub:
**[Security → Report a vulnerability](https://github.com/tkothe/uhura/security/advisories/new)**.

Helpful to include:

- what an attacker can do (for example: place a call, read someone else's calls, act as
  the voice agent, get around a guardrail),
- what they need for it (network access to the service, a user token, nothing at all),
- steps to reproduce, ideally against the test setup in `tests/conftest.py`, which needs
  no real accounts.

This is a hobby project maintained by one person. You will get an answer, but there is no
guaranteed response time. Fixes go to the `main` branch; there are no other supported
versions.

## In scope

- The service's authentication: user tokens, the tool secret, the MCP endpoint `/mcp`.
- One user seeing or steering another user's calls.
- Getting around a guardrail that is enforced in code: dialling without a confirmed
  draft, dialling a draft twice, emergency or premium-rate numbers, countries that are
  not allowed, the daily and monthly limits, the fixed opening line.
- Secrets or call content leaking into logs, error messages or responses.

## Known and documented

These are known limitations, listed in [docs/roadmap.md](docs/roadmap.md); no need to
report them:

- The name of the person a call is made for (`principal`) is free text inserted into the
  fixed opening line, so a brief can change what that line says.
- Over MCP, approval before dialling relies on the client following the server's
  instructions; Uhura does not check it in code.
- Rules the agent follows through its prompt (no bookings, the re-introduction after a
  phone menu, short turns) are not enforced by code.

## Out of scope

- Vulnerabilities in ElevenLabs, Twilio or other providers: please report those to them.
- Attacks that need access to the machine running Uhura or to its `.env` file.
- Social engineering of the people being called.
