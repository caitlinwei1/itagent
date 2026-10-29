import pytest

from helpdesk.case import CaseFrame, priority
from helpdesk.policy import PolicyEngine
from helpdesk.tools import ToolContext, execute, parse_faults
from helpdesk.world import World


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr("helpdesk.tools.VAR_DIR", tmp_path)
    world = World.load().fork()
    user = world.users["u-maya"]
    return ToolContext(world=world, policy=PolicyEngine(world.policy, world.users),
                       case=CaseFrame(case_id="t", user_id="u-maya"), user=user, trace_id="t")


def verify(ctx):
    execute(ctx, "start_identity_verification", {})
    code = ctx.notices[-1]["text"].split()[-1]
    result, _ = execute(ctx, "confirm_identity_code", {"code": code})
    assert result["status"] == "verified"


def test_read_tools_return_evidence_ids(ctx):
    r1, _ = execute(ctx, "check_system_status", {"systems": ["sso"]})
    r2, _ = execute(ctx, "get_account_diagnostics", {"area": "sign_in"})
    assert (r1["evidence_id"], r2["evidence_id"]) == ("E1", "E2")
    assert "Okta (owner:" in r1["text"] and ctx.case.evidence[0].record["services"][0]["service"] == "Okta"


def test_kb_search_skips_deprecated_articles(ctx):
    result, _ = execute(ctx, "search_kb", {"query": "cisco anyconnect vpn disconnects"})
    ids = [r["article_id"] for r in result["results"]]
    assert "KB-099" not in ids and "KB-120" in ids


def test_wrong_code_does_not_verify_and_locks_after_three(ctx):
    execute(ctx, "start_identity_verification", {})
    for _ in range(3):
        result, _ = execute(ctx, "confirm_identity_code", {"code": "000000x"})
    assert result["status"] == "failed_locked" and not ctx.case.identity_verified


def test_unlock_flow_changes_account_and_audits(ctx):
    blocked, _ = execute(ctx, "perform_action", {"action": "unlock_account", "justification": "locked"})
    assert blocked["executed"] is False
    verify(ctx)
    done, _ = execute(ctx, "perform_action", {"action": "unlock_account", "justification": "locked"})
    assert done["executed"] and ctx.user["account"]["status"] == "active"
    assert [a.executed for a in ctx.case.actions] == [False, True]


def test_fork_isolates_changes(ctx):
    verify(ctx)
    execute(ctx, "perform_action", {"action": "unlock_account", "justification": "x"})
    assert World.load().users["u-maya"]["account"]["status"] == "locked"


def test_confirmed_hypothesis_needs_real_evidence(ctx):
    result, _ = execute(ctx, "update_case", {"hypotheses": [{"id": "H1", "cause": "saved password", "status": "confirmed",
                                                             "evidence_refs": ["E9"]}]})
    assert ctx.case.hypotheses[0].status == "open" and result["warnings"]
    execute(ctx, "get_account_diagnostics", {"area": "sign_in"})
    execute(ctx, "update_case", {"hypotheses": [{"id": "H1", "cause": "saved password", "status": "confirmed",
                                                 "evidence_refs": ["E1"]}]})
    assert ctx.case.hypotheses[0].status == "confirmed"


def say(ctx, text):
    """What agent.respond() does with each employee message."""
    ctx.case.employee_messages.append(text)


def close_resolved(ctx, words):
    return execute(ctx, "close_case", {"outcome": "resolved", "summary": "x", "employee_words": words})


def test_resolved_needs_the_employees_own_words_after_the_fix(ctx):
    say(ctx, "I can't log into Okta, I'm locked out")
    verify(ctx)
    assert execute(ctx, "perform_action", {"action": "unlock_account", "justification": "locked"})[0]["executed"]
    assert close_resolved(ctx, None)[1] and ctx.case.status == "open"  # no words at all
    assert close_resolved(ctx, "it works now")[1]  # words the employee never sent
    assert close_resolved(ctx, "I'm locked out")[1]  # sent before the unlock, so it can't confirm it
    say(ctx, "Okay, I'm back in. Thanks!")
    result, is_error = close_resolved(ctx, "I'm back in")
    assert not is_error and ctx.case.status == "resolved"


def test_resolved_without_a_change_needs_words_after_the_opening(ctx):
    say(ctx, "Outlook stopped sending, it works now? no")
    assert close_resolved(ctx, "it works now")[1]  # the opening message can't confirm a fix
    say(ctx, "Emptied Deleted Items, sending works again")
    assert not close_resolved(ctx, "sending works again")[1]


