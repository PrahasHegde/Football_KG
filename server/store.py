"""
Read-only query layer over app_data/football.duckdb.

Used by both the REST API (graph exploration) and the agent's tools.
Every public method opens its own cursor, so it is safe to call from FastAPI's thread pool.
"""
from __future__ import annotations

import re
import threading
from functools import cached_property
from typing import Any

import duckdb
import networkx as nx

from .build_db import DB_PATH, build, needs_rebuild, normalize

LABELS = ["Team", "Player", "Club", "Match", "Goal", "Tournament", "City", "Country", "FormerName"]

# Hubs make every path trivially short (everything is "in" the Friendly tournament), so
# path finding ignores them.
PATH_EXCLUDED = {"Tournament", "City", "Country", "FormerName"}


def _rows(cur: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, (_jsonable(v) for v in row))) for row in cur.fetchall()]


def _jsonable(v: Any) -> Any:
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


class Store:
    def __init__(self) -> None:
        if needs_rebuild():
            build()
        self.con = duckdb.connect(
            str(DB_PATH),
            read_only=True,
            config={"enable_external_access": False, "lock_configuration": True},
        )
        self._path_lock = threading.Lock()
        self._path_graph: nx.Graph | None = None

    # ------------------------------------------------------------------ helpers
    def q(self, sql: str, params: list | tuple | None = None) -> list[dict[str, Any]]:
        cur = self.con.cursor()
        try:
            cur.execute(sql, params or [])
            return _rows(cur)
        finally:
            cur.close()

    def node(self, node_id: str) -> dict[str, Any] | None:
        r = self.q("SELECT id, label, name, degree FROM nodes WHERE id = ?", [node_id])
        return r[0] if r else None

    def players_info(self, ids: list[str]) -> list[dict[str, Any]]:
        if not ids:
            return []
        ph = ",".join("?" * len(ids))
        return self.q(f"SELECT player_id AS id, name, team FROM players WHERE player_id IN ({ph})", ids)

    # ------------------------------------------------------------------ meta
    @cached_property
    def meta(self) -> dict[str, Any]:
        labels = self.q("SELECT label, count(*) AS count FROM nodes GROUP BY label ORDER BY count DESC")
        rels = self.q("SELECT type, count(*) AS count FROM edges GROUP BY type ORDER BY count DESC")
        span = self.q("SELECT min(date) AS first, max(date) AS last FROM matches")[0]
        return {
            "labels": labels,
            "relationships": rels,
            "nodes": sum(r["count"] for r in labels),
            "edges": sum(r["count"] for r in rels),
            "first_match": span["first"],
            "last_match": span["last"],
        }

    # ------------------------------------------------------------------ search
    def search(self, text: str, label: str | None = None, limit: int = 12) -> list[dict[str, Any]]:
        norm = normalize(text)
        if not norm:
            return []
        # Goals have no meaningful names and Matches are better found through their teams,
        # so default search covers the "named" entities and ranks by exactness then importance.
        labels = [label] if label else ["Team", "Player", "Club", "Tournament", "Country", "City", "FormerName"]
        words = [w for w in norm.split() if w]
        where = " AND ".join("name_norm LIKE ?" for _ in words)
        params: list[Any] = [f"%{w}%" for w in words]
        rows = self.q(
            f"""
            SELECT n.id, n.label, n.name, n.degree,
                   CASE WHEN name_norm = ? THEN 0
                        WHEN name_norm LIKE ? OR name_norm LIKE ? THEN 1 ELSE 2 END AS rank,
                   p.team AS team
            FROM nodes n LEFT JOIN players p ON p.player_id = n.id
            WHERE label IN ({",".join("?" * len(labels))}) AND {where}
            ORDER BY rank, degree DESC
            LIMIT ?
            """,
            [norm, f"{norm}%", f"% {norm}%", *labels, *params, limit],
        )
        for r in rows:
            r.pop("rank", None)
        return rows

    # ------------------------------------------------------------------ entity details
    def entity(self, node_id: str) -> dict[str, Any] | None:
        n = self.node(node_id)
        if not n:
            return None
        handler = getattr(self, f"_detail_{n['label'].lower()}", None)
        details = handler(node_id) if handler else {}
        rels = self.q(
            """
            SELECT type, 'out' AS direction, count(*) AS count FROM edges WHERE src = ? GROUP BY type
            UNION ALL
            SELECT type, 'in', count(*) FROM edges WHERE dst = ? GROUP BY type
            ORDER BY count DESC
            """,
            [node_id, node_id],
        )
        return {**n, "details": details, "relationships": rels}

    def _detail_team(self, tid: str) -> dict[str, Any]:
        t = self.q("SELECT * FROM teams WHERE team_id = ?", [tid])
        return {
            "record": t[0] if t else None,
            "top_scorers": self.q(
                "SELECT player_id AS id, name, total_goals AS goals FROM players WHERE team_id = ? "
                "AND total_goals > 0 ORDER BY total_goals DESC LIMIT 8",
                [tid],
            ),
            "top_opponents": self.q(
                """
                SELECT CASE WHEN home_team_id = ? THEN away_team_id ELSE home_team_id END AS id,
                       CASE WHEN home_team_id = ? THEN away_team ELSE home_team END AS name,
                       count(*) AS meetings,
                       count(*) FILTER (WHERE winner = (SELECT name FROM teams WHERE team_id = ?)) AS wins,
                       count(*) FILTER (WHERE winner IS NULL) AS draws
                FROM matches WHERE ? IN (home_team_id, away_team_id)
                GROUP BY ALL ORDER BY meetings DESC LIMIT 8
                """,
                [tid, tid, tid, tid],
            ),
            "tournaments": self.q(
                "SELECT tournament_id AS id, tournament AS name, count(*) AS matches FROM matches "
                "WHERE ? IN (home_team_id, away_team_id) GROUP BY ALL ORDER BY matches DESC LIMIT 8",
                [tid],
            ),
            "recent_matches": self.q(
                "SELECT match_id AS id, date, home_team, away_team, home_score, away_score, tournament "
                "FROM matches WHERE ? IN (home_team_id, away_team_id) ORDER BY date DESC LIMIT 8",
                [tid],
            ),
            "elo": self.q("SELECT year, round(elo) AS elo, rank FROM elo_yearly WHERE team_id = ? ORDER BY year", [tid]),
            "elo_peak": self.q(
                "SELECT round(max(elo)) AS elo, arg_max(date, elo) AS date FROM elo_history WHERE team_id = ?", [tid]
            )[0],
            "shootout_wins": self.q(
                "SELECT count(*) AS n FROM edges WHERE src = ? AND type = 'WON_SHOOTOUT'", [tid]
            )[0]["n"],
            "former_names": self.q(
                "SELECT former_id AS id, former_name AS name, start_date, end_date FROM former_names "
                "WHERE team_id = ?",
                [tid],
            ),
        }

    def _detail_player(self, pid: str) -> dict[str, Any]:
        p = self.q("SELECT * FROM players WHERE player_id = ?", [pid])
        return {
            "profile": p[0] if p else None,
            "hat_tricks": self.q(
                "SELECT count(*) AS n FROM (SELECT match_id FROM goals WHERE player_id = ? "
                "AND NOT own_goal GROUP BY match_id HAVING count(*) >= 3)",
                [pid],
            )[0]["n"],
            "by_tournament": self.q(
                "SELECT tournament AS name, count(*) AS goals FROM goals WHERE player_id = ? "
                "AND NOT own_goal GROUP BY ALL ORDER BY goals DESC LIMIT 6",
                [pid],
            ),
            "victims": self.q(
                "SELECT opponent AS name, count(*) AS goals FROM goals WHERE player_id = ? "
                "AND NOT own_goal GROUP BY ALL ORDER BY goals DESC LIMIT 6",
                [pid],
            ),
            "clubs": self.q(
                "SELECT club_id AS id, club AS name, apps, goals, assists, minutes, league_apps, league_goals, "
                "europe_apps, europe_goals, first_app, last_app FROM club_careers WHERE player_id = ? "
                "ORDER BY first_app",
                [pid],
            ),
            "goals": self.q(
                """
                SELECT g.goal_id AS id, g.match_id, g.date, g.minute, g.penalty, g.own_goal, g.source,
                       g.opponent, g.tournament, m.home_team, m.away_team, m.home_score, m.away_score
                FROM goals g JOIN matches m USING (match_id)
                WHERE g.player_id = ? ORDER BY g.date DESC, g.minute DESC LIMIT 12
                """,
                [pid],
            ),
        }

    def _detail_club(self, cid: str) -> dict[str, Any]:
        c = self.q("SELECT * FROM clubs WHERE club_id = ?", [cid])
        leaders = """
            SELECT player_id AS id, player_name AS name, nationality, apps, goals, assists, first_app, last_app
            FROM club_careers WHERE club_id = ? ORDER BY {col} DESC, apps DESC LIMIT 10
        """
        return {
            "club": c[0] if c else None,
            "totals": self.q(
                "SELECT count(*) AS players, sum(apps) AS apps, sum(goals) AS goals, sum(assists) AS assists, "
                "count(player_id) AS internationals, min(first_app) AS first_app, max(last_app) AS last_app "
                "FROM club_careers WHERE club_id = ?",
                [cid],
            )[0],
            "top_scorers": self.q(leaders.format(col="goals"), [cid]),
            "top_assisters": self.q(leaders.format(col="assists"), [cid]),
            "most_apps": self.q(leaders.format(col="apps"), [cid]),
            "seasons": self.q(
                "SELECT season, sum(played) AS played, sum(wins) AS wins, sum(draws) AS draws, "
                "sum(losses) AS losses, sum(goals_for) AS goals_for, sum(goals_against) AS goals_against, "
                "sum(played) FILTER (WHERE competition_type = 'domestic_league') AS league_played, "
                "sum(wins) FILTER (WHERE competition_type = 'domestic_league') AS league_wins "
                "FROM club_seasons WHERE club_id = ? GROUP BY season ORDER BY season",
                [cid],
            ),
            "nations": self.q(
                "SELECT nationality AS name, count(*) AS players FROM club_careers WHERE club_id = ? "
                "AND nationality IS NOT NULL GROUP BY ALL ORDER BY players DESC LIMIT 8",
                [cid],
            ),
        }

    def _detail_match(self, mid: str) -> dict[str, Any]:
        m = self.q("SELECT * FROM matches WHERE match_id = ?", [mid])
        return {
            "match": m[0] if m else None,
            "goals": self.q(
                "SELECT goal_id AS id, player_id, scorer, team, minute, penalty, own_goal, assister, source "
                "FROM goals WHERE match_id = ? ORDER BY minute NULLS LAST",
                [mid],
            ),
        }

    def _detail_goal(self, gid: str) -> dict[str, Any]:
        g = self.q("SELECT * FROM goals WHERE goal_id = ?", [gid])
        return {"goal": g[0] if g else None}

    def _detail_tournament(self, tid: str) -> dict[str, Any]:
        t = self.q("SELECT * FROM tournaments WHERE tournament_id = ?", [tid])
        return {
            "summary": t[0] if t else None,
            "most_wins": self.q(
                "SELECT winner AS name, count(*) AS wins FROM matches WHERE tournament_id = ? "
                "AND winner IS NOT NULL GROUP BY ALL ORDER BY wins DESC LIMIT 8",
                [tid],
            ),
            "top_scorers": self.q(
                "SELECT player_id AS id, scorer AS name, team, count(*) AS goals FROM goals "
                "WHERE tournament = (SELECT name FROM tournaments WHERE tournament_id = ?) "
                "AND NOT own_goal GROUP BY ALL ORDER BY goals DESC LIMIT 8",
                [tid],
            ),
            "hosts": self.q(
                "SELECT country AS name, count(*) AS matches FROM matches WHERE tournament_id = ? "
                "GROUP BY ALL ORDER BY matches DESC LIMIT 6",
                [tid],
            ),
        }

    def _detail_country(self, cid: str) -> dict[str, Any]:
        return {
            "hosted": self.q(
                "SELECT count(*) AS matches, min(date) AS first, max(date) AS last FROM matches "
                "WHERE country_id = ?",
                [cid],
            )[0],
            "cities": self.q(
                "SELECT city_id AS id, city AS name, count(*) AS matches FROM matches WHERE country_id = ? "
                "GROUP BY ALL ORDER BY matches DESC LIMIT 8",
                [cid],
            ),
        }

    def _detail_city(self, cid: str) -> dict[str, Any]:
        return {
            "hosted": self.q(
                "SELECT count(*) AS matches, min(date) AS first, max(date) AS last, any_value(country) AS country "
                "FROM matches WHERE city_id = ?",
                [cid],
            )[0],
            "recent_matches": self.q(
                "SELECT match_id AS id, date, home_team, away_team, home_score, away_score, tournament "
                "FROM matches WHERE city_id = ? ORDER BY date DESC LIMIT 8",
                [cid],
            ),
        }

    def _detail_formername(self, fid: str) -> dict[str, Any]:
        f = self.q("SELECT * FROM former_names WHERE former_id = ?", [fid])
        return {"former": f[0] if f else None}

    # ------------------------------------------------------------------ graph exploration
    def expand(self, node_id: str, limit: int = 15, rel_type: str | None = None,
               direction: str | None = None) -> dict[str, Any]:
        """Neighbourhood of a node, capped per (relationship, direction) group.

        Matches are ranked most-recent first, everything else by degree (importance).
        """
        type_filter = "AND type = ?" if rel_type else ""
        dir_filter = "AND direction = ?" if direction else ""
        extra = ([rel_type] if rel_type else [])
        rows = self.q(
            f"""
            WITH nb AS (
                SELECT e.type, 'out' AS direction, e.src, e.dst, e.dst AS other, e.side, e.goals
                FROM edges e WHERE e.src = ? {type_filter}
                UNION ALL
                SELECT e.type, 'in', e.src, e.dst, e.src, e.side, e.goals
                FROM edges e WHERE e.dst = ? {type_filter}
            ), ranked AS (
                SELECT nb.*, n.label, n.name, n.degree,
                       count(*) OVER (PARTITION BY nb.type, nb.direction) AS group_total,
                       row_number() OVER (
                           PARTITION BY nb.type, nb.direction
                           ORDER BY m.date DESC NULLS LAST, n.degree DESC, n.id
                       ) AS rn
                FROM nb JOIN nodes n ON n.id = nb.other
                LEFT JOIN matches m ON m.match_id = nb.other
                WHERE 1=1 {dir_filter}
            )
            SELECT * FROM ranked WHERE rn <= ? ORDER BY type, direction, rn
            """,
            [node_id, *extra, node_id, *extra, *([direction] if direction else []), limit],
        )
        center = self.node(node_id)
        nodes = {node_id: center} if center else {}
        edges, truncated = [], {}
        for r in rows:
            nodes[r["other"]] = {"id": r["other"], "label": r["label"], "name": r["name"], "degree": r["degree"]}
            edges.append(_edge(r["src"], r["dst"], r["type"], side=r["side"], goals=r["goals"]))
            key = f"{r['type']}:{r['direction']}"
            if r["group_total"] > limit:
                truncated[key] = r["group_total"]
        return {"nodes": list(nodes.values()), "edges": edges, "truncated": truncated}

    def subgraph(self, ids: list[str]) -> dict[str, Any]:
        """The given nodes plus every edge that runs between two of them."""
        ids = list(dict.fromkeys(i for i in ids if i))[:500]
        if not ids:
            return {"nodes": [], "edges": []}
        ph = ",".join("?" * len(ids))
        nodes = self.q(f"SELECT id, label, name, degree FROM nodes WHERE id IN ({ph})", ids)
        edges = self.q(
            f"SELECT src, dst, type, side, goals FROM edges WHERE src IN ({ph}) AND dst IN ({ph})",
            ids + ids,
        )
        return {"nodes": nodes, "edges": [_edge(**e) for e in edges]}

    # One-hop "anchors" that give a set of entities visible structure when drawn together.
    ANCHORS = """
        SELECT e.dst AS anchor FROM edges e WHERE e.src IN ({ph}) AND e.type IN ('PLAYS_FOR', 'IN_MATCH', 'PART_OF', 'LOCATED_IN')
        UNION
        SELECT e.src FROM edges e WHERE e.dst IN ({ph}) AND e.type IN ('PLAYED_IN', 'SCORED', 'FORMERLY_KNOWN_AS')
    """

    def subgraph_with_context(self, ids: list[str]) -> dict[str, Any]:
        """Like subgraph(), plus anchors (player->team, match->teams/tournament, goal->scorer/match)."""
        ids = list(dict.fromkeys(i for i in ids if i))[:80]
        if not ids:
            return {"nodes": [], "edges": []}
        ph = ",".join("?" * len(ids))
        anchors = [r["anchor"] for r in self.q(self.ANCHORS.format(ph=ph), ids + ids)]
        # A handful of teams with nothing between them: show their recent head-to-heads.
        teams = [i for i in ids if i.startswith("T:")]
        if 2 <= len(teams) <= 3 and not any(i.startswith(("M", "G")) for i in ids):
            tp = ",".join("?" * len(teams))
            anchors += [r["match_id"] for r in self.q(
                f"SELECT match_id FROM matches WHERE home_team_id IN ({tp}) AND away_team_id IN ({tp}) "
                "ORDER BY date DESC LIMIT 16", teams + teams)]
        return self.subgraph(ids + anchors)

    # Overview groups = confederations (teams.confederation, derived in build_db). Fixed slot order keeps
    # colours stable across eras; Non-FIFA sides use the neutral slot 7.
    CONFEDERATIONS = [
        ("UEFA", "Europe"), ("CONMEBOL", "South America"), ("CONCACAF", "North & Central America"),
        ("CAF", "Africa"), ("AFC", "Asia"), ("OFC", "Oceania"),
    ]
    CONF_SLOT = {c: i for i, (c, _) in enumerate(CONFEDERATIONS)}

    def overview(self, top: int = 200, min_meetings: int | None = None,
                 start: int | None = None, end: int | None = None, backbone: int = 3) -> dict[str, Any]:
        """The international football "universe" for an era: the most active teams, linked when they met
        often, plus each team's `backbone` most frequent opponents so no team floats alone.

        Node size follows the team's Elo at the end of the era; colour/grouping follows its confederation.
        """
        start = start or 1872
        end = end or 2100
        span = max(1, min(end, 2026) - max(start, 1872) + 1)
        min_meetings = min_meetings or max(4, round(span * 0.1))
        era = "date BETWEEN make_date(?, 1, 1) AND make_date(?, 12, 31)"
        teams = self.q(
            f"""
            WITH t AS (
                SELECT home_team_id AS id, home_team AS name FROM matches WHERE {era}
                UNION ALL SELECT away_team_id, away_team FROM matches WHERE {era}
            ), counted AS (
                SELECT id, any_value(name) AS name, count(*) AS matches FROM t GROUP BY id
                ORDER BY matches DESC LIMIT ?
            )
            SELECT c.*, e.elo, tm.confederation FROM counted c
            JOIN teams tm ON tm.team_id = c.id
            LEFT JOIN elo_yearly e ON e.team_id = c.id
                 AND e.year = (SELECT max(year) FROM elo_yearly x WHERE x.team_id = c.id AND x.year <= ?)
            ORDER BY c.matches DESC
            """,
            [start, end, start, end, top, end],
        )
        conf_out = [{"key": c, "name": n, "slot": i, "teams": 0} for i, (c, n) in enumerate(self.CONFEDERATIONS)]
        conf_out.append({"key": "Non-FIFA", "name": "Non-FIFA sides", "slot": 7, "teams": 0})
        if not teams:
            return {"nodes": [], "edges": [], "confederations": conf_out}
        ids = [t["id"] for t in teams]
        ph = ",".join("?" * len(ids))
        pairs = self.q(
            f"""
            WITH p AS (
                SELECT least(home_team_id, away_team_id) AS a, greatest(home_team_id, away_team_id) AS b,
                       count(*) AS meetings
                FROM matches
                WHERE home_team_id IN ({ph}) AND away_team_id IN ({ph}) AND {era}
                GROUP BY ALL
            ), ranked AS (
                SELECT a, b, meetings, row_number() OVER (PARTITION BY a ORDER BY meetings DESC, b) AS r
                FROM (SELECT a, b, meetings FROM p UNION ALL SELECT b, a, meetings FROM p)
            )
            SELECT least(a, b) AS a, greatest(a, b) AS b, any_value(meetings) AS meetings,
                   bool_and(meetings < ?) AS backbone
            FROM ranked WHERE meetings >= ? OR r <= ?
            GROUP BY ALL
            """,
            ids + ids + [start, end, min_meetings, min_meetings, backbone],
        )
        nodes = []
        for t in teams:
            slot = self.CONF_SLOT.get(t["confederation"], 7)
            conf_out[slot if slot < 7 else -1]["teams"] += 1
            nodes.append({"id": t["id"], "label": "Team", "name": t["name"], "degree": t["matches"],
                          "elo": t["elo"], "group": slot, "conf": t["confederation"]})
        edges = [
            {"id": f"{p['a']}|MET|{p['b']}", "source": p["a"], "target": p["b"], "type": "MET",
             "weight": p["meetings"], "virtual": True, "backbone": p["backbone"]}
            for p in pairs
        ]
        return {"nodes": nodes, "edges": edges, "confederations": conf_out,
                "era": {"start": max(start, 1872), "end": min(end, 2026), "min_meetings": min_meetings}}

    # ------------------------------------------------------------------ head to head
    def h2h(self, a: str, b: str) -> dict[str, Any] | None:
        na, nb = self.node(a), self.node(b)
        if not na or not nb or na["label"] != "Team" or nb["label"] != "Team":
            return None
        both = "((home_team_id = ? AND away_team_id = ?) OR (home_team_id = ? AND away_team_id = ?))"
        bp = [a, b, b, a]
        summary = self.q(
            f"""
            SELECT count(*) AS matches,
                   count(*) FILTER (WHERE winner = ?) AS a_wins,
                   count(*) FILTER (WHERE winner = ?) AS b_wins,
                   count(*) FILTER (WHERE winner IS NULL) AS draws,
                   sum(CASE WHEN home_team_id = ? THEN home_score ELSE away_score END) AS a_goals,
                   sum(CASE WHEN home_team_id = ? THEN home_score ELSE away_score END) AS b_goals,
                   min(date) AS first, max(date) AS last
            FROM matches WHERE {both}
            """,
            [na["name"], nb["name"], a, b, *bp],
        )[0]
        by_decade = self.q(
            f"""
            SELECT (year(date) // 10) * 10 AS decade,
                   count(*) FILTER (WHERE winner = ?) AS a_wins,
                   count(*) FILTER (WHERE winner IS NULL) AS draws,
                   count(*) FILTER (WHERE winner = ?) AS b_wins
            FROM matches WHERE {both} GROUP BY ALL ORDER BY decade
            """,
            [na["name"], nb["name"], *bp],
        )
        cols = "match_id AS id, date, home_team, away_team, home_score, away_score, tournament, upset"

        def biggest(team: str) -> list[dict[str, Any]]:
            return self.q(
                f"SELECT {cols} FROM matches WHERE {both} AND winner = ? "
                "ORDER BY abs(home_score - away_score) DESC, date DESC LIMIT 3",
                [*bp, team],
            )

        recent = self.q(f"SELECT {cols} FROM matches WHERE {both} ORDER BY date DESC LIMIT 10", bp)
        competitions = self.q(
            f"SELECT tournament AS name, count(*) AS matches FROM matches WHERE {both} "
            "GROUP BY ALL ORDER BY matches DESC LIMIT 5",
            bp,
        )
        first_year = int(str(summary["first"])[:4]) if summary["first"] else 1872
        elo = self.q(
            "SELECT team_id, year, round(elo) AS elo FROM elo_yearly WHERE team_id IN (?, ?) AND year >= ? ORDER BY year",
            [a, b, first_year],
        )
        return {
            "a": na, "b": nb, "summary": summary, "by_decade": by_decade,
            "biggest_wins": {"a": biggest(na["name"]), "b": biggest(nb["name"])},
            "recent": recent, "competitions": competitions,
            "elo": {
                "a": [{"year": r["year"], "elo": r["elo"]} for r in elo if r["team_id"] == a],
                "b": [{"year": r["year"], "elo": r["elo"]} for r in elo if r["team_id"] == b],
            },
        }

    def h2h_graph(self, a: str, b: str, limit: int = 24) -> dict[str, Any]:
        ms = [r["match_id"] for r in self.q(
            "SELECT match_id FROM matches WHERE (home_team_id = ? AND away_team_id = ?) "
            "OR (home_team_id = ? AND away_team_id = ?) ORDER BY date DESC LIMIT ?",
            [a, b, b, a, limit])]
        return self.subgraph([a, b, *ms])

    # ------------------------------------------------------------------ discover
    @cached_property
    def discover(self) -> dict[str, Any]:
        """Precomputed insights for the Discover panel."""
        last_year = self.q("SELECT max(year) AS y FROM elo_yearly")[0]["y"]
        top_now = self.q(
            """
            SELECT e.team_id AS id, e.team AS name, round(e.elo) AS elo,
                   round(e.elo - coalesce(p.elo, e.elo)) AS change
            FROM elo_yearly e
            LEFT JOIN elo_yearly p ON p.team_id = e.team_id AND p.year = e.year - 4
            JOIN teams t ON t.team_id = e.team_id
            WHERE e.year = ? AND t.last_match >= make_date(? - 2, 1, 1)
            ORDER BY e.elo DESC LIMIT 10
            """,
            [last_year, last_year],
        )
        peaks = self.q(
            """
            SELECT h.team_id AS id, t.name, round(max(h.elo)) AS elo, arg_max(h.date, h.elo) AS date
            FROM elo_history h JOIN teams t USING (team_id)
            GROUP BY ALL ORDER BY elo DESC LIMIT 10
            """
        )
        upsets = self.q(
            """
            SELECT match_id AS id, date, home_team, away_team, home_score, away_score, tournament,
                   winner, winner_win_exp
            FROM matches
            WHERE upset AND tournament <> 'Friendly'
              AND least(home_elo, away_elo) > 1600   -- between established teams
            ORDER BY winner_win_exp LIMIT 10
            """
        )
        rivalries = self.q(
            """
            SELECT least(home_team_id, away_team_id) AS a, greatest(home_team_id, away_team_id) AS b,
                   any_value(CASE WHEN home_team_id < away_team_id THEN home_team ELSE away_team END) AS a_name,
                   any_value(CASE WHEN home_team_id < away_team_id THEN away_team ELSE home_team END) AS b_name,
                   count(*) AS meetings, min(year(date)) AS since
            FROM matches GROUP BY ALL ORDER BY meetings DESC LIMIT 10
            """
        )
        # Globetrotters: the widest range of different opponents (graph degree in the team projection)
        central = self.q(
            """
            WITH pairs AS (
                SELECT home_team_id AS id, away_team_id AS opp FROM matches
                UNION SELECT away_team_id, home_team_id FROM matches
            )
            SELECT p.id, t.name, count(*) AS score FROM pairs p JOIN teams t ON t.team_id = p.id
            GROUP BY ALL ORDER BY score DESC LIMIT 10
            """
        )
        return {"year": last_year, "top_elo": top_now, "peaks": peaks, "upsets": upsets,
                "rivalries": rivalries, "central": central}

    # ------------------------------------------------------------------ path finding
    def _graph_for_paths(self) -> nx.Graph:
        with self._path_lock:
            if self._path_graph is None:
                cur = self.con.cursor()
                cur.execute(
                    f"""
                    SELECT e.src, e.dst FROM edges e
                    JOIN nodes a ON a.id = e.src JOIN nodes b ON b.id = e.dst
                    WHERE a.label NOT IN ({",".join("?" * len(PATH_EXCLUDED))})
                      AND b.label NOT IN ({",".join("?" * len(PATH_EXCLUDED))})
                    """,
                    [*PATH_EXCLUDED, *PATH_EXCLUDED],
                )
                g = nx.Graph()
                g.add_edges_from(cur.fetchall())
                cur.close()
                labels = dict(self.con.cursor().execute("SELECT id, label FROM nodes").fetchall())
                nx.set_node_attributes(g, {n: labels[n] for n in g}, "label")
                self._path_graph = g
            return self._path_graph

    def find_path(self, src: str, dst: str, via_teams: bool = True) -> dict[str, Any]:
        """Shortest connection between two entities.

        via_teams=True allows hops through national teams (Player -> Team -> Match ...);
        via_teams=False only links players through goals scored in shared matches.
        """
        g = self._graph_for_paths()
        for n in (src, dst):
            if n not in g:
                return {"found": False, "reason": f"{n} is not a Player, Team, Match or Goal", "nodes": [], "edges": []}
        view = g if via_teams else nx.subgraph_view(
            g, filter_node=lambda n: g.nodes[n]["label"] not in ("Team", "Club") or n in (src, dst)
        )
        try:
            path = nx.bidirectional_shortest_path(view, src, dst)
        except nx.NetworkXNoPath:
            return {"found": False, "reason": "no connection", "nodes": [], "edges": []}
        sub = self.subgraph(path)
        on_path = {frozenset(p) for p in zip(path, path[1:])}
        sub["edges"] = [e for e in sub["edges"] if frozenset((e["source"], e["target"])) in on_path]
        order = {n: i for i, n in enumerate(path)}
        sub["nodes"].sort(key=lambda n: order[n["id"]])
        return {"found": True, "path": path, **sub}

    # ------------------------------------------------------------------ agent SQL
    SQL_ALLOWED = re.compile(r"^\s*(with|select|from|summarize|describe|show)\b", re.I)

    def run_sql(self, sql: str, max_rows: int = 200, timeout_s: float = 20.0) -> dict[str, Any]:
        """Run a single read-only query (the connection is read-only with no file access)."""
        clean = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.S).strip().rstrip(";").strip()
        if ";" in clean:
            raise ValueError("Only a single statement is allowed.")
        if not self.SQL_ALLOWED.match(clean):
            raise ValueError("Only read-only SELECT / WITH queries are allowed.")
        cur = self.con.cursor()
        timer = threading.Timer(timeout_s, cur.interrupt)
        timer.start()
        try:
            cur.execute(clean)
            cols = [d[0] for d in cur.description]
            data = cur.fetchmany(max_rows + 1)
        except duckdb.InterruptException:
            raise ValueError(f"Query exceeded {timeout_s:.0f}s and was cancelled; simplify it.")
        finally:
            timer.cancel()
            cur.close()
        truncated = len(data) > max_rows
        rows = [[_jsonable(v) for v in row] for row in data[:max_rows]]
        return {"columns": cols, "rows": rows, "row_count": len(rows), "truncated": truncated}

    @cached_property
    def schema_text(self) -> str:
        """Compact schema description for the agent's system prompt."""
        lines = []
        for t in ["matches", "goals", "players", "teams", "tournaments", "former_names", "clubs", "club_careers",
                  "club_seasons",
                  "elo_yearly", "elo_history", "nodes", "edges"]:
            cols = self.q(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = ? ORDER BY ordinal_position",
                [t],
            )
            n = self.q(f"SELECT count(*) AS n FROM {t}")[0]["n"]
            lines.append(f"{t} ({n:,} rows): " + ", ".join(f"{c['column_name']} {c['data_type']}" for c in cols))
        return "\n".join(lines)


def _edge(src: str, dst: str, type: str, side: str | None = None, goals: int | None = None) -> dict[str, Any]:
    e: dict[str, Any] = {"id": f"{src}|{type}|{dst}", "source": src, "target": dst, "type": type}
    if side:
        e["side"] = side
    if goals is not None:
        e["goals"] = goals
    return e
