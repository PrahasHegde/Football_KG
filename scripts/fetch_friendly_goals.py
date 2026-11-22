"""
Import FRIENDLY-match goalscorers from Wikipedia's "List of international goals scored by …" pages.

The source dataset (martj42/international_results) records scorers for competitive matches only.
Wikipedia has per-goal lists for ~165 prolific scorers, and those lists include friendlies. This script:

  1. finds every "List of international goals scored by …" page,
  2. downloads each page once (HTML cached in app_data/wiki_goal_lists/),
  3. reads the goals table (one row per goal: date, opponent, competition, …),
  4. matches each goal to a match in results.csv by date + both teams,
  5. keeps only goals in matches that results.csv classes as "Friendly" (so nothing overlaps
     goalscorers.csv), and drops any match where the imported goals exceed the team's real score,
  6. writes data_extra/friendly_goals_wikipedia.csv in the same shape as goalscorers.csv
     (plus `source` and `source_page` columns).

    python scripts/fetch_friendly_goals.py

Respects Wikimedia's rate limits through server.media._get_json (serial, throttled; set
WIKIMEDIA_CONTACT in .env to use the faster identified tier). Stop the app server while it runs:
both share the same per-client quota.
"""
from __future__ import annotations

import hashlib
import io
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")
from server.media import WIKI_API, _get_json  # noqa: E402  (after .env so the contact is picked up)

SRC = ROOT / "data" / "international_results"
CACHE = ROOT / "app_data" / "wiki_goal_lists"
OUT = ROOT / "data_extra" / "friendly_goals_wikipedia.csv"
TITLE_PREFIX = "List of international goals scored by "

# Wikipedia names that differ from results.csv (which always uses current names).
OPPONENT_ALIASES = {
    "west germany": "Germany", "fr germany": "Germany", "east germany": "German DR", "gdr": "German DR",
    "soviet union": "Russia", "ussr": "Russia", "cis": "Russia", "yugoslavia": "Serbia",
    "fr yugoslavia": "Serbia", "serbia and montenegro": "Serbia", "czechoslovakia": "Czech Republic",
    "czechia": "Czech Republic", "china": "China PR", "korea republic": "South Korea",
    "korea dpr": "North Korea", "ireland": "Republic of Ireland", "usa": "United States",
    "dutch east indies": "Indonesia", "zaire": "DR Congo", "congo dr": "DR Congo",
    "democratic republic of the congo": "DR Congo", "burma": "Myanmar", "ceylon": "Sri Lanka",
    "dahomey": "Benin", "upper volta": "Burkina Faso", "rhodesia": "Zimbabwe", "cote d'ivoire": "Ivory Coast",
    "côte d'ivoire": "Ivory Coast", "the gambia": "Gambia", "cape verde islands": "Cape Verde",
    "macedonia": "North Macedonia", "fyr macedonia": "North Macedonia", "swaziland": "Eswatini",
    "united arab emirates": "United Arab Emirates", "uae": "United Arab Emirates", "türkiye": "Turkey",
    "kyrgyz republic": "Kyrgyzstan", "chinese taipei": "Taiwan", "brunei darussalam": "Brunei",
    "east timor": "Timor-Leste", "saar": "Saarland", "republic of china": "Taiwan",
    "khmer republic": "Cambodia", "kampuchea": "Cambodia", "south vietnam": "Vietnam Republic",
    "korea": "South Korea", "trinidad": "Trinidad and Tobago", "dominican": "Dominican Republic",
    "u.s. virgin islands": "United States Virgin Islands", "us virgin islands": "United States Virgin Islands",
}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\[.*?\]|\(.*?\)", "", s)  # footnotes, parentheses
    return re.sub(r"\s+", " ", s).strip().lower()


slug = lambda s: str(s).strip().lower().replace(" ", "_")  # noqa: E731  (same rule as build_kg.py)


def list_pages() -> list[str]:
    titles, offset = [], 0
    while True:
        r = _get_json(WIKI_API, {"action": "query", "format": "json", "list": "search", "srlimit": 50,
                                 "sroffset": offset, "srsearch": f'intitle:"{TITLE_PREFIX.strip()}"'})
        titles += [p["title"] for p in r["query"]["search"] if p["title"].startswith(TITLE_PREFIX)]
        if "continue" not in r:
            return sorted(set(titles))
        offset = r["continue"]["sroffset"]


def page_html(title: str) -> str:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{hashlib.sha1(title.encode()).hexdigest()}.html"
    if f.exists():
        return f.read_text(encoding="utf-8")
    r = _get_json(WIKI_API, {"action": "parse", "format": "json", "formatversion": 2, "page": title,
                             "prop": "text", "redirects": 1})
    html = r.get("parse", {}).get("text", "")
    f.write_text(html, encoding="utf-8")
    return html


def goal_rows(html: str) -> pd.DataFrame:
    """All rows of the page's goal tables (tables with Date + Opponent columns), one row per goal."""
    try:
        tables = pd.read_html(io.StringIO(html))
    except ValueError:
        return pd.DataFrame()
    keep = []
    for t in tables:
        if isinstance(t.columns, pd.MultiIndex):
            t.columns = [c[-1] for c in t.columns]
        cols = {str(c).strip().lower(): c for c in t.columns}
        if "date" in cols and "opponent" in cols and len(t) >= 1:
            comp = cols.get("competition")
            keep.append(pd.DataFrame({
                "date": t[cols["date"]].astype(str),
                "opponent": t[cols["opponent"]].astype(str),
                "competition": t[comp].astype(str) if comp is not None else "",
                "goals": pd.to_numeric(t[cols["goals"]], errors="coerce") if "goals" in cols else 1,
            }))
    if not keep:
        return pd.DataFrame()
    df = pd.concat(keep, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"].str.replace(r"\[.*?\]", "", regex=True).str.strip(),
                                errors="coerce", format="mixed")
    df = df.dropna(subset=["date"])
    df["goals"] = df["goals"].fillna(1).clip(1, 10).astype(int)
    return df


