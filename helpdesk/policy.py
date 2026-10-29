"""Deterministic policy engine. Every state-changing tool call passes through `PolicyEngine.evaluate`.

The model can ask what policy says (check_policy) but cannot change the answer: decisions come from
data/policy.yaml plus facts code already knows (who is signed in, whether identity was verified).
"""

from dataclasses import dataclass, field

from . import facts
from .case import CaseFrame, consent_given


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
    "not_already_current": "The approved version is already installed; there is nothing to push.",
    "incident_covers_employee": "The records don't put the employee in this incident's scope: its affected locations "
                                "don't include where they work (or no open incident has that id). Troubleshoot their "
                                "own case instead.",
    "employee_consent": "Ask the employee whether they want this exact change, naming it, and wait for their answer. "
                        "Then pass their own words in consent_words. A verification code is not agreement.",
}


class PolicyEngine:
    def __init__(self, policy: dict, users: dict[str, dict], incidents: list[dict]):
        self.actions = policy["actions"]
        self.resources = policy["resources"]
        self.approved_software = policy["approved_software"]
        self.teams = policy["teams"]
        self.users = users
        self.incidents = incidents
        self.fix_actions = frozenset(a for a, rule in self.actions.items() if rule.get("fix"))

    def _precondition(self, name: str, user: dict, case: CaseFrame, target: str | None,
                      consent_words: str | None = None) -> bool:
        if name == "employee_consent":
            resource = self.resources.get(target or "", {})
            names = [target, resource.get("name"), (resource.get("name") or "").split(" ")[0]]
            return consent_given(case, consent_words, names)
        if name == "incident_covers_employee":
            incident = next((i for i in facts.open_incidents(self.incidents) if i["id"] == target), None)
            return incident is not None and facts.incident_covers(incident, user)
        if name == "identity_verified":
            return case.identity_verified
        if name == "account_locked":
            return user["account"]["status"] == "locked"
        if name == "account_not_suspended":
            return user["account"]["status"] != "suspended"
        if name == "package_approved":
            return facts.software_state(user, self.approved_software, target or "")["approved"] is not None
        if name == "device_managed":
            return facts.managed_laptop(user) is not None
        if name == "not_already_current":
            return not facts.software_state(user, self.approved_software, target or "")["current"]
        raise ValueError(f"unknown precondition {name}")

    def _approvers(self, resource: dict, user: dict) -> list[str]:
        manager = self.users.get(user.get("manager_id") or "", {}).get("name", "the employee's manager")
        approvers = [f"{manager} (manager)"]
        if resource["approval"] == "manager+data_owner":
            approvers.append(f"{resource.get('data_owner', 'the data owner')} (data owner)")
        return approvers

    def evaluate(self, action: str, user: dict, case: CaseFrame, target: str | None = None,
                 consent_words: str | None = None) -> Decision:
        rule = self.actions.get(action)
        if rule is None:
            return Decision("forbidden", f"'{action}' is not an action the helpdesk agent can take. "
                                         f"Known actions: {', '.join(sorted(self.actions))}.")
        if action in ("grant_access", "request_access_approval"):
            return self._access(action, rule, user, case, target, consent_words)

        decision = rule["decision"]
        if decision in ("human_only", "forbidden"):
            return Decision(decision, rule["description"], team=rule.get("team"))
        if missing := self._missing(rule, user, case, target, consent_words):
            return missing
        return Decision("allow", rule["description"])

    def _missing(self, rule: dict, user: dict, case: CaseFrame, target: str | None, consent_words: str | None):
        """The rule's `requires` that don't hold, as a precondition_failed decision (None when all hold)."""
        missing = [p for p in rule.get("requires", []) if not self._precondition(p, user, case, target, consent_words)]
        if missing:
            return Decision("precondition_failed", " ".join(PRECONDITION_HINTS[m] for m in missing), missing=missing)
        return None

    def _access(self, action: str, rule: dict, user: dict, case: CaseFrame, target: str | None,
                consent_words: str | None = None) -> Decision:
        resource = self.resources.get(target or "")
        if resource is None:
            return Decision("invalid", f"Unknown resource '{target}'. Catalog ids: {', '.join(sorted(self.resources))}.")
        if facts.has_access(user, target):
            return Decision("invalid", f"The employee already has {resource['name']}.")
        approval = resource["approval"]
        need = facts.approval_needed(user, resource)
        if need == "not_requestable":
            return Decision("human_only", f"{resource['name']} is not requestable by individuals "
                                          f"({resource.get('typical_turnaround', '')}).", team=resource.get("team"))
        auto_ok = need == "none"

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
        if missing := self._missing(rule, user, case, target, consent_words):
            return missing
        return Decision("allow", f"{resource['name']} is auto-approved for {user['department']}.")
