"""
Club data (2012/13 -> July 2026) from transfermarkt-datasets (https://github.com/dcaribou/transfermarkt-datasets,
CC0 1.0). The dataset is collected from Transfermarkt by its author and published as public domain.

Covers ~800 clubs in 14+ top leagues (Premier League, LaLiga, Bundesliga, Serie A, Ligue 1, Eredivisie, Liga
Portugal, MLS, Saudi Pro League, ...) plus their cups and European competitions: every appearance with goals,
assists and minutes. Coverage starts with the 2012/13 season.

This script:
  1. downloads the dataset's DuckDB file once into app_data/transfermarkt/ (~200 MB, not committed),
  2. links Transfermarkt players to the knowledge graph's players (same name + national team, from
     nationality or current national team; a surname + first-initial fallback within the same team and era),
  3. writes small CSVs to data_extra/ that build_kg.py / build_db.py use as optional inputs:
       clubs.csv              one row per club (league, country, stadium, coach)
       club_player_stats.csv  one row per player per club: apps, goals, assists, minutes (all competitions
                              and league only), first/last appearance, + kg_player_id when linked
       club_seasons.csv       one row per club, season and competition: played, W/D/L, goals for/against
       player_profiles.csv    linked players only: position, date of birth, international caps

    python scripts/fetch_club_data.py            (use --refresh to download the file again)
"""
from __future__ import annotations

import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from fetch_wikipedia_goals import OPPONENT_ALIASES, norm  # noqa: E402

SRC = ROOT / "data" / "international_results"
TM_URL = "https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/transfermarkt-datasets.duckdb"
TM_FILE = ROOT / "app_data" / "transfermarkt" / "transfermarkt-datasets.duckdb"
OUT = ROOT / "data_extra"
# Transfermarkt nationality -> results.csv team name (when they differ)
NATION_ALIASES = {
    "bosnia-herzegovina": "Bosnia and Herzegovina", "korea, south": "South Korea", "korea, north": "North Korea",
    "cote d'ivoire": "Ivory Coast", "ireland": "Republic of Ireland", "hongkong": "Hong Kong",
    "the gambia": "Gambia", "curacao": "Curaçao", "brunei darussalam": "Brunei", "southern sudan": "South Sudan",
    "st. kitts & nevis": "Saint Kitts and Nevis", "st. lucia": "Saint Lucia",
    "st. vincent & grenadinen": "Saint Vincent and the Grenadines", "turkiye": "Turkey", "türkiye": "Turkey",
    "chinese taipei": "Taiwan", "neukaledonien": "New Caledonia", "sao tome and principe": "São Tomé and Príncipe",
    "macao": "Macau", "china": "China PR", "saint-martin": "Saint Martin", "congo": "Congo",
}
slug = lambda s: str(s).strip().lower().replace(" ", "_")  # noqa: E731  (same rule as build_kg.py)


def name_key(name: str) -> str:
    """Order- and hyphen-insensitive name key: "Heung-min Son" == "Son Heung-min", "El Arabi" == "El-Arabi"."""
    return " ".join(sorted(norm(str(name)).replace("-", " ").split()))
# readable names for competition slugs ("laliga" -> "LaLiga")
COMPETITION_NAMES = {
    "GB1": "Premier League", "ES1": "LaLiga", "L1": "Bundesliga", "IT1": "Serie A", "FR1": "Ligue 1",
    "NL1": "Eredivisie", "PO1": "Liga Portugal", "BE1": "Jupiler Pro League", "TR1": "Süper Lig",
    "SC1": "Scottish Premiership", "RU1": "Russian Premier League", "UKR1": "Ukrainian Premier League",
    "A1": "Austrian Bundesliga", "C1": "Swiss Super League", "DK1": "Danish Superliga", "GR1": "Greek Super League",
    "MLS1": "Major League Soccer", "SA1": "Saudi Pro League", "BRA1": "Brasileirão Série A", "JAP1": "J1 League",
    "RSK1": "K League 1", "MEX1": "Liga MX", "ARG1": "Argentine Primera División", "AUS1": "A-League Men",
    "CL": "UEFA Champions League", "EL": "UEFA Europa League", "UCOL": "UEFA Conference League",
    "USC": "UEFA Super Cup", "FAC": "FA Cup", "CDR": "Copa del Rey", "DFB": "DFB-Pokal", "CIT": "Coppa Italia",
}


