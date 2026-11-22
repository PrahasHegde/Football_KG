"""The knowledge graph built by src/build_kg.py: schema, ID conventions and structural invariants."""
from __future__ import annotations

import re
from collections import Counter, defaultdict

import pandas as pd
import pytest

from conftest import DATA

pytestmark = pytest.mark.needs_build

# (edge type) -> (source label, target label): the schema in CLAUDE.md
SCHEMA = {
    "SCORED": ("Player", "Goal"),
    "ASSISTED": ("Player", "Goal"),
    "IN_MATCH": ("Goal", "Match"),
    "FOR_TEAM": ("Goal", "Team"),
    "PLAYS_FOR": ("Player", "Team"),
    "PLAYED_IN": ("Team", "Match"),
    "PART_OF": ("Match", "Tournament"),
    "WON_BY": ("Match", "Team"),
    "WON_SHOOTOUT": ("Team", "Match"),
    "HELD_IN": ("Match", "City"),
    "LOCATED_IN": ("City", "Country"),
    "REPRESENTS": ("Team", "Country"),
    "FORMERLY_KNOWN_AS": ("Team", "FormerName"),
    "PLAYED_FOR_CLUB": ("Player", "Club"),
    "BASED_IN": ("Club", "Country"),
}
ID_PATTERN = {
    "Team": r"^T:\S+$", "Tournament": r"^TR:\S+$", "Country": r"^C:\S+$", "City": r"^CI:.+@.+$",
    "Match": r"^M\d+$", "Goal": r"^G\d+$", "FormerName": r"^FN:\S+$", "Player": r"^P:.+@\S+$", "Club": r"^CL:\S+$",
}


def test_every_node_has_a_known_label_and_a_conventional_id(graph):
    bad = [(n, d.get("label")) for n, d in graph.nodes(data=True)
           if d.get("label") not in ID_PATTERN or not re.match(ID_PATTERN[d["label"]], n)]
    assert not bad[:10], f"{len(bad)} nodes break the ID conventions, e.g. {bad[:10]}"


def test_every_edge_follows_the_schema(graph):
    labels = nx_labels(graph)
    bad = Counter()
    for u, v, d in graph.edges(data=True):
        expected = SCHEMA.get(d.get("type"))
        if expected is None or (labels[u], labels[v]) != expected:
            bad[(d.get("type"), labels[u], labels[v])] += 1
    assert not bad, f"edges outside the schema: {dict(bad)}"


def test_one_match_node_per_results_row(graph):
    results = pd.read_csv(DATA / "results.csv")
    matches = sum(1 for _, d in graph.nodes(data=True) if d["label"] == "Match")
    assert matches == len(results)


def test_every_match_has_one_home_and_one_away_side(graph):
    sides = defaultdict(list)
    for u, v, d in graph.edges(data=True):
        if d["type"] == "PLAYED_IN":
            sides[v].append(d["side"])
    bad = [m for m, s in sides.items() if sorted(s) != ["away", "home"]]
    assert not bad[:5], f"{len(bad)} matches without exactly one home + one away side"


def test_every_goal_is_fully_connected(graph):
    """Each goal: exactly one scorer, one match and one team."""
    counts = defaultdict(Counter)
    for u, v, d in graph.edges(data=True):
        if d["type"] == "SCORED":
            counts[v]["SCORED"] += 1
        elif d["type"] in ("IN_MATCH", "FOR_TEAM"):
            counts[u][d["type"]] += 1
    goals = [n for n, d in graph.nodes(data=True) if d["label"] == "Goal"]
    bad = [g for g in goals if counts[g] != Counter({"SCORED": 1, "IN_MATCH": 1, "FOR_TEAM": 1})]
    assert not bad[:5], f"{len(bad)} goals not linked to exactly one scorer/match/team"


def test_winners_follow_the_score(graph):
    won_by = {u: v for u, v, d in graph.edges(data=True) if d["type"] == "WON_BY"}
    home = {v: u for u, v, d in graph.edges(data=True) if d["type"] == "PLAYED_IN" and d["side"] == "home"}
    for m, d in graph.nodes(data=True):
        if d["label"] != "Match":
            continue
        if d["home_score"] == d["away_score"]:
            assert m not in won_by, f"{m} is a draw but has a winner"
        else:
            assert (won_by[m] == home[m]) == (d["home_score"] > d["away_score"]), f"wrong winner for {m}"


def test_goal_sources_are_known(graph):
    sources = Counter(d.get("source") for _, d in graph.nodes(data=True) if d["label"] == "Goal")
    assert set(sources) <= {"dataset", "wikipedia"}
    assert sources["dataset"] > 40_000


def nx_labels(graph) -> dict:
    return {n: d["label"] for n, d in graph.nodes(data=True)}
