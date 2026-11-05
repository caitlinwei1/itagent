"""The agent loop: one `HelpdeskAgent` per conversation, `respond()` once per employee message.

Per message: the employee's text and the current case record go into the conversation, then Claude is called
in a loop. Each round it may call tools (executed by code, writes gated by policy) or reply. Before a reply is
shown, code checks it (cited KB articles were actually read, ticket numbers exist), then a second model looks for
claims the evidence doesn't support (checker.py). Either sends it back for a fix, at most twice a turn; code checks
every rewrite too, since a fix can bring in a new citation. Budgets cap model and tool calls per message so a confused run cannot spin.
"""

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

import anthropic

from . import checker
from .case import CaseFrame
from .config import Settings, call_cost
from .policy import PolicyEngine
from .prompts import GUARD_NOTE, SYSTEM_PROMPT, case_block, session_block
from .tools import TOOL_SPECS, TOOLS, ToolContext, execute, parse_faults, step_label, step_outcome
from .trace import Tracer
from .world import World

KB_REF = re.compile(r"\bKB-\d{3}\b")
TICKET_REF = re.compile(r"\b(?:IT|INC|REQ|TKT)-\d{4,5}\b")
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class TurnResult:
    reply: str
    notices: list[dict]
    case: dict
    usage: dict
    events: list[dict] = field(default_factory=list)


