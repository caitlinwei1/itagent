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
    saved = json.loads(md.with_suffix(".json").read_text())
    assert saved["run"] == meta and saved["results"] == [result]
