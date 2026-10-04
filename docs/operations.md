# Operations

Day-to-day use once [setup](setup.md) is done.

## Starting a session (laptop)

Three things must be running before a call or rehearsal:

```sh
uv run uhura serve          # 1. the service, on http://127.0.0.1:8787
ngrok http 8787             # 2. the tunnel that lets ElevenLabs reach the service
uv run uhura setup-tools    # 3. tell the agent's tools the tunnel's current address
uv run uhura check          # everything should say ok
```

Step 3 is needed after every ngrok restart, because a free ngrok address changes each
time. Skipping it does not stop calls, but the agent can then neither ask you questions
nor receive follow-up instructions, and `uhura check` reports "tools can reach this
service" as failed.

When you are done, stop ngrok. The tunnel makes the service reachable from the internet;
its endpoints are protected by the tool secret and the user tokens, but there is no
reason to leave it open.

A hosted instance has a fixed address: run `setup-tools --url …` once and skip the tunnel.

## Making a call

```sh
uv run uhura draft --to "030 23125 000" --principal "Erika Musterfrau" \
  --goal "Fragen, ob der Laden am Freitag geöffnet hat." \
  --fact "Erika Musterfrau möchte am Freitag vorbeikommen."
uv run uhura rehearse <id>     # optional: try it in text first
uv run uhura confirm <id>      # dials and follows the call
```

Writing a good briefing:

- **Goal:** what to find out, in the language of the call. List several questions in the
  order they should be asked.
- **Facts:** everything the agent may say about you. It is told not to share or invent
  anything else, and to ask you when it lacks an answer.
- **Tone:** the opening line is fixed and formal. For a friend, add a fact such as "Die
  angerufene Person heißt Anna und ist eine Freundin. Duze sie."
- **Limits:** use `--must-not` for anything specific to this call.

While the call runs, `confirm` shows the agent's questions and prompts you for answers.
You have `UHURA_ASK_TIMEOUT` seconds (default 100); after that the agent tells the other
person you will follow up by email. `uhura say <id> "…"` from a second terminal queues a
follow-up instruction.

## What to expect from a call

- **Voicemail.** The agent recognises a mailbox greeting. Without `--voicemail` it hangs
  up without a message; with `--voicemail "…"` it leaves that text, preceded by the fixed
  introduction "Hallo, hier spricht ein KI-Assistent im Auftrag von …", then hangs up. The
  draft shows the full message. The call shows as `done` with the greeting and the
  message in the transcript.
- **The other person hangs up.** The call ends as `done` with the transcript so far.
- **The result arrives after the call.** There is no live transcript of a phone call; the
  transcript appears a few seconds after it ends. Rehearsals do show the conversation live.
- **No audio is kept**, at Uhura or at ElevenLabs. ElevenLabs lists each conversation in
  its dashboard with the transcript.

## Using Uhura from Claude Code (MCP)

The service serves its MCP tools itself at `/mcp`. Register it once per project, with a
token from `UHURA_TOKENS`:

```sh
claude mcp add --transport http uhura http://localhost:8787/mcp \
  --header "Authorization: Bearer <token>"
```

For a hosted instance, use its address instead (`https://uhura.example.org/mcp`). This
needs neither a checkout of the repository nor uv on the machine.

This stores a copy of the token in Claude Code's private configuration for that project
(`~/.claude.json`), not in any repository. In a running session, type `/mcp` to connect;
new sessions pick it up automatically. Remove it with `claude mcp remove uhura -s local`.
If the token in `.env` changes, remove and add the server again.

MCP clients that can only start a local program can use the stdio adapter instead. It
forwards to the service over HTTP and offers the same tools:

```sh
claude mcp add uhura -e UHURA_URL=http://localhost:8787 -e UHURA_TOKEN=<token> \
  -- uv run --directory /path/to/uhura uhura-mcp
```

The tools only work while the service is running. Claude then has the tools
`draft_call`, `confirm_call`, `wait_for_event`, `answer`, `send_instruction`, `get_call`,
`list_calls`, `rehearse_call`, `rehearse_say` and `end_rehearsal`. The server instructs
the client to show each draft and to dial only after approval, and to answer the agent's
questions only from what the user has said.

A limit to know: the agent waits about 100 seconds for an answer. If Claude has to ask
you, your reply must arrive within that time.

## What it costs

Measured on the free plan in October 2026; treat the numbers as rough.

| Item | Observed |
|---|---|
| A 22-second phone call | 287 ElevenLabs credits, about 780 per minute |
| The free plan's 10,000 credits | about 12 minutes of calls per month |
| A text rehearsal of a few turns | roughly 30 to 150 credits |
| `uhura check --rehearsal` | roughly 100 to 150 credits |
| Language model (`gemini-3.5-flash`) | $0.02 per minute by ElevenLabs' estimate, billed from the same credits |
| Phone line | Billed separately by the phone provider |

`uhura show <id>` and `uhura list` show what each finished call cost in credits, as
reported by ElevenLabs. Check the remaining credits in the ElevenLabs app before a longer call. Uhura's own
limits (`UHURA_DAILY_CALLS`, `UHURA_MONTHLY_MINUTES`, `UHURA_DAILY_REHEARSALS`) count calls,
minutes and rehearsals, not credits.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `cannot reach the Uhura service` | The service is not running, or `UHURA_URL` is wrong. Start `uhura serve`. |
| `unknown token` | `UHURA_TOKEN` is not one of the tokens in `UHURA_TOKENS`. |
| `check`: "tools can reach this service" fails | The tunnel is down or its address changed. Start ngrok and run `uhura setup-tools`. |
| `no ngrok tunnel found on this machine` | ngrok is not running; start it, or pass `--url`. |
| `ElevenLabs refused … (401)` with `missing_permissions` | The API key lacks a permission; see the key section in [setup.md](setup.md). |
| `no phone number configured` | `ELEVENLABS_PHONE_NUMBER_ID` is empty. |
| The call fails immediately | Twilio geo permissions for the destination country, a trial account calling an unverified number, or no credit at the phone provider. The ElevenLabs dashboard shows the reason on the conversation. |
| ElevenLabs shows "Missing required dynamic variables in tools: uhura_call_id" | The agent was set up before the placeholder existed. Run `uhura setup-agent`. |
| The agent answers instead of asking you, or skips its final check | The language model is not following the rules. Use the default model, and re-run `uhura check --rehearsal` after changing the model or `prompt.py`. |
| The agent never asks and never receives instructions | Its tools point at an old address; run `uhura setup-tools`. |
| `daily limit … reached` or `monthly budget … used up` | Uhura's own limits; raise them in `.env` and restart the service. A call needs 20 minutes of budget left, and running calls count as 20 minutes until they end. |
| `no disclosure line for language …` | That language has no opening line yet; see "Adding a language" in [setup.md](setup.md). |
| A call stays `in_progress` | The service was stopped during the call. Start it again; it resumes tracking and gives up after `max_call_seconds`. |
| An editor swap file such as `.env.swp` appears | It can contain the keys. It is ignored by git; delete it when the editor is closed. |
