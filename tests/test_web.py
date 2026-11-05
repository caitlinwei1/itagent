from helpdesk import web


def test_reply_verification_can_be_switched_per_conversation(monkeypatch):
    monkeypatch.setattr(web, "CHECKER_MODEL", "claude-haiku-5-5")
    on = web.new_session(web.NewSession(user_id="u-maya"))
    off = web.new_session(web.NewSession(user_id="u-maya", verify=False))
    assert on["verify"] and not off["verify"] and on["verify_available"]
    assert web.SESSIONS[off["session_id"]].settings.checker_model == ""
    assert web.set_verify(web.VerifyIn(session_id=on["session_id"], on=False)) == {"verify": False}
    assert web.SESSIONS[on["session_id"]].settings.checker_model == ""  # applies from the next reply
    assert web.set_verify(web.VerifyIn(session_id=on["session_id"], on=True)) == {"verify": True}
    assert web.new_session(web.NewSession(user_id="u-maya"))["case_id"] != on["case_id"]  # each is its own case
