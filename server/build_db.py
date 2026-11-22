"""
Build the application database (DuckDB) from the knowledge graph.

    python -m server.build_db            # football_kg.gpickle -> app_data/football.duckdb

The graph (football_kg.gpickle, produced by src/build_kg.py) stays the source of truth.
This step flattens it into:
  * nodes / edges            - the graph itself, used for exploration and path finding
  * matches / goals / players / teams / tournaments / former_names
                             - tidy analytical tables, used by the API and by the agent's SQL tool
"""
from __future__ import annotations

import pickle
import sys
import time
import unicodedata
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
GRAPH_PATH = ROOT / "football_kg.gpickle"
DB_PATH = ROOT / "app_data" / "football.duckdb"


def normalize(s: str) -> str:
    """Lowercase + strip accents, so 'Pele' finds 'Pelé'."""
    s = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def needs_rebuild() -> bool:
    return not DB_PATH.exists() or DB_PATH.stat().st_mtime < GRAPH_PATH.stat().st_mtime


def build(graph_path: Path = GRAPH_PATH, db_path: Path = DB_PATH) -> None:
    t0 = time.time()
    if not graph_path.exists():
        sys.exit(f"{graph_path} not found - run `python src/build_kg.py` first.")
    with open(graph_path, "rb") as fh:
        G = pickle.load(fh)

    nodes = pd.DataFrame(
        [
            {
                "id": n,
                "label": d["label"],
                "name": d.get("name", n),
                "date": d.get("date"),
                "home_score": d.get("home_score"),
                "away_score": d.get("away_score"),
                "neutral": d.get("neutral"),
                "minute": d.get("minute"),
                "penalty": d.get("penalty"),
                "own_goal": d.get("own_goal"),
                "source": d.get("source"),
            }
            for n, d in G.nodes(data=True)
        ]
    )
    # Goals have no name in the graph; give them a readable one later in SQL.
    nodes["name_norm"] = nodes["name"].map(normalize)
    edges = pd.DataFrame(
        [
            {
                "src": u,
                "dst": v,
                "type": d["type"],
                "side": d.get("side"),
                "goals": d.get("goals"),
                "start_date": d.get("start"),
                "end_date": d.get("end"),
                "source": d.get("source"),  # ASSISTED: 'dataset' | 'statsbomb'
            }
            for u, v, d in G.edges(data=True)
        ]
    )
    del G

    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".tmp.duckdb")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    con.register("nodes_df", nodes)
    con.register("edges_df", edges)

    con.execute("""
        CREATE TABLE raw_nodes AS SELECT * FROM nodes_df;
        CREATE TABLE edges AS
            SELECT src, dst, type, side, CAST(goals AS INTEGER) AS goals,
                   start_date, end_date, source
            FROM edges_df;
    """)

    # ---------- tidy tables ----------
    con.execute("""
        CREATE TABLE matches AS
        WITH side AS (
            SELECT e.dst AS match_id,
                   max(CASE WHEN e.side = 'home' THEN e.src END) AS home_team_id,
                   max(CASE WHEN e.side = 'away' THEN e.src END) AS away_team_id
            FROM edges e WHERE e.type = 'PLAYED_IN' GROUP BY e.dst
        )
        SELECT m.id AS match_id,
               CAST(m.date AS DATE) AS date,
               ht.name AS home_team, awt.name AS away_team,
               CAST(m.home_score AS INTEGER) AS home_score,
               CAST(m.away_score AS INTEGER) AS away_score,
               tr.name AS tournament,
               ci.name AS city, co.name AS country,
               CAST(m.neutral AS BOOLEAN) AS neutral,
               CASE WHEN m.home_score > m.away_score THEN ht.name
                    WHEN m.away_score > m.home_score THEN awt.name END AS winner,
               so.name AS shootout_winner,
               s.home_team_id, s.away_team_id,
               ptr.dst AS tournament_id, hin.dst AS city_id, loc.dst AS country_id
        FROM raw_nodes m
        JOIN side s ON s.match_id = m.id
        JOIN raw_nodes ht ON ht.id = s.home_team_id
        JOIN raw_nodes awt ON awt.id = s.away_team_id
        LEFT JOIN edges ptr ON ptr.src = m.id AND ptr.type = 'PART_OF'
        LEFT JOIN raw_nodes tr ON tr.id = ptr.dst
        LEFT JOIN edges hin ON hin.src = m.id AND hin.type = 'HELD_IN'
        LEFT JOIN raw_nodes ci ON ci.id = hin.dst
        LEFT JOIN edges loc ON loc.src = hin.dst AND loc.type = 'LOCATED_IN'
        LEFT JOIN raw_nodes co ON co.id = loc.dst
        LEFT JOIN edges sw ON sw.dst = m.id AND sw.type = 'WON_SHOOTOUT'
        LEFT JOIN raw_nodes so ON so.id = sw.src
        WHERE m.label = 'Match';
    """)

    con.execute("""
        CREATE TABLE goals AS
        SELECT g.id AS goal_id, im.dst AS match_id, m.date, m.tournament,
               t.name AS team, ft.dst AS team_id,
               CASE WHEN ft.dst = m.home_team_id THEN m.away_team ELSE m.home_team END AS opponent,
               sc.src AS player_id, p.name AS scorer,
               CAST(g.minute AS INTEGER) AS minute,
               CAST(g.penalty AS BOOLEAN) AS penalty,
               CAST(g.own_goal AS BOOLEAN) AS own_goal,
               asd.src AS assist_player_id, ap.name AS assister, asd.source AS assist_source,
               coalesce(g.source, 'dataset') AS source   -- 'dataset' (goalscorers.csv) | 'wikipedia' (gap-filling)
        FROM raw_nodes g
        JOIN edges im ON im.src = g.id AND im.type = 'IN_MATCH'
        JOIN matches m ON m.match_id = im.dst
        JOIN edges ft ON ft.src = g.id AND ft.type = 'FOR_TEAM'
        JOIN raw_nodes t ON t.id = ft.dst
        JOIN edges sc ON sc.dst = g.id AND sc.type = 'SCORED'
        JOIN raw_nodes p ON p.id = sc.src
        LEFT JOIN edges asd ON asd.dst = g.id AND asd.type = 'ASSISTED'
        LEFT JOIN raw_nodes ap ON ap.id = asd.src
        WHERE g.label = 'Goal';
    """)

    con.execute("""
        CREATE TABLE players AS
        SELECT p.id AS player_id, p.name, t.name AS team, pf.dst AS team_id,
               -- goals: from the dataset only (same coverage for everyone, so fair for rankings)
               count(g.goal_id) FILTER (WHERE NOT g.own_goal AND g.source = 'dataset') AS goals,
               -- wiki_goals: gap-filling goals from the player's Wikipedia goal list (~165 stars only)
               count(g.goal_id) FILTER (WHERE g.source = 'wikipedia')          AS wiki_goals,
               count(g.goal_id) FILTER (WHERE NOT g.own_goal)                 AS total_goals,
               count(g.goal_id) FILTER (WHERE NOT g.own_goal AND g.tournament = 'Friendly') AS friendly_goals,
               count(g.goal_id) FILTER (WHERE g.penalty AND NOT g.own_goal)   AS penalty_goals,
               count(g.goal_id) FILTER (WHERE g.own_goal)                     AS own_goals,
               (SELECT count(*) FROM goals a WHERE a.assist_player_id = p.id) AS assists,
               count(DISTINCT g.match_id) FILTER (WHERE NOT g.own_goal)       AS matches_scored_in,
               min(g.date) AS first_goal_date, max(g.date) AS last_goal_date
        FROM raw_nodes p
        JOIN edges pf ON pf.src = p.id AND pf.type = 'PLAYS_FOR'
        JOIN raw_nodes t ON t.id = pf.dst
        LEFT JOIN goals g ON g.player_id = p.id
        WHERE p.label = 'Player'
        GROUP BY ALL;
    """)

    con.execute("""
        CREATE TABLE teams AS
        WITH per_team AS (
            SELECT home_team_id AS team_id, home_score AS gf, away_score AS ga, date FROM matches
            UNION ALL
            SELECT away_team_id, away_score, home_score, date FROM matches
        )
        SELECT t.id AS team_id, t.name,
               count(*) AS matches,
               count(*) FILTER (WHERE gf > ga) AS wins,
               count(*) FILTER (WHERE gf = ga) AS draws,
               count(*) FILTER (WHERE gf < ga) AS losses,
               sum(gf) AS goals_for, sum(ga) AS goals_against,
               min(pt.date) AS first_match, max(pt.date) AS last_match
        FROM raw_nodes t JOIN per_team pt ON pt.team_id = t.id
        WHERE t.label = 'Team'
        GROUP BY ALL;
    """)

    con.execute("""
        CREATE TABLE tournaments AS
        SELECT tournament_id, tournament AS name, count(*) AS matches,
               min(date) AS first_match, max(date) AS last_match
        FROM matches GROUP BY ALL;

        CREATE TABLE former_names AS
        SELECT f.id AS former_id, f.name AS former_name, t.name AS team, e.src AS team_id,
               CAST(e.start_date AS DATE) AS start_date, CAST(e.end_date AS DATE) AS end_date
        FROM raw_nodes f
        JOIN edges e ON e.dst = f.id AND e.type = 'FORMERLY_KNOWN_AS'
        JOIN raw_nodes t ON t.id = e.src;
    """)

    add_elo(con)
    add_confederations(con)
    add_clubs(con)

    # ---------- graph node table with degree (importance) and readable names ----------
    con.execute("""
        CREATE TABLE nodes AS
        WITH deg AS (
            SELECT id, count(*) AS degree FROM (
                SELECT src AS id FROM edges UNION ALL SELECT dst FROM edges
            ) GROUP BY id
        )
        SELECT n.id, n.label,
               CASE WHEN n.label = 'Goal'
                    THEN g.scorer || ' ' || coalesce(CAST(g.minute AS VARCHAR) || '''', '')
                         || CASE WHEN g.own_goal THEN ' (o.g.)' WHEN g.penalty THEN ' (pen)' ELSE '' END
                    ELSE n.name END AS name,
               n.name_norm,
               coalesce(d.degree, 0) AS degree
        FROM raw_nodes n
        LEFT JOIN deg d ON d.id = n.id
        LEFT JOIN goals g ON g.goal_id = n.id;

        DROP TABLE raw_nodes;
        CREATE INDEX edges_src ON edges(src);
        CREATE INDEX edges_dst ON edges(dst);
        CREATE INDEX nodes_id ON nodes(id);
        CREATE INDEX matches_id ON matches(match_id);
        CREATE INDEX goals_match ON goals(match_id);
        CREATE INDEX goals_player ON goals(player_id);
    """)

    counts = con.execute("""
        SELECT 'nodes', count(*) FROM nodes UNION ALL SELECT 'edges', count(*) FROM edges
        UNION ALL SELECT 'matches', count(*) FROM matches UNION ALL SELECT 'goals', count(*) FROM goals
        UNION ALL SELECT 'players', count(*) FROM players UNION ALL SELECT 'teams', count(*) FROM teams
    """).fetchall()
    con.close()
    tmp.replace(db_path)
    print(f"Built {db_path.relative_to(ROOT)} in {time.time() - t0:.1f}s")
    for name, n in counts:
        print(f"  {name:8s} {n:,}")


