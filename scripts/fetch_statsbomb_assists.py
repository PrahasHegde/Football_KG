"""
Assists from StatsBomb Open Data for major tournaments the source dataset has no assists for.

The source dataset (martj42/international_results) records assists for FIFA World Cup matches only. No free,
licensed source publishes complete career international assists (Opta is commercial; FBref/Transfermarkt
forbid scraping). StatsBomb Open Data (https://github.com/statsbomb/open-data) is free for research and
analysis with attribution and has full event data, including assists (the pass with `goal_assist`), for:
UEFA Euro 2020 and 2024, Copa América 2024 and the 2023 Africa Cup of Nations.

This script:
  1. downloads each match's events + lineups once (serially; only the goals are kept, in
     app_data/statsbomb/<match_id>.json),
  2. matches each StatsBomb match to results.csv by date (±1 day) + both teams,
  3. matches each goal to the dataset's goal (same team, minute within 2) and resolves the assister to the
     spelling the dataset uses for that team's players,
  4. writes data_extra/statsbomb_assists.csv: one row per dataset goal that gets an assist.

The 2018 and 2022 World Cups are processed too, but only to VALIDATE the matching against the dataset's own
assists (the agreement rate is printed); the dataset's World Cup assists are left untouched.

    python scripts/fetch_statsbomb_assists.py

Attribution: "Data: StatsBomb Open Data" (required when sharing results based on it).
"""
from __future__ import annotations

import difflib
import json
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from fetch_wikipedia_goals import OPPONENT_ALIASES, norm  # noqa: E402

SRC = ROOT / "data" / "international_results"
CACHE = ROOT / "app_data" / "statsbomb"
OUT = ROOT / "data_extra" / "statsbomb_assists.csv"
BASE = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"
FILL = [(55, 43, "UEFA Euro 2020"), (55, 282, "UEFA Euro 2024"), (223, 282, "Copa América 2024"),
        (1267, 107, "Africa Cup of Nations 2023")]
VALIDATE = [(43, 3, "FIFA World Cup 2018"), (43, 106, "FIFA World Cup 2022")]
SB_ALIASES = {"czechia": "Czech Republic", "türkiye": "Turkey", "turkiye": "Turkey",
              "cote d'ivoire": "Ivory Coast", "côte d'ivoire": "Ivory Coast", "korea republic": "South Korea",
              "ir iran": "Iran", "cabo verde": "Cape Verde", "congo dr": "DR Congo",
              "united states": "United States", "usa": "United States"}


