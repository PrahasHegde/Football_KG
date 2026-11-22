"""
Football KG web application.

    uvicorn server.main:app --reload          # API on http://localhost:8000
    (cd web && npm run dev)                    # UI with hot reload on http://localhost:5173

After `npm run build` in web/, the API also serves the built UI at http://localhost:8000.
"""
from __future__ import annotations

import json
import threading
import os
from pathlib import Path
from typing import Iterator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from .agent import MODEL, Agent  # noqa: E402  (reads env vars at import)
from .media import CONTACT, OFFLINE, Media  # noqa: E402
from .store import LABELS, Store  # noqa: E402

store = Store()
agent = Agent(store)
media = Media(store.q("SELECT team_id AS id, name, matches FROM teams"))
# Resolve every team's flag + crest in the background (one-off; cached in app_data/media.sqlite).
media.clubs = store.q("SELECT club_id AS id, tm_club_id AS tm, name FROM clubs")
if OFFLINE:
    # no network: serve whatever is cached, never wait for a warm-up that can't happen
    media._teams_ready.set()
    media._clubs_ready.set()
else:
    threading.Thread(target=media.warm_all, daemon=True).start()  # teams, then club logos (serial)
    # Pre-fetch top scorers at low priority so famous players already have photos (fewer when we're on
    # Wikimedia's slow anonymous tier, so the warm-up doesn't delay what you actually look at).
    media.enqueue(store.q("SELECT player_id AS id, name, team FROM players ORDER BY goals DESC LIMIT ?",
                          [300 if CONTACT else 40]), priority=5)

app = FastAPI(title="Football KG", version="1.0.0")


# ---------------------------------------------------------------- media (Wikidata / Wikipedia)
@app.get("/api/media/teams")
def media_teams() -> dict:
    """Flag + crest URLs for every team; `ready` is false while the first-ever lookup is running."""
    ready = media._teams_ready.is_set()
    return {"ready": ready, "teams": media.team_images() if ready else {}}


class IdsRequest(BaseModel):
    ids: list[str] = Field(..., max_length=60)


@app.get("/api/media/clubs")
def media_clubs() -> dict:
    ready = media._clubs_ready.is_set()
    return {"ready": ready, "clubs": media.club_images() if ready else {}}


@app.post("/api/media/players")
def media_players(req: IdsRequest) -> dict:
    """Photos for players on the canvas: cached ones now, the rest queued (`pending`) for a re-poll."""
    rows = store.players_info([i for i in req.ids if i.startswith("P:")])
    return media.players(rows)


@app.get("/api/media/entity/{node_id:path}")
def media_entity(node_id: str) -> dict:
    """Rich media for the Inspector: player bio + clubs, or team flag + crest + summary."""
    n = store.node(node_id)
    if n is None:
        raise HTTPException(404, f"No entity {node_id}")
    if n["label"] == "Player":
        info = store.players_info([node_id])
        if info:
            return {"player": Media.player_public(media.player(node_id, info[0]["name"], info[0]["team"]))}
    if n["label"] == "Team":
        return {"team": media.team_detail(node_id, n["name"])}
    return {}


@app.get("/api/img", include_in_schema=False)
def image(u: str) -> FileResponse:
    """Same-origin, disk-cached proxy for Wikimedia images (keeps the canvas exportable)."""
    path = media.image_file(u)
    if path is None:
        raise HTTPException(404, "Image unavailable")
    return FileResponse(path, headers={"Cache-Control": "public, max-age=2592000, immutable"})


# ---------------------------------------------------------------- graph API
@app.get("/api/meta")
def meta() -> dict:
    return {
        **store.meta,
        "agent": {"model": MODEL, "provider": "Groq", "api_key_set": bool(os.getenv("GROQ_API_KEY"))},
    }


@app.get("/api/search")
def search(q: str = Query(..., min_length=1), label: str | None = None, limit: int = Query(12, le=50)) -> list[dict]:
    if label and label not in LABELS:
        raise HTTPException(400, f"label must be one of {LABELS}")
    return store.search(q, label, limit)


@app.get("/api/entity/{node_id:path}")
def entity(node_id: str) -> dict:
    e = store.entity(node_id)
    if e is None:
        raise HTTPException(404, f"No entity {node_id}")
    return e


@app.get("/api/expand/{node_id:path}")
def expand(node_id: str, limit: int = Query(12, ge=1, le=200), type: str | None = None,
           direction: str | None = Query(None, pattern="^(in|out)$")) -> dict:
    if store.node(node_id) is None:
        raise HTTPException(404, f"No entity {node_id}")
    return store.expand(node_id, limit, type, direction)


class SubgraphRequest(BaseModel):
    ids: list[str] = Field(..., max_length=500)


@app.post("/api/subgraph")
def subgraph(req: SubgraphRequest) -> dict:
    return store.subgraph(req.ids)


@app.get("/api/overview")
def overview(top: int = Query(200, ge=10, le=337), min_meetings: int | None = Query(None, ge=1),
             start: int | None = Query(None, ge=1872, le=2100), end: int | None = Query(None, ge=1872, le=2100),
             backbone: int = Query(3, ge=0, le=8)) -> dict:
    return store.overview(top, min_meetings, start, end, backbone)


@app.get("/api/clubs/universe")
def club_universe() -> dict:
    return store.club_universe()


@app.get("/api/clubs/pipeline")
def club_pipeline() -> dict:
    return store.pipeline()


@app.get("/api/h2h")
def h2h(a: str, b: str) -> dict:
    res = store.h2h(a, b)
    if res is None:
        raise HTTPException(404, "Both ids must be teams")
    return {**res, "graph": store.h2h_graph(a, b)}


@app.get("/api/compare/players")
def compare_players(a: str, b: str) -> dict:
    res = store.compare_players(a, b)
    if res is None:
        raise HTTPException(404, "Both ids must be players")
    return res


@app.get("/api/quiz/new")
def quiz_new(level: str = Query("easy", pattern="^(easy|medium|hard)$")) -> dict:
    return store.quiz_new(level)


class GuessRequest(BaseModel):
    token: str = Field(..., max_length=40)
    guess: str | None = Field(None, max_length=200)
    reveal: bool = False


@app.post("/api/quiz/guess")
def quiz_guess(req: GuessRequest) -> dict:
    res = store.quiz_guess(req.token, req.guess, req.reveal)
    if res is None:
        raise HTTPException(404, "Unknown or finished quiz")
    return res


@app.get("/api/discover")
def discover() -> dict:
    return store.discover


@app.get("/api/path")
def path(src: str, dst: str, via_teams: bool = True) -> dict:
    return store.find_path(src, dst, via_teams)


# ---------------------------------------------------------------- agent API
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    conversation_id: str | None = None


def _sse(events: Iterator[dict]) -> Iterator[str]:
    for ev in events:
        yield f"data: {json.dumps(ev, default=str, ensure_ascii=False)}\n\n"


@app.post("/api/chat")
def chat(req: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        _sse(agent.chat(req.conversation_id, req.message)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.delete("/api/chat/{conversation_id}")
def reset_chat(conversation_id: str) -> dict:
    agent.reset(conversation_id)
    return {"ok": True}


# ---------------------------------------------------------------- built frontend
DIST = ROOT / "web" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        candidate = DIST / full_path
        if full_path and candidate.is_file() and DIST in candidate.resolve().parents:
            return FileResponse(candidate)
        return FileResponse(DIST / "index.html")