def main() -> None:
    results = pd.read_csv(SRC / "results.csv")
    former = pd.read_csv(SRC / "former_names.csv")
    goalscorers = pd.read_csv(SRC / "goalscorers.csv")
    teams = set(results.home_team) | set(results.away_team)
    name_to_team = {norm(t): t for t in teams}
    name_to_team.update({norm(f): c for f, c in zip(former.former, former.current)})
    name_to_team.update({k: v for k, v in OPPONENT_ALIASES.items() if v in teams})
    results["date"] = pd.to_datetime(results["date"])
    by_date: dict[pd.Timestamp, list] = {}
    for r in results.itertuples():
        by_date.setdefault(r.date, []).append(r)
    known_scorers = {(norm(s), t) for s, t in zip(goalscorers.scorer, goalscorers.team)}

    titles = list_pages()
    print(f"{len(titles)} goal-list pages")
    out, unmatched_opp, stats = [], Counter(), Counter()
    for i, title in enumerate(titles, 1):
        player = re.sub(r"\s*\(.*\)$", "", title[len(TITLE_PREFIX):]).strip()
        rows = goal_rows(page_html(title))
        if rows.empty:
            stats["pages_without_table"] += 1
            continue
        # Resolve each row to (match, the player's team) by date + opponent.
        resolved = []
        for g in rows.itertuples():
            opp = name_to_team.get(norm(g.opponent))
            if not opp:
                unmatched_opp[g.opponent] += 1
                continue
            cands = [m for m in by_date.get(g.date, []) if opp in (m.home_team, m.away_team)]
            if len(cands) != 1:
                stats["no_unique_match"] += 1
                continue
            m = cands[0]
            resolved.append((m, m.away_team if m.home_team == opp else m.home_team, g.goals))
        if not resolved:
            continue
        team = Counter(t for _, t, _ in resolved).most_common(1)[0][0]  # the player's national team
        # Prefer the spelling the dataset already uses for this player.
        scorer = next((s for s, t in zip(goalscorers.scorer, goalscorers.team)
                       if t == team and norm(s) == norm(player)), player)
        agree = sum(1 for _, t, _ in resolved if t == team) / len(resolved)
        if (norm(scorer), team) not in known_scorers:
            # Prolific men's scorers normally already have competitive goals in goalscorers.csv. Players
            # who don't (early-era stars who mostly played friendlies) must show strong evidence that
            # their list really maps onto this team's matches; women's / youth / other-sport lists
            # only line up by coincidence and fail this.
            strong = len(resolved) >= 10 and len(resolved) >= 0.5 * len(rows) and agree >= 0.9
            if not strong:
                stats["pages_rejected_unknown_player"] += 1
                print(f"[{i}/{len(titles)}] skip {player}: not in the dataset and weak match "
                      f"({len(resolved)}/{len(rows)} rows, {agree:.0%} agree)")
                continue
            stats["players_new_to_dataset"] += 1
            print(f"[{i}/{len(titles)}] accept new player {player} ({team}): "
                  f"{len(resolved)}/{len(rows)} rows matched, {agree:.0%} agree")
        if agree < 0.8:  # the goals don't consistently belong to one national team
            stats["pages_rejected_inconsistent"] += 1
            continue
        friendlies = 0
        for m, t, n in resolved:
            if t != team:
                continue
            if m.tournament != "Friendly":
                stats["skipped_competitive"] += n  # already covered by goalscorers.csv
                continue
            for _ in range(n):
                out.append({"date": m.date.strftime("%Y-%m-%d"), "home_team": m.home_team,
                            "away_team": m.away_team, "team": team, "scorer": scorer, "minute": None,
                            "own_goal": False, "penalty": False, "assist": None,
                            "source": "wikipedia", "source_page": title})
                friendlies += 1
        stats["friendly_goals"] += friendlies
        print(f"[{i}/{len(titles)}] {scorer} ({team}): {friendlies} friendly goals")

    df = pd.DataFrame(out)
    if not df.empty:
        # Sanity check: a player list can't give a team more goals in a match than it scored.
        score = {(r.date.strftime("%Y-%m-%d"), r.home_team, r.away_team): (r.home_team, r.home_score, r.away_score)
                 for r in results.itertuples()}
        counts = df.groupby(["date", "home_team", "away_team", "team"]).size()
        bad = set()
        for (d, h, a, t), n in counts.items():
            home, hs, as_ = score[(d, h, a)]
            if n > (hs if t == home else as_):
                bad.add((d, h, a, t))
        if bad:
            stats["goals_dropped_over_score"] = int(sum(counts[b] for b in bad))
            df = df[[k not in bad for k in zip(df.date, df.home_team, df.away_team, df.team)]]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)
    print(f"\nWrote {len(df):,} friendly goals by {df.scorer.nunique() if len(df) else 0} players "
          f"in {df[['date', 'home_team', 'away_team']].drop_duplicates().shape[0] if len(df) else 0} matches -> "
          f"{OUT.relative_to(ROOT)}")
    print(dict(stats))
    if unmatched_opp:
        print("Unmatched opponent names (add to OPPONENT_ALIASES):", unmatched_opp.most_common(15))


if __name__ == "__main__":
    main()
