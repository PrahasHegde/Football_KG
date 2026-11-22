"""server/agent.py without the LLM: tool argument validation, tool execution, and history compaction."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.needs_build


@pytest.fixture(scope="module")
def agent(store):
    from server.agent import Agent
    return Agent(store)


def test_entity_link_pattern():
    from server.agent import ENTITY_LINK
    text = "[Messi](<entity:P:lionel_messi@argentina>) beat [Brazil](<entity:T:brazil>)"
    assert ENTITY_LINK.findall(text) == ["P:lionel_messi@argentina", "T:brazil"]


@pytest.mark.parametrize("name,raw", [
    ("run_sql", "not json"),
    ("run_sql", "[1, 2]"),
    ("run_sql", '{"sql": 5}'),
    ("find_path", '{"from_id": "a", "to_id": "b"}'),
    ("no_such_tool", "{}"),
])
def test_bad_tool_arguments_are_rejected(agent, name, raw):
    from server.agent import InvalidToolInput
    with pytest.raises(InvalidToolInput):
        agent._parse_args(name, raw)


def test_tools_run_against_the_store(agent):
    res, summary, graph = agent._run_tool("search_entities", {"query": "messi"})
    assert res and "match" in summary and graph is None
    res, _, _ = agent._run_tool("run_sql", {"sql": "SELECT count(*) AS n FROM teams"})
    assert res["rows"][0][0] > 300
    res, _, graph = agent._run_tool("find_path", {"from_id": "P:lionel_messi@argentina",
                                                  "to_id": "P:cristiano_ronaldo@portugal", "via_teams": True})
    assert res["found"] and graph["type"] == "graph" and graph["path"]
    res, _, graph = agent._run_tool("show_in_graph", {"node_ids": ["T:brazil", "nope"], "caption": "x"})
    assert res["unknown_ids"] == ["nope"] and graph["nodes"]
    with pytest.raises(ValueError):
        agent._run_tool("get_entity", {"id": "P:nobody@nowhere"})


def test_history_compaction_keeps_turns_small():
    from server.agent import COMPACT_CHARS, _compact_turn
    history = [{"role": "user", "content": "q"}, {"role": "tool", "content": "x" * (COMPACT_CHARS * 5)},
               {"role": "assistant", "content": "y" * (COMPACT_CHARS * 5)}]
    _compact_turn(history, 0)
    assert len(history[1]["content"]) < COMPACT_CHARS + 50
    assert len(history[2]["content"]) == COMPACT_CHARS * 5  # answers are kept


def test_system_prompt_mentions_every_table(agent):
    for table in ["matches", "goals", "players", "teams", "clubs", "club_careers", "elo_yearly"]:
        assert table in agent.system
