"""HTTP endpoints (server/main.py) through FastAPI's test client. Offline: no Wikimedia or Groq calls."""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.needs_build

MESSI = "P:lionel_messi@argentina"


@pytest.mark.parametrize("url", [
    "/api/meta", "/api/search?q=messi", "/api/search?q=brazil&label=Team", "/api/overview",
    "/api/overview?start=1950&end=1959", "/api/clubs/universe", "/api/clubs/pipeline", "/api/discover",
    "/api/h2h?a=T:brazil&b=T:argentina", f"/api/entity/{MESSI}", "/api/entity/CL:fc_barcelona",
    f"/api/expand/{MESSI}?limit=5", f"/api/path?src={MESSI}&dst=P:pelé@brazil",
    f"/api/compare/players?a={MESSI}&b=P:cristiano_ronaldo@portugal", "/api/quiz/new?level=hard",
    "/api/media/teams", "/api/media/clubs",
])
def test_get_endpoints_answer(client, url):
    r = client.get(url)
    assert r.status_code == 200, r.text
    assert r.json() is not None


@pytest.mark.parametrize("url,status", [
    ("/api/entity/P:nobody@nowhere", 404),
    ("/api/h2h?a=T:brazil&b=P:lionel_messi@argentina", 404),
    ("/api/compare/players?a=T:brazil&b=T:argentina", 404),
    ("/api/quiz/new?level=impossible", 422),
    ("/api/search?q=", 422),
    ("/api/expand/T:brazil?limit=100000", 422),
    ("/api/overview?start=1700", 422),
])
def test_bad_requests_are_rejected_cleanly(client, url, status):
    assert client.get(url).status_code == status


def test_subgraph_post(client):
    r = client.post("/api/subgraph", json={"ids": [MESSI, "T:argentina"]})
    assert r.status_code == 200 and len(r.json()["nodes"]) == 2
    assert client.post("/api/subgraph", json={"ids": ["x"] * 501}).status_code == 422


def test_quiz_flow(client):
    q = client.get("/api/quiz/new?level=easy").json()
    wrong = client.post("/api/quiz/guess", json={"token": q["token"], "guess": "P:nobody@nowhere"})
    assert wrong.status_code == 200 and wrong.json() == {"correct": False}
    reveal = client.post("/api/quiz/guess", json={"token": q["token"], "reveal": True}).json()
    assert reveal["answer"]["id"].startswith("P:")
    assert client.post("/api/quiz/guess", json={"token": q["token"], "guess": "x"}).status_code == 404


def test_image_proxy_only_serves_wikimedia(client):
    for u in ["https://example.com/x.png", "http://upload.wikimedia.org/x.png", "file:///etc/passwd",
              "https://upload.wikimedia.org.evil.com/x.png"]:
        assert client.get("/api/img", params={"u": u}).status_code == 404


def test_media_players_offline(client):
    r = client.post("/api/media/players", json={"ids": [MESSI]})
    assert r.status_code == 200 and "photos" in r.json()


def test_chat_without_a_key_reports_a_readable_error(client):
    """With no GROQ_API_KEY the agent must explain the problem, not crash."""
    r = client.post("/api/chat", json={"message": "Who scored the most goals?"})
    assert r.status_code == 200
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    assert events[0]["type"] == "conversation"
    assert any(e["type"] == "error" and "GROQ_API_KEY" in e["message"] for e in events)


def test_spa_is_served(client):
    r = client.get("/")
    assert r.status_code in (200, 404)  # 404 only when web/dist has not been built