def test_status_changes_only_in_transition():
    """One owner for the case status: nothing outside case.transition() assigns it."""
    import re
    from pathlib import Path
    from helpdesk.case import OUTCOME_GUARDS, Outcome
    from typing import get_args
    for path in Path("helpdesk").glob("*.py"):
        text = path.read_text()
        hits = re.findall(r"\bcase\.status\s*=[^=]", text)
        assert path.name == "case.py" and len(hits) == 1 if hits else True, path
    assert set(OUTCOME_GUARDS) | {"open"} == set(get_args(Outcome))  # every outcome has a guard entry


def test_escalation_validates_and_computes_priority(ctx):
    execute(ctx, "get_account_diagnostics", {"area": "sign_in"})
    execute(ctx, "update_case", {"urgency": "high"})  # the ticket's urgency comes from the case record
    handoff = {"summary": "s", "impact": "low", "assignment_group": "Identity & Access",
               "escalation_reason": "fix_failed", "user_impact": "cannot sign in", "steps_already_taken": [],
               "evidence": [{"evidence_id": "E7", "finding": "x"}], "question_for_specialist": "q",
               "recommended_next_action": "r"}
    bad, is_error = execute(ctx, "escalate_to_human", handoff)
    assert is_error and "E7" in bad["error"]
    handoff["evidence"] = [{"evidence_id": "E1", "finding": "locked by phone"}]
    ok, is_error = execute(ctx, "escalate_to_human", handoff)
    assert not is_error and ok["priority"] == "P3" and ctx.case.status == "escalated"
    dup, is_error = execute(ctx, "escalate_to_human", handoff)
    assert is_error


def test_security_incident_is_always_p1():
    assert priority("low", "low", security=True) == "P1"


def test_invalid_input_gets_actionable_error(ctx):
    result, is_error = execute(ctx, "get_account_diagnostics", {"area": "everything"})
    assert is_error and "area" in result["error"]


def test_fault_injection_retries_then_reports(ctx):
    ctx.faults = parse_faults("check_system_status:timeout,search_kb:flaky")
    down, is_error = execute(ctx, "check_system_status", {"systems": ["okta"]})
    assert is_error and "Do not guess" in down["error"]
    flaky, is_error = execute(ctx, "search_kb", {"query": "okta locked"})
    assert not is_error and flaky["results"]


def test_outcome_names_and_guard_actions_have_one_owner():
    """Outcomes are defined by case.Outcome; the other mentions must match it. Actions belong to policy.yaml."""
    from typing import get_args
    from helpdesk.case import OUTCOME_GUARDS, Outcome
    from helpdesk.prompts import SYSTEM_PROMPT
    from helpdesk.tools import CloseCase, TOOLS_BY_NAME
    from helpdesk.world import World
    closable = set(get_args(CloseCase.model_fields["outcome"].annotation))
    assert closable == set(get_args(Outcome)) - {"open", "escalated"}  # escalation has its own tool
    for text in (SYSTEM_PROMPT, TOOLS_BY_NAME["close_case"].description):
        assert all(name in text for name in closable), text[:60]
    policy_actions = set(World.load().policy["actions"])
    import inspect, re
    used = set(re.findall(r'_done\("(\w+)"', inspect.getsource(__import__("helpdesk.case", fromlist=["x"]))))
    assert used and used <= policy_actions  # a renamed action in policy.yaml fails here, not silently


def test_devices_are_described_in_words_from_the_fact_functions(ctx):
    """One line per app: what is installed, then the approved version, so the two can't be read as one."""
    ctx.user = ctx.world.users["u-ken"]
    result, _ = execute(ctx, "get_account_diagnostics", {"area": "devices"})
    assert set(result) == {"evidence_id", "text"}  # the model and the judge see the text only
    assert "- Docker Desktop: not installed. Approved version: 4.38." in result["text"]
    assert "Approved list" not in result["text"]
    assert ctx.case.evidence[-1].record["devices"]  # the raw record is kept with the evidence


def test_a_push_to_an_app_already_at_the_approved_version_is_refused(ctx):
    ctx.user = ctx.world.users["u-ken"]  # GlobalProtect 6.2.3 is already the approved version
    verify(ctx)
    say(ctx, "my VPN and Docker")
    ctx.case.agent_replies = ["I can push GlobalProtect or Docker Desktop. Shall I?"]
    say(ctx, "yes please")
    same, _ = execute(ctx, "perform_action", {"action": "push_software_update", "target": "GlobalProtect",
                                              "justification": "x", "consent_words": "yes please"})
    assert same["executed"] is False and "not_already_current" in same["missing_preconditions"]
    pushed, _ = execute(ctx, "perform_action", {"action": "push_software_update", "target": "Docker Desktop",
                                                "justification": "x", "consent_words": "yes please"})
    assert pushed["executed"] and pushed["readback"]["installed_version_now"] == "4.38"  # read again after the write


