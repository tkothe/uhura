# Setup

## What you need

- Python 3.11+ with [uv](https://docs.astral.sh/uv/), or Docker.
- An **ElevenLabs** account and API key. The free plan was enough for everything in
  [verified.md](verified.md); its 10,000 monthly credits last for about 12 minutes of
  calls (see [operations.md](operations.md#what-it-costs)).
- A **phone number connected to ElevenLabs**, for real calls only. Either a Twilio number
  or a number from another provider connected as a SIP trunk.
- A **public https address** for the service, for steering only. A hosted instance has
  one; on a laptop, use a tunnel such as ngrok.

Rehearsals need the ElevenLabs account and the public address, but no phone number.

## 1. Install and configure

```sh
uv sync
cp .env.example .env
```

Fill in `.env`:

| Setting | Meaning |
|---|---|
| `ELEVENLABS_API_KEY` | Your ElevenLabs key; see "The ElevenLabs API key" below |
| `ELEVENLABS_AGENT_ID` | Printed by `uhura setup-agent` (step 2) |
| `ELEVENLABS_PHONE_NUMBER_ID` | The id of the imported number (step 4) |
| `UHURA_TELEPHONY` | `twilio` (default) or `sip` |
| `UHURA_VOICE_ID` | ElevenLabs voice of the agent; empty keeps the dashboard's choice |
| `UHURA_VOICE_ID_<LANG>` | Optional different voice for one language, e.g. `UHURA_VOICE_ID_EN` |
| `UHURA_LLM` | Language model of the agent, default `gemini-3.5-flash` (why: [verified.md](verified.md#choice-of-language-model)) |
| `UHURA_TOKENS` | Who may use the service: `name:token,name:token` |
| `UHURA_TOOL_SECRET` | Shared secret between the agent's tools and the service |
| `UHURA_DB` | SQLite file, default `data/uhura.db` |
| `UHURA_ALLOWED_COUNTRIES` | Countries that may be called, e.g. `DE,AT` |
| `UHURA_DAILY_CALLS`, `UHURA_MONTHLY_MINUTES`, `UHURA_DAILY_REHEARSALS` | Per-person limits |
| `UHURA_PROGRESS` | `on` (default) or `off`: whether calls report progress unless the brief says otherwise |
| `UHURA_ASK_TIMEOUT` | Seconds the agent waits for an answer (default 100, maximum 280) |
| `UHURA_RETENTION_DAYS` | Calls and transcripts are deleted after this (0 = keep) |
| `UHURA_URL`, `UHURA_TOKEN` | Used by the CLI and the MCP server to reach the service |

Generate the token and the secret with `openssl rand -hex 24`. The service refuses to start
if a token or the tool secret is a placeholder (`change-me`) or shorter than 16
characters, and `setup-tools` refuses such a tool secret; `uhura check` reports it.
`UHURA_TOKEN` must be one of the tokens in `UHURA_TOKENS`. `.env` is ignored by git; never commit or share it.

### The ElevenLabs API key

Create it in the ElevenLabs app under your account menu → **API Keys** → Create. Switch on
**Restrict Key** and allow only:

| Permission (German label in brackets) | Setting | Used for |
|---|---|---|
| ElevenAgents | Write ("Schreiben"); includes reading | Calls, rehearsals, results, agent and tool setup, the tool secret |
| User ("Benutzer") | Access ("Zugriff") | `uhura check` confirms the key works |

Leave everything else on "no access". Optionally set a credit limit per billing period on
the key. A key restricted like this has been verified for setup, checks, rehearsals and
calls; requests outside it are refused with `missing_permissions`.

The service reads the key at startup, so restart `uhura serve` after changing it.

## 2. Create the agent

```sh
uv run uhura setup-agent
```

This creates one agent called "Uhura": every language from `src/uhura/disclosures.py`,
audio saving off, a 10-minute limit, authentication required, and permission to receive
a briefing, opening line and language per call. Put the printed id into
`ELEVENLABS_AGENT_ID`. Running the command again updates that agent, for example after
changing `UHURA_LLM` or adding a language.

Find the agent in the ElevenLabs app by switching to the agents side of the product
("ElevenAgents") and opening **Agents** → Uhura.

**Voice.** A new agent starts with ElevenLabs' default voice, which is not a native German
voice: it gave German names an American "l" and softened "t" to "d". Pick a voice native
to the language you call in. Either put its id into `UHURA_VOICE_ID` (in the ElevenLabs
app: the voice's menu → "Copy voice ID") and run `uhura setup-agent`, or select it on the
agent's Voice tab and leave `UHURA_VOICE_ID` empty. That voice speaks all languages
unless you set a different one for a language with `UHURA_VOICE_ID_<LANG>`, for example
`UHURA_VOICE_ID_EN` so that English calls do not get a German accent. Whether the
per-language voice is used when a call's language is set per call has not been
verified in a spoken call yet.
To compare voices, use ElevenLabs' Text to Speech page with the model "Eleven Flash v2.5"
and the opening line. Cloning a voice needs a paid plan (Starter or higher) and the
consent of the person whose voice it is.

**Names that are mispronounced.** Try a native voice for the language first. If a name is
still wrong, create a pronunciation dictionary in the ElevenLabs app with an alias rule
(the name, and how it should be spoken) and attach it to the agent's voice settings. This
changes only the speech; transcripts keep the real spelling. The restricted API key above
cannot manage dictionaries, so this is done in the dashboard. Not tried yet.

**Testing in the dashboard.** The agent's test button works, but a conversation started
there has no briefing: the agent speaks the opening line, apologises that the call was
placed by mistake and hangs up. That is enough to judge a voice. For a real conversation
without dialling, use `uhura rehearse`.

## 3. Start the service and connect the agent's tools

```sh
uv run uhura serve                 # terminal 1
ngrok http 8787                    # terminal 2, laptop only
uv run uhura setup-tools           # reads the address from the local ngrok tunnel
# or: uv run uhura setup-tools --url https://uhura.example.org
```

`setup-tools` stores the tool secret in ElevenLabs' secret store, creates the tools
`ask_principal` and `final_check`, and attaches them to the agent. Run it again whenever
the public address changes; a free ngrok address changes on every restart.

Only `/agent-tools/*` needs to be reachable from the internet. Those endpoints require
the tool secret; everything else, including the MCP endpoint `/mcp`, requires a user
token.

## 4. Connect a phone number

### Twilio (verified)

1. In Twilio, buy a number with voice capability.
2. Create an API key in the Twilio console (Account → API keys & tokens) and note its SID
   and secret.
3. In the ElevenLabs app: agents side → **Phone Numbers** → Import number → Twilio. Enter a
   label, the number in international format, and the SID and secret. The Twilio
   credentials live in ElevenLabs; Uhura never sees them.
4. Put the number's id into `ELEVENLABS_PHONE_NUMBER_ID`. It is shown in the dashboard and
   starts with `phnum_`. Keep `UHURA_TELEPHONY=twilio`.

Things that can get in the way:

- **Country of the number.** A number from another country works for calling Germany; the
  person called sees a foreign caller ID, and the call is billed as international. Twilio
  may refuse a German number if it cannot verify your identity or address; German numbers
  need proof of a German address at every provider.
- **Trial accounts.** A Twilio trial account can normally call only numbers verified in
  Twilio and plays a trial announcement first.
- **Geo permissions.** Calls to a country must be enabled in Twilio under Voice → Settings
  → Geo permissions. A call that fails immediately is often this.
- **Showing your own number.** ElevenLabs supports Twilio "verified caller IDs" for
  outbound calls. Not set up or tested here.

### Another provider, as a SIP trunk (not verified)

Connect the number in the same Phone Numbers page as a SIP trunk, set
`UHURA_TELEPHONY=sip` and put its id into `ELEVENLABS_PHONE_NUMBER_ID`. ElevenLabs connects
over TCP or TLS and authenticates with a username and password or an IP allowlist; it
names Telnyx, Plivo, Vonage and others as compatible. Open question for German providers
such as sipgate or Placetel: they usually expect the customer's system to register with
them, and it is not known whether they accept ElevenLabs' calls without that.

### Incoming calls

On import, ElevenLabs assigns the number to the agent, so people who call the number reach
it. It has no briefing for them: it speaks the opening line, apologises and hangs up.
Unassign the number in the dashboard if you do not want that.

## 5. Check

```sh
uv run uhura check               # configuration, agent settings, tools, reachability
uv run uhura check --rehearsal   # plus a scripted conversation with the real agent
```

The rehearsal check uses a small amount of ElevenLabs credit. It verifies that the
opening line is used, the briefing is followed, a question reaches you and your answer
reaches the conversation, and a follow-up instruction is delivered.

## Docker

```sh
docker build -t uhura .
docker run --env-file .env -p 8787:8787 -v uhura-data:/data uhura
```

The image runs as a non-root user and keeps its database in `/data`. Setup commands run
from the same image:

```sh
docker run --rm --env-file .env uhura uhura setup-agent
docker run --rm --env-file .env uhura uhura setup-tools --url https://uhura.example.org
```

## Hosting for several people

Run one instance behind https and give each person a line in `UHURA_TOKENS`
(`anna:<token>,ben:<token>`). Limits and call lists apply per name. Each person sets
`UHURA_URL` and their `UHURA_TOKEN` for the CLI, or adds `https://<host>/mcp` with their
token to their MCP client (see [operations.md](operations.md)); they need no ElevenLabs
account and, for MCP, no copy of the repository.

Before doing this for work calls, read [legal-notes.md](legal-notes.md): the operator of
a shared instance processes other people's calls.

Not built yet for shared use: per-person caller IDs, an admin view, token rotation
without a restart (see [roadmap.md](roadmap.md)).

## Adding a language

1. Add the opening line to `DISCLOSURES` in `src/uhura/disclosures.py`. It must say that
   an AI is calling, on whose behalf, that the call is transcribed without audio being
   kept, and ask for agreement. Have a native speaker check it.
   Add the short re-introduction to `REINTRODUCTIONS` and its topic sentence to `TOPICS`
   in the same file; the agent says it word for word to the first person it reaches after
   a phone menu or queue. Have it checked too.
2. Add its English name to `LANGUAGE_NAMES` in `src/uhura/prompt.py`.
3. Run `uhura setup-agent` so the agent gets the language.
