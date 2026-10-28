"""Terminal chat with the agent.

    python -m helpdesk.cli                 # pick an employee, then chat
    python -m helpdesk.cli --user u-priya  # start as a given employee
    python -m helpdesk.cli --trace         # also show tool calls and reasoning summaries
Commands inside the chat: /case (show the case record), /quit.
"""

import argparse
import json

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from .agent import HelpdeskAgent
from .config import Settings
from .world import World

console = Console()


def _show_event(event: dict) -> None:
    kind = event["type"]
    if kind == "tool_call":
        args = json.dumps(event["input"], ensure_ascii=False)
        flag = "[red]error[/]" if event["is_error"] else "ok"
        console.print(f"  [dim]tool {event['name']}({args[:140]}) -> {flag}[/]")
    elif kind == "thinking":
        console.print(f"  [dim italic]thinking: {event['text'][:300]}[/]")
    elif kind == "guard":
        console.print(f"  [yellow]quality check sent the reply back: {' '.join(event['problems'])}[/]")


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat with the IT helpdesk agent.")
    parser.add_argument("--user", help="employee id, e.g. u-maya")
    parser.add_argument("--trace", action="store_true", help="show tool calls and reasoning summaries")
    args = parser.parse_args()

    world = World.load()
    employees = [u for u in world.users.values() if u["devices"]]
    user_id = args.user
    if not user_id:
        console.print("[bold]Sign in as (mock SSO):[/]")
        for i, u in enumerate(employees, 1):
            console.print(f"  {i}. {u['name']} - {u['title']}, {u['department']}, {u['location']} ({u['user_id']})")
        choice = Prompt.ask("Number", default="1")
        user_id = employees[int(choice) - 1]["user_id"]

    agent = HelpdeskAgent(world, user_id, Settings(), on_event=_show_event if args.trace else None)
    console.print(Panel(f"Signed in as {agent.user['name']}. Describe your IT problem. /case shows the case record, "
                        f"/quit exits.", title="Acme IT Support"))
    while True:
        text = Prompt.ask("[bold cyan]You[/]").strip()
        if not text:
            continue
        if text in ("/quit", "/exit"):
            break
        if text == "/case":
            console.print_json(agent.case.model_dump_json())
            continue
        with console.status("Checking..."):
            result = agent.respond(text)
        for notice in result.notices:
            console.print(f"[magenta]{notice['text']}[/]")
        console.print(Panel(result.reply, title="IT Support", border_style="green"))
        if agent.case.status != "open":
            console.print(f"[dim]Case {agent.case.case_id}: {agent.case.status}"
                          f"{' ' + agent.case.ticket_id if agent.case.ticket_id else ''}. "
                          f"Cost so far ${result.usage['cost_usd']}.[/]")


if __name__ == "__main__":
    main()
