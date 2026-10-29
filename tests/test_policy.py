import pytest

from helpdesk.case import CaseFrame
from helpdesk.policy import PolicyEngine
from helpdesk.world import World


@pytest.fixture(scope="module")
def world():
    return World.load()


def engine(world):
    return PolicyEngine(world.policy, world.users)


def case(user_id, verified=False):
    return CaseFrame(case_id="t", user_id=user_id, identity_verified=verified)


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
    d = engine(world).evaluate("grant_access", world.users["u-alex"], case("u-alex", True), "grafana-viewer")
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
    assert e.evaluate("push_software_update", world.users["u-priya"], case("u-priya", True), "GlobalProtect").decision == "allow"
    assert e.evaluate("push_software_update", world.users["u-priya"], case("u-priya", True), "uTorrent").decision == "precondition_failed"


def test_policy_reads_employee_facts_only_through_facts_py():
    """One owner per fact: policy asks helpdesk/facts.py instead of reading the employee record itself."""
    from pathlib import Path
    text = Path("helpdesk/policy.py").read_text()
    assert 'user["access"]' not in text  # who holds what: facts.has_access
    assert '"all" in' not in text  # who is eligible: facts.eligible (listing the departments in a message is fine)


def test_facts():
    from helpdesk import facts
    user = {"access": ["jira"], "department": "Sales"}
    assert facts.has_access(user, "jira") and not facts.has_access(user, "grafana-viewer")
    assert facts.eligible(user, {"eligible_departments": ["all"]})
    assert facts.eligible(user, {"eligible_departments": ["Sales", "Marketing"]})
    assert not facts.eligible(user, {"eligible_departments": ["Engineering"]})
    assert not facts.eligible(user, {})  # no list means no one is auto-eligible
