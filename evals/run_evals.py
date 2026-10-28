"""Run the scenario suite: a simulated employee talks to the agent, then deterministic checks and a judge score it.

    python -m evals.run_evals                       # all scenarios, once
    python -m evals.run_evals --only vpn_disconnects okta_lockout_urgent
    python -m evals.run_evals --repeats 3           # pass^3: a scenario counts only if all 3 runs pass
    python -m evals.run_evals --no-judge            # skip the judge model (cheaper)

Writes evals/reports/<timestamp>.md and .json.
"""

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import anthropic
import yaml
from pydantic import BaseModel, Field

from helpdesk.agent import HelpdeskAgent
from helpdesk.config import Settings
from helpdesk.world import World

HERE = Path(__file__).parent
END = "<<END>>"

SIMULATOR_PROMPT = """\
You are role-playing an Acme employee chatting with the company's IT support agent. Stay in character.

Your opening message was: {opening}
Facts about your situation (reveal a fact only when the agent asks about it or it is clearly relevant): {facts}

Rules:
- Write like a busy employee: one to three short sentences, plain language.
- Answer what the agent asks. Don't volunteer every fact at once. Don't invent facts beyond the ones above; if asked \
something you don't know, say so.
- If the agent asks for a verification code and a system notice showed you one, reply with just that code.
- If the agent asks you to try something, pretend you did it and report the result that follows from your facts.
- If the agent asks you to wait or check back later, pretend the time has passed and report the result.
- If you said you would try something yourself, try it now and report the result in your next message.
- If your problem is solved, or the agent has handed you to a person with a ticket and asks nothing more of you, or \
the agent has clearly finished, reply with exactly {end}
- Never write as the agent.
"""


class Judgement(BaseModel):
    grounded: bool = Field(description="True if every factual claim the agent made to the employee is supported by "
                                       "the tool results or the knowledge base articles it read.")
    unsupported_claims: list[str] = Field(description="Claims not supported by tool results (empty if none).")
    diagnosis_quality: int = Field(description="1-5: checked shared status before individual troubleshooting, "
                                               "used evidence, asked only necessary questions.")
    honesty_about_confidence: int = Field(description="1-5: clear about what is confirmed vs likely vs unknown.")
    handoff_quality: int | None = Field(description="1-5 if escalated: could a specialist act without re-asking the "
                                                    "employee? null if not escalated.")
    employee_experience: int = Field(description="1-5: clear, concise, warm, right next step, no jargon.")
    notes: str = Field(description="One or two sentences on the main weakness, if any.")


JUDGE_PROMPT = """\
You are reviewing an IT support agent's conversation with an employee. Judge strictly against the evidence.

Scenario: {scenario}

Session context the agent was given by the system (treat as ground truth, like a tool result):
{session}

Conversation (employee and agent messages, with system notices):
{transcript}

Tool calls the agent made, with results (this is the ground truth the agent had):
{tools}

Final case record:
{case}

Handoff ticket (if escalated):
{handoff}
"""


def simulate_employee(client, settings: Settings, scenario: dict, transcript: list[str]) -> str:
    response = client.messages.create(
        model=settings.simulator_model, max_tokens=400,
        system=SIMULATOR_PROMPT.format(opening=scenario["opening"], facts=scenario["facts"], end=END),
        messages=[{"role": "user", "content": "Conversation so far:\n" + "\n".join(transcript) +
                                               "\n\nWrite your next message as the employee."}],
    )
    return "".join(b.text for b in response.content if b.type == "text").strip()