def test_software_facts():
    from helpdesk import facts
    approved = {"Docker Desktop": "4.38", "GlobalProtect": "6.2.3"}
    user = {"devices": [{"type": "laptop", "managed": True, "asset_id": "LT-1",
                         "software": {"Docker Desktop": "not installed", "GlobalProtect": "6.0.1"}}]}
    assert facts.software_state(user, approved, "Docker Desktop")["installed"] is None
    assert facts.software_state(user, approved, "GlobalProtect") == {"device": "LT-1", "installed": "6.0.1",
                                                                     "approved": "6.2.3", "current": False}
    assert facts.software_state(user, approved, "Excel")["approved"] is None


def test_mailbox_is_described_against_the_quota(ctx):
    from helpdesk import facts
    ctx.user = ctx.world.users["u-jordan"]
    result, _ = execute(ctx, "get_account_diagnostics", {"area": "mailbox"})
    m = facts.mailbox_state(ctx.user)
    assert set(result) == {"evidence_id", "text"}
    assert f"{m['used_gb']} GB used of a {m['quota_gb']} GB quota" in result["text"]  # the text shows the fact's values
    assert "0.4 GB left" in result["text"] and "has not been reached" in result["text"]
    assert facts.mailbox_state({"diagnostics": {"mailbox": {"size_gb": 50, "quota_gb": 50}}})["over_quota"]
    assert facts.mailbox_state({"diagnostics": {}}) is None


def test_status_says_whether_an_incident_covers_the_employee_and_when_changes_happened(ctx):
    from helpdesk import facts
    ctx.user = ctx.world.users["u-david"]  # Chicago
    result, _ = execute(ctx, "check_system_status", {"systems": ["salesforce"]})
    inc = next(i for i in ctx.world.incidents if i["id"] == "INC-2051")
    assert facts.incident_covers(inc, ctx.user) and "this covers your location" in result["text"]
    assert "CHG-4420 on 2026-10-21 13:30 UTC" in result["text"] and "This was 40 minutes before INC-2051 started" in result["text"]
    assert "cause" not in result["text"].lower()  # facts only: what changed and when, not what it caused
    ctx.user = ctx.world.users["u-maya"]  # not in Chicago
    assert "does not cover your location" in execute(ctx, "check_system_status", {"systems": ["salesforce"]})[0]["text"]
    assert facts.minutes_before("2026-10-21T13:30:00Z", "2026-10-21T14:10:00Z") == 40


def test_access_says_what_is_already_held_and_what_getting_the_rest_takes(ctx):
    ctx.user = ctx.world.users["u-ken"]  # Engineering; already holds grafana-viewer
    text = execute(ctx, "get_account_diagnostics", {"area": "access"})[0]["text"]
    assert "Already held:" in text and "(grafana-viewer)" in text.split("\n")[1]
    assert "- Grafana dashboards (viewer) (grafana-viewer): not held" not in text  # held items are not offered
    assert "(snowflake-prod-read): not held; needs the manager's and the data owner's approval" in text
    assert "(salesforce-standard): not held; needs the manager's approval" in text


def test_linking_to_an_incident_promises_no_updates(ctx):
    result, _ = execute(ctx, "perform_action", {"action": "add_user_to_incident", "target": "INC-2051",
                                                "justification": "affected"})
    assert result["executed"] and "subscribed" not in result["detail"] and "Nothing here sends" in result["detail"]


def test_ticket_urgency_comes_from_the_case_and_its_deadline():
    from helpdesk.case import handoff_urgency
    case = CaseFrame(case_id="t", user_id="u", urgency="normal")
    now = "2026-10-22T14:30:00Z"
    assert handoff_urgency(case, now) == "medium"
    case.deadline = "2026-10-23T08:00:00Z"  # tomorrow morning
    assert handoff_urgency(case, now) == "high"
    case.deadline = "2026-10-30T08:00:00Z"
    assert handoff_urgency(case, now) == "medium"
    case.deadline = "tomorrow morning"  # unparseable: left as it is
    assert handoff_urgency(case, now) == "medium"
