# Productionizing the helpdesk agent

This is a short note on what changes between this local demo and a production deployment.

## Channel and identity
- **Front end.** Run the agent as a Slack or Teams app. `HelpdeskAgent.respond()` already has the right shape: one employee message in, one reply plus system notices out. The SSE progress events the web demo already streams (step labels such as "Reading your VPN connection log") become a live status message that is edited in place.
- **Identity.** The chat platform's SSO identity replaces the mock sign-in (the user id comes from the session, never from the message). Step-up verification uses Okta Verify push, or SMS to the HR-registered phone, instead of the simulated code. An employee who is locked out of Okta still has Slack on their phone through device trust; when they don't, a kiosk or phone line falls back to a human.

## Systems behind the tools
Each tool keeps its schema and swaps the JSON file for a real adapter:

| Tool | Production source |
|---|---|
| search_kb / get_kb_article | Confluence or ServiceNow Knowledge, with hybrid search (BM25 + embeddings) and the same active/employee-facing filter |
| check_system_status | Statuspage / ServiceNow incidents, plus the CAB change calendar |
| get_my_profile | Workday (HR record) + Okta (account) + Jamf/Intune (devices) |
| get_account_diagnostics | Okta System Log, GlobalProtect/Panorama logs, Jenkins API, Microsoft Graph |
| search_ticket_history | ServiceNow resolved incidents, embedding search |
| perform_action | Okta API, ServiceNow access catalog (approvals), Jamf Self Service, ServiceNow incident "affected users" |
| escalate_to_human | ServiceNow incident create, with the handoff JSON in the work notes and fields mapped to category, assignment group, impact and urgency |

The policy file becomes owned by IT security, reviewed through change control, and versioned. Every decision is already logged with the policy outcome, so audits can replay why an action ran.

## Observability
- Traces are JSONL per case today. In production, emit the same events as OpenTelemetry spans (one span per model call and tool call, the case id as trace id) and send the audit log to the SIEM.
- Dashboards: containment rate (cases closed without a human), escalations by reason and team, reopen rate within 7 days, time to resolution, cost per case, tool error rates, guard trigger rate.
- `var/knowledge_gaps.jsonl` (searches with no article) and `var/kb_feedback.jsonl` (articles the agent found wrong) feed the knowledge team weekly, following KCS "flag it or fix it".

## Cost and latency
- The system prompt and tool definitions are frozen, so they are cached across all conversations; per-session facts go in mid-conversation system messages. In test runs, 70-95% of input tokens were cache reads.
- Typical common case: 3-5 model calls per employee message. To cut latency, route simple, high-volume intents (password help, known incidents) to a lower effort setting.
- A per-message cap on model and tool calls bounds the worst case.

## Safety in production
- Write actions stay behind the deterministic policy engine and step-up identity. New actions are added to the policy file before the tool can run them.
- Add rate limits per employee on account actions (for example, at most two unlocks per day before a human looks).
- Red-team the prompt-injection paths (ticket notes, KB articles edited by many people, email content) on every release, using the eval suite.

## Evaluation in production
- Keep the scenario suite as a regression gate in CI (deterministic checks on every prompt or tool change; judge scores nightly).
- Sample real conversations weekly for human review; turn every reopened or mis-escalated case into a new scenario.