def run_conversation(world: World, scenario: dict, client, max_turns: int) -> dict:
    settings = Settings()
    settings.faults = scenario.get("faults", "")
    events: list[dict] = []
    agent = HelpdeskAgent(world, scenario["user"], settings, client=client, on_event=events.append)
    transcript, replies = [], []
    message = scenario["opening"]
    started = time.time()
    said_goodbye = False
    for _ in range(max_turns):
        transcript.append(f"EMPLOYEE: {message}")
        result = agent.respond(message)
        for notice in result.notices:
            transcript.append(f"SYSTEM NOTICE (visible to the employee): {notice['text']}")
        transcript.append(f"AGENT: {result.reply}")
        replies.append(result.reply)
        if agent.case.status != "open" and "?" not in result.reply[-200:]:
            break
        message = simulate_employee(client, settings, scenario, transcript)
        if END in message or not message:
            if agent.case.status == "open" and not said_goodbye:
                said_goodbye = True
                message = "Thanks, that's all for now."  # employees leave; the agent should still wrap up the case
                continue
            break
    handoff = None
    if agent.case.ticket_id:
        handoff = json.loads((Path(__file__).parent.parent / "var" / "tickets" / f"{agent.case.ticket_id}.json").read_text())
    return {"agent": agent, "transcript": transcript, "replies": replies, "events": events, "handoff": handoff,
            "seconds": round(time.time() - started, 1), "usage": agent.usage}


def check(scenario: dict, run: dict) -> list[str]:
    """Deterministic checks. Returns a list of failures (empty = pass)."""
    expect, case = scenario["expect"], run["agent"].case
    failures = []
    if case.status not in expect["outcome"]:
        failures.append(f"outcome {case.status} not in {expect['outcome']}")
    if "team" in expect and case.status == "escalated":
        team = run["handoff"]["assignment_group"]
        if team not in expect["team"]:
            failures.append(f"team {team} not in {expect['team']}")
    if "priority" in expect and run["handoff"] and run["handoff"]["priority"] != expect["priority"]:
        failures.append(f"priority {run['handoff']['priority']} != {expect['priority']}")
    called = {e["name"] for e in run["events"] if e["type"] == "tool_call"}
    for tool in expect.get("tools", []):
        if tool not in called:
            failures.append(f"never called {tool}")
    executed = {a.action for a in case.actions if a.executed} | {f"{a.action}:{a.target}" for a in case.actions if a.executed}
    for action in expect.get("actions", []):
        if action not in executed:
            failures.append(f"action {action} not executed")
    for action in expect.get("forbidden_actions", []):
        if action in executed:
            failures.append(f"forbidden action {action} executed")
    text = "\n".join(run["replies"]).lower()
    if expect.get("mention_any") and not any(m.lower() in text for m in expect["mention_any"]):
        failures.append(f"replies mention none of {expect['mention_any']}")
    for m in expect.get("mention_none", []):
        if m.lower() in text:
            failures.append(f"replies mention forbidden '{m}'")
    if expect.get("first_reply_asks") and "?" not in run["replies"][0] and "？" not in run["replies"][0]:
        failures.append("first reply did not ask a clarifying question")
    return failures


def judge(client, settings: Settings, scenario: dict, run: dict) -> dict:
    tools = [f"{e['name']}({json.dumps(e['input'], ensure_ascii=False)}) -> "
             f"{json.dumps(e['result'], ensure_ascii=False, default=str)[:1500]}"
             for e in run["events"] if e["type"] == "tool_call"]
    prompt = JUDGE_PROMPT.format(session=run["agent"]._session_note, scenario=json.dumps({k: scenario[k] for k in ("opening", "facts")}),
                                 transcript="\n".join(run["transcript"]), tools="\n".join(tools),
                                 case=run["agent"].case.model_dump_json(include={"status", "hypotheses", "claims",
                                                                                 "actions", "outcome_summary"}),
                                 handoff=json.dumps(run["handoff"], ensure_ascii=False)[:6000] if run["handoff"] else "none")
    response = client.messages.parse(model=settings.judge_model, max_tokens=4000,
                                     messages=[{"role": "user", "content": prompt}], output_format=Judgement)
    return response.parsed_output.model_dump()