EXTRA = ROOT / "data_extra"


def add_clubs(con: duckdb.DuckDBPyConnection) -> None:
    """Club tables from scripts/fetch_club_data.py (transfermarkt-datasets, 2012/13+). Optional: without the CSVs
    the tables exist but are empty, so queries and the UI keep working."""
    have = all((EXTRA / f).exists() for f in ("clubs.csv", "club_player_stats.csv", "club_seasons.csv",
                                              "player_profiles.csv"))
    if not have:
        con.execute("""
            CREATE TABLE clubs (club_id VARCHAR, tm_club_id INTEGER, name VARCHAR, league VARCHAR, country VARCHAR,
                                stadium VARCHAR, stadium_seats INTEGER, coach VARCHAR, squad_size INTEGER);
            CREATE TABLE club_careers (player_id VARCHAR, tm_player_id INTEGER, player_name VARCHAR,
                nationality VARCHAR, club_id VARCHAR, club VARCHAR, apps INTEGER, goals INTEGER, assists INTEGER,
                minutes INTEGER, league_apps INTEGER, league_goals INTEGER, league_assists INTEGER,
                europe_apps INTEGER, europe_goals INTEGER, first_app DATE, last_app DATE);
            CREATE TABLE club_seasons (club_id VARCHAR, season INTEGER, competition VARCHAR,
                competition_type VARCHAR, played INTEGER, wins INTEGER, draws INTEGER, losses INTEGER,
                goals_for INTEGER, goals_against INTEGER);
            ALTER TABLE players ADD COLUMN position VARCHAR;
            ALTER TABLE players ADD COLUMN date_of_birth DATE;
            ALTER TABLE players ADD COLUMN caps INTEGER;
        """)
        return
    q = lambda f: str(EXTRA / f).replace("\\", "/")  # noqa: E731
    con.execute(f"""
        CREATE TABLE clubs AS
        SELECT 'CL:' || replace(lower(trim(name)), ' ', '_') AS club_id, CAST(club_id AS INTEGER) AS tm_club_id,
               name, league, country, stadium, CAST(stadium_seats AS INTEGER) AS stadium_seats, coach,
               CAST(squad_size AS INTEGER) AS squad_size
        FROM read_csv_auto('{q("clubs.csv")}', header = true);

        CREATE TABLE club_careers AS
        SELECT s.kg_player_id AS player_id, CAST(s.tm_player_id AS INTEGER) AS tm_player_id, s.player_name,
               s.nationality, c.club_id, c.name AS club,
               CAST(s.apps AS INTEGER) AS apps, CAST(s.goals AS INTEGER) AS goals,
               CAST(s.assists AS INTEGER) AS assists, CAST(s.minutes AS INTEGER) AS minutes,
               CAST(s.league_apps AS INTEGER) AS league_apps, CAST(s.league_goals AS INTEGER) AS league_goals,
               CAST(s.league_assists AS INTEGER) AS league_assists, CAST(s.europe_apps AS INTEGER) AS europe_apps,
               CAST(s.europe_goals AS INTEGER) AS europe_goals,
               CAST(s.first_app AS DATE) AS first_app, CAST(s.last_app AS DATE) AS last_app
        FROM read_csv_auto('{q("club_player_stats.csv")}', header = true) s
        JOIN clubs c ON c.tm_club_id = s.club_id;

        CREATE TABLE club_seasons AS
        SELECT c.club_id, CAST(s.season AS INTEGER) AS season, s.competition, s.competition_type,
               CAST(s.played AS INTEGER) AS played, CAST(s.wins AS INTEGER) AS wins,
               CAST(s.draws AS INTEGER) AS draws, CAST(s.losses AS INTEGER) AS losses,
               CAST(s.goals_for AS INTEGER) AS goals_for, CAST(s.goals_against AS INTEGER) AS goals_against
        FROM read_csv_auto('{q("club_seasons.csv")}', header = true) s
        JOIN clubs c ON c.tm_club_id = s.club_id;

        ALTER TABLE players ADD COLUMN position VARCHAR;
        ALTER TABLE players ADD COLUMN date_of_birth DATE;
        ALTER TABLE players ADD COLUMN caps INTEGER;
        UPDATE players SET position = coalesce(p.sub_position, p.position),
                           date_of_birth = CAST(p.date_of_birth AS DATE),
                           caps = CAST(p.international_caps AS INTEGER)
        FROM read_csv_auto('{q("player_profiles.csv")}', header = true) p
        WHERE players.player_id = p.kg_player_id;
    """)


