"""Deterministic policy engine. Every state-changing tool call passes through `PolicyEngine.evaluate`.

The model can ask what policy says (check_policy) but cannot change the answer: decisions come from
data/policy.yaml plus facts code already knows (who is signed in, whether identity was verified).
"""

from dataclasses import dataclass, field

from .case import CaseFrame


@dataclass
class Decision:
    decision: str  # allow | needs_approval | human_only | forbidden | precondition_failed | invalid
    reason: str
    team: str | None = None
    approvers: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        out = {"decision": self.decision, "reason": self.reason}
        if self.team:
            out["team"] = self.team
        if self.approvers:
            out["approvers"] = self.approvers
        if self.missing:
            out["missing_preconditions"] = self.missing
        return out


PRECONDITION_HINTS = {
    "identity_verified": "Verify identity first: start_identity_verification, then confirm_identity_code.",
    "account_locked": "The account is not locked, so there is nothing to unlock.",
    "account_not_suspended": "The account is suspended; only Identity & Access can act on it.",
    "package_approved": "That software or version is not on the approved list.",
    "device_managed": "The employee has no managed laptop for Self Service to push to.",
}


class PolicyEngine:
    def __init__(self, policy: dict, users: dict[str, dict]):
        self.actions = policy["actions"]
        self.resources = policy["resources"]
        self.approved_software = policy["approved_software"]
        self.teams = policy["teams"]
        self.users = users

    def _precondition(self, name: str, user: dict, case: CaseFrame, target: str | None) -> bool:
        if name == "identity_verified":
            return case.identity_verified
        if name == "account_locked":
            return user["account"]["status"] == "locked"
        if name == "account_not_suspended":
            return user["account"]["status"] != "suspended"
        if name == "package_approved":
            return target in self.approved_software
        if name == "device_managed":
            return any(d["type"] == "laptop" and d["managed"] for d in user["devices"])
        raise ValueError(f"unknown precondition {name}")

    def _eligible(self, resource: dict, user: dict) -> bool:
        eligible = resource.get("eligible_departments", [])
        return "all" in eligible or user["department"] in eligible

    def _approvers(self, resource: dict, user: dict) -> list[str]:
        manager = self.users.get(user.get("manager_id") or "", {}).get("name", "the employee's manager")
        approvers = [f"{manager} (manager)"]
        if resource["approval"] == "manager+data_owner":
            approvers.append(f"{resource.get('data_owner', 'the data owner')} (data owner)")
        return approvers

    def evaluate(self, action: str, user: dict, case: CaseFrame, target: str | None = None) -> Decision:
        rule = self.actions.get(action)
        if rule is None:
            return Decision("forbidden", f"'{action}' is not an action the helpdesk agent can take. "
                                         f"Known actions: {', '.join(sorted(self.actions))}.")
        if action in ("grant_access", "request_access_approval"):
            return self._access(action, rule, user, case, target)

        decision = rule["decision"]
        if decision in ("human_only", "forbidden"):
            return Decision(decision, rule["description"], team=rule.get("team"))
        missing = [p for p in rule.get("requires", []) if not self._precondition(p, user, case, target)]
        if missing:
            return Decision("precondition_failed", " ".join(PRECONDITION_HINTS[m] for m in missing), missing=missing)
        return Decision("allow", rule["description"])

    def _access(self, action: str, rule: dict, user: dict, case: CaseFrame, target: str | None) -> Decision:
        resource = self.resources.get(target or "")
        if resource is None:
            return Decision("invalid", f"Unknown resource '{target}'. Catalog ids: {', '.join(sorted(self.resources))}.")
        if target in user["access"]:
            return Decision("invalid", f"The employee already has {resource['name']}.")
        approval = resource["approval"]
        if approval == "not_requestable":
            return Decision("human_only", f"{resource['name']} is not requestable by individuals "
                                          f"({resource.get('typical_turnaround', '')}).", team=resource.get("team"))
        auto_ok = approval == "auto" and self._eligible(resource, user)

        if action == "request_access_approval":
            if auto_ok:
                return Decision("invalid", f"{resource['name']} is auto-approved for {user['department']}; use grant_access instead.")
            return Decision("allow", f"File an approval request for {resource['name']}; usual turnaround "
                                     f"{resource.get('typical_turnaround', 'unknown')}.",
                            approvers=self._approvers(resource, user) if approval != "auto" else
                            [f"{self.users.get(user.get('manager_id') or '', {}).get('name', 'the manager')} (manager, department not on the eligible list)"])

        # grant_access
        if not auto_ok:
            approvers = self._approvers(resource, user)
            why = "requires approval" if approval != "auto" else f"is auto-approved only for {', '.join(resource['eligible_departments'])}"
            return Decision("needs_approval", f"{resource['name']} {why}. File request_access_approval instead; "
                                              f"usual turnaround {resource.get('typical_turnaround', 'unknown')}.",
                            approvers=approvers)
        if not case.identity_verified:
            return Decision("precondition_failed", PRECONDITION_HINTS["identity_verified"], missing=["identity_verified"])
        return Decision("allow", f"{resource['name']} is auto-approved for {user['department']}.")
