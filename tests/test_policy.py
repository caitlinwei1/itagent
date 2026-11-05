import pytest

from helpdesk.case import CaseFrame, Evidence
from helpdesk.policy import PolicyEngine
from helpdesk.world import World


@pytest.fixture(scope="module")
def world():
    return World.load()


def engine(world):
    return PolicyEngine(world.policy, world.users, world.incidents)


def case(user_id, verified=False, offered=None):
    """A case; with `offered`, the agent named that change and the employee answered "yes, go ahead"."""
    c = CaseFrame(case_id="t", user_id=user_id, identity_verified=verified)
    if offered:
        c.employee_messages = ["help", "yes, go ahead"]
        c.agent_replies = [f"Shall I install {offered} for you?"]
    return c


def test_unlock_requires_identity_verification(world):
    user = world.users["u-maya"]
    assert engine(world).evaluate("unlock_account", user, case("u-maya")).decision == "precondition_failed"
    assert engine(world).evaluate("unlock_account", user, case("u-maya", True)).decision == "allow"


def test_unlock_refused_when_not_locked(world):
    d = engine(world).evaluate("unlock_account", world.users["u-priya"], case("u-priya", True))
    assert d.decision == "precondition_failed" and d.missing == ["account_locked"]


def test_mfa_reset_and_suspended_account_are_human_only(world):
    e = engine(world)
    assert e.evaluate("reset_mfa_factor", world.users["u-ken"], case("u-ken", True)).team == "Identity & Access"
    assert e.evaluate("reactivate_account", world.users["u-olivia"], case("u-olivia", True)).decision == "human_only"


def test_local_admin_is_forbidden_even_when_verified(world):
    assert engine(world).evaluate("grant_local_admin", world.users["u-ken"], case("u-ken", True)).decision == "forbidden"


def test_unknown_action_is_forbidden(world):
    assert engine(world).evaluate("delete_all_users", world.users["u-ken"], case("u-ken", True)).decision == "forbidden"


def test_auto_access_for_eligible_department(world):
    d = engine(world).evaluate("grant_access", world.users["u-alex"], case("u-alex", True, "Grafana viewer"),
                               "grafana-viewer", "yes, go ahead")
    assert d.decision == "allow"


def test_auto_access_still_needs_identity(world):
    d = engine(world).evaluate("grant_access", world.users["u-alex"], case("u-alex"), "grafana-viewer")
    assert d.decision == "precondition_failed"


def test_auto_resource_for_ineligible_department_needs_approval(world):
    d = engine(world).evaluate("grant_access", world.users["u-priya"], case("u-priya", True), "grafana-viewer")
    assert d.decision == "needs_approval"


def test_snowflake_prod_needs_manager_and_data_owner(world):
    d = engine(world).evaluate("grant_access", world.users["u-alex"], case("u-alex", True), "snowflake-prod-read")
    assert d.decision == "needs_approval"
    assert any("Lena Park" in a for a in d.approvers) and any("data owner" in a for a in d.approvers)


def test_snowflake_prod_write_not_requestable(world):
    e = engine(world)
    for action in ("grant_access", "request_access_approval"):
        assert e.evaluate(action, world.users["u-alex"], case("u-alex", True), "snowflake-prod-write").decision == "human_only"


def test_existing_access_and_unknown_resource_are_invalid(world):
    e = engine(world)
    assert e.evaluate("grant_access", world.users["u-sam"], case("u-sam", True), "snowflake-dev").decision == "invalid"
    assert e.evaluate("grant_access", world.users["u-sam"], case("u-sam", True), "prod-db").decision == "invalid"


def test_software_push_only_for_approved_packages(world):
    e = engine(world)
    assert e.evaluate("push_software_update", world.users["u-priya"], case("u-priya", True, "GlobalProtect"),
                      "GlobalProtect", "yes, go ahead").decision == "allow"
    assert e.evaluate("push_software_update", world.users["u-priya"], case("u-priya", True), "uTorrent").decision == "precondition_failed"


