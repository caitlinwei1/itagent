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
                       case=CaseFrame(case_id="t", user_id="u-maya"), user=user, trace_id="t",
                       first_message="I can't log into Okta")


def verify(ctx):
    execute(ctx, "start_identity_verification", {})
    code = ctx.notices[-1]["text"].split()[-1]
    result, _ = execute(ctx, "confirm_identity_code", {"code": code})
    assert result["status"] == "verified"


def test_read_tools_return_evidence_ids(ctx):
    r1, _ = execute(ctx, "check_system_status", {"systems": ["sso"]})
    r2, _ = execute(ctx, "get_account_diagnostics", {"area": "sign_in"})
    assert (r1["evidence_id"], r2["evidence_id"]) == ("E1", "E2")
    assert r1["services"][0]["service"] == "Okta"


def test_kb_search_skips_deprecated_articles(ctx):
    result, _ = execute(ctx, "search_kb", {"query": "cisco anyconnect vpn disconnects"})
    ids = [r["article_id"] for r in result["results"]]
    assert "KB-099" not in ids and "KB-120" in ids


def test_status_marks_incident_scope_by_location(ctx):
    result, _ = execute(ctx, "check_system_status", {"systems": ["salesforce"]})
    inc = result["services"][0]["open_incidents"][0]
    assert inc["id"] == "INC-2051" and inc["applies_to_employee_location"] is False  # Maya is in New York


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


def test_resolved_requires_employee_confirmation(ctx):
    result, is_error = execute(ctx, "close_case", {"outcome": "resolved", "summary": "x", "employee_confirmed": False})
    assert is_error and ctx.case.status == "open"


def test_escalation_validates_and_computes_priority(ctx):
    execute(ctx, "get_account_diagnostics", {"area": "sign_in"})
    handoff = {"summary": "s", "impact": "low", "urgency": "high", "assignment_group": "Identity & Access",
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
