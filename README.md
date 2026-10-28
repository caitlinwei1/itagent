# Acme IT Support Agent

An AI IT support agent that replaces "file a ticket and wait". An employee describes the problem in their own words; the agent investigates across the company's systems, fixes what it is allowed to fix, and hands everything else to the right human team with a complete handoff, so nobody has to ask the employee the same questions again.

Everything runs locally against a mock company ("Acme": 15 services, 23 knowledge base articles, 13 employees, open incidents, a change log, 22 past tickets and a policy file).

```
python -m helpdesk.web        # web chat with a live trace panel: http://127.0.0.1:8000
python -m helpdesk.cli --trace
python -m evals.run_evals
```

---

## 1. The problem and the scope

**User:** an employee with an IT problem, not a helpdesk agent. They know symptoms ("Okta won't let me in"), not causes.

**Why this problem:** most IT tickets are repetitive and documented (lockouts, VPN, access requests, known outages), yet each waits in a queue for a person to look up the same records. The time goes into investigation and back-and-forth, which an agent can do in a minute.

**Interaction model:** a chat. The web UI (for the demo) shows the conversation on the left and, on the right, every tool call, the agent's reasoning summary, the case record and the handoff ticket. A terminal CLI is included. The agent itself is a Python class with one method, `respond(message)`, so Slack or Teams can be added without changing it.

**What success looks like for the employee:** the problem is fixed, or they know exactly what happens next, within a couple of minutes and without filing anything. For a known outage: in one reply they learn it's not them, the workaround and the ETA. For an escalation: they get a ticket number, who will contact them and when, a workaround if there is one, and they never repeat their story.

| Area | The agent resolves | The agent hands off |
|---|---|---|
| Sign-in (Okta) | Locked accounts: finds the cause in the sign-in log, verifies identity, unlocks, and explains how to stop it re-locking. Password reset links. | MFA factor reset (new or lost phone), suspended accounts, repeated lockouts with no explanation |
| Apps (Salesforce etc.) | Known incidents: adds the employee to the incident, gives workaround and ETA, no new ticket. Single-user issues with a KB fix. | Unknown cause after checks |
| VPN | Old client bug: confirms from session log + installed version, pushes the approved version | Network-side problems |
| Access | Auto-approved access granted on the spot; approval requests filed with the right approvers ("pending approval" is not an escalation) | Non-requestable access (production write) |
| Data pipelines | Diagnosis: links the failure to the maintenance changes, explains what's confirmed and what isn't | The fix (firewall/allow-list, reruns), with the evidence attached |
| Security | Immediate safety steps | Phishing, lost devices: always escalated as P1 to Security Operations |
| Everything else | KB how-to answers, general advice clearly labeled as such | Out-of-scope requests are redirected, not ticketed |

## 2. Why an agent (and not a chatbot, FAQ search or rule engine)

The same symptom has different causes for different people, and telling them apart needs several sources at once:

- "I reset my password and still can't log in" is, for this employee, an Outlook app on their phone retrying the old password and re-locking the account. Only the sign-in log shows it. A FAQ search returns the password-reset article, which is the step that already failed.
- "Salesforce is slow" is a known incident for the Chicago office, but a browser problem for someone in New York. The answer depends on the incident's scope and the employee's location.
- "Jenkins times out since Friday" needs the change log (three changes in that window), the job's error ("connect timed out", not "authentication failed") and the runbook that says which error points to which change.

A rule engine needs every path written in advance and fails on the problem nobody wrote down; the interviewers will type new ones. A chatbot over the KB can't see the employee's records and can't act. The agent decides **what to check next and what to ask**, which is what changes from case to case. What must not change from case to case (who may do what, whether identity is verified, what counts as evidence, the handoff format, the ticket priority) is decided by code.

## 3. Architecture

```
employee ──► web / CLI ──► HelpdeskAgent.respond() ──────────────────────────────► reply + system notices
                               │  conversation + case record (code-maintained)
                               ▼
                         Claude (tool use loop, adaptive thinking)
                               │ tool calls
                               ▼
               tool runtime: validate input ─► retry once on failure ─► evidence ids for read results
                     │ read tools                                  │ write tools
                     ▼                                             ▼
         KB · status/incidents/changes ·              policy engine (data/policy.yaml + identity state)
         directory · diagnostics · ticket history       allow / needs_approval / human_only / forbidden
                                                                   │
                                                                   ▼
                                                     mock systems + audit log + ticket files
         reply checks before sending: cited KB articles were read; ticket numbers exist
```

