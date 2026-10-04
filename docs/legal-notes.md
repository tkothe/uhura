# Legal notes (Germany)

These notes record the reasoning behind Uhura's defaults. They are **not legal advice**
and no lawyer has reviewed them. If you use Uhura regularly or for work, have the
transcription question checked.

## What Uhura does and why

**It announces itself.** Every call opens with a fixed line saying that an AI assistant
is calling, on whose behalf, and that the call is transcribed, and asks whether that is
okay. Reasons: the EU AI Act requires telling people when they are talking to an AI;
consent removes the "unauthorised" element discussed below; and it is fair to the person
answering.

**It keeps a transcript, not audio.** Recording someone's non-public spoken words without
authorisation is a criminal offence in Germany (§ 201 StGB). A written transcript is not
an audio recording. Uhura switches audio saving off on the ElevenLabs agent and
`uhura check` verifies that setting.

**The open point.** For transcription, the audio is streamed to ElevenLabs and processed
there. Whether that transient processing counts as "recording" under § 201 is, as far as
we know, not settled. The opening line's request for agreement is the mitigation: if the
person agrees and continues, the processing is not unauthorised.

**If the person objects**, the agent is instructed to apologise and end the call. This is
a rule the language model follows, not a technical block.

**Information only.** The agent is instructed not to book, buy, cancel or agree to
anything, so a call cannot create an obligation for the person it is made for.

## Phone menus and waiting queues

Many companies answer with a phone menu and a queue before a person picks up. The fixed
opening line is then spoken to the menu, because the agent cannot know beforehand who
answers.

**The menu itself needs no consent.** A pre-recorded announcement played to every caller
is not a person's non-public spoken word under § 201 StGB, it normally contains no
personal data, and the AI Act's disclosure duty concerns people, not machines. A machine
cannot agree to anything anyway.

**The person after the menu does.** They have not heard the opening line. The agent's
rules tell it to say, before anything else and word for word, a fixed re-introduction
(`REINTRODUCTIONS` in `disclosures.py`): that it is an AI assistant calling on whose
behalf, what the call is about, that the call is transcribed as text only without audio,
and whether that is okay. If the person objects, it ends the call as usual. Their first words (typically a
greeting with their name) are transcribed before they are asked; that is the same open
point as above.

**The difference from a normal call.** At the start of a call the disclosure is fixed in
code and a brief cannot change it. After a menu or queue the wording is fixed in code too,
and the brief's `topic` is limited to one short phrase so it cannot add sentences, but
that the agent says it depends on a prompt rule: the language model is told to, nothing
forces it to. Check transcripts of calls
that went through a menu.

**Recording by the company.** Menus often ask the caller to agree to the company
recording the call. The agent is instructed never to agree, since Uhura tells the person
it calls for that no audio is kept.

## Data protection

- A transcript contains personal data of whoever answers.
- A private person making personal calls is probably covered by the GDPR household
  exemption. The providers involved are not.
- Someone who runs Uhura for other people processes those people's calls and the other
  parties' data. For work use that needs the employer's agreement, a data-processing
  agreement with ElevenLabs, and a look at where data is stored. EU data residency at
  ElevenLabs has not been confirmed for non-enterprise plans.
- The conversation text is also processed by the provider of the language model selected
  in `UHURA_LLM`.
- Uhura deletes its own copies after `UHURA_RETENTION_DAYS`. ElevenLabs' copy follows
  ElevenLabs' retention setting.

## Not covered here

Other countries, calls to consumers for marketing (which Uhura is not meant for), and
recording laws that require all-party consent in other jurisdictions.