def run_one(world, client, scenario, use_judge, max_turns) -> dict:
    settings = Settings()
    try:
        run = run_conversation(world, scenario, client, max_turns)
    except Exception as exc:  # an API error fails the scenario but not the whole suite
        return {"id": scenario["id"], "passed": False, "failures": [f"crashed: {type(exc).__name__}: {exc}"]}
    failures = check(scenario, run)
    verdict = judge(client, settings, scenario, run) if use_judge else None
    if verdict and not verdict["grounded"]:
        failures.append("judge: ungrounded claims " + "; ".join(verdict["unsupported_claims"])[:300])
    return {"id": scenario["id"], "passed": not failures, "failures": failures, "outcome": run["agent"].case.status,
            "ticket": run["handoff"]["ticket_id"] if run["handoff"] else None,
            "team": run["handoff"]["assignment_group"] if run["handoff"] else None,
            "turns": run["agent"].case.turns, "seconds": run["seconds"], "cost_usd": run["usage"]["cost_usd"],
            "tool_calls": run["usage"]["tool_calls"], "judge": verdict, "transcript": run["transcript"]}


def write_report(results: list[dict], repeats: int) -> Path:
    out_dir = HERE / "reports"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (out_dir / f"{stamp}.json").write_text(json.dumps(results, indent=1, ensure_ascii=False))

    by_id: dict[str, list[dict]] = {}
    for r in results:
        by_id.setdefault(r["id"], []).append(r)
    pass_all = sum(all(r["passed"] for r in runs) for runs in by_id.values())
    pass_runs = sum(r["passed"] for r in results)
    judged = [r["judge"] for r in results if r.get("judge")]
    avg = lambda key: round(sum(j[key] for j in judged if j[key]) / max(1, sum(1 for j in judged if j[key])), 2)
    lines = [f"# Eval report {stamp}", "",
             f"- Scenarios: {len(by_id)}, runs per scenario: {repeats}",
             f"- Runs passed: {pass_runs}/{len(results)}",
             f"- pass^{repeats} (every run of a scenario passed): {pass_all}/{len(by_id)}",
             f"- Mean cost per conversation: ${round(sum(r.get('cost_usd', 0) for r in results) / len(results), 3)}; "
             f"mean wall time {round(sum(r.get('seconds', 0) for r in results) / len(results), 1)} s; "
             f"mean employee turns {round(sum(r.get('turns', 0) for r in results) / len(results), 1)}"]
    if judged:
        lines.append(f"- Judge means (1-5): diagnosis {avg('diagnosis_quality')}, confidence honesty "
                     f"{avg('honesty_about_confidence')}, handoff {avg('handoff_quality')}, employee experience "
                     f"{avg('employee_experience')}; grounded {sum(j['grounded'] for j in judged)}/{len(judged)}")
    lines += ["", "| Scenario | Pass | Outcome | Team | Turns | Tools | Cost $ | Failures / judge notes |",
              "|---|---|---|---|---|---|---|---|"]
    for r in results:
        note = "; ".join(r["failures"]) or (r["judge"]["notes"] if r.get("judge") else "")
        lines.append(f"| {r['id']} | {'yes' if r['passed'] else 'NO'} | {r.get('outcome', '-')} | {r.get('team') or '-'} "
                     f"| {r.get('turns', '-')} | {r.get('tool_calls', '-')} | {r.get('cost_usd', '-')} | {note.replace('|', '/')} |")
    lines += ["", "## Transcripts", ""]
    for r in results:
        lines += [f"### {r['id']} ({'pass' if r['passed'] else 'FAIL'})", "", "```text", *r.get("transcript", []), "```", ""]
    path = out_dir / f"{stamp}.md"
    path.write_text("\n".join(lines))
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", help="scenario ids to run")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--max-turns", type=int, default=7)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    scenarios = yaml.safe_load((HERE / "scenarios.yaml").read_text())
    if args.only:
        scenarios = [s for s in scenarios if s["id"] in args.only]
    world, client = World.load(), anthropic.Anthropic()
    jobs = [s for s in scenarios for _ in range(args.repeats)]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda s: run_one(world, client, s, not args.no_judge, args.max_turns), jobs))
    for r in results:
        print(f"{'PASS' if r['passed'] else 'FAIL'}  {r['id']:<34} {r.get('outcome', '-'):<16} {'; '.join(r['failures'])}")
    print(f"\nReport: {write_report(results, args.repeats)}")


if __name__ == "__main__":
    main()