def download(refresh: bool) -> None:
    if TM_FILE.exists() and not refresh:
        return
    TM_FILE.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {TM_URL} ...")
    req = urllib.request.Request(TM_URL, headers={"User-Agent": "football-kg/1.0"})
    tmp = TM_FILE.with_suffix(".part")
    with urllib.request.urlopen(req, timeout=600) as r, open(tmp, "wb") as fh:
        while chunk := r.read(1 << 20):
            fh.write(chunk)
    tmp.replace(TM_FILE)


def kg_players() -> pd.DataFrame:
    """Every (name, team, first/last year) the graph will contain, from the same inputs build_kg.py reads."""
    frames = [pd.read_csv(SRC / "goalscorers.csv")]
    for extra in ("wikipedia_goals.csv",):
        if (OUT / extra).exists():
            frames.append(pd.read_csv(OUT / extra))
    g = pd.concat([f[["date", "team", "scorer", "assist"]] for f in frames], ignore_index=True)
    people = pd.concat([g[["date", "team", "scorer"]].rename(columns={"scorer": "name"}),
                        g[["date", "team", "assist"]].rename(columns={"assist": "name"})]).dropna()
    if (OUT / "statsbomb_assists.csv").exists():
        sb = pd.read_csv(OUT / "statsbomb_assists.csv")
        people = pd.concat([people, sb[["date", "team", "assist"]].rename(columns={"assist": "name"})])
    people["year"] = people.date.str[:4].astype(int)
    return people.groupby(["name", "team"]).year.agg(["min", "max", "median"]).reset_index()


