# What has been verified

Unit tests cover the service logic against fakes. This page records what was checked
against the **real ElevenLabs API**, on a free-plan account, on 2026-10-01/02. Anything
not listed as verified here should be treated as untested.

## Verified

| What | How |
|---|---|
| Agent creation and update (`setup-agent`): languages, audio saving off, authentication, per-call overrides, built-in tools | Settings read back from the API after writing |
| Tool setup (`setup-tools`): secret in the secret store, both webhook tools, attached to the agent | Read back; tools then called by ElevenLabs |
| ElevenLabs reaches the service through an ngrok tunnel, with the secret header and the call id | Requests seen in the service log |
| Per-call briefing, opening line and language are applied | Text conversations: the agent opened with the fixed line and pursued the briefed goal |
| `ask_principal`: question reaches the client, answer reaches the conversation | `uhura check --rehearsal` |
| The agent waits a full `UHURA_ASK_TIMEOUT` for an answer | Unanswered question: ElevenLabs held the tool call for 100 s, then the agent said the principal would follow up by email |
| Follow-up instructions arrive through `final_check` and are acted on | `uhura check --rehearsal` |
| The agent says goodbye and hangs up by itself (`end_call`), ending the session | Interactive `uhura rehearse` |
| Docker image builds, runs as non-root, serves requests | Local build and smoke test |

| Real phone calls through a Twilio number (US number calling German mobiles, 2026-10-02): dialled, fixed opening line spoken in German, briefing followed | Three calls of 17 to 22 seconds; the person called hung up after the agent's first question |
| Result polling for a phone call: status `done`, transcript, duration | Stored by the service as returned by ElevenLabs |
| A per-call voicemail message is left on a mailbox, introduction first | One call: greeting at 4 s, message started at 8 s and ran to the end (24 s); whether its first words fell before the beep could not be checked |
| Voicemail is recognised: without a voicemail text the agent hangs up without leaving a message | Three calls that reached a mailbox greeting ended after 7 to 11 seconds |
| A call driven entirely through the MCP tools (`draft_call`, `confirm_call`, `wait_for_event`, `get_call`) | Two calls placed from Claude Code |
| Claude Code connects to the service's `/mcp` endpoint by URL with a bearer header, lists and calls the tools; a 25 s `wait_for_event` returns normally (2026-10-02) | Local service with a test token and database, `claude -p` with an HTTP MCP config; drafts only, no call placed over this route yet |
| A restricted API key (ElevenAgents write, User access) is enough for setup, checks, rehearsals and calls | All of them run with such a key; other endpoints answer `missing_permissions` |
| Conversations not started by Uhura (dashboard test) start once the agent has the call-id placeholder | Setting read back from the API; before the fix ElevenLabs refused them |
| No audio is kept for a phone call | ElevenLabs reports `has_audio: false` for that conversation |
| When the credits run out mid-call, ElevenLabs cuts the call off and reports it `failed`, with `metadata.error` as an object (`code` 1002, `reason` "This request exceeds your quota limit.") (2026-10-03) | A real call ended after 467 s; read back from the conversation API |
| A German mobile number verified in Twilio ("Verified Caller IDs") and imported into ElevenLabs as outbound-only works as the caller ID: a call to a German mobile number got through and showed that number (2026-10-04) | Real test call; the person called confirmed the displayed number. Calls to landlines with this caller ID not tested yet |
| The agent presses a key in a phone menu, does not agree to recording, and stays silent on hold with `skip_turn` (2026-10-03) | Real call through a travel agency's menu and a 16-minute queue: pressed 1, no word in the queue, 56 s of agent speech in 17.5 minutes |
| No live transcript during a phone call: about 9 minutes into a running call, the conversation API returned status `in-progress`, an empty transcript and a duration of 0 (2026-10-03). Live insight would need ElevenLabs' Enterprise-only monitoring | Polled five times, 10 s apart |
| Each finished conversation reports `metadata.cost` (credits) and `charging` with the billed voice minutes after the silence discount and dollar prices for platform and language model; on the free tier the language model was not deducted as credits (2026-10-03) | Read for two real calls: 467 s billed as 7.53 min, and 1049 s with a silent queue billed as 6.95 min |
| The agent accepts `play_keypad_touch_tone` (`suppress_turn_after_dtmf`), `skip_turn` (`wait_timeout_secs: -1`) and a maximum call length of 1200 s (2026-10-03) | Read back from the API after `setup-agent`; `uhura check` passes |

Apart from these phone calls, all conversations above were **text-only rehearsals**. They exercise the same agent,
briefing and tools as a phone call, but no speech.

## Not verified

| What | Why it matters |
|---|---|
| Placing a call through a SIP trunk (`UHURA_TELEPHONY=sip`) | Only the Twilio path has been used |
| Steering during a phone call: `ask_principal` and `final_check` with a person on the line | No real call has lasted long enough to need either |
| How the agent behaves in speech: interruptions, silence, phone menus | Two 22-second calls are not enough to judge |
| Progress reports (`report_progress`, asynchronous) during a phone call, added 2026-10-03 | Only tried in a text rehearsal so far |
| Language switching mid-call (`language_detection`) | Not exercised |
| English calls | Only German conversations were run |
| How long ElevenLabs keeps the transcript of a phone call | Its retention setting is still the default (unlimited) |
| Behaviour when a second question arrives while one is pending | Not exercised |

## Choice of language model

The agent's rules only work if the model follows them. With ElevenLabs' default model
(`qwen35-397b-a17b`) the agent skipped `final_check` in two of two runs and offered the
person it had called "further help".

Models were then compared on three situations where the agent should ask the principal
instead of answering: a customer number it does not have, a time it does not know, and an
offer to book an appointment. One run per cell, same briefing, final prompt. "Did not ask" means the agent answered, deflected or said it did not know:

| Model | Customer number | Time | Booking offer | Price per minute |
|---|---|---|---|---|
| `gemini-3.5-flash` | asked | asked | asked | $0.0205 |
| `gpt-5.4-mini` | asked | asked | did not ask | $0.0102 |
| `claude-sonnet-5` | did not ask | asked | asked | $0.0294 |
| `claude-haiku-4-5` | asked | did not ask | did not ask | $0.0134 |
| `gpt-4.1` | did not ask | asked | did not ask | $0.0261 |
| `gpt-4.1-mini` | did not ask | did not ask | did not ask | $0.0052 |

`gemini-3.5-flash` is the default (`UHURA_LLM`). It then passed all six checks of
`uhura check --rehearsal`. Prices are ElevenLabs' own estimate for a 2,500-character
prompt and come on top of the per-minute platform fee.

This is a small sample: one run per cell, in German, in text. It shows that the choice of
model matters and which one did best here, not that any model is reliable. Re-run
`uhura check --rehearsal` after changing the model or the rules in `prompt.py`.

## Re-verifying

```sh
uv run uhura check --rehearsal
```