def fetch(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "football-kg/1.0 (StatsBomb open data reader)"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception:
            if attempt == 3:
                raise
            time.sleep(3 * (attempt + 1))
    return None


def match_goals(match_id: int) -> dict:
    """Goals of one match (cached): scorer, assister (nicknames where StatsBomb has them), minute, team."""
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{match_id}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    events = fetch(f"{BASE}/events/{match_id}.json")
    lineups = fetch(f"{BASE}/lineups/{match_id}.json")
    time.sleep(0.3)
    nick = {p["player_id"]: (p.get("player_nickname") or p["player_name"], p["player_name"])
            for team in lineups for p in team["lineup"]}
    by_id = {e["id"]: e for e in events}
    goals = []
    for e in events:
        if e.get("period", 0) > 4:  # penalty shoot-out
            continue
        shot = e.get("shot")
        if e["type"]["name"] == "Shot" and shot and shot["outcome"]["name"] == "Goal":
            assist = None
            kp = by_id.get(shot.get("key_pass_id", ""))
            if kp and kp.get("pass", {}).get("goal_assist"):
                assist = nick.get(kp["player"]["id"], (kp["player"]["name"],) * 2)
            goals.append({"team": e["team"]["name"], "period": e["period"], "minute": e["minute"],
                          "scorer": nick.get(e["player"]["id"], (e["player"]["name"],) * 2),
                          "assist": assist, "own_goal": False})
        elif e["type"]["name"] == "Own Goal For":
            goals.append({"team": e["team"]["name"], "period": e["period"], "minute": e["minute"],
                          "scorer": None, "assist": None, "own_goal": True})
    out = {"goals": goals}
    f.write_text(json.dumps(out), encoding="utf-8")
    return out


def display_minute(period: int, minute: int) -> int:
    """StatsBomb minutes are elapsed (0-based, running on in added time); datasets cap at 45/90/105/120."""
    m = minute + 1
    cap = {1: 45, 2: 90, 3: 105, 4: 120}.get(period, 120)
    return min(m, cap)


def main() -> None:
    results = pd.read_csv(SRC / "results.csv")
    gs = pd.read_csv(SRC / "goalscorers.csv")
    teams = set(results.home_team) | set(results.away_team)
    n2t = {k: v for k, v in OPPONENT_ALIASES.items() if v in teams}
    n2t.update({k: v for k, v in SB_ALIASES.items() if v in teams})
    n2t.update({norm(t): t for t in teams})
    by_date = defaultdict(list)
    for r in results.itertuples():
        by_date[r.date].append(r)
    ds_goals = defaultdict(list)  # (date, home, away) -> goalscorers rows
    for r in gs.itertuples():
        ds_goals[(r.date, r.home_team, r.away_team)].append(r)
    # every spelling the dataset uses for a team's players (scorers and assisters), with the years seen
    names = defaultdict(lambda: defaultdict(set))
    for s, a, t, d in zip(gs.scorer, gs.assist, gs.team, gs.date):
        names[t][s].add(int(d[:4]))
        if isinstance(a, str):
            names[t][a].add(int(d[:4]))

    def resolve(person, team: str, year: int):
        """StatsBomb (nickname, full name) -> the dataset's spelling for this team, else the nickname.

        Tries, among the team's players seen within a few years of the match: exact name, all tokens of a
        dataset name contained in the StatsBomb names ("Lionel Messi" in "Lionel Andres Messi Cuccittini"),
        same surname + same first 3 letters of the first name ("Phil Foden" / "Philip Foden"), and finally a
        close spelling ("Abdallah El Said" / "Abdalla El Said"). Every step must give a single candidate."""
        if not person:
            return None
        nickname, full = person
        cands = [c for c, ys in names[team].items() if any(year - 12 <= y <= year + 8 for y in ys)]
        for n in (nickname, full):
            hit = [c for c in cands if norm(c) == norm(n)]
            if hit:
                return hit[0]
        toks = set(norm(full).split()) | set(norm(nickname).split())
        hit = [c for c in cands if len(norm(c).split()) >= 2 and set(norm(c).split()) <= toks]
        if len(hit) == 1:
            return hit[0]
        for n in (nickname, full):
            t = norm(n).split()
            if len(t) < 2:
                continue
            hit = [c for c in cands if len(norm(c).split()) >= 2 and norm(c).split()[-1] == t[-1]
                   and norm(c).split()[0][:3] == t[0][:3]]
            if len(hit) == 1:
                return hit[0]
        scored = sorted(((difflib.SequenceMatcher(None, norm(nickname), norm(c)).ratio(), c) for c in cands),
                        reverse=True)
        if scored and scored[0][0] >= 0.85 and (len(scored) == 1 or scored[1][0] < scored[0][0] - 0.05):
            return scored[0][1]
        return nickname

    rows, stats, check = [], Counter(), Counter()
    for comp, season, label in FILL + VALIDATE:
        validate = (comp, season, label) in VALIDATE
        matches = fetch(f"{BASE}/matches/{comp}/{season}.json")
        print(f"{label}: {len(matches)} matches")
        for i, sm in enumerate(matches, 1):
            h, a = n2t.get(norm(sm["home_team"]["home_team_name"])), n2t.get(norm(sm["away_team"]["away_team_name"]))
            d = pd.Timestamp(sm["match_date"])
            cands = []
            for off in (0, -1, 1):
                day = (d + pd.Timedelta(days=off)).strftime("%Y-%m-%d")
                cands = [m for m in by_date.get(day, []) if {m.home_team, m.away_team} == {h, a}]
                if cands:
                    break
            if len(cands) != 1:
                stats["match_not_found"] += 1
                print(f"   ! no match in results.csv for {sm['match_date']} {sm['home_team']['home_team_name']} "
                      f"v {sm['away_team']['away_team_name']}")
                continue
            m = cands[0]
            key = (m.date, m.home_team, m.away_team)
            pool = list(ds_goals.get(key, []))
            sb = match_goals(sm["match_id"])["goals"]
            for g in sb:
                team = n2t.get(norm(g["team"]))
                minute = display_minute(g["period"], g["minute"])
                same = [r for r in pool if r.team == team and bool(r.own_goal) == g["own_goal"]
                        and pd.notna(r.minute) and abs(r.minute - minute) <= 2]
                if not same:
                    stats["goal_not_found"] += 1
                    continue
                r = min(same, key=lambda r: abs(r.minute - minute))
                pool.remove(r)
                assist = resolve(g["assist"], team, int(r.date[:4])) if g["assist"] else None
                if validate:
                    ds_a = r.assist if isinstance(r.assist, str) else None
                    if ds_a and assist and norm(ds_a) != norm(assist) or bool(ds_a) != bool(assist):
                        print(f"   ~ {r.date} {r.scorer} {int(r.minute)}: dataset={ds_a} statsbomb={assist}")
                    if ds_a is None and assist is None:
                        check["both_none"] += 1
                    elif ds_a and assist and norm(ds_a) == norm(assist):
                        check["same"] += 1
                    elif ds_a and assist:
                        check["different_name"] += 1
                    else:
                        check["one_side_only"] += 1
                    continue
                stats["goals_matched"] += 1
                if assist is None or isinstance(r.assist, str):
                    continue
                rows.append({"date": r.date, "home_team": r.home_team, "away_team": r.away_team, "team": r.team,
                             "scorer": r.scorer, "minute": r.minute, "assist": assist,
                             "source": "statsbomb", "competition": label})
                stats["assists"] += 1
    df = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)
    print(f"\nWrote {len(df):,} assists -> {OUT.relative_to(ROOT)}")
    print(dict(stats))
    total = sum(check.values())
    if total:
        print(f"World Cup validation vs the dataset's own assists ({total} goals): {dict(check)}; "
              f"agreement {(check['same'] + check['both_none']) / total:.1%}")


if __name__ == "__main__":
    main()