def main() -> None:
    download("--refresh" in sys.argv)
    tm = duckdb.connect(str(TM_FILE), read_only=True)
    results = pd.read_csv(SRC / "results.csv")
    teams = set(results.home_team) | set(results.away_team)
    nation = {k: v for k, v in OPPONENT_ALIASES.items() if v in teams}
    nation.update({k: v for k, v in NATION_ALIASES.items() if v in teams})
    nation.update({norm(t): t for t in teams})
    to_team = lambda c: nation.get(norm(c)) if isinstance(c, str) else None  # noqa: E731

    # ---------- players: link to the knowledge graph ----------
    players = tm.execute("""
        SELECT p.player_id, p.name, p.first_name, p.last_name, p.country_of_citizenship, nt.name AS national_team,
               p.position, p.sub_position, p.date_of_birth, p.international_caps, p.international_goals,
               (SELECT min(date) FROM appearances a WHERE a.player_id = p.player_id) AS first_app,
               (SELECT max(date) FROM appearances a WHERE a.player_id = p.player_id) AS last_app
        FROM players p LEFT JOIN national_teams nt ON nt.national_team_id = CAST(p.current_national_team_id AS VARCHAR)
    """).df()
    kg = kg_players()
    exact = defaultdict(list)          # (norm name, team) -> (kg name, first, last goal year)
    by_surname = defaultdict(list)     # (surname, team) -> (kg name, first, last goal year)
    for r in kg.itertuples():
        exact[(name_key(r.name), r.team)].append((r.name, r.min, r.max, r.median))
        toks = norm(r.name).split()
        if len(toks) >= 2:
            by_surname[(toks[-1], r.team)].append((r.name, r.min, r.max, r.median))

    def plausible(p, first: int, last: int, median: float) -> bool:
        """The graph player's international goals must fit this person's playing life: the median goal year
        aged 16-40 (from the date of birth), or without one, within 8 years of their club career. Stops
        namesakes from other eras; using the median tolerates a stray dataset goal, e.g. a father's goal under
        the same name (Aubameyang)."""
        dob = pd.to_datetime(p.date_of_birth, errors="coerce")
        if pd.notna(dob):
            return dob.year + 16 <= median <= dob.year + 40
        if pd.isna(p.first_app):
            return False
        return p.first_app.year - 8 <= last and first <= p.last_app.year + 8

    def link(p) -> str | None:
        cand_teams = {t for t in (to_team(p.country_of_citizenship), to_team(p.national_team)) if t}
        names = [p.name, f"{p.first_name or ''} {p.last_name or ''}".strip()]
        for team in cand_teams:
            for n in names:
                hits = [k for k, a, b, m in exact.get((name_key(n), team), []) if plausible(p, a, b, m)]
                if hits:
                    return f"P:{slug(hits[0])}@{slug(team)}"
        # surname + same first initial, within the same era
        toks = norm(p.name).split()
        if len(toks) >= 2:
            for team in cand_teams:
                hits = [k for k, a, b, m in by_surname.get((toks[-1], team), [])
                        if norm(k)[0] == toks[0][0] and plausible(p, a, b, m)]
                if len(hits) == 1:
                    return f"P:{slug(hits[0])}@{slug(team)}"
        return None

    players["kg_player_id"] = [link(p) for p in players.itertuples()]
    # one Transfermarkt player per graph player (keep the one with more caps)
    players = players.sort_values("international_caps", ascending=False)
    dup = players.kg_player_id.notna() & players.duplicated("kg_player_id")
    players.loc[dup, "kg_player_id"] = None
    linked = players[players.kg_player_id.notna()]
    print(f"Linked {len(linked):,} of {len(players):,} Transfermarkt players to graph players")

    # ---------- clubs ----------
    clubs = tm.execute("""
        SELECT CAST(c.club_id AS INTEGER) AS club_id, c.name, c.domestic_competition_id AS league_id, co.name AS league_slug,
               co.country_name AS country, c.stadium_name AS stadium, c.stadium_seats, c.coach_name AS coach,
               c.squad_size, c.last_season
        FROM clubs c LEFT JOIN competitions co ON co.competition_id = c.domestic_competition_id
    """).df()
    clubs["league"] = [COMPETITION_NAMES.get(i, str(n or "").replace("-", " ").title())
                       for i, n in zip(clubs.league_id, clubs.league_slug)]
    clubs["country_team"] = clubs.country.map(to_team)

    # ---------- per player per club ----------
    stats = tm.execute("""
        SELECT a.player_id, a.player_club_id AS club_id,
               count(*) AS apps, sum(a.goals) AS goals, sum(a.assists) AS assists, sum(a.minutes_played) AS minutes,
               count(*) FILTER (WHERE co.type = 'domestic_league') AS league_apps,
               coalesce(sum(a.goals) FILTER (WHERE co.type = 'domestic_league'), 0) AS league_goals,
               coalesce(sum(a.assists) FILTER (WHERE co.type = 'domestic_league'), 0) AS league_assists,
               count(*) FILTER (WHERE co.competition_id IN ('CL', 'EL', 'UCOL')) AS europe_apps,
               coalesce(sum(a.goals) FILTER (WHERE co.competition_id IN ('CL', 'EL', 'UCOL')), 0) AS europe_goals,
               min(a.date) AS first_app, max(a.date) AS last_app
        FROM appearances a JOIN competitions co USING (competition_id)
        WHERE co.type <> 'national_team_competition' AND a.player_club_id IN (SELECT CAST(club_id AS INTEGER) FROM clubs)
        GROUP BY ALL
    """).df()
    stats = stats.merge(players[["player_id", "name", "country_of_citizenship", "kg_player_id"]], on="player_id")
    stats = stats.rename(columns={"player_id": "tm_player_id", "name": "player_name",
                                  "country_of_citizenship": "nationality"})

    # ---------- club season records ----------
    seasons = tm.execute("""
        SELECT cg.club_id, g.season, g.competition_id, co.name AS competition_slug, co.type AS competition_type,
               count(*) AS played, count(*) FILTER (WHERE cg.own_goals > cg.opponent_goals) AS wins,
               count(*) FILTER (WHERE cg.own_goals = cg.opponent_goals) AS draws,
               count(*) FILTER (WHERE cg.own_goals < cg.opponent_goals) AS losses,
               sum(cg.own_goals) AS goals_for, sum(cg.opponent_goals) AS goals_against
        FROM club_games cg JOIN games g USING (game_id) JOIN competitions co ON co.competition_id = g.competition_id
        WHERE cg.club_id IN (SELECT CAST(club_id AS INTEGER) FROM clubs) AND co.type <> 'national_team_competition'
        GROUP BY ALL
    """).df()
    seasons["competition"] = [COMPETITION_NAMES.get(i, str(n).replace("-", " ").title())
                              for i, n in zip(seasons.competition_id, seasons.competition_slug)]

    profiles = linked[["kg_player_id", "player_id", "position", "sub_position", "date_of_birth",
                       "international_caps"]].rename(columns={"player_id": "tm_player_id"})

    OUT.mkdir(exist_ok=True)
    clubs.drop(columns=["league_slug"]).to_csv(OUT / "clubs.csv", index=False)
    stats.to_csv(OUT / "club_player_stats.csv", index=False)
    seasons.drop(columns=["competition_slug"]).to_csv(OUT / "club_seasons.csv", index=False)
    profiles.to_csv(OUT / "player_profiles.csv", index=False)
    print(f"clubs.csv {len(clubs):,} · club_player_stats.csv {len(stats):,} "
          f"({stats.kg_player_id.notna().sum():,} linked) · club_seasons.csv {len(seasons):,} · "
          f"player_profiles.csv {len(profiles):,}")


if __name__ == "__main__":
    main()
