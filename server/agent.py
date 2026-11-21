"""
The knowledge-graph agent: an LLM on Groq + tools over the Store, streamed as events to the UI.

Event stream (each item is a dict sent to the browser as one SSE message):
  {"type": "thinking", "delta": str}                 model reasoning, when the model exposes it
  {"type": "text", "delta": str}                     answer text (markdown)
  {"type": "tool_start", "id", "name", "input"}      a tool call is about to run
  {"type": "tool_result", "id", "ok", "summary", "result"?}
  {"type": "graph", "nodes", "edges", "caption", "focus"?}   draw this on the canvas
  {"type": "done", "usage": {...}}
  {"type": "error", "message": str}

Configuration (.env):
  GROQ_API_KEY            required
  GROQ_MODEL              default openai/gpt-oss-120b (needs tool calling; qwen/qwen3.8-27b also works)
  GROQ_REASONING_EFFORT   low | medium | high (gpt-oss models only), default medium
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from collections.abc import Iterator
from typing import Any

import groq

from .store import LABELS, Store

MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
REASONING_EFFORT = os.getenv("GROQ_REASONING_EFFORT", "medium")
MAX_TOOL_ROUNDS = 10
ENTITY_LINK = re.compile(r"\(<entity:([^>\s]+)>\)")
SQL_MAX_ROWS = 60          # Groq free tier has small tokens-per-minute limits: keep results compact
MAX_RESULT_CHARS = 9_000
COMPACT_CHARS = 1_200

SYSTEM_TEMPLATE = """\
You are the analyst inside "Football KG", an application for exploring a knowledge graph of \
international men's football results from 1872 to today. People ask you questions about teams, \
players, matches and tournaments; you answer from the data using your tools, and you use the \
graph canvas next to the chat to show what you found.

## The knowledge graph
(Player)-[:SCORED]->(Goal)-[:IN_MATCH]->(Match)-[:PART_OF]->(Tournament)
(Player)-[:ASSISTED]->(Goal)-[:FOR_TEAM]->(Team)
(Player)-[:PLAYS_FOR]->(Team)-[:PLAYED_IN {{side, goals}}]->(Match)
(Match)-[:WON_BY]->(Team)          (Team)-[:WON_SHOOTOUT]->(Match)
(Match)-[:HELD_IN]->(City)-[:LOCATED_IN]->(Country)<-[:REPRESENTS]-(Team)
(Team)-[:FORMERLY_KNOWN_AS {{start, end}}]->(FormerName)

Node IDs: Team `T:brazil`, Player `P:lionel_messi@argentina` (name + national team), \
Match `M123`, Goal `G456`, Tournament `TR:fifa_world_cup`, City `CI:london@england`, \
Country `C:england`, FormerName `FN:dahomey`. Slugs are lowercase with spaces as underscores; \
accents are kept (`P:pelé@brazil`), so resolve names with search_entities rather than guessing IDs.

## SQL tables (DuckDB dialect, read-only)
{schema}

Notes on the tables:
- `matches.winner` is the team name, or NULL for a draw. Shootouts are in `shootout_winner` and do not change `winner`.
- In `goals`, `team` is the team the goal counted for. For an own goal (`own_goal = true`) the scorer \
actually played for the opponent, and the player node is attached to the benefiting team: exclude own goals \
when ranking scorers.
- Elo ratings (replayed over every match; K by importance, home advantage 100): `matches.home_elo` / \
`away_elo` are pre-match ratings, `home_win_exp` the home side's expectancy, `upset` = winner's expectancy \
was below 25% (`winner_win_exp`). `elo_yearly` has each team's year-end rating and world `rank`; \
`elo_history` the rating after every match. Use these for "best team of the 1970s", upsets, form.
- `players.goals` already excludes own goals. `nodes`/`edges` are the raw graph (edges: src, dst, type, side, goals).
- Use `tournament = 'FIFA World Cup'` for finals tournaments; qualifiers are 'FIFA World Cup qualification'. \
Other names include 'UEFA Euro', 'Copa América', 'African Cup of Nations', 'AFC Asian Cup', 'Friendly'. \
When unsure of a tournament's exact name, query `tournaments`.

