"""Ask (报告内追问) request/response shapes — mirrors §18.5.1.

`cite` and `deep` share the citation shape and the same validation; only how the
answer body is produced differs. That is deliberate: "引用合法性只需实现一次".
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AskMode = Literal["cite", "deep"]
AskStatus = Literal["queued", "running", "cancelling", "done", "failed", "cancelled"]
Confidence = Literal["high", "medium", "low"]


class AskRequest(BaseModel):
    #: Only a hard ceiling against absurd payloads — the *product* limit is
    #: `ASK_MAX_QUESTION_CHARS`, enforced by `ask_service.normalize_question()` after
    #: NFKC normalization. Declaring 500 here would let pydantic answer first with the
    #: generic `validation_error` and make the setting silently ineffective.
    question: str = Field(min_length=1, max_length=4000)
    mode: AskMode = "cite"


class Citation(BaseModel):
    sourceId: str
    title: str | None = None
    publisher: str | None = None


class AskHit(BaseModel):
    """One retrieved block, verbatim (§18.5.1).

    `blockId` follows the single derivation rule `f"{sectionId}#{index}"` from
    `services/block_text.block_ids` (V4-P1-2) — the front end maps it to a scroll
    target, it is never invented locally.
    """

    blockId: str
    sectionId: str
    sectionTitle: str = ""
    blockType: str
    score: float
    excerpt: str
    citations: list[str] = Field(default_factory=list)


class AskAnswer(BaseModel):
    #: `deep` only — the model's own ≤1200-character answer.
    answerText: str | None = None
    #: `cite` only — the verbatim blocks that matched.
    hits: list[AskHit] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    #: Set when there is nothing to ground on (provisional report, empty sources).
    hint: str | None = None


class AskOut(BaseModel):
    id: str
    reportId: str
    question: str
    mode: AskMode
    status: AskStatus
    grounded: bool = False
    confidence: float | None = None
    citationsDropped: int = 0
    answer: AskAnswer = Field(default_factory=AskAnswer)
    costUsd: float | None = None
    costKnown: bool = False
    tokensIn: int | None = None
    tokensOut: int | None = None
    latencyMs: int | None = None
    errorCode: str | None = None
    errorMessage: str | None = None
    createdAt: str
    finishedAt: str | None = None


class AskListResponse(BaseModel):
    items: list[AskOut]
    total: int


class AskQueuedResponse(BaseModel):
    """`deep` → 202. `streamUrl` is what the client subscribes to."""

    id: str
    askId: str
    status: AskStatus
    streamUrl: str
