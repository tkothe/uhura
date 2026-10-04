# Roadmap: possible improvements

Ideas collected while building and using Uhura, none of them built yet. Each entry says
what it is, why it matters, and what is still open. "Next" marks what would help most
right now; the rest is unordered within its section.

Before building one, check [verified.md](verified.md) and the code: other work may have
covered part of it since this list was written (October 2026).

## Setup and daily operation

**Next: `uhura up`, one command to start a session.** Starts the service and the ngrok
tunnel, points the agent's tools at the new address, runs `uhura check`, and stops both
together on Ctrl-C. Replaces the three manual steps in [operations.md](operations.md),
which are easy to get wrong (forgetting `setup-tools` silently disables steering). It
should also write the log to a fixed file (e.g. `data/uhura.log`, already ignored by git)
instead of wherever the starting shell points it, and stop the service with a short
graceful-shutdown timeout: a restart currently waits indefinitely for an MCP client's
open connection and had to be forced several times.

**Next: `uhura init`, a setup assistant.** Writes `.env` (generating token and tool
secret), asks for the ElevenLabs key, creates the agent, asks for the Twilio number and
API key and imports the number into ElevenLabs through the API (`POST
/v1/convai/phone-numbers`) instead of the dashboard, optionally enables the allowed
countries in Twilio's geo permissions, and ends with `uhura check`. Goal: a colleague
creates two accounts, runs one command, and is done. Open: whether the number import
works with the restricted key (probably, it is ElevenAgents write); whether geo
permissions can be changed with a restricted Twilio key.

**A fixed public address.** A free ngrok address changes on every start, which is why
`setup-tools` has to run each time. Options: ngrok's one free static domain, a Cloudflare
named tunnel on your own domain, or hosting (below). With a fixed address, `setup-tools`
runs once.

**Infrastructure as code: deliberately not Terraform.** Uhura's own `setup-agent` and
`setup-tools` already build the ElevenLabs side from code and can be re-run safely;
Terraform or the ElevenLabs CLI's "agents as code" would describe the same agent a second
time. For one phone number, Terraform adds risk (`destroy` releases the number) more than
it saves. Reconsider for a hosted instance with many numbers.

**Hosting.** A small always-on instance (Docker on a server) with https. Needed for
incoming calls and for colleagues; removes the tunnel. Missing for shared use: an admin
view, adding or revoking tokens without a restart, per-person budgets in credits rather
than minutes, limits for the instance as a whole (not only per person), per-person caller
IDs.

**Before others use an instance: two open audit findings.** From the security review of
2026-10-03, deliberately left for later (weak tokens, the third, is fixed): `principal` is
free text inserted into the fixed opening line, so a brief can undermine the disclosure
(`topic` is already limited to one short phrase; `principal` needs the same); and over
MCP, approval before dialling rests only on the client following the server's
instructions, so `confirm_call` must never be on an auto-allow list, or Uhura needs a
check in code such as a confirmation code shown only in the draft.

## Telephony

**Next: a German caller ID.** Calls currently come from a US number, which people may
not pick up and which is billed as international. Candidate: sipgate's "trunking 2"
plan, listed at 0 €/month and open to private customers, connected as a SIP trunk
(`UHURA_TELEPHONY=sip`, untested). Open: whether sipgate (or Placetel) accepts
ElevenLabs' calls without SIP registration.

**Your own number as caller ID.** ElevenLabs supports Twilio "verified caller IDs" for
outbound calls. Callers would see your mobile number, and call-backs reach you. Open: how
that combines with the AI disclosure; not tried.

**Incoming call screening.** Forward calls from numbers not in your contacts to Uhura:
on iOS, "silence unknown callers" plus conditional call forwarding (`**004*<number>#`) to
the Uhura number. Needs: a standing "screening" briefing for calls Uhura did not place
(today such calls get an apology and a hang-up), a summary sent to you after each call,
optionally your contacts so known people can be told you will call back. Open: whether
silenced calls forward immediately or after the ring time; carrier costs (Telekom and
Vodafone bill forwarding like an outgoing call); work phones need the employer's
agreement; legal notes for answering on someone's behalf.

