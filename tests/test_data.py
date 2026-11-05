import json
from pathlib import Path

from helpdesk import facts

USERS = json.loads(Path("data/users.json").read_text())


def test_every_employee_can_be_signed_in_as_in_the_demo():
    """The web picker lists everyone, so every record has what the tools read: a managed laptop, an MFA factor and
    a manager who exists (one person, the CEO, has none)."""
    ids = {u["user_id"] for u in USERS}
    for u in USERS:
        assert facts.managed_laptop(u), u["user_id"]
        assert u["account"]["mfa_factors"], u["user_id"]
        assert u["manager_id"] is None or u["manager_id"] in ids, u["user_id"]
    assert [u["user_id"] for u in USERS if u["manager_id"] is None] == ["u-erin"]
    assert len({d["asset_id"] for u in USERS for d in u["devices"]}) == sum(len(u["devices"]) for u in USERS)