def test_policy_reads_employee_facts_only_through_facts_py():
    """One owner per fact: policy asks helpdesk/facts.py instead of reading the employee record itself."""
    from pathlib import Path
    text = Path("helpdesk/policy.py").read_text()
    assert 'user["access"]' not in text  # who holds what: facts.has_access
    assert '"all" in' not in text  # who is eligible: facts.eligible (listing the departments in a message is fine)
    assert '== "auto" and' not in text  # what an item takes: facts.approval_needed
    assert "in self.approved_software" not in text and 'd["managed"]' not in text  # software: facts.software_state


def test_facts():
    from helpdesk import facts
    user = {"access": ["jira"], "department": "Sales"}
    assert facts.has_access(user, "jira") and not facts.has_access(user, "grafana-viewer")
    assert facts.eligible(user, {"eligible_departments": ["all"]})
    assert facts.eligible(user, {"eligible_departments": ["Sales", "Marketing"]})
    assert not facts.eligible(user, {"eligible_departments": ["Engineering"]})
    assert not facts.eligible(user, {})  # no list means no one is auto-eligible


def test_a_change_needs_the_employees_agreement_to_that_change(world):
    e, user = engine(world), world.users["u-priya"]
    push = lambda c, words: e.evaluate("push_software_update", user, c, "GlobalProtect", words)  # noqa: E731
    assert push(case("u-priya", True, "GlobalProtect"), "yes, go ahead").decision == "allow"
    code_only = case("u-priya", True, "GlobalProtect")
    code_only.employee_messages = ["help", "622854"]
    assert "employee_consent" in push(code_only, "622854").missing  # a verification code is not agreement
    assert "employee_consent" in push(case("u-priya", True, "GlobalProtect"), "sure thing").missing  # not their words
    assert "employee_consent" in push(case("u-priya", True, "Zoom"), "yes, go ahead").missing  # agreed to something else
    assert "employee_consent" in push(case("u-priya", True), None).missing  # nobody asked


def test_approval_needed(world):
    from helpdesk import facts
    r = world.policy["resources"]
    assert facts.approval_needed(world.users["u-alex"], r["grafana-viewer"]) == "none"  # Data Engineering
    assert facts.approval_needed(world.users["u-priya"], r["grafana-viewer"]) == "manager"  # not eligible
    assert facts.approval_needed(world.users["u-alex"], r["snowflake-prod-read"]) == "manager+data_owner"
    assert facts.approval_needed(world.users["u-alex"], r["snowflake-prod-write"]) == "not_requestable"


def test_linking_to_an_incident_needs_the_records_and_the_employees_own_words(world):
    e = engine(world)

    def link(uid, said, words, found_in_turn=1):
        c = case(uid)
        c.employee_messages = said
        if found_in_turn:  # the agent's status lookup in that turn showed the incident
            c.evidence = [Evidence(id="E1", tool="check_system_status", args={}, summary="INC-2051 open",
                                   turn=found_in_turn)]
        return e.evaluate("add_user_to_incident", world.users[uid], c, "INC-2051", situation_words=words)

    office = ["Salesforce is slow", "I'm in the Chicago office on the office wifi"]
    assert link("u-david", office, "in the Chicago office on the office wifi").decision == "allow"
    assert link("u-priya", office, "in the Chicago office").missing == ["incident_covers_employee"]  # Austin
    # The record puts David in Chicago, but that alone doesn't say the incident applies to him today
    assert link("u-david", ["Salesforce is slow"], None).missing == ["employee_describes_incident"]
    assert link("u-david", office, "I'm on the Chicago office network").missing == ["employee_describes_incident"]
    # The opening came before the agent knew of the incident, so it can't be the confirmation
    opening = ["Salesforce is slow here in the Chicago office", "yes"]
    assert link("u-david", opening, "here in the Chicago office").missing == ["employee_describes_incident"]
    assert link("u-david", office, "in the Chicago office", found_in_turn=None).missing == ["employee_describes_incident"]


def test_someone_with_no_manager_has_a_named_approver(world):
    """The CEO's manager-approval requests read "the employee's manager (manager)"; policy now names who approves."""
    e, erin = engine(world), world.users["u-erin"]
    d = e.evaluate("request_access_approval", erin, case("u-erin"), "tableau-creator")
    assert d.decision == "allow" and d.approvers == ["Identity & Access (no manager on record)"]
    jordan = e.evaluate("request_access_approval", world.users["u-jordan"], case("u-jordan"), "tableau-creator")
    assert jordan.approvers == ["Mark Evans (manager)"]