CONMEBOL = ("Argentina", "Bolivia", "Brazil", "Chile", "Colombia", "Ecuador", "Paraguay", "Peru", "Uruguay",
            "Venezuela")


def add_confederations(con: duckdb.DuckDBPyConnection) -> None:
    """teams.confederation, derived from the data: the confederation of the team's most recent
    continental competition (so Australia -> AFC, Israel / Kazakhstan -> UEFA). The Copa América invites
    guests, so CONMEBOL's ten members are listed explicitly. Teams with no continental fixtures -> 'Non-FIFA'."""
    conmebol = ", ".join(f"'{t}'" for t in CONMEBOL)
    con.execute(f"""
        ALTER TABLE teams ADD COLUMN confederation VARCHAR;
        UPDATE teams SET confederation = c.conf FROM (
            WITH tm AS (
                SELECT home_team_id AS team_id, tournament, date FROM matches
                UNION ALL SELECT away_team_id, tournament, date FROM matches
            ), tagged AS (
                SELECT team_id, date, CASE
                    WHEN tournament IN ('UEFA Euro', 'UEFA Euro qualification', 'UEFA Nations League') THEN 'UEFA'
                    WHEN tournament LIKE 'African Cup of Nations%' OR tournament LIKE 'African Nations Championship%'
                        THEN 'CAF'
                    WHEN tournament LIKE 'AFC %' OR tournament LIKE 'AFF Championship%' OR tournament LIKE 'EAFF%'
                        OR tournament LIKE 'SAFF%' OR tournament LIKE 'WAFF%' OR tournament LIKE 'Gulf Cup%' THEN 'AFC'
                    WHEN tournament LIKE 'Gold Cup%' OR tournament LIKE 'CONCACAF%'
                        OR tournament LIKE 'CFU Caribbean Cup%' OR tournament LIKE 'UNCAF%' OR tournament LIKE 'CCCF%'
                        THEN 'CONCACAF'
                    WHEN tournament LIKE 'Oceania Nations Cup%' OR tournament LIKE 'OFC %' THEN 'OFC'
                END AS conf
                FROM tm
            )
            SELECT t.team_id,
                   CASE WHEN t.name IN ({conmebol}) THEN 'CONMEBOL'
                        ELSE coalesce(arg_max(g.conf, g.date) FILTER (WHERE g.conf IS NOT NULL), 'Non-FIFA') END AS conf
            FROM teams t LEFT JOIN tagged g USING (team_id)
            GROUP BY t.team_id, t.name
        ) c WHERE teams.team_id = c.team_id;
    """)


