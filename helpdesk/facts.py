"""Facts about the signed-in employee, each computed in exactly one function.

Policy and actions call these to decide; tools call the same functions to describe the situation to the model.
Nothing else works a fact out on its own, so a decision and what the model was told can't disagree.
"""


def has_access(user: dict, resource_id: str) -> bool:
    """The employee already holds this catalog resource."""
    return resource_id in user["access"]


def eligible(user: dict, resource: dict) -> bool:
    """The employee's department is on the resource's eligible list ("all" means everyone)."""
    departments = resource.get("eligible_departments", [])
    return "all" in departments or user["department"] in departments


def approval_needed(user: dict, resource: dict) -> str:
    """What getting a catalog resource takes for this employee: "none" (granted on request), "manager",
    "manager+data_owner" or "not_requestable". An auto-approved item outside the employee's department needs the
    manager's approval."""
    approval = resource["approval"]
    if approval == "auto":
        return "none" if eligible(user, resource) else "manager"
    return approval


def managed_laptop(user: dict) -> dict | None:
    """The employee's managed laptop, the only device Self Service can push to."""
    return next((d for d in user["devices"] if d["type"] == "laptop" and d["managed"]), None)


def installed_version(device: dict | None, app: str) -> str | None:
    """The version of an app on one device; None when it isn't installed (the record may say "not installed")."""
    raw = (device or {}).get("software", {}).get(app)
    return None if raw in (None, "not installed") else raw


def software_state(user: dict, approved: dict[str, str], app: str) -> dict:
    """One app on the managed laptop: the installed version (None when not installed), the approved version
    (None when the app isn't approved), and whether the installed version is the approved one."""
    laptop = managed_laptop(user)
    installed = installed_version(laptop, app)
    return {"device": laptop["asset_id"] if laptop else None, "installed": installed, "approved": approved.get(app),
            "current": installed is not None and installed == approved.get(app)}


def mailbox_state(user: dict) -> dict | None:
    """Mailbox size against the mailbox's own three limits, from the mailbox record (None when there is no record):
    "ok", "warning" (Outlook warns), "send_blocked" (sending stops, receiving works) or "full" (both stop)."""
    m = user.get("diagnostics", {}).get("mailbox")
    if not m:
        return None
    used = m["size_gb"]
    state = ("full" if used >= m["quota_gb"] else "send_blocked" if used >= m["send_limit_gb"]
             else "warning" if used >= m["warning_gb"] else "ok")
    return {"as_of": m["measured_at"], "used_gb": used, "warning_gb": m["warning_gb"],
            "send_limit_gb": m["send_limit_gb"], "quota_gb": m["quota_gb"], "state": state}


def open_incidents(incidents: list[dict]) -> list[dict]:
    """The incidents that aren't resolved yet."""
    return [i for i in incidents if i["status"] != "resolved"]


def incident_covers(incident: dict, user: dict) -> bool:
    """The incident's affected locations include where the employee works ("all" means everyone)."""
    scope = incident["affected_locations"]
    return "all" in scope or any(loc in user["location"] for loc in scope)


def minutes_before(earlier_iso: str, later_iso: str) -> int:
    """How many minutes the first time is before the second (negative when it is after)."""
    from datetime import datetime
    parse = lambda t: datetime.fromisoformat(t.replace("Z", "+00:00"))  # noqa: E731
    return round((parse(later_iso) - parse(earlier_iso)).total_seconds() / 60)
