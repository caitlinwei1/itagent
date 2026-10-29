"""The case frame (what the agent knows about this conversation) and the escalation handoff schema.

The model proposes updates through tools; code owns the fields that must be trustworthy:
identity status, evidence, actions taken and the final outcome.
"""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, computed_field

Symptom = Literal["login_failed", "mfa", "slow", "disconnects", "missing_access", "job_failure",
                  "error_message", "security", "how_to", "other", "unknown"]
RequestType = Literal["incident", "service_request", "question", "security", "out_of_scope", "unknown"]
Outcome = Literal["open", "resolved", "linked_incident", "pending_approval", "answered", "refused", "escalated"]


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Evidence(BaseModel):
    id: str
    tool: str
    args: dict
    summary: str
    record: dict | None = None  # the raw records behind a text result (audit, handoff); the model sees the text


class Claim(BaseModel):
    text: str
    status: Literal["unverified", "confirmed", "contradicted"] = "unverified"
    evidence_refs: list[str] = []


class Hypothesis(BaseModel):
    id: str
    cause: str
    status: Literal["open", "confirmed", "ruled_out"] = "open"
    evidence_refs: list[str] = []


class ActionRecord(BaseModel):
    action: str
    target: str | None
    decision: str
    executed: bool
    detail: str
    turn: int = 0  # the employee message (1-based) this action followed
    at: str = Field(default_factory=utcnow)


class CaseFrame(BaseModel):
    case_id: str
    user_id: str
    identity_verified: bool = False  # only code sets this (confirm_identity_code)
    request_type: RequestType = "unknown"
    systems: list[str] = []
    symptom: Symptom = "unknown"
    user_goal: str | None = None
    deadline: str | None = None
    urgency: Literal["low", "normal", "high", "critical"] = "normal"
    claims: list[Claim] = []
    hypotheses: list[Hypothesis] = []
    evidence: list[Evidence] = []
    actions: list[ActionRecord] = []
    kb_articles_read: list[str] = []
    next_step: str | None = None
    status: Outcome = "open"
    outcome_summary: str | None = None
    ticket_id: str | None = None
    employee_messages: list[str] = []  # verbatim, one per turn (index 0 is turn 1)

    @computed_field
    @property
    def turns(self) -> int:
        return len(self.employee_messages)

    def evidence_ids(self) -> set[str]:
        return {e.id for e in self.evidence}


def _norm(text: str) -> str:
    return " ".join(text.lower().replace("\u2019", "'").split())


def _resolved(case: "CaseFrame", basis: dict) -> str | None:
    """The employee's own words saying it works, from a message after the last executed fix (or after the opening
    message when nothing was changed). Whether the words mean "it works" is the model's judgement."""
    words = _norm(basis.get("employee_words") or "")
    last_fix = max((a.turn for a in case.actions if a.executed and a.action in basis.get("fix_actions", ())), default=1)
    later = [_norm(m) for m in case.employee_messages[last_fix:]]
    if not words or not any(words in m for m in later):
        return ("'resolved' needs the employee's own words saying it works, quoted from a message they sent after "
                "the last change. If they haven't said so yet, ask them to test it now.")
    return None


def _done(action: str, message: str):
    return lambda case, basis: None if any(a.action == action and a.executed for a in case.actions) else message


OUTCOME_GUARDS = {  # each outcome: what must be true before a case may end with it (None = nothing to check)
    "resolved": _resolved,
    "linked_incident": _done("add_user_to_incident", "Add the employee to the incident first (perform_action "
                                                     "add_user_to_incident)."),
    "pending_approval": _done("request_access_approval", "No approval request was filed."),
    "escalated": lambda case, basis: None if case.ticket_id else "No ticket was created.",
    "answered": None,
    "refused": None,
}


def frozen(case: "CaseFrame") -> str | None:
    """An escalated case stays escalated: no other ending and no second ticket."""
    return f"Case already escalated as {case.ticket_id}. Do not create a duplicate." if case.status == "escalated" else None


def transition(case: "CaseFrame", to: str, basis: dict | None = None) -> str | None:
    """The only place a case's status changes. Returns why not, or None when the case moved to `to`."""
    if problem := frozen(case):
        return problem
    guard = OUTCOME_GUARDS[to]
    problem = guard(case, basis or {}) if guard else None
    if problem is None:
        case.status = to
    return problem


class EvidenceItem(BaseModel):
    evidence_id: str = Field(description="An evidence id from a tool result, e.g. E4")
    finding: str = Field(description="What that result showed, in one sentence")


class HandoffInput(BaseModel):
    """What the model writes for the specialist. Code adds identity, case facts, actions and the trace id."""

    summary: str = Field(description="Two or three sentences: the problem, its impact, and where the investigation stands.")
    impact: Literal["high", "medium", "low"] = Field(
        description="ITIL impact. high: many people, a whole office or a security exposure; medium: a team or "
                    "one business process; low: one person.")
    urgency: Literal["high", "medium", "low"] = Field(
        description="ITIL urgency. high: work is blocked and time-critical (deadline within hours) or damage grows "
                    "fast; medium: blocked or degraded without an imminent deadline; low: can wait.")
    assignment_group: str = Field(description="The team that must act. One of the teams in the policy.")
    escalation_reason: Literal["policy_requires_human", "security_incident", "fix_failed", "root_cause_unknown",
                               "conflicting_evidence", "tool_failure", "identity_unverified",
                               "user_requested_human", "outside_agent_scope"]
    user_impact: str = Field(description="What the employee cannot do and any deadline.")
    steps_already_taken: list[str] = Field(description="What the employee and the agent already did, so nobody repeats it.")
    evidence: list[EvidenceItem] = Field(description="Key findings, each tied to an evidence id.")
    causes_ruled_out: list[str] = []
    causes_still_open: list[str] = []
    question_for_specialist: str = Field(description="The exact question or decision the specialist needs to handle.")
    recommended_next_action: str
    workaround_given: str | None = None
    similar_tickets: list[str] = Field(default=[], description="Past ticket ids that look similar (hints only).")


class Handoff(HandoffInput):
    ticket_id: str
    priority: str  # computed by code from impact x urgency (see priority())
    employee_words: str  # the employee's first message, verbatim (KCS: capture the requester's context)
    created_at: str
    employee: dict
    identity_verified: bool
    systems: list[str]
    symptom: str
    request_type: str
    deadline: str | None
    claims: list[Claim]
    hypotheses: list[Hypothesis]
    actions_taken: list[ActionRecord]
    kb_articles_used: list[str]
    trace_id: str


# ITIL impact x urgency matrix. Code computes priority so the model cannot inflate or deflate it.
_MATRIX = {("high", "high"): "P1", ("high", "medium"): "P2", ("high", "low"): "P3",
           ("medium", "high"): "P2", ("medium", "medium"): "P3", ("medium", "low"): "P4",
           ("low", "high"): "P3", ("low", "medium"): "P4", ("low", "low"): "P4"}

RESPONSE_TARGETS = {"P1": "15 minutes", "P2": "1 hour", "P3": "4 business hours", "P4": "2 business days"}


def priority(impact: str, urgency: str, security: bool = False) -> str:
    return "P1" if security else _MATRIX[(impact, urgency)]
