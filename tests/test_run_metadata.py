import json

from evals import run_evals


def test_a_report_says_what_produced_it(tmp_path, monkeypatch):
    """Two reports are comparable only when commit, models, prompt and scenarios match, so each report carries them."""
    monkeypatch.setattr(run_evals, "HERE", tmp_path)
    (tmp_path / "scenarios.yaml").write_text("- id: x\n")
    meta = run_evals.run_metadata(repeats=3, use_judge=False, only=["x"])
    assert meta["commit"] and meta["agent_model"] and meta["judge_model"] is None and len(meta["prompt_hash"]) == 12
    result = {"id": "x", "passed": True, "failures": [], "transcript": []}
    md = run_evals.write_report([result], 3, meta)
    assert f"commit {meta['commit']}" in md.read_text() and "judge off" in md.read_text()
    assert "scenarios scenarios.yaml" in md.read_text()  # which file: scenarios.yaml or the held-out set
    saved = json.loads(md.with_suffix(".json").read_text())
    assert saved["run"] == meta and saved["results"] == [result]


def test_the_judge_sees_the_rules_the_agent_was_given():
    """Without them the judge called "I can only see the signed-in employee's records" ungrounded (held-out run 1)."""
    assert "{rules}" in run_evals.JUDGE_PROMPT and "standing instructions" in run_evals.Judgement.model_fields["grounded"].description


def test_the_report_counts_harmful_claims_apart(tmp_path, monkeypatch):
    """A true but unlabeled tip and an invented process both fail grounding; the report counts the harmful ones."""
    monkeypatch.setattr(run_evals, "HERE", tmp_path)
    (tmp_path / "scenarios.yaml").write_text("- id: x\n")
    judge = {"grounded": False, "unsupported_claims": ["tip", "reply on the ticket"], "harmful_claims": ["reply on the ticket"],
             "notes": ""}
    result = {"id": "x", "passed": False, "failures": [], "transcript": [], "judge": judge}
    md = run_evals.write_report([result], 1, run_evals.run_metadata(repeats=1, use_judge=True, only=None))
    assert "runs with a harmful claim 1/1 (1 claims)" in md.read_text()


def test_the_report_splits_cost_by_model_and_counts_checker_flags(tmp_path, monkeypatch):
    """The cost line used to cover the agent only, so what the judge and the simulator spent was invisible."""
    monkeypatch.setattr(run_evals, "HERE", tmp_path)
    (tmp_path / "scenarios.yaml").write_text("- id: x\n")
    result = {"id": "x", "passed": True, "failures": [], "transcript": [], "cost_usd": 0.05, "checker_cost_usd": 0.01,
              "simulator_cost_usd": 0.002, "judge_cost_usd": 0.09, "judge_output_tokens": 2500,
              "checker": {"checker_calls": 3, "checker_flagged": 1, "checker_errors": 0}}
    text = run_evals.write_report([result], 1, run_evals.run_metadata(repeats=1, use_judge=True, only=None)).read_text()
    assert "agent $0.050 (of which checker $0.010), simulated employee $0.002, judge $0.090 (2500 output tokens)" in text
    assert "total $0.142" in text and "flagged 1 of 3 first drafts; 0 errors" in text


def test_a_failing_judge_keeps_the_finished_conversation(monkeypatch):
    """The API ran out of credit mid-run; the judge's exception ended the suite and every finished run was lost."""
    from types import SimpleNamespace
    agent = SimpleNamespace(case=SimpleNamespace(status="answered", turns=1, actions=[], ticket_id=None),
                            _session_note="")
    run = {"agent": agent, "events": [], "replies": ["ok"], "transcript": [], "handoff": None, "seconds": 1.0,
           "usage": {"cost_usd": 0.01, "checker_cost_usd": 0.0, "tool_calls": 0, "checker_calls": 0,
                     "checker_flagged": 0, "checker_errors": 0, "checker_skipped": 0}, "simulator_cost_usd": 0.0}
    monkeypatch.setattr(run_evals, "run_conversation", lambda *a: run)

    def no_credit(*a):
        raise RuntimeError("credit balance is too low")

    monkeypatch.setattr(run_evals, "judge", no_credit)
    result = run_evals.run_one(None, None, {"id": "x", "expect": {"outcome": ["answered"]}}, "all", 3)
    assert result["passed"] and result["judge"] is None and "credit" in result["judge_error"]
