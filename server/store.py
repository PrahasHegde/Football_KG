"""
Read-only query layer over app_data/football.duckdb.

Used by both the REST API (graph exploration) and the agent's tools.
Every public method opens its own cursor, so it is safe to call from FastAPI's thread pool.
"""
from __future__ import annotations

import math
import random
import re
import secrets
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
            "club_teammates": self.club_teammates(pid, 10),
            "scored_with": self.scored_with(pid, 8),
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

    # ------------------------------------------------------------------ teammates
    def club_teammates(self, pid: str, limit: int = 10) -> list[dict[str, Any]]:
        """Players who were at the same club at the same time (overlapping spells, 2012/13+), longest overlap
        first. `id` is set when the teammate is also in the international graph."""
        return self.q(
            """
            SELECT o.player_id AS id, o.player_name AS name, o.nationality, m.club_id, m.club,
                   greatest(m.first_app, o.first_app) AS since, least(m.last_app, o.last_app) AS until,
                   date_diff('day', greatest(m.first_app, o.first_app), least(m.last_app, o.last_app)) AS days
            FROM club_careers m JOIN club_careers o
                 ON o.club_id = m.club_id AND o.tm_player_id <> m.tm_player_id
                AND o.first_app <= m.last_app AND o.last_app >= m.first_app
            WHERE m.player_id = ? AND o.apps >= 10
            ORDER BY (o.player_id IS NOT NULL) DESC, days DESC LIMIT ?
            """,
            [pid, limit],
        )

    def scored_with(self, pid: str, limit: int = 8) -> list[dict[str, Any]]:
        """National-team teammates who scored in the same matches (same side)."""
        return self.q(
            """
            SELECT o.player_id AS id, any_value(o.scorer) AS name, count(DISTINCT o.match_id) AS matches,
                   min(o.date) AS first, max(o.date) AS last
            FROM goals m JOIN goals o ON o.match_id = m.match_id AND o.team_id = m.team_id AND o.player_id <> m.player_id
            WHERE m.player_id = ? AND NOT m.own_goal AND NOT o.own_goal
            GROUP BY o.player_id ORDER BY matches DESC, last DESC LIMIT ?
            """,
            [pid, limit],
        )

    # ------------------------------------------------------------------ player vs player
    def compare_players(self, a: str, b: str) -> dict[str, Any] | None:
        na, nb = self.node(a), self.node(b)
        if not na or not nb or na["label"] != "Player" or nb["label"] != "Player":
            return None

        def side(pid: str) -> dict[str, Any]:
            prof = self.q("SELECT * FROM players WHERE player_id = ?", [pid])
            club = self.q(
                "SELECT coalesce(sum(apps), 0) AS apps, coalesce(sum(goals), 0) AS goals, "
                "coalesce(sum(assists), 0) AS assists, coalesce(sum(minutes), 0) AS minutes FROM club_careers "
                "WHERE player_id = ?", [pid])[0]
            return {
                "id": pid, "profile": prof[0] if prof else None, "club_totals": club,
                "hat_tricks": self.q("SELECT count(*) AS n FROM (SELECT match_id FROM goals WHERE player_id = ? "
                                     "AND NOT own_goal GROUP BY match_id HAVING count(*) >= 3)", [pid])[0]["n"],
                "by_tournament": self.q("SELECT tournament AS name, count(*) AS goals FROM goals WHERE player_id = ? "
                                        "AND NOT own_goal GROUP BY ALL ORDER BY goals DESC LIMIT 5", [pid]),
                "clubs": self.q("SELECT club_id AS id, club AS name, apps, goals, assists, first_app, last_app "
                                "FROM club_careers WHERE player_id = ? ORDER BY first_app", [pid]),
                "by_year": self.q("SELECT year(date) AS year, count(*) AS goals FROM goals WHERE player_id = ? "
                                  "AND NOT own_goal GROUP BY ALL ORDER BY year", [pid]),
            }

        shared_matches = self.q(
            """
            SELECT m.match_id AS id, m.date, m.home_team, m.away_team, m.home_score, m.away_score, m.tournament,
                   (ga.team_id = gb.team_id) AS same_side
            FROM (SELECT DISTINCT match_id, team_id FROM goals WHERE player_id = ? AND NOT own_goal) ga
            JOIN (SELECT DISTINCT match_id, team_id FROM goals WHERE player_id = ? AND NOT own_goal) gb USING (match_id)
            JOIN matches m USING (match_id) ORDER BY m.date DESC LIMIT 12
            """,
            [a, b],
        )
        shared_clubs = self.q(
            """
            SELECT ca.club_id AS id, ca.club AS name,
                   (ca.first_app <= cb.last_app AND ca.last_app >= cb.first_app) AS together
            FROM club_careers ca JOIN club_careers cb USING (club_id)
            WHERE ca.player_id = ? AND cb.player_id = ?
            """,
            [a, b],
        )
        ta = {(t["id"] or t["name"]) for t in self.club_teammates(a, 400)}
        tb = {(t["id"] or t["name"]) for t in self.club_teammates(b, 400)}
        common = sorted(x for x in ta & tb if x not in (a, b))
        return {"a": side(a), "b": side(b), "shared_matches": shared_matches, "shared_clubs": shared_clubs,
                "common_teammates": len(common),
                "common_teammates_sample": [self.node(x)["name"] if x.startswith("P:") and self.node(x) else x
                                            for x in common[:8]]}

    # ------------------------------------------------------------------ "guess the player" game
    _quizzes: dict[str, str] = {}
    _quiz_lock = threading.Lock()
    QUIZ_LEVELS = {"easy": (40, 1995), "medium": (25, 1975), "hard": (12, 1872)}  # (min goals, last goal since)

    def quiz_new(self, level: str = "easy") -> dict[str, Any]:
        min_goals, since = self.QUIZ_LEVELS.get(level, self.QUIZ_LEVELS["easy"])
        pool = self.q(
            "SELECT player_id FROM players WHERE total_goals >= ? AND year(last_goal_date) >= ?",
            [min_goals, since],
        )
        pid = random.choice(pool)["player_id"]
        p = self.q("SELECT p.*, t.confederation FROM players p JOIN teams t ON t.team_id = p.team_id "
                   "WHERE player_id = ?", [pid])[0]
        top_comp = self.q("SELECT tournament, count(*) AS n FROM goals WHERE player_id = ? AND NOT own_goal "
                          "GROUP BY ALL ORDER BY n DESC LIMIT 1", [pid])
        victim = self.q("SELECT opponent, count(*) AS n FROM goals WHERE player_id = ? AND NOT own_goal "
                        "GROUP BY ALL ORDER BY n DESC LIMIT 1", [pid])
        clubs = [c["club"] for c in self.q("SELECT club FROM club_careers WHERE player_id = ? ORDER BY apps DESC "
                                           "LIMIT 3", [pid])]
        y0, y1 = str(p["first_goal_date"])[:4], str(p["last_goal_date"])[:4]
        words = p["name"].split()
        clues = [
            {"icon": "globe", "label": "Confederation",
             "text": f"Plays (or played) in {p['confederation']}" + (f" · {p['position']}" if p.get("position") else "")},
            {"icon": "calendar", "label": "Era", "text": f"Scored for their country between {y0} and {y1}"},
            {"icon": "trophy", "label": "Favourite competition",
             "text": f"Most goals in: {top_comp[0]['tournament']} ({top_comp[0]['n']})" if top_comp else "–"},
            {"icon": "target", "label": "Favourite victim",
             "text": f"Scored most against {victim[0]['opponent']} ({victim[0]['n']})" if victim else "–"},
            {"icon": "goal", "label": "Goals", "text": f"{p['total_goals']} international goals in the graph"},
            {"icon": "shield", "label": "Clubs",
             "text": ("Clubs since 2012: " + ", ".join(clubs)) if clubs else "No club data (career before 2012)"},
            {"icon": "flag", "label": "Nation", "text": f"Plays for {p['team']}"},
            {"icon": "type", "label": "Initials",
             "text": " ".join(w[0] + "." for w in words) + f" ({len(words)} word{'s' if len(words) > 1 else ''})"},
        ]
        token = secrets.token_urlsafe(9)
        with self._quiz_lock:
            if len(self._quizzes) > 2000:
                self._quizzes.clear()
            self._quizzes[token] = pid
        return {"token": token, "level": level, "clues": clues}

    def quiz_guess(self, token: str, guess: str | None, reveal: bool = False) -> dict[str, Any] | None:
        with self._quiz_lock:
            pid = self._quizzes.get(token)
        if not pid:
            return None
        correct = guess == pid
        out: dict[str, Any] = {"correct": correct}
        if correct or reveal:
            n = self.node(pid)
            out["answer"] = {"id": pid, "name": n["name"] if n else pid,
                             "team": self.q("SELECT team FROM players WHERE player_id = ?", [pid])[0]["team"]}
            with self._quiz_lock:
                self._quizzes.pop(token, None)
        return out

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

    # ------------------------------------------------------------------ club universe / pipeline
    # Seven big leagues get their own palette slot (fixed order); every other league is "Rest of world" (slot 7).
    LEAGUES = [("Premier League", "England"), ("LaLiga", "Spain"), ("Bundesliga", "Germany"), ("Serie A", "Italy"),
               ("Ligue 1", "France"), ("Eredivisie", "Netherlands"), ("Liga Portugal", "Portugal")]
    LEAGUE_SLOT = {name: i for i, (name, _) in enumerate(LEAGUES)}

    def _club_rows(self, ids: list[str] | None = None, others: int = 24) -> list[dict[str, Any]]:
        """Clubs for the universe: big-league clubs with 4+ league seasons since 2012/13, plus the `others`
        winningest clubs elsewhere. Win rate over all competitions drives node size."""
        ph = ",".join("?" * len(self.LEAGUE_SLOT))
        return self.q(
            f"""
            WITH rec AS (
                SELECT club_id, sum(played) AS played, sum(wins) AS wins, sum(draws) AS draws,
                       sum(goals_for) AS goals_for, sum(goals_against) AS goals_against,
                       count(DISTINCT season) FILTER (WHERE competition_type = 'domestic_league') AS seasons
                FROM club_seasons GROUP BY club_id
            ), pick AS (
                SELECT c.club_id, c.name, c.league, c.country, r.*, c.league IN ({ph}) AS big,
                       row_number() OVER (PARTITION BY c.league IN ({ph}) ORDER BY r.wins DESC) AS rk
                FROM clubs c JOIN rec r USING (club_id)
            )
            SELECT * FROM pick WHERE (big AND seasons >= 4) OR (NOT big AND rk <= ?)
            ORDER BY wins DESC
            """,
            [*self.LEAGUE_SLOT, *self.LEAGUE_SLOT, others],
        )

    def _league_groups(self, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = [{"key": n, "name": country, "slot": i, "teams": 0} for i, (n, country) in enumerate(self.LEAGUES)]
        out.append({"key": "Rest of world", "name": "Other leagues", "slot": 7, "teams": 0})
        for n in nodes:
            out[n["group"] if n["group"] < 7 else -1]["teams"] += 1
        return out

    def club_universe(self, min_shared: int = 5, backbone: int = 2) -> dict[str, Any]:
        """Clubs since 2012/13, clustered by league, linked by the players they shared (5+ apps at both)."""
        clubs = self._club_rows()
        if not clubs:
            return {"nodes": [], "edges": [], "groups": []}
        ids = [c["club_id"] for c in clubs]
        ph = ",".join("?" * len(ids))
        pairs = self.q(
            f"""
            WITH p AS (
                SELECT a.club_id AS a, b.club_id AS b, count(*) AS shared
                FROM club_careers a JOIN club_careers b ON a.tm_player_id = b.tm_player_id AND a.club_id < b.club_id
                WHERE a.apps >= 5 AND b.apps >= 5 AND a.club_id IN ({ph}) AND b.club_id IN ({ph})
                GROUP BY ALL
            ), ranked AS (
                SELECT a, b, shared, row_number() OVER (PARTITION BY a ORDER BY shared DESC, b) AS r
                FROM (SELECT a, b, shared FROM p UNION ALL SELECT b, a, shared FROM p)
            )
            SELECT least(a, b) AS a, greatest(a, b) AS b, any_value(shared) AS shared,
                   bool_and(shared < ?) AS backbone
            FROM ranked WHERE shared >= ? OR r <= ? GROUP BY ALL
            """,
            ids + ids + [min_shared, min_shared, backbone],
        )
        nodes = []
        for c in clubs:
            slot = self.LEAGUE_SLOT.get(c["league"], 7)
            wr = c["wins"] / c["played"] if c["played"] else 0
            nodes.append({
                "id": c["club_id"], "label": "Club", "name": c["name"], "degree": c["played"], "group": slot,
                "conf": c["league"] if slot < 7 else "Rest of world",
                "strength": max(0.0, min(1.0, (wr - 0.25) / 0.5)),
                "subtitle": f"{c['league']} · {round(100 * wr)}% wins · {c['played']:,} games since 2012/13",
            })
        edges = [{"id": f"{e['a']}|SHARED|{e['b']}", "source": e["a"], "target": e["b"], "type": "SHARED_PLAYERS",
                  "weight": e["shared"], "unit": "shared players", "virtual": True, "backbone": e["backbone"]}
                 for e in pairs]
        return {"nodes": nodes, "edges": edges, "groups": self._league_groups(nodes)}

    def pipeline(self, nations: int = 40, clubs: int = 64, min_players: int = 3) -> dict[str, Any]:
        """Talent pipeline: the nations with the most internationals in club football (outer ring, by
        confederation) and the clubs that used the most of them (inner ring, by league). A link = how many of
        that nation's internationals played for that club (since 2012/13)."""
        teams = self.q(
            """
            SELECT p.team_id AS id, p.team AS name, t.confederation AS conf, count(DISTINCT cc.player_id) AS players,
                   (SELECT elo FROM elo_yearly e WHERE e.team_id = p.team_id ORDER BY year DESC LIMIT 1) AS elo
            FROM club_careers cc JOIN players p USING (player_id) JOIN teams t ON t.team_id = p.team_id
            GROUP BY ALL ORDER BY players DESC LIMIT ?
            """,
            [nations],
        )
        if not teams:
            return {"nodes": [], "edges": [], "groups": []}
        tids = [t["id"] for t in teams]
        tph = ",".join("?" * len(tids))
        club_rows = self.q(
            f"""
            SELECT c.club_id AS id, c.name, c.league, count(DISTINCT cc.player_id) AS players
            FROM club_careers cc JOIN players p USING (player_id) JOIN clubs c ON c.club_id = cc.club_id
            WHERE p.team_id IN ({tph}) GROUP BY ALL ORDER BY players DESC LIMIT ?
            """,
            tids + [clubs],
        )
        cids = [c["id"] for c in club_rows]
        cph = ",".join("?" * len(cids))
        links = self.q(
            f"""
            SELECT p.team_id AS team, cc.club_id AS club, count(DISTINCT cc.player_id) AS players
            FROM club_careers cc JOIN players p USING (player_id)
            WHERE p.team_id IN ({tph}) AND cc.club_id IN ({cph}) GROUP BY ALL HAVING count(DISTINCT cc.player_id) >= ?
            """,
            tids + cids + [min_players],
        )

        def ring(items: list[dict[str, Any]], key, radius: float, gap: float) -> None:
            """Spread items round a circle, grouped by key, with a gap between groups."""
            items.sort(key=lambda x: (key(x), -x["players"]))
            groups = [k for i, k in enumerate(map(key, items)) if i == 0 or k != key(items[i - 1])]
            step = (2 * math.pi - gap * len(groups)) / max(1, len(items))
            a = -math.pi / 2
            prev = None
            for it in items:
                if prev is not None and key(it) != prev:
                    a += gap
                it["hx"], it["hy"] = radius * math.cos(a), radius * math.sin(a)
                a += step
                prev = key(it)

        conf_slot = self.CONF_SLOT
        ring(teams, lambda t: conf_slot.get(t["conf"], 7), 520, 0.12)
        ring(club_rows, lambda c: self.LEAGUE_SLOT.get(c["league"], 7), 300, 0.16)
        nodes = []
        for t in teams:
            nodes.append({"id": t["id"], "label": "Team", "name": t["name"], "degree": t["players"],
                          "elo": t["elo"], "group": conf_slot.get(t["conf"], 7), "conf": t["conf"],
                          "hx": t["hx"], "hy": t["hy"],
                          "subtitle": f"{t['conf']} · {t['players']} internationals in club data"})
        for c in club_rows:
            nodes.append({"id": c["id"], "label": "Club", "name": c["name"], "degree": c["players"], "group": 7,
                          "conf": c["league"] if c["league"] in self.LEAGUE_SLOT else "Rest of world",
                          "strength": min(1.0, c["players"] / 110), "hx": c["hx"], "hy": c["hy"],
                          "subtitle": f"{c['league']} · {c['players']} internationals from these nations"})
        edges = [{"id": f"{e['team']}|PIPELINE|{e['club']}", "source": e["team"], "target": e["club"],
                  "type": "PLAYERS_AT", "weight": e["players"], "unit": "internationals", "virtual": True}
                 for e in links]
        groups = [{"key": c, "name": n, "slot": i, "teams": sum(1 for t in teams if t["conf"] == c)}
                  for i, (c, n) in enumerate(self.CONFEDERATIONS)]
        return {"nodes": nodes, "edges": edges, "groups": [g for g in groups if g["teams"]]}

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
