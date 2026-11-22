"""server/store.py: the data-access layer behind every endpoint and agent tool."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.needs_build

MESSI, RONALDO, SALAH = "P:lionel_messi@argentina", "P:cristiano_ronaldo@portugal", "P:mohamed_salah@egypt"


# ---------------------------------------------------------------- search & entities
def test_search_ranks_exact_names_first(store):
    assert store.search("lionel messi")[0]["id"] == MESSI
    assert store.search("Brazil", label="Team")[0]["id"] == "T:brazil"


def test_search_is_accent_and_case_insensitive(store):
    assert any(h["id"] == "P:pelé@brazil" for h in store.search("PELE"))


def test_search_handles_odd_input(store):
    assert store.search("") == []
    assert store.search("   ") == []
    assert store.search("%_'\";--") == []  # LIKE wildcards and quotes are data, not syntax


@pytest.mark.parametrize("node_id,label", [
    ("T:brazil", "Team"), (MESSI, "Player"), ("CL:fc_barcelona", "Club"), ("TR:fifa_world_cup", "Tournament"),
    ("C:england", "Country"), ("CI:london@england", "City"), ("FN:dahomey", "FormerName"), ("M0", "Match"),
])
def test_entity_detail_for_every_label(store, node_id, label):
    e = store.entity(node_id)
    assert e and e["label"] == label and e["details"] is not None and e["relationships"]


def test_unknown_entity_is_none(store):
    assert store.entity("P:nobody@nowhere") is None


def test_player_totals(store):
    p = store.entity(MESSI)["details"]["profile"]
    assert p["total_goals"] == p["goals"] + p["wiki_goals"] >= 100
    assert p["caps"] and p["caps"] > 150


def test_teammates(store):
    mates = store.club_teammates(MESSI, 10)
    assert mates and all(m["club_id"] for m in mates)
    assert any(m["name"] == "Sergio Busquets" for m in mates)
    assert all(m["id"] != MESSI for m in mates)
    with_ = store.scored_with(MESSI, 8)
    assert with_ and all(w["matches"] >= 1 and w["id"] != MESSI for w in with_)


# ---------------------------------------------------------------- graph navigation
def test_expand_respects_the_limit(store):
    sub = store.expand("T:brazil", limit=5)
    per_type = {}
    for e in sub["edges"]:
        per_type[e["type"]] = per_type.get(e["type"], 0) + 1
    assert all(n <= 5 for n in per_type.values())
    assert sub["truncated"]


def test_subgraph_returns_edges_between_the_given_nodes(store):
    sub = store.subgraph([MESSI, "T:argentina"])
    assert {n["id"] for n in sub["nodes"]} == {MESSI, "T:argentina"}
    assert any(e["type"] == "PLAYS_FOR" for e in sub["edges"])


def test_find_path(store):
    res = store.find_path(MESSI, RONALDO)
    assert res["found"] and res["path"][0] == MESSI and res["path"][-1] == RONALDO
    assert not store.find_path(MESSI, "P:nobody@nowhere")["found"]


def test_h2h(store):
    h = store.h2h("T:brazil", "T:argentina")
    s = h["summary"]
    assert s["matches"] == s["a_wins"] + s["b_wins"] + s["draws"] > 100
    assert store.h2h("T:brazil", MESSI) is None


# ---------------------------------------------------------------- home views
def test_nations_overview(store):
    o = store.overview()
    ids = {n["id"] for n in o["nodes"]}
    assert len(o["nodes"]) == 200
    assert all(e["source"] in ids and e["target"] in ids for e in o["edges"])
    assert all(n["conf"] for n in o["nodes"])
    decade = store.overview(140, None, 1970, 1979)
    assert 0 < len(decade["nodes"]) <= 140 and decade["era"]["start"] == 1970


def test_club_universe(store):
    u = store.club_universe()
    ids = {n["id"] for n in u["nodes"]}
    assert 150 < len(ids) < 260
    assert all(e["source"] in ids and e["target"] in ids and e["weight"] >= 1 for e in u["edges"])
    assert all(0 <= n["strength"] <= 1 and n["label"] == "Club" for n in u["nodes"])
    assert sum(g["teams"] for g in u["groups"]) == len(u["nodes"])


def test_pipeline_rings(store):
    p = store.pipeline()
    teams = [n for n in p["nodes"] if n["label"] == "Team"]
    clubs = [n for n in p["nodes"] if n["label"] == "Club"]
    assert teams and clubs
    r = lambda n: (n["hx"] ** 2 + n["hy"] ** 2) ** 0.5  # noqa: E731
    assert min(map(r, teams)) > max(map(r, clubs))  # nations outside, clubs inside
    ids = {n["id"] for n in p["nodes"]}
    assert all(e["source"].startswith("T:") and e["target"].startswith("CL:") and e["target"] in ids for e in p["edges"])


def test_discover_cards(store):
    d = store.discover  # cached property
    cards = {k: v for k, v in d.items() if isinstance(v, list)}
    assert len(cards) >= 4 and all(cards.values()), {k: len(v) for k, v in cards.items()}


# ---------------------------------------------------------------- compare & game
def test_compare_players(store):
    c = store.compare_players(MESSI, "P:luis_suárez@uruguay")
    assert c["a"]["id"] == MESSI and c["b"]["profile"]["team"] == "Uruguay"
    assert any(s["id"] == "CL:fc_barcelona" and s["together"] for s in c["shared_clubs"])
    assert c["common_teammates"] > 10
    assert store.compare_players(MESSI, "T:brazil") is None


@pytest.mark.parametrize("level", ["easy", "medium", "hard"])
def test_quiz_round(store, level):
    q = store.quiz_new(level)
    assert len(q["clues"]) == 8 and all(c["text"] for c in q["clues"])
    assert store.quiz_guess(q["token"], "P:nobody@nowhere") == {"correct": False}
    revealed = store.quiz_guess(q["token"], None, reveal=True)
    assert revealed["answer"]["id"].startswith("P:")
    assert store.quiz_guess(q["token"], revealed["answer"]["id"]) is None  # finished quizzes are gone


def test_quiz_correct_guess(store):
    q = store.quiz_new("easy")
    answer = store._quizzes[q["token"]]
    r = store.quiz_guess(q["token"], answer)
    assert r["correct"] and r["answer"]["id"] == answer
    # the answer never leaks into the clues by id
    assert answer not in str(q)


# ---------------------------------------------------------------- the agent's SQL tool
def test_run_sql_reads(store):
    r = store.run_sql("SELECT name FROM teams ORDER BY matches DESC LIMIT 3")
    assert r["row_count"] == 3 and r["columns"] == ["name"]
    assert store.run_sql("SELECT * FROM matches", max_rows=5)["truncated"]


@pytest.mark.parametrize("sql", [
    "DROP TABLE matches",
    "DELETE FROM players",
    "INSERT INTO teams VALUES (1)",
    "SELECT 1; DROP TABLE matches",
    "ATTACH 'x.db' AS x",
    "COPY matches TO 'out.csv'",
    "PRAGMA database_list",
    "CREATE TABLE t AS SELECT 1",
])
def test_run_sql_rejects_writes_and_multiple_statements(store, sql):
    with pytest.raises(ValueError):
        store.run_sql(sql)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM read_csv_auto('data/international_results/results.csv')",
    "SELECT * FROM 'C:/Windows/win.ini'",
])
def test_run_sql_cannot_read_files(store, sql):
    with pytest.raises(Exception):
        store.run_sql(sql)


def test_database_is_read_only(store):
    with pytest.raises(Exception):
        store.q("CREATE TABLE hacked AS SELECT 1")
