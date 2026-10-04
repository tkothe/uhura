"""Request and response shapes shared by the service, the CLI and the MCP server."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Brief(BaseModel):
    """Everything the voice agent is told about one call."""

    to: str = Field(description="Number to call, E.164 or national format")
    language: str = Field(default="de", description="ISO 639-1 code the agent speaks")
    principal: str = Field(description="Person the agent calls on behalf of")
    topic: str | None = Field(
        default=None,
        max_length=80,
        # It goes into a fixed, reviewed line; one phrase, not sentences of its own.
        pattern=r"^[^.!?\n]*$",
        description="What the call is about, completing 'Es geht um …' / 'It is about …', e.g. 'eine Reiseanfrage'",
    )
    goal: str = Field(description="What the call should find out or achieve")
    facts: list[str] = Field(default_factory=list, description="Facts the agent may share")
    must_not: list[str] = Field(default_factory=list, description="Extra limits for this call")
    region: str = Field(default="DE", description="Country used to parse national numbers")
    progress: bool | None = Field(
        default=None,
        description="Whether the agent reports progress during the call; default: the service's UHURA_PROGRESS",
    )
    voicemail: str | None = Field(
        default=None,
        description="Message to leave if a mailbox answers; without it the agent hangs up on voicemail",
    )


# The stages the agent reports with `report_progress`.
STAGES = ("menu", "hold", "talking", "wrapping_up")


class Answer(BaseModel):
    question_id: int
    text: str


class Instruction(BaseModel):
    text: str


class Utterance(BaseModel):
    text: str
