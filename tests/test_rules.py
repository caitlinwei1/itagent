from evals import rules


def conversation(*replies, tools=(), said=("My VPN keeps dropping",)):
    events = [{"type": "user_message", "text": t} for t in said]
    events += [{"type": "tool_call", "name": n, "input": i, "result": r} for n, i, r in tools]
    return events + [{"type": "assistant_message", "text": t} for t in replies]


def found(events, context=""):
    return {f.rule for f in rules.check(events, context)}


def test_a_fact_must_come_from_the_evidence_or_the_employee():
    status = ("get_account_diagnostics", {"area": "devices"},
              {"text": "LT-2231: GlobalProtect version 6.0.1 installed. Approved version: 6.2.3."})
    assert not found(conversation("You're on GlobalProtect 6.0.1; the approved one is 6.2.3 (LT-2231).", tools=[status]))
    assert "number_not_in_evidence" in found(conversation("Update to GlobalProtect 6.3.", tools=[status]))
    assert "unknown_device" in found(conversation("Your phone PH-9999 has the old password.", tools=[status]))
    # arithmetic on the record is not a made-up fact
    assert not found(conversation("You're about 0.1 GB over the limit.", tools=[status]))
    # times: on the evidence's clock, said by the employee, or the session's "now"
    eta = ("check_system_status", {}, {"text": "ETA: 2026-10-22 20:00 UTC (Thu Oct 22 3:00 PM CDT local)"})
    assert not found(conversation("The fix is expected around 3:00 PM.", tools=[eta]))
    assert not found(conversation("You need it by 1:00 PM.", said=["need it by 1pm"]))
    assert not found(conversation("It's 10:30 AM for you now."), context="local time is 10:30 AM")
    assert "number_not_in_evidence" in found(conversation("The fix is expected around 4:00 PM.", tools=[eta]))


def test_a_reply_cannot_claim_an_action_that_did_not_run():
    assert "action_claimed_not_done" in found(conversation("I've unlocked your account."))
    unlocked = ("perform_action", {"action": "unlock_account"}, {"text": "Done. Okta account unlocked."})
    assert not found(conversation("I've unlocked your account.", tools=[unlocked]))
    refused = ("perform_action", {"action": "unlock_account"}, {"text": "Not done. Policy decision: precondition_failed."})
    assert "action_claimed_not_done" in found(conversation("I've unlocked your account.", tools=[refused]))
    assert not found(conversation("I can unlock it once you're verified."))  # an offer, not a claim


def test_promises_nothing_here_keeps_are_caught():
    assert "follow_up_promise" in found(conversation("I'll check back with you tomorrow."))
    assert "repeat_promise" in found(conversation("You won't need to explain it again."))


def test_the_reply_follows_the_employees_language_not_a_code():
    said = ["你好，我需要 Tableau 权限", "049154"]
    assert not found(conversation("身份验证通过了。", said=said))
    assert "wrong_language" in found(conversation("You're verified.", said=said))


def test_style_findings_are_soft():
    findings = rules.check(conversation("Is it every page? And since when?"))
    assert [(f.rule, f.hard) for f in findings] == [("several_questions", False)]