## Data caveats you must respect
- Results cover ~50k matches, but goalscorer data covers only ~15.5k matches and contains no friendlies. \
Scorer totals are "goals in competitive matches with recorded scorers", not official career totals. Say so \
when you report scorer counts.
- Assists are recorded for only ~2k goals, so assist counts are very incomplete.
- Team names are current names; historical names live in former_names (e.g. Dahomey -> Benin).
- The data runs through {last_match}. Do not claim knowledge beyond it, and do not fill gaps from memory: \
if the data cannot answer, say what it can answer instead.

## How to work
- Resolve people, teams and tournaments with search_entities, then compute with run_sql. Prefer a single \
well-built query over many small ones.
- Use find_path for "how are X and Y connected" questions.
- To show something specific on the canvas, call show_in_graph (for example the Matches of a rivalry). \
Their teams, tournaments and scorers are added automatically. find_path draws its own result. Entities you \
link in your answer are drawn automatically if you do not call show_in_graph.
- Only call tools through the tool interface. Never write tool calls, node id lists or JSON into your answer, \
and do not describe the canvas: the person can already see it.

## How to answer
- Lead with the direct answer, then the supporting numbers. Use a short markdown table for rankings or lists.
- Keep it tight: a few sentences plus a table is typical. No preamble.
- Link entities so they are clickable in the app: [Lionel Messi](<entity:P:lionel_messi@argentina>). \
Use exactly that form with the real ID, angle brackets included.
- Mention a caveat only when it affects this answer, in one sentence. Do not describe the graph drawing.
"""


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


TOOLS = [
    _tool(
        "search_entities",
        "Find entities by name (accent- and case-insensitive, all words must match). Returns ids, labels, "
        "names and, for players, their national team. Use this to resolve any name before querying.",
        {
            "query": {"type": "string", "description": "Name or part of a name, e.g. 'messi' or 'world cup'."},
            "label": {"type": "string", "enum": LABELS, "description": "Optional label filter."},
        },
        ["query"],
    ),
    _tool(
        "get_entity",
        "Profile of one entity by id: record, top scorers, opponents, goals, etc. depending on its label, "
        "plus counts of each relationship type.",
        {"id": {"type": "string"}},
        ["id"],
    ),
    _tool(
        "run_sql",
        f"Run one read-only DuckDB SELECT/WITH query over the tables in the system prompt. Returns at most "
        f"{SQL_MAX_ROWS} rows. Always add ORDER BY and LIMIT for rankings.",
        {"sql": {"type": "string"}},
        ["sql"],
    ),
    _tool(
        "find_path",
        "Shortest connection between two Player/Team/Match/Goal entities in the graph. via_teams=true allows "
        "hops through national teams; false links players only through goals in shared matches "
        "('six degrees'). Draws the path on the canvas.",
        {
            "from_id": {"type": "string"},
            "to_id": {"type": "string"},
            "via_teams": {"type": "boolean"},
        },
        ["from_id", "to_id", "via_teams"],
    ),
    _tool(
        "show_in_graph",
        "Draw entities on the graph canvas beside the chat, with every edge between them. Use it at the end of "
        "an answer to visualise the entities involved.",
        {
            "node_ids": {"type": "array", "items": {"type": "string"}, "description": "3-40 node ids."},
            "focus_id": {"type": "string", "description": "Optional id to centre and select."},
            "caption": {"type": "string", "description": "Short caption, e.g. 'Brazil vs Argentina finals'."},
        },
        ["node_ids", "caption"],
    ),
]

_REQUIRED_TYPES: dict[str, dict[str, type | tuple[type, ...]]] = {
    "search_entities": {"query": str},
    "get_entity": {"id": str},
    "run_sql": {"sql": str},
    "find_path": {"from_id": str, "to_id": str, "via_teams": bool},
    "show_in_graph": {"node_ids": list, "caption": str},
}


class InvalidToolInput(Exception):
    pass


class Agent:
    def __init__(self, store: Store) -> None:
        self.store = store
        self._client: groq.Groq | None = None
        self.system = SYSTEM_TEMPLATE.format(schema=store.schema_text, last_match=store.meta["last_match"])
        self.conversations: dict[str, list[dict[str, Any]]] = {}
        self._locks: dict[str, threading.Lock] = {}

    @property
    def client(self) -> groq.Groq:
        if self._client is None:
            if not os.getenv("GROQ_API_KEY"):
                raise MissingKey()
            # free-tier rate limits are tight; let the SDK wait out 429s (it honours retry-after)
            self._client = groq.Groq(max_retries=4)
        return self._client

    # ------------------------------------------------------------------ tools
    def _parse_args(self, name: str, raw: str) -> dict[str, Any]:
        spec = _REQUIRED_TYPES.get(name)
        if spec is None:
            raise InvalidToolInput(f"Unknown tool {name}")
        try:
            args = json.loads(raw or "{}")
        except json.JSONDecodeError as e:
            raise InvalidToolInput(f"Arguments are not valid JSON: {e}") from e
        if not isinstance(args, dict):
            raise InvalidToolInput("Arguments must be a JSON object")
        for key, typ in spec.items():
            if not isinstance(args.get(key), typ):
                raise InvalidToolInput(f"'{key}' is missing or has the wrong type")
        return args

    def _run_tool(self, name: str, args: dict[str, Any]) -> tuple[Any, str, dict | None]:
        """Returns (result for the model, one-line summary for the UI, optional graph event)."""
        s = self.store
        if name == "search_entities":
            res = s.search(args["query"], args.get("label"), limit=8)
            return res, f"{len(res)} match{'es' if len(res) != 1 else ''} for “{args['query']}”", None
        if name == "get_entity":
            res = s.entity(args["id"])
            if res is None:
                raise ValueError(f"No entity with id {args['id']}; use search_entities.")
            return res, f"{res['label']}: {res['name']}", None
        if name == "run_sql":
            res = s.run_sql(args["sql"], max_rows=SQL_MAX_ROWS)
            more = " (truncated)" if res["truncated"] else ""
            return res, f"{res['row_count']} row{'s' if res['row_count'] != 1 else ''}{more}", None
        if name == "find_path":
            res = s.find_path(args["from_id"], args["to_id"], via_teams=args["via_teams"])
            if not res["found"]:
                return res, f"No path: {res['reason']}", None
            names = [n["name"] for n in res["nodes"]]
            graph = {
                "type": "graph", "nodes": res["nodes"], "edges": res["edges"],
                "caption": f"{names[0]} → {names[-1]}", "focus": res["path"][0], "path": res["path"],
            }
            model_view = {"found": True, "hops": len(res["path"]) - 1, "path": names, "ids": res["path"]}
            return model_view, f"{len(res['path']) - 1} hops", graph
        if name == "show_in_graph":
            ids = [i for i in args["node_ids"] if isinstance(i, str)][:60]
            sub = s.subgraph_with_context(ids)
            missing = sorted(set(ids) - {n["id"] for n in sub["nodes"]})
            graph = {"type": "graph", **sub, "caption": args["caption"], "focus": args.get("focus_id")}
            res = {"shown_nodes": len(sub["nodes"]), "shown_edges": len(sub["edges"]), "unknown_ids": missing}
            return res, f"Drew {len(sub['nodes'])} nodes, {len(sub['edges'])} edges", graph
        raise InvalidToolInput(f"Unknown tool {name}")

    # ------------------------------------------------------------------ conversation
    def chat(self, conversation_id: str | None, message: str) -> Iterator[dict[str, Any]]:
        cid = conversation_id or uuid.uuid4().hex
        lock = self._locks.setdefault(cid, threading.Lock())
        if not lock.acquire(blocking=False):
            yield {"type": "error", "message": "This conversation is still answering the previous question."}
            return
        history = self.conversations.setdefault(cid, [])
        checkpoint = len(history)
        try:
            yield {"type": "conversation", "id": cid}
            history.append({"role": "user", "content": message})
            yield from self._loop(history)
            _compact_turn(history, checkpoint)
        except Exception as e:  # noqa: BLE001 - keep history consistent, report a readable message
            del history[checkpoint:]
            yield {"type": "error", "message": _friendly_error(e)}
        finally:
            lock.release()

    def reset(self, conversation_id: str) -> None:
        self.conversations.pop(conversation_id, None)

    def _loop(self, history: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        drew = False
        answer = ""
        rounds = 0
        bad_calls = 0
        while True:
            force_answer = rounds >= MAX_TOOL_ROUNDS
            kwargs: dict[str, Any] = dict(
                model=MODEL,
                messages=[{"role": "system", "content": self.system}, *history],
                tools=TOOLS,
                tool_choice="none" if force_answer else "auto",
                stream=True,
                temperature=0.2,
                max_completion_tokens=8192,
            )
            if REASONING_EFFORT and MODEL.startswith("openai/gpt-oss"):
                kwargs["reasoning_effort"] = REASONING_EFFORT

            text, calls, finish = "", {}, None
            try:
                for chunk in self.client.chat.completions.create(**kwargs):
                    x = getattr(chunk, "x_groq", None)
                    if x is not None and getattr(x, "usage", None):
                        usage["prompt_tokens"] += x.usage.prompt_tokens or 0
                        usage["completion_tokens"] += x.usage.completion_tokens or 0
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    d = choice.delta
                    reasoning = getattr(d, "reasoning", None)
                    if reasoning:
                        yield {"type": "thinking", "delta": reasoning}
                    if d.content:
                        text += d.content
                        yield {"type": "text", "delta": d.content}
                    for tc in d.tool_calls or []:
                        acc = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                        if tc.id:
                            acc["id"] = tc.id
                        if tc.function and tc.function.name:
                            acc["name"] += tc.function.name
                        if tc.function and tc.function.arguments:
                            acc["arguments"] += tc.function.arguments
                    if choice.finish_reason:
                        finish = choice.finish_reason
            except groq.BadRequestError as e:
                # Groq rejects a turn whose tool call the model garbled ("tool_use_failed"); retry it.
                if "tool_use_failed" in str(e) and bad_calls < 2:
                    bad_calls += 1
                    continue
                raise

            answer += text
            if not calls:
                history.append({"role": "assistant", "content": text})
                if finish == "length":
                    yield {"type": "text", "delta": "\n\n_(answer cut off at the length limit)_"}
                break

            tool_calls = [
                {"id": c["id"] or f"call_{uuid.uuid4().hex[:8]}", "type": "function",
                 "function": {"name": c["name"], "arguments": c["arguments"]}}
                for _, c in sorted(calls.items())
            ]
            history.append({"role": "assistant", "content": text or None, "tool_calls": tool_calls})
            rounds += 1
            for call in tool_calls:
                name, raw = call["function"]["name"], call["function"]["arguments"]
                try:
                    preview = json.loads(raw or "{}")
                except json.JSONDecodeError:
                    preview = {"raw": raw}
                yield {"type": "tool_start", "id": call["id"], "name": name, "input": preview}
                try:
                    args = self._parse_args(name, raw)
                    result, summary, graph = self._run_tool(name, args)
                except Exception as e:  # noqa: BLE001 - tool errors go back to the model to fix
                    history.append({"role": "tool", "tool_call_id": call["id"],
                                    "content": json.dumps({"error": str(e)})})
                    yield {"type": "tool_result", "id": call["id"], "ok": False, "summary": str(e)[:300]}
                    continue
                content = json.dumps(result, default=str, ensure_ascii=False)
                if len(content) > MAX_RESULT_CHARS:
                    content = content[:MAX_RESULT_CHARS] + " ... [truncated: narrow the query]"
                history.append({"role": "tool", "tool_call_id": call["id"], "content": content})
                ui_result = result if name == "run_sql" else None
                yield {"type": "tool_result", "id": call["id"], "ok": True, "summary": summary, "result": ui_result}
                if graph:
                    drew = True
                    yield graph
        if not drew:
            ids = list(dict.fromkeys(ENTITY_LINK.findall(answer)))
            if ids:
                sub = self.store.subgraph_with_context(ids[:40])
                if sub["nodes"]:
                    yield {"type": "graph", **sub, "caption": "Entities in this answer", "focus": None}
        yield {"type": "done", "usage": usage}


class MissingKey(Exception):
    pass


def _compact_turn(history: list[dict[str, Any]], start: int) -> None:
    """Shrink a finished turn's tool outputs so follow-up questions stay within rate limits."""
    for msg in history[start:]:
        if msg.get("role") == "tool" and len(msg["content"]) > COMPACT_CHARS:
            msg["content"] = msg["content"][:COMPACT_CHARS] + " ... [older result shortened]"


def _friendly_error(e: Exception) -> str:
    if isinstance(e, MissingKey):
        return "GROQ_API_KEY is not set. Add it to .env and restart the server."
    if isinstance(e, groq.AuthenticationError):
        return "Groq rejected the API key. Check GROQ_API_KEY in .env and restart the server."
    if isinstance(e, groq.RateLimitError):
        return "Groq's free-tier rate limit was reached. Wait a minute and ask again."
    if isinstance(e, groq.APIConnectionError):
        return "Could not reach the Groq API. Check your network connection."
    if isinstance(e, groq.APIStatusError):
        return f"Groq API error {e.status_code}: {e.message}"
    return str(e) or type(e).__name__
