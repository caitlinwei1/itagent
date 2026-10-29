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
