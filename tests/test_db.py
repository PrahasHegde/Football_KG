"""The app database built by server/build_db.py: tables and data-integrity invariants.

These are the guarantees the app and the agent rely on. If one fails after changing an import script or
the build, the change broke the data, not the test.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.needs_build

TABLES = ["nodes", "edges", "matches", "goals", "players", "teams", "tournaments", "former_names",
          "elo_history", "elo_yearly", "clubs", "club_careers", "club_seasons"]

# Known faults in the SOURCE dataset (data/ is never edited); anything beyond these is a regression.
KNOWN_OVER_SCORE = {("M1811", "T:romania")}  # Switzerland 2-0 Romania (1933) lists two Romanian goals


def one(store, sql: str, params=None):
    return next(iter(store.q(sql, params)[0].values()))


@pytest.mark.parametrize("table", TABLES)
def test_table_exists_and_is_not_empty(store, table):
    assert one(store, f"SELECT count(*) FROM {table}") > 0


def test_imported_goals_never_exceed_the_score(store):
    """Dataset + Wikipedia goals credited to a team in a match never exceed what the team scored."""
    rows = store.q("""
        SELECT g.match_id, g.team_id FROM (SELECT match_id, team_id, count(*) AS n FROM goals GROUP BY ALL) g
        JOIN matches m USING (match_id)
        WHERE g.n > CASE WHEN g.team_id = m.home_team_id THEN m.home_score ELSE m.away_score END
    """)
    assert {(r["match_id"], r["team_id"]) for r in rows} <= KNOWN_OVER_SCORE


def test_goals_belong_to_a_team_in_the_match(store):
    assert one(store, """SELECT count(*) FROM goals g JOIN matches m USING (match_id)
                         WHERE g.team_id NOT IN (m.home_team_id, m.away_team_id)""") == 0


def test_player_goal_columns_are_consistent(store):
    assert one(store, "SELECT count(*) FROM players WHERE total_goals <> goals + wiki_goals") == 0
    assert one(store, "SELECT count(*) FROM players WHERE friendly_goals > total_goals") == 0
    assert one(store, "SELECT count(*) - count(DISTINCT player_id) FROM players") == 0


def test_winner_column_matches_the_score(store):
    assert one(store, """SELECT count(*) FROM matches WHERE
        (home_score > away_score AND winner IS DISTINCT FROM home_team) OR
        (away_score > home_score AND winner IS DISTINCT FROM away_team) OR
        (home_score = away_score AND winner IS NOT NULL)""") == 0


def test_upsets_agree_with_the_stored_expectancy(store):
    assert one(store, "SELECT count(*) FROM matches WHERE upset AND winner_win_exp >= 0.25") == 0
    assert one(store, "SELECT count(*) FROM matches WHERE NOT upset AND winner_win_exp < 0.25") == 0
    assert one(store, "SELECT count(*) FROM matches WHERE home_win_exp NOT BETWEEN 0 AND 1") == 0


def test_elo_ratings_are_plausible(store):
    lo, hi = store.q("SELECT min(elo) AS lo, max(elo) AS hi FROM elo_history")[0].values()
    assert 600 < lo < 1500 < hi < 2600


def test_every_team_has_a_confederation(store):
    confs = {r["confederation"] for r in store.q("SELECT DISTINCT confederation FROM teams")}
    assert confs == {"UEFA", "CONMEBOL", "CONCACAF", "CAF", "AFC", "OFC", "Non-FIFA"}
    for team, conf in [("Brazil", "CONMEBOL"), ("Australia", "AFC"), ("Israel", "UEFA"), ("Japan", "AFC"),
                       ("Mexico", "CONCACAF"), ("New Zealand", "OFC"), ("Egypt", "CAF")]:
        assert one(store, "SELECT confederation FROM teams WHERE name = ?", [team]) == conf


def test_assists_have_a_known_source(store):
    rows = store.q("SELECT assist_source AS s, count(*) AS n, count(assist_player_id) AS linked FROM goals GROUP BY 1")
    by = {r["s"]: r for r in rows}
    assert set(by) <= {None, "dataset", "statsbomb"}
    assert by[None]["linked"] == 0
    for s in ("dataset", "statsbomb"):
        if s in by:
            assert by[s]["linked"] == by[s]["n"]


def test_club_tables_are_consistent(store):
    assert one(store, "SELECT count(*) - count(DISTINCT club_id) FROM clubs") == 0
    assert one(store, "SELECT count(*) FROM club_careers WHERE club_id NOT IN (SELECT club_id FROM clubs)") == 0
    assert one(store, """SELECT count(*) FROM club_careers WHERE player_id IS NOT NULL
                         AND player_id NOT IN (SELECT player_id FROM players)""") == 0
    assert one(store, """SELECT count(*) FROM (SELECT tm_player_id, club_id FROM club_careers
                         GROUP BY ALL HAVING count(*) > 1)""") == 0
    assert one(store, "SELECT count(*) FROM club_seasons WHERE wins + draws + losses <> played") == 0
    assert one(store, "SELECT count(*) FROM club_careers WHERE first_app > last_app OR goals < 0 OR apps <= 0") == 0


def test_linked_club_players_fit_their_era(store):
    """A graph player linked to club data must have been alive and of playing age (guards the name linker)."""
    bad = store.q("""
        SELECT player_id, name, date_of_birth, first_goal_date, last_goal_date FROM players
        WHERE date_of_birth IS NOT NULL
          AND (year(last_goal_date) < year(date_of_birth) + 15 OR year(first_goal_date) > year(date_of_birth) + 42)
    """)
    # the Aubameyang father/son dataset quirk widens one player's span; nothing else may be off
    assert [r["player_id"] for r in bad if "aubameyang" not in r["player_id"]] == []


@pytest.mark.parametrize("player_id,club", [
    ("P:kaká@brazil", "Real Madrid"),                # not the other Kaká born a year earlier
    ("P:adam_johnson@england", "Sunderland AFC"),    # not Andy Johnson (surname-only match)
    ("P:son_heung-min@south_korea", "Tottenham Hotspur"),  # "Heung-min Son" vs "Son Heung-min"
    ("P:lionel_messi@argentina", "FC Barcelona"),
])
def test_namesakes_link_to_the_right_person(store, player_id, club):
    clubs = {r["club"] for r in store.q("SELECT club FROM club_careers WHERE player_id = ?", [player_id])}
    assert club in clubs, clubs