def _elo_k(tournament: str) -> int:
    """K-factor by match importance (World Football Elo convention)."""
    if tournament == "FIFA World Cup":
        return 60
    if tournament in CONTINENTAL_FINALS:
        return 50
    if "qualification" in tournament or "Nations League" in tournament:
        return 40
    if tournament == "Friendly":
        return 20
    return 30


CONTINENTAL_FINALS = {
    "UEFA Euro", "Copa América", "African Cup of Nations", "AFC Asian Cup", "Gold Cup",
    "CONCACAF Championship", "Oceania Nations Cup", "Confederations Cup",
}


def add_elo(con: duckdb.DuckDBPyConnection) -> None:
    """Elo ratings replayed over every match in date order.

    Adds pre-match ratings, the favourite's win expectancy and an upset flag to `matches`,
    plus `elo_history` (rating after each match) and `elo_yearly` (year-end rating and rank).
    """
    df = con.execute(
        "SELECT match_id, date, home_team_id, away_team_id, home_score, away_score, tournament, neutral "
        "FROM matches ORDER BY date, CAST(substr(match_id, 2) AS INTEGER)"
    ).df()
    rating: dict[str, float] = {}
    pre_h, pre_a, exp_h, hist = [], [], [], []
    for r in df.itertuples(index=False):
        rh, ra = rating.get(r.home_team_id, 1500.0), rating.get(r.away_team_id, 1500.0)
        adv = 0 if r.neutral else 100
        e_home = 1 / (1 + 10 ** (-(rh + adv - ra) / 400))
        result = 1.0 if r.home_score > r.away_score else 0.5 if r.home_score == r.away_score else 0.0
        gd = abs(r.home_score - r.away_score)
        mult = 1 if gd <= 1 else 1.5 if gd == 2 else (11 + gd) / 8
        delta = _elo_k(r.tournament) * mult * (result - e_home)
        rating[r.home_team_id], rating[r.away_team_id] = rh + delta, ra - delta
        pre_h.append(rh); pre_a.append(ra); exp_h.append(e_home)
        hist.append((r.home_team_id, r.date, r.match_id, rh + delta))
        hist.append((r.away_team_id, r.date, r.match_id, ra - delta))

    elo = pd.DataFrame({"match_id": df.match_id, "home_elo": pre_h, "away_elo": pre_a, "home_win_exp": exp_h})
    history = pd.DataFrame(hist, columns=["team_id", "date", "match_id", "elo"])
    con.register("elo_df", elo)
    con.register("hist_df", history)
    con.execute("""
        CREATE TABLE matches2 AS
        SELECT m.*, round(e.home_elo) AS home_elo, round(e.away_elo) AS away_elo,
               round(e.home_win_exp, 3) AS home_win_exp,
               -- an upset: the winner's pre-match expectancy was below 25%
               CASE WHEN m.winner = m.home_team THEN e.home_win_exp < 0.25
                    WHEN m.winner = m.away_team THEN (1 - e.home_win_exp) < 0.25
                    ELSE false END AS upset,
               CASE WHEN m.winner = m.home_team THEN round(e.home_win_exp, 3)
                    WHEN m.winner = m.away_team THEN round(1 - e.home_win_exp, 3) END AS winner_win_exp
        FROM matches m JOIN elo_df e USING (match_id);
        DROP TABLE matches;
        ALTER TABLE matches2 RENAME TO matches;

        CREATE TABLE elo_history AS
        SELECT team_id, CAST(date AS DATE) AS date, match_id, round(elo, 1) AS elo FROM hist_df;

        -- year-end rating for every year a team was active (carried between matches)
        CREATE TABLE elo_yearly AS
        WITH last_per_year AS (
            SELECT team_id, year(date) AS year, arg_max(elo, date) AS elo
            FROM elo_history GROUP BY ALL
        ), span AS (
            SELECT team_id, min(year) AS y0, max(year) AS y1 FROM last_per_year GROUP BY ALL
        ), grid AS (
            SELECT s.team_id, y.year FROM span s, range(1872, 2027) y(year) WHERE y.year BETWEEN s.y0 AND s.y1
        ), filled AS (
            SELECT g.team_id, g.year,
                   last_value(l.elo IGNORE NULLS) OVER (PARTITION BY g.team_id ORDER BY g.year) AS elo
            FROM grid g LEFT JOIN last_per_year l USING (team_id, year)
        )
        SELECT f.team_id, t.name AS team, f.year, f.elo,
               rank() OVER (PARTITION BY f.year ORDER BY f.elo DESC) AS rank
        FROM filled f JOIN teams t USING (team_id);
    """)


if __name__ == "__main__":
    build()
