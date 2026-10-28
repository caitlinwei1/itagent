"""Runtime configuration, read from the environment (and an optional .env file)."""

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
VAR_DIR = ROOT / "var"


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader so the project needs no extra dependency for it."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0]  # inline comment, as in .env.example
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")

# USD per million tokens (input, output, cache read), used only for the cost estimate in traces.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}


@dataclass
class Settings:
    agent_model: str = field(default_factory=lambda: os.getenv("AGENT_MODEL", "claude-opus-5-5"))
    agent_effort: str = field(default_factory=lambda: os.getenv("AGENT_EFFORT", "medium"))
    simulator_model: str = field(default_factory=lambda: os.getenv("SIMULATOR_MODEL", "claude-haiku-5-5"))
    judge_model: str = field(default_factory=lambda: os.getenv("JUDGE_MODEL", "claude-opus-5-5"))
    # Per employee message: model calls and tool calls the agent may make before it must wrap up.
    max_model_calls_per_turn: int = int(os.getenv("MAX_MODEL_CALLS_PER_TURN", "10"))
    max_tool_calls_per_turn: int = int(os.getenv("MAX_TOOL_CALLS_PER_TURN", "14"))
    # Comma separated tool:mode pairs, e.g. "check_system_status:timeout,search_kb:flaky".
    faults: str = field(default_factory=lambda: os.getenv("FAULTS", ""))
    # The demo world is frozen at this time so "yesterday" and "last Friday" match the data.
    now: str = field(default_factory=lambda: os.getenv("DEMO_NOW", "2026-10-22T14:30:00Z"))
    use_fallbacks: bool = os.getenv("USE_FALLBACKS", "1") == "1"
