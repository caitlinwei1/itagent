"""Web chat for the demo: a chat pane plus a live panel with the agent's tool calls, case record and handoff.

    uvicorn helpdesk.web:app --port 8000      (or: python -m helpdesk.web)
"""

import json
import queue
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .agent import HelpdeskAgent
from .config import VAR_DIR, Settings
from .world import World

app = FastAPI(title="Acme IT Support Agent")
WORLD = World.load()
SESSIONS: dict[str, HelpdeskAgent] = {}
STATIC = Path(__file__).parent / "static"


class NewSession(BaseModel):
    user_id: str
    faults: str = ""


class ChatIn(BaseModel):
    session_id: str
    message: str


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/users")
def users() -> list[dict]:
    return [{"user_id": u["user_id"], "name": u["name"], "title": u["title"], "department": u["department"],
             "location": u["location"]} for u in WORLD.users.values() if u["devices"]]


@app.post("/api/session")
def new_session(body: NewSession) -> dict:
    if body.user_id not in WORLD.users:
        raise HTTPException(404, "unknown user")
    settings = Settings()
    settings.faults = body.faults
    session_id = uuid.uuid4().hex
    SESSIONS[session_id] = HelpdeskAgent(WORLD, body.user_id, settings)
    agent = SESSIONS[session_id]
    return {"session_id": session_id, "case_id": agent.case.case_id, "user": agent.user["name"]}


@app.post("/api/chat")
def chat(body: ChatIn) -> StreamingResponse:
    agent = SESSIONS.get(body.session_id)
    if agent is None:
        raise HTTPException(404, "unknown session")
    events: queue.Queue = queue.Queue()

    def work() -> None:
        agent.on_event = events.put
        try:
            result = agent.respond(body.message)
            events.put({"type": "done", "reply": result.reply, "notices": result.notices, "case": result.case,
                        "usage": result.usage})
        except Exception as exc:  # surface API errors in the UI instead of hanging the stream
            events.put({"type": "error", "message": f"{type(exc).__name__}: {exc}"})

    threading.Thread(target=work, daemon=True).start()

    def stream():
        while True:
            event = events.get()
            yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
            if event["type"] in ("done", "error"):
                break

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/tickets/{ticket_id}")
def ticket(ticket_id: str) -> dict:
    path = VAR_DIR / "tickets" / f"{ticket_id}.json"
    if not path.exists() or not ticket_id.startswith("IT-"):
        raise HTTPException(404, "no such ticket")
    return json.loads(path.read_text())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