class HelpdeskAgent:
    def __init__(self, world: World, user_id: str, settings: Settings | None = None,
                 client: anthropic.Anthropic | None = None, on_event: Callable[[dict], None] | None = None):
        self.settings = settings or Settings()
        self.client = client or anthropic.Anthropic()
        self.world = world.fork()
        if user_id not in self.world.users:
            raise KeyError(f"unknown user {user_id}")
        self.user = self.world.users[user_id]
        case_id = f"case-{uuid.uuid4().hex[:8]}"
        self.case = CaseFrame(case_id=case_id, user_id=user_id)
        self.tracer = Tracer(case_id)
        self.ctx = ToolContext(world=self.world, policy=PolicyEngine(self.world.policy, self.world.users, self.world.incidents),
                               case=self.case, user=self.user, trace_id=case_id, now=self.settings.now,
                               faults=parse_faults(self.settings.faults))
        self.messages: list[dict] = []
        self.on_event = on_event or (lambda e: None)
        self.usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 0, "model_calls": 0, "tool_calls": 0, "cost_usd": 0.0,
                      "checker_calls": 0, "checker_flagged": 0, "checker_errors": 0, "checker_skipped": 0,
                      "checker_cost_usd": 0.0}
        self._cost = 0.0
        manager = self.world.users.get(self.user.get("manager_id") or "", {}).get("name")
        self._session_note = session_block(self.user, self.settings.now, manager)

    # ---- public -------------------------------------------------------------------------------------

    def respond(self, text: str) -> TurnResult:
        started = time.time()
        events: list[dict] = []

        def emit(event: dict) -> None:
            if not event["type"].endswith("_delta"):  # token deltas go to the live UI only, not the trace file
                events.append(event)
                self.tracer.log(event)
            self.on_event(event)

        self.case.employee_messages.append(text)
        self.ctx.notices = []
        emit({"type": "user_message", "text": text})

        history_len = len(self.messages)
        self.messages.append({"role": "user", "content": text})
        context = self._case_context()
        if self.case.turns == 1:
            context = self._session_note + "\n\n" + context
        self.messages.append({"role": "system", "content": context})

        try:
            reply = self._run_loop(emit)
        except Exception as exc:
            # Drop the failed turn so a retry starts from a valid history (user, system, assistant...).
            # Left in place, the dangling system message makes every later request a 400.
            del self.messages[history_len:]
            self.tracer.log({"type": "error", "message": f"{type(exc).__name__}: {exc}"})  # the caller reports it
            raise
        if reply is None:
            reply = ("Sorry, I couldn't finish checking this. I've kept everything I found; please try again, "
                     "or ask me to hand this to a specialist.")
        self.case.agent_replies.append(reply)
        emit({"type": "assistant_message", "text": reply, "latency_s": round(time.time() - started, 1)})
        return TurnResult(reply=reply, notices=list(self.ctx.notices), case=self.case.model_dump(),
                          usage=dict(self.usage), events=events)

    # ---- loop ---------------------------------------------------------------------------------------

    def _run_loop(self, emit) -> str | None:
        tool_calls = 0
        rewrites, claims_checked = 0, False
        seen_calls: dict[str, int] = {}
        for round_no in range(self.settings.max_model_calls_per_turn):
            last_round = round_no == self.settings.max_model_calls_per_turn - 1
            response = self._call_model(emit, allow_tools=not last_round)
            self.messages.append({"role": "assistant", "content": response.content})

            for block in response.content:
                if block.type == "thinking" and getattr(block, "thinking", ""):
                    emit({"type": "thinking", "text": block.thinking})

            if response.stop_reason == "refusal":
                return "Sorry, I can't help with that request here."
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                reply = "".join(b.text for b in response.content if b.type == "text").strip()
                problems = self._check_reply(reply)  # exact checks run on every draft, rewrites included
                if not problems and not claims_checked:  # the second model sees one clean draft per turn
                    claims_checked = True
                    problems = self._check_claims(reply, emit)
                if problems and rewrites < 2:
                    rewrites += 1
                    emit({"type": "guard", "problems": problems})
                    emit({"type": "reply_reset"})  # the UI drops the draft it streamed
                    self.messages.append({"role": "user", "content": GUARD_NOTE.format(problems=" ".join(problems))})
                    continue
                return reply

            results = []
            for block in tool_uses:
                key = f"{block.name}:{json.dumps(block.input, sort_keys=True)}"
                seen_calls[key] = seen_calls.get(key, 0) + 1
                tool_calls += 1
                self.usage["tool_calls"] += 1
                if tool_calls > self.settings.max_tool_calls_per_turn:
                    result, is_error = {"error": "Tool budget for this message is used up. Reply to the employee "
                                                 "now with what you know: ask one question, or escalate."}, True
                elif seen_calls[key] > 2:
                    result, is_error = {"error": "You already made this exact call twice. Use the earlier result."}, True
                else:
                    label = step_label(block.name, block.input)
                    emit({"type": "tool_start", "id": block.id, "name": block.name, "label": label})
                    t0 = time.time()
                    result, is_error = execute(self.ctx, block.name, block.input)
                    record = result.pop("record", None) or self._record(result)  # raw fields: UI and trace only
                    emit({"type": "tool_call", "id": block.id, "name": block.name, "label": label,
                          "outcome": step_outcome(block.name, result, is_error, record), "input": block.input,
                          "result": result, "record": record, "is_error": is_error,
                          "ms": int((time.time() - t0) * 1000)})
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": json.dumps(result, ensure_ascii=False, default=str), "is_error": is_error})
            # All results go back in one message so the model keeps making parallel calls.
            self.messages.append({"role": "user", "content": results})
        return None

    def _call_model(self, emit, allow_tools: bool):
        s = self.settings
        kwargs = dict(
            model=s.agent_model, max_tokens=16000,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            tools=TOOL_SPECS, messages=self.messages,
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": s.agent_effort},
            cache_control={"type": "ephemeral"},
        )
        if not allow_tools:
            kwargs["tool_choice"] = {"type": "none"}
        t0 = time.time()
        # Streamed so the UI can show the reasoning summary and the reply as they are written.
        if s.use_fallbacks:
            stream_ctx = self.client.beta.messages.stream(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
        else:
            stream_ctx = self.client.messages.stream(**kwargs)
        with stream_ctx as stream:
            for event in stream:
                if event.type == "content_block_start" and event.content_block.type == "text":
                    emit({"type": "text_start"})
                elif event.type == "content_block_delta":
                    if event.delta.type == "thinking_delta" and event.delta.thinking:
                        emit({"type": "thinking_delta", "text": event.delta.thinking})
                    elif event.delta.type == "text_delta":
                        emit({"type": "text_delta", "text": event.delta.text})
            response = stream.get_final_message()
        self._account(response)
        emit({"type": "model_call", "model": response.model, "stop_reason": response.stop_reason,
              "ms": int((time.time() - t0) * 1000), "input_tokens": response.usage.input_tokens,
              "output_tokens": response.usage.output_tokens,
              "cache_read_input_tokens": response.usage.cache_read_input_tokens or 0})
        return response

    def _account(self, response, model: str | None = None) -> float:
        """Adds one call's tokens and cost to the conversation's usage; returns the call's cost."""
        u = response.usage
        self.usage["model_calls"] += 1
        for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            self.usage[key] += getattr(u, key, 0) or 0
        cost = call_cost(model or self.settings.agent_model, u)
        self._cost += cost
        self.usage["cost_usd"] = round(self._cost, 4)
        return cost

    # ---- context and checks -------------------------------------------------------------------------

    def _case_context(self) -> str:
        c = self.case
        view = {
            "status": c.status, "identity_verified": c.identity_verified, "request_type": c.request_type,
            "systems": c.systems, "symptom": c.symptom, "user_goal": c.user_goal, "deadline": c.deadline,
            "claims": [f"[{x.status}] {x.text}" for x in c.claims],
            "hypotheses": [f"{h.id} [{h.status}] {h.cause} {h.evidence_refs}"
                           + (f" (not yet checked: {h.not_yet_checked})" if h.not_yet_checked else "") for h in c.hypotheses],
            "evidence_collected": [f"{e.id} {e.tool}({json.dumps(e.args, ensure_ascii=False)})" for e in c.evidence],
            "kb_articles_read": c.kb_articles_read,
            "actions": [f"{a.action}({a.target}) -> {a.decision}, executed={a.executed}" for a in c.actions],
            "ticket_id": c.ticket_id, "next_step": c.next_step,
        }
        return case_block(json.dumps(view, ensure_ascii=False))

    def _record(self, result: dict) -> dict | None:
        """The raw record kept with a text result's evidence (for the UI's step label)."""
        return next((e.record for e in self.case.evidence if e.id == result.get("evidence_id")), None)

    def _tool_results(self) -> list[str]:
        """Every tool call in this conversation with its result, as the agent saw them."""
        calls = {b.id: f"{b.name}({json.dumps(b.input, ensure_ascii=False)})" for m in self.messages
                 if m["role"] == "assistant" and isinstance(m["content"], list)
                 for b in m["content"] if getattr(b, "type", None) == "tool_use"}
        return [f"{calls.get(r['tool_use_id'], 'tool')} -> {r['content']}" for m in self.messages
                if m["role"] == "user" and isinstance(m["content"], list)
                for r in m["content"] if isinstance(r, dict) and r.get("type") == "tool_result"]

    def _capabilities(self) -> list[str]:
        """What the agent can do, as it was told: its tools, and each action with the policy's decision."""
        return ([f"{t.name}: {t.description}" for t in TOOLS] +
                [f"perform_action {name}: {rule['decision']}. {rule['description']}"
                 for name, rule in self.ctx.policy.actions.items()])

    def _check_claims(self, reply: str, emit) -> list[str]:
        """A second model's flags for claims the evidence doesn't support; none when it is off or fails."""
        if not self.settings.checker_model:
            return []
        if not checker.worth_checking(reply):
            self.usage["checker_skipped"] += 1
            return []
        t0 = time.time()
        try:
            flags, response = checker.find_harmful_claims(
                self.client, self.settings.checker_model, reply=reply, rules=SYSTEM_PROMPT,
                session=self._session_note, employee_messages=self.case.employee_messages,
                tool_results=self._tool_results(), capabilities=self._capabilities())
        except Exception as exc:  # a checker outage must not block the reply; it is counted and traced
            self.usage["checker_errors"] += 1
            emit({"type": "claim_check", "error": f"{type(exc).__name__}: {exc}"})
            return []
        cost = self._account(response, self.settings.checker_model)
        self.usage["checker_calls"] += 1
        self.usage["checker_flagged"] += bool(flags)
        self.usage["checker_cost_usd"] = round(self.usage["checker_cost_usd"] + cost, 4)
        emit({"type": "claim_check", "flags": flags, "ms": int((time.time() - t0) * 1000)})
        if not flags:
            return []
        return ["These sentences aren't supported by your tool results or your instructions. Remove them, or say "
                "plainly that they are your own suggestion: " + " | ".join(flags)]

    def _check_reply(self, reply: str) -> list[str]:
        problems = []
        if not reply:
            return ["The reply was empty."]
        unread = sorted(set(KB_REF.findall(reply)) - set(self.case.kb_articles_read))
        if unread:
            problems.append(f"You cited {', '.join(unread)} without reading it with get_kb_article. Read it first "
                            f"or remove the citation and any steps that came from it.")
        unknown = sorted(set(TICKET_REF.findall(reply)) - self.world.known_ticket_ids())
        if unknown:
            problems.append(f"{', '.join(unknown)} does not exist in any system. Never invent ticket, incident or "
                            f"request numbers.")
        return problems
