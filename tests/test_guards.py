from helpdesk.agent import HelpdeskAgent
from helpdesk.world import World


def make_agent():
    return HelpdeskAgent(World.load(), "u-priya", client=object())


def test_citing_unread_article_is_flagged():
    agent = make_agent()
    assert agent._check_reply("Update the client (KB-120).")
    agent.case.kb_articles_read.append("KB-120")
    assert agent._check_reply("Update the client (KB-120).") == []


def test_invented_ticket_numbers_are_flagged():
    agent = make_agent()
    assert agent._check_reply("I opened ticket IT-99999 for you.")
    assert agent._check_reply("This is the known incident INC-2051.") == []


def test_session_note_is_not_in_frozen_system_prompt():
    from helpdesk.prompts import SYSTEM_PROMPT
    assert "Priya" not in SYSTEM_PROMPT  # per-session facts would break prompt caching


def test_failed_turn_leaves_valid_history():
    import pytest

    agent = make_agent()

    def boom(*args, **kwargs):
        raise RuntimeError("API down")

    agent._call_model = boom
    with pytest.raises(RuntimeError):
        agent.respond("My VPN keeps dropping.")
    # A dangling system message here would make every retry a 400.
    assert agent.messages == []



def test_prompt_does_not_restate_policy():
    """policy.yaml owns which actions only a human may run; the prompt only says how to learn it."""
    from helpdesk.prompts import SYSTEM_PROMPT
    from helpdesk.world import World
    for action, rule in World.load().policy["actions"].items():
        assert action not in SYSTEM_PROMPT or rule["decision"] in ("allow", "by_resource"), action
    assert "human_only (MFA" not in SYSTEM_PROMPT


def _reply(text):
    from types import SimpleNamespace
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn")


def _usage():
    from types import SimpleNamespace
    return SimpleNamespace(usage=SimpleNamespace(input_tokens=1000, output_tokens=100, cache_read_input_tokens=0,
                                                 cache_creation_input_tokens=0))


def test_a_flagged_first_draft_is_rewritten_once_and_the_rewrite_goes_out(monkeypatch):
    """The checker sees the first draft only, so a turn costs at most one rewrite."""
    from helpdesk import checker
    agent = make_agent()
    drafts = iter([_reply("You can reply on the ticket any time."), _reply("The team will contact you.")])
    agent._call_model = lambda emit, allow_tools: next(drafts)
    seen = []

    def fake_checker(client, model, **kw):
        seen.append(kw["reply"])
        return ["'You can reply on the ticket any time.': nothing says the employee can reply"], _usage()

    monkeypatch.setattr(checker, "find_harmful_claims", fake_checker)
    events = []
    reply = agent._run_loop(events.append)
    assert reply == "The team will contact you." and seen == ["You can reply on the ticket any time."]
    assert "reply on the ticket" in agent.messages[-2]["content"]  # the note sent back for the rewrite
    assert agent.usage["cost_usd"] > 0 and any(e["type"] == "claim_check" for e in events)


def test_a_rewrite_is_checked_by_code_again(monkeypatch):
    """A checker note named KB-141; the rewrite cited it unread and went out, since rewrites skipped the code checks."""
    from helpdesk import checker
    agent = make_agent()
    drafts = iter([_reply("It's the pipeline, for sure."), _reply("KB-141 says it's usually the pipeline."),
                   _reply("It's usually the pipeline; I haven't confirmed it.")])
    agent._call_model = lambda emit, allow_tools: next(drafts)
    monkeypatch.setattr(checker, "find_harmful_claims",
                        lambda client, model, **kw: (["'for sure': KB-141 only says usually"], _usage()))
    assert agent._run_loop(lambda e: None) == "It's usually the pipeline; I haven't confirmed it."


def test_the_checker_never_blocks_a_reply(monkeypatch):
    """Off, or failing, it lets the draft through; a failure is counted so an eval can't miss that it never ran."""
    from helpdesk import checker
    agent = make_agent()

    def broken(client, model, **kw):
        raise RuntimeError("timeout")

    monkeypatch.setattr(checker, "find_harmful_claims", broken)
    assert agent._check_claims("Fine.", lambda e: None) == [] and agent.usage["checker_errors"] == 1
    agent.settings.checker_model = ""
    monkeypatch.setattr(checker, "find_harmful_claims", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert agent._check_claims("Fine.", lambda e: None) == []


def test_the_checker_gets_each_tool_call_with_its_result():
    from types import SimpleNamespace
    agent = make_agent()
    agent.messages = [
        {"role": "assistant", "content": [SimpleNamespace(type="tool_use", id="t1", name="search_kb", input={"query": "vpn"})]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": '{"results": []}'}]}]
    assert agent._tool_results() == ['search_kb({"query": "vpn"}) -> {"results": []}']


def test_the_checker_knows_what_the_agent_can_do():
    """Without its tools it flagged true offers ("I can push the Zoom update", "a code to your phone") as invented."""
    caps = "\n".join(make_agent()._capabilities())
    assert "perform_action push_software_update: allow" in caps and "registered phone" in caps
    assert "perform_action wipe_or_lock_device: human_only" in caps