**Ending a call from Uhura.** There is no way to hang up a running call from the service
or MCP. With Twilio, the call SID returned at dial time would allow ending it through
Twilio's API.

## Steering during a call

**Next: verify steering in a spoken call.** `ask_principal` and `final_check` are proven
in text rehearsals only. One real call where the other side asks something the agent
cannot know would close that gap.

**Getting questions to you faster.** The agent waits about 100 seconds for an answer.
Through Claude Code that only works if you happen to be watching. A push notification
(phone or desktop) when a question arrives, with a reply action, would make the window
realistic. Alternatively MCP elicitation, so the question pops up in the client for the
human directly, where clients support it.

**Instructions that arrive immediately, and a live transcript.** Today instructions wait
for the agent's next tool call. Pushing them at once needs either ElevenLabs' Enterprise
monitoring or routing the call audio through Uhura (Twilio media streams to the agent's
WebSocket). A live transcript is not available otherwise: checked on a real call, the
conversation API returns nothing until the call ends. The agent's progress reports
(`report_progress`) now cover "what is happening"; routing audio is a large change and
only worth it with a real need.

**Make progress reports cheaper.** The agent still sometimes sends the same report
several times in a row; Uhura stores it once, but each repeat is a language-model step.
Verified so far: the menu report on a real call, all five moments in rehearsals.

**Fewer polling turns for MCP clients.** `wait_for_event` waits 25 seconds by default;
the service allows 55. A longer default roughly halves the turns a client like Claude
Code spends waiting during a call.

**Call back with the answer.** When a question times out, the agent promises a follow-up
by email. A "call back" option could place a second call with the answer once you reply.

## Agent quality

**Next: make the `topic` hard to forget.** Without it, the re-introduction after a menu
does not say what the call is about; on a real call the first answer was "Kommt drauf
an, um was es geht." The draft could warn when it is missing, and the MCP server's
instructions could ask for it every time.

**Shorter turns.** The rules ask for one or two sentences and no repetition. That helped,
but the agent still repeats details from the goal in its questions and sometimes asks
several things at once. Concrete examples of good and bad turns in the rules are the next
thing to try, measured with the same rehearsal script.

**A leaked thought.** Once in a rehearsal, the language model (`gemini-3.5-flash`) put a
fragment `<thought` into the agent's text. In a phone call it could be spoken. Not seen
on real calls yet; worth watching in transcripts and, if it recurs, a reason to compare
models.

**Review the English re-introduction.** `REINTRODUCTIONS["en"]` in `disclosures.py` was
written without a native speaker's check, which the project requires before use.

**Pronunciation of names.** A native German voice fixed most of it. If names are still
wrong, an ElevenLabs pronunciation dictionary with alias rules, attached to the agent,
changes only the speech. Could be set up by `setup-agent` from a list in `.env`; needs the
pronunciation-dictionary permission on the key.

**Verify per-language voices and language switching.** `UHURA_VOICE_ID_EN` is stored on
the agent but no English call has been made; switching language mid-call
(`language_detection`) is untested.

**A repeatable model evaluation.** The model comparison in [verified.md](verified.md) was
one run per case. An `uhura eval` command running a fixed set of rehearsal scenarios
several times per model would make choosing and re-checking the model (or prompt changes)
measurable. Costs credits per run.

**Voicemail timing.** The message starts a few seconds after the greeting; whether its
first words land before the beep is unknown. A short pause or beep detection if it turns
out to be cut off.

## Data and privacy

**Next: shorten ElevenLabs' transcript retention.** Uhura deletes its own copies after
`UHURA_RETENTION_DAYS`, but ElevenLabs keeps transcripts indefinitely by default.
`setup-agent` could set the agent's retention to match, or Uhura could delete each
conversation at ElevenLabs once it has stored the transcript (check what that does to
ElevenLabs' cost and analysis data first).

**Rehearsal costs.** Calls store what ElevenLabs charged (`cost`); rehearsals use credits
too but are not recorded. The text session would need its conversation id kept so the
cost can be fetched afterwards.

**Results to where you need them.** A hook when a call ends (webhook or command) so an
assistant can file the result: a calendar entry, a note, an email to you. Keeps Uhura
generic while letting a personal setup act on outcomes.
