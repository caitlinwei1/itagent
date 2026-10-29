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