| Part | Decides | Deterministic? |
|---|---|---|
| Claude (`helpdesk/agent.py`) | what to check, what to ask, how to explain, when it has enough evidence | No |
| Tool runtime (`helpdesk/tools.py`) | input validation, retries, evidence ids, "the signed-in employee only" | Yes |
| Policy engine (`helpdesk/policy.py`, `data/policy.yaml`) | whether an action may run, needs approval, or needs a human team | Yes |
| Identity (`confirm_identity_code`) | whether identity is verified; the model never sees the code | Yes |
| Case record (`helpdesk/case.py`) | a cause can be "confirmed" only by citing evidence ids that exist | Yes |
| Escalation (`escalate_to_human`) | handoff schema, valid team, valid evidence ids, priority from ITIL impact x urgency | Yes |
| Reply checks | KB citations must have been read; ticket/incident numbers must exist | Yes |

### How the agent works a case

The system prompt describes how an experienced specialist works, based on the [CompTIA troubleshooting methodology](https://www.comptia.org/blog/troubleshooting-methodology), ITIL incident handling and [KCS](https://library.serviceinnovation.org/KCS/Knowledge-Centered_Success_Practices_Guide/201-Solve_Loop):

1. **Understand** the system, symptom, goal and deadline; record them (`update_case`). The employee's statements are *claims* until a tool confirms them.
2. **Check before asking.** Shared picture first (status, open incidents, recent changes: "what changed?"), then the employee's own records, the KB and similar past tickets, in parallel.
3. **Hypotheses with evidence.** Candidate causes are tracked in the case record. Code refuses to mark a cause confirmed or ruled out without an evidence id from a tool result. Past tickets are hints only.
4. **Ask one question** only when the records can't separate the remaining causes. Never ask what can be looked up.
5. **Act within policy**, after step-up identity verification for account and access changes.
6. **Verify** with a tool read-back plus the employee's confirmation; give the step that prevents a repeat. Code won't close a case as `resolved` without the employee's confirmation.
7. **Close or escalate.** Every case ends as `resolved`, `linked_incident`, `pending_approval`, `answered`, `refused` or `escalated`.

Alongside: if there is a deadline the fix might miss, the agent gives a workaround for the real goal first (join the client meeting from the phone app).

Design choices worth calling out:

- **Constraints in code, choices in the model.** A stricter design that routes every step through a code state machine was considered and rejected: on problems nobody anticipated it either breaks or needs a rule for every path, and the interviewers will type new problems. Instead, code enforces invariants (policy, identity, evidence, budgets, handoff schema) and the model chooses the path.
- **Single agent, hand-written loop** (Anthropic SDK, no framework). Each step is visible and easy to explain; the guard, budget and policy hooks sit exactly where needed. Multi-agent orchestration would add latency and failure modes without a clear gain at this scope.
- **Evidence ids.** Every read result gets an id (E1, E2...). Hypotheses, claims and the handoff cite them, so a specialist can trace every statement to the record it came from.
- **Prompt caching.** The system prompt and tool list are frozen; session facts and the per-turn case record go in mid-conversation system messages. In test runs, 70-95% of input tokens were cache reads.
- **Live progress over SSE.** `POST /api/chat` returns a `text/event-stream`. The agent loop runs in a worker thread and pushes events as they happen: `thinking_delta` (the reasoning summary), `tool_start` / `tool_call` (each check, with a plain-language label such as "Reading your VPN connection log" and its outcome), `text_delta` (the reply, token by token), `reply_reset` (when the reply guard rejects a draft and the agent rewrites it) and `done`. The chat shows a progress card that ticks off each check and collapses to "Checked N things · Xs" once the reply is in; the trace panel shows the raw tool inputs, results and token usage. Bookkeeping calls (`update_case`) appear only in the trace panel.
- **Model:** `claude-opus-5-5` at medium effort, with adaptive thinking (summaries streamed to the chat and the trace panel) and the server-side refusal fallback. All configurable in `.env`.

## 4. Resolution vs. escalation

The boundary is in `data/policy.yaml`, not in the prompt:

| Decision | Example | What the agent does |
|---|---|---|
| `allow` (+ preconditions) | unlock own account (identity verified, account locked); grant auto-approved access; push an approved software version | runs it, reads the result back |
| `needs_approval` | Snowflake PROD read (manager + data owner) | files an approval request with the right approvers, closes as `pending_approval` |
| `human_only` | MFA reset, suspended account, firewall, production reruns, PROD write | escalates to the team named in the policy |
| `forbidden` | local admin rights | explains why and offers the approved route (Self Service) |

The agent also escalates for: security incidents (always P1 to Security Operations), an unknown cause after checks, conflicting evidence, a source that keeps failing, a fix that didn't work, or an employee asking for a person.

**The handoff** (`var/tickets/IT-xxxxx.json`, also shown in the UI) is a decision brief, not a transcript:

- the employee's original words, their profile and whether identity was verified
- systems, symptom, request type, deadline
- what the employee and the agent already did (from the audit log, not from the model)
- findings, each tied to an evidence id
- causes ruled out / still open; similar past tickets (as hints)
- **the exact question the specialist must answer** and the recommended next action
- the workaround already given
- priority computed by code from ITIL impact x urgency (security incidents are always P1), and the trace id

## 5. Data sources (simulated)

| Source | File | Why it exists |
|---|---|---|
| Knowledge base and runbooks | `data/kb/*.md` (23, one deprecated) | grounded fix steps; the deprecated article tests filtering |
| Service catalog and health | `data/services.json` | maps "the vpn" or "SFDC" to a service and owner team |
| Incidents | `data/incidents.json` | known outages with scope by location, workaround, ETA |
| Change log | `data/changes.json` | "what changed?", e.g. the Friday maintenance window |
| Employee directory and diagnostics | `data/users.json` | department, manager, devices and versions, access, account state, sign-in and VPN logs, team jobs |
| Resolution history | `data/tickets.json` (22) | likely causes as hints; one ticket's free text contains a prompt injection |
| Policy and access catalog | `data/policy.yaml` | what the agent may do, with which preconditions, and who approves |

The data is small but built so that each of the five example problems needs at least two sources to answer correctly, and so that some sources disagree (the status page says Jenkins and Snowflake are healthy while every run fails).

## 6. Assumptions and tradeoffs

- The helpdesk chat is reachable without Okta (Slack on a trusted phone), so a locked-out employee can still use it. That sign-in is enough for lookups; account and access changes need step-up verification (a simulated SMS code).
- The agent only sees the signed-in employee's own records. Team data (the pipeline) is visible to that team's members.
- Mock data is static JSON and per-conversation copies; nothing persists between conversations except tickets, traces and audit logs in `var/`.
- KB retrieval is BM25 with a metadata boost. At 22 articles, embeddings add a dependency for little gain; the interface allows swapping later.
- One model for the agent, at medium effort: quality of diagnosis mattered more than shaving seconds. Typical replies take 10-30 s because the agent checks several sources first; the trace panel shows the progress.
- The reply streams while it is written, but the guard checks it only once it is complete. If the guard rejects it (for example, it cites a KB article the agent never opened), the UI clears the draft and the corrected reply streams in its place.
- Not built: real integrations, persistence across restarts, multi-language KB, attachments/screenshots, proactive notifications when an incident is resolved.

## 7. How to run

Requires Python 3.10+ and an Anthropic API key.

```bash
cd it-helpdesk-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then put your key in .env

python -m helpdesk.web      # open http://127.0.0.1:8000
```

In the web UI, pick who you are signed in as (each mock employee has a matching example problem under the chat box), then type. The **Fault injection** menu makes a tool time out or fail, to show how the agent behaves when a source is down. When the agent sends an SMS verification code, it appears as a yellow system notice; type it back in the chat.

Terminal: `python -m helpdesk.cli --trace` (`--user u-maya` to skip the picker; `/case` prints the case record).

Tests (no API calls): `python -m pytest -q`

## 8. Evaluation

Two layers:

1. **Unit tests** (`tests/`, 27 tests, no model calls): policy decisions, identity verification, evidence rules, fault injection, handoff validation, priority matrix, reply guards.
2. **Scenario evals** (`evals/`): 17 scenarios. For each, a simulated employee (Claude Haiku, given a persona and hidden facts it reveals only when asked) talks to the agent until the case closes. Then:
   - **deterministic checks** on the final state, as in [τ-bench](https://proceedings.iclr.cc/paper_files/paper/2025/file/1b126cc38b8638e07bef37e7b2bb72bf-Paper-Conference.pdf): outcome, assigned team, priority, tools called, actions executed and actions that must never execute, facts that must or must not appear;
   - **a judge model** (Claude Opus, structured output) that compares every claim in the replies with the tool results it had, and scores diagnosis, honesty about confidence, handoff quality and employee experience.
   - `--repeats 3` reports pass^3 (a scenario counts only if all runs pass), because agents are less consistent than single-run pass rates suggest.

The scenarios cover the five example problems plus: MFA on a new phone, a suspended account, local-admin pressure ("my manager approved it"), phishing, a lost laptop, a vague "my computer is broken", mailbox full, a London printer outage, a non-IT request, the status page being down (the agent must not invent the incident it can't see), a prompt injection planted in a past ticket, and an employee who wants a human.

### Results

Iteration history (same 17 scenarios; Claude Opus 5.5 at medium effort; reports in `evals/reports/`):

| Round | Change before the round | Runs passed | Judge: grounded | Judge: handoff (1-5) |
|---|---|---|---|---|
| 1 | first version | 7/17 | 7/17 | 3.3 |
| 2 | fixed ticket-id collision between parallel conversations (one escalation overwrote another's ticket file); gave the judge the session context; prompt: no unsupported explanations or promises | 9/17 | 10/17 | 3.7 |
| 3 | local time zone in the session context; `add_ticket_note` (the agent had claimed to add details to a ticket it couldn't edit); KB article for non-IT requests; simulator reports results of steps it said it would try | 11/17 | 12/17 | 4.8 |
| 4 | KB-160 says what to do with the laptop after phishing; `--repeats 2` | 27/34 runs (pass^2 11/17) | 28/33 | 4.2 |

Round 4 numbers: mean cost $0.07 per conversation, mean 27 s wall time per conversation (all turns), 2.1 employee messages per conversation. Every deterministic safety check passed in every run: no forbidden action executed, no invented incident when the status page was down, the planted prompt injection was recognised and ignored, phishing always went to Security Operations as P1.

What still fails, and why:

- **Small unsupported embellishments** (most judge failures): "these steps often fix this", "approvers check your training as part of review". They are not dangerous, but they are claims without a source. Next step: a narrow verifier pass on the draft reply that checks each factual sentence against the case evidence before sending, as the KB-citation guard already does for article ids.
- **Ending without confirmation.** In one Okta run the employee left before confirming sign-in worked, and the agent closed the case as `answered` rather than `resolved`. That is the intended behavior (`resolved` requires confirmation), but in production the case should instead stay open with an automatic follow-up.
- **Judge false positives.** A few flags are wrong (for example, that the code-delivery channel was not in any tool result, when it was). Spot-checking judge verdicts against transcripts is part of reading each report.
- One run of the status-page scenario crashed because the API key reached its usage limit during the run; it is counted as a failure.


## 9. What I would improve with more time

- Run the suite with `--repeats 5` in CI and track pass^k per scenario over prompt versions.
- Hybrid KB search and an article-quality loop: the agent already logs searches with no answer (`var/knowledge_gaps.jsonl`) and wrong articles (`var/kb_feedback.jsonl`); route them to article owners.
- Proactive follow-up: message employees linked to an incident when it's resolved, and reopen the case if they say it isn't.
- Lower latency: lower effort for simple intents and a quick first acknowledgement while checks run.
- Real integrations and SSO, and a human-review queue for a sample of closed cases. See `docs/design_note.md`.

## Repository layout

```
helpdesk/
  agent.py      the loop: context, model calls, tool execution, budgets, reply checks
  tools.py      14 tools: schemas, implementations, evidence ids, retries, fault injection
  policy.py     deterministic policy engine
  case.py       case record and handoff schemas, ITIL priority matrix
  prompts.py    system prompt and per-turn context
  world.py      loads the mock company; BM25 KB index; per-conversation copies
  trace.py      JSONL traces per case
  web.py, static/index.html   web chat with live trace
  cli.py        terminal chat
data/           mock company data and policy
evals/          scenarios.yaml, run_evals.py (simulator, checks, judge), reports/
tests/          unit tests
docs/design_note.md   productionization note
var/            created at runtime: traces, audit log, tickets, knowledge gaps
```
