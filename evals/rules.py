"""Checks code can decide on a finished conversation. The judge (run_evals.py) is for what these can't decide.

Each rule reads the events the agent emitted, the same stream the trace file holds: the employee's messages, tool
calls with their results, and the replies. Everything a reply states as a fact must come from somewhere the agent
was given, so a version, a clock time, a device or a reference that appears in no tool result and nowhere in what
the employee said was made up. A finding names the rule, the turn and the sentence. Hard rules fail the run; soft
ones are only counted.
"""

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

# What a reply says it did, and the action that must have run for it to be true (patterns match the past tense
# only, so "I can unlock it" or "I haven't pushed it" don't count).
ACTION_CLAIMS = {
    "unlock_account": r"\bI(?:'ve| have) unlocked\b|\byour account is (?:now )?unlocked\b|已(?:经)?(?:为您|为你|帮你)?解锁",
    "send_password_reset_link": r"\bI(?:'ve| have) sent (?:you )?(?:a |the )?(?:password )?reset link",
    "grant_access": r"\bI(?:'ve| have) (?:granted|given you)\b|已(?:经)?(?:为您|为你|帮你)?开通",
    "push_software_update": r"\bI(?:'ve| have) pushed\b|\bhas been pushed\b",
    "request_access_approval": r"\bI(?:'ve| have) (?:filed|submitted) (?:an |the |your )?(?:approval )?request",
    "add_user_to_incident": r"\bI(?:'ve| have) added you to\b",
    "escalate_to_human": r"\bI(?:'ve| have) (?:created|opened|raised) (?:a |the )?ticket",
    "add_ticket_note": r"\bI(?:'ve| have) (?:added|noted) (?:that|this|it|these)\b[^.]*\bticket",
}
FOLLOW_UP = re.compile(r"\bI(?:'ll| will) (?:check back|follow up|remind|keep you (?:posted|updated)|notify you|"
                       r"let you know when|send you an? (?:update|reminder))", re.I)
REPEAT = re.compile(r"\b(?:won't|will not|don't|do not) (?:have|need) to (?:repeat|explain|tell)[^.]*\bagain\b|"
                    r"\bwithout (?:having to )?(?:repeat|explain)ing\b", re.I)
# A version: three-part numbers anywhere, or a number right after a software name or "version". Plain decimals are
# left alone: "about 0.1 GB over" is arithmetic on the record, not a made-up fact.
_APPS = yaml.safe_load((Path(__file__).parent.parent / "data" / "policy.yaml").read_text())["approved_software"]
VERSION = re.compile(r"\b\d+\.\d+\.\d+\b|(?:version|%s)\s+v?(\d+(?:\.\d+)+)" % "|".join(map(re.escape, _APPS)), re.I)
CLOCK = re.compile(r"\b(\d{1,2}):(\d{2}) ?([AaPp])\.? ?[Mm]\b")
ASSET = re.compile(r"\b(?:LT|PH)-\d{4}\b")
REFERENCE = re.compile(r"\b(?:IT|INC|REQ|TKT)-\d{4,5}\b")
KB_REF = re.compile(r"\bKB-\d{3}\b")
CJK = re.compile(r"[一-鿿]")
SENTENCE = re.compile(r"(?<=[.!?。！？])\s+|\n+")
HARD = {"action_claimed_not_done", "number_not_in_evidence", "unknown_device", "unknown_reference",
        "unread_article_cited", "follow_up_promise", "repeat_promise", "wrong_language"}


@dataclass
class Finding:
    rule: str
    hard: bool
    turn: int
    quote: str


def _cjk_share(text: str) -> float:
    letters = [ch for ch in text if ch.isalpha()]
    return sum(bool(CJK.match(ch)) for ch in letters) / len(letters) if letters else 0.0


def check(events: list[dict], context: str = "") -> list[Finding]:
    """`context` is what the agent was told outside tool results (the session note: who, where, the time now)."""
    findings: list[Finding] = []
    turn, evidence, said, done, read, words = 0, context, "", set(), set(), ""

    def flag(rule: str, quote: str) -> None:
        findings.append(Finding(rule, rule in HARD, turn, quote.strip()[:200]))

    for e in events:
        kind = e.get("type")
        if kind == "user_message":
            turn += 1
            said += "\n" + e["text"]
            words = e["text"] if any(ch.isalpha() for ch in e["text"]) else words  # a code like "049154" has no language
        elif kind == "tool_call":
            result = e.get("result") or {}
            evidence += "\n" + json.dumps(result, ensure_ascii=False, default=str)
            failed = e.get("is_error") or "error" in result
            if e["name"] == "perform_action" and str(result.get("text", "")).startswith("Done."):
                done.add(e["input"].get("action"))
            elif e["name"] in ("escalate_to_human", "add_ticket_note") and not failed:
                done |= {e["name"], "add_ticket_note"}  # what the ticket was created with is on the ticket too
            elif e["name"] == "get_kb_article" and not failed:
                read.add(result.get("article_id"))
        elif kind == "assistant_message":
            reply, known = e["text"], evidence + said
            for sentence in filter(None, SENTENCE.split(reply)):
                for action, pattern in ACTION_CLAIMS.items():
                    if action not in done and re.search(pattern, sentence, re.I):
                        flag("action_claimed_not_done", sentence)
                if any((m.group(1) or m.group(0)) not in known for m in VERSION.finditer(sentence)):
                    flag("number_not_in_evidence", sentence)
                for h, m, ap in CLOCK.findall(sentence):
                    employee_said = re.search(rf"\b{int(h)}(?::{m})?\s?{ap}\.?m\b", said, re.I)
                    if f"{int(h)}:{m} {ap.upper()}M" not in known and not employee_said:
                        flag("number_not_in_evidence", sentence)
                if any(a not in known for a in ASSET.findall(sentence)):
                    flag("unknown_device", sentence)
                if any(r not in known for r in REFERENCE.findall(sentence)):
                    flag("unknown_reference", sentence)
                if any(k not in read for k in KB_REF.findall(sentence)):
                    flag("unread_article_cited", sentence)
                if FOLLOW_UP.search(sentence):
                    flag("follow_up_promise", sentence)
                if REPEAT.search(sentence):
                    flag("repeat_promise", sentence)
                if "UTC" in sentence:
                    flag("told_utc", sentence)
            if reply.count("?") + reply.count("？") > 1:
                flag("several_questions", reply.splitlines()[-1] if reply else "")
            if words and (_cjk_share(words) >= 0.3) != (_cjk_share(reply) >= 0.3):  # Chinese keeps English product names
                flag("wrong_language", reply[:120])
    return findings


def as_dicts(findings: list[Finding]) -> list[dict]:
    return [asdict(f) for f in findings]
