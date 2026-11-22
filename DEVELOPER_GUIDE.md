# Football KG: Developer Guide

This guide explains the whole project: what it is, how the pieces fit together, how data flows from
raw CSV files to the graph on your screen, and how to change or extend each part.

If you only want to run the app, see [README.md](README.md). For the short rules Claude Code follows
in this repo, see [CLAUDE.md](CLAUDE.md).

---

## Contents

1. [What the project is](#1-what-the-project-is)
2. [Quick start](#2-quick-start)
3. [The big picture](#3-the-big-picture)
4. [Repository map](#4-repository-map)
5. [Stage 1: building the knowledge graph](#5-stage-1-building-the-knowledge-graph-srcbuild_kgpy)
6. [Stage 2: building the app database](#6-stage-2-building-the-app-database-serverbuild_dbpy)
7. [Stage 3: the backend API](#7-stage-3-the-backend-api-server)
8. [The AI agent](#8-the-ai-agent-serveragentpy)
9. [The frontend](#9-the-frontend-web)
10. [What happens when…](#10-what-happens-when-end-to-end-walkthroughs)
11. [Development workflow](#11-development-workflow)
    - [11½. Testing and quality](#11-testing-and-quality)
12. [How to extend the project](#12-how-to-extend-the-project)
13. [Troubleshooting](#13-troubleshooting)
14. [Known limitations and roadmap](#14-known-limitations-and-roadmap)
15. [Glossary](#15-glossary)

---

## 1. What the project is

**Football KG** is a knowledge graph of international men's football from 1872 to today, plus a web
application to explore it.

- **The knowledge graph** turns ~50,000 match results, ~48,000 goals and ~16,000 goalscorers into
  connected entities: teams, players, matches, goals, tournaments, cities and countries.
- **The web app** lets you see and walk through that graph visually, compare teams, travel through
  football history decade by decade, and ask questions in plain English to an AI agent that answers
  from the data.

The data comes from the public dataset
[martj42/international_results](https://github.com/martj42/international_results).

### Tech stack at a glance

| Layer | Technology | Why |
|---|---|---|
| Graph build | Python, pandas, NetworkX | Easy to model nodes/edges; reproducible from raw CSVs |
| App database | DuckDB (embedded, file-based) | Fast analytical SQL, zero server setup, read-only mode for safety |
| Backend API | FastAPI + Uvicorn | Simple typed endpoints, streaming responses for the agent |
| AI agent | Groq API (`openai/gpt-oss-120b` by default) | Free tier, fast, supports tool calling |
| Frontend | React 19 + TypeScript + Vite | Modern component UI with fast dev reloads |
| Graph rendering | `react-force-graph-2d` (canvas + d3-force) | Smooth physics layout for hundreds of nodes |
| Charts | Hand-written SVG components | Small, theme-aware, no chart library needed |
| Markdown in chat | `react-markdown` + `remark-gfm` | Renders the agent's tables and links |
| Icons | `lucide-react` | Clean, consistent icon set |

---

## 2. Quick start

```bash
# 1. Python environment
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt   # app + test tools (requirements.txt = app only)

# 2. Data + graph
python scripts/get_data.py         # clones the dataset into data/international_results/
python src/build_kg.py             # builds football_kg.gpickle and neo4j_import/

# 3. LLM key (free): https://console.groq.com/keys
copy .env.example .env             # macOS/Linux: cp .env.example .env
#    then put GROQ_API_KEY=... in .env

# 4. Frontend build
cd web && npm install && npm run build && cd ..

# 5. Run
uvicorn server.main:app --port 8000
#    open http://localhost:8000
```

On its first start the server builds `app_data/football.duckdb` from the graph (about 10 seconds).

Check that everything works (tests, typecheck, build: about 30 seconds, no network needed):

```bash
python scripts/check.py
```

---

## 3. The big picture

The project is a **pipeline of three stages**, then an **app** on top.

```
 ┌──────────────────────┐   scripts/get_data.py
 │  GitHub dataset      │ ─────────────────────────┐
 │  (4 CSV files)       │                          ▼
 └──────────────────────┘              data/international_results/*.csv
                                                   │
                     STAGE 1  src/build_kg.py      │  entity resolution, IDs, edges
                                                   ▼
                     football_kg.gpickle  (NetworkX MultiDiGraph: 117k nodes, 404k edges)
                     neo4j_import/        (CSV + Cypher, optional, for Neo4j)
                                                   │
                     STAGE 2  server/build_db.py   │  flatten graph, tidy tables, Elo ratings
                                                   ▼
                     app_data/football.duckdb  (read-only at runtime)
                                                   │
                     STAGE 3  server/  (FastAPI)   │
          ┌────────────────────────────────────────┼─────────────────────────┐
          │  store.py   all data access (SQL + path finding)                  │
          │  agent.py   LLM tool-calling loop (Groq) ──► uses store.py tools  │
          │  main.py    HTTP endpoints /api/*  +  serves the built UI         │
          └────────────────────────────────────────┬─────────────────────────┘
                                                   │  JSON  +  Server-Sent Events
                                                   ▼
                     web/  (React app in the browser)
                     graph canvas · inspector · head-to-head · discover · chat
```

### Key design decisions

1. **The graph is the source of truth; the database is derived.** `football_kg.gpickle` is built
   only from the raw CSVs. Everything the app uses is computed from it. You can delete
   `app_data/` at any time; it is rebuilt automatically.
2. **Two views of the same data.** The DuckDB file holds both the **raw graph** (`nodes`, `edges`
   tables) for exploration and **tidy analytical tables** (`matches`, `goals`, `players`…) for
   aggregations. Graph questions ("what is connected to X?") and statistics questions ("who scored
   most?") each get the shape that suits them.
3. **One data-access layer.** Only `server/store.py` talks to the database. The REST API and the
   AI agent both call the same `Store` methods, so they always agree.
4. **Read-only and sandboxed at runtime.** The server opens DuckDB read-only, with file access
   disabled and its configuration locked. Even if the agent writes bad SQL, it cannot change data or
   read files on your disk.
5. **No database server needed.** DuckDB is a single file, so the project runs on a laptop with no
   Docker or Neo4j. The Neo4j export still exists if you want to use Neo4j later.

---

## 4. Repository map

```
football-kg/
├── README.md                 How to install and run, feature list
├── DEVELOPER_GUIDE.md        This file
├── CLAUDE.md                 Project rules/notes for Claude Code (schema, data facts, conventions)
├── requirements.txt          Python dependencies of the app
├── requirements-dev.txt      + test tools (pytest, httpx)
├── pytest.ini                Test settings (testpaths, the needs_build marker)
├── .env.example              Template for GROQ_API_KEY, GROQ_MODEL, WIKIMEDIA_CONTACT
├── .github/workflows/ci.yml  CI: build from raw data, run every check on each push
│
├── scripts/
│   ├── get_data.py                git clone / pull of the source dataset into data/
│   ├── fetch_wikipedia_goals.py   optional: goals missing from the dataset, from Wikipedia goal lists
│   ├── fetch_statsbomb_assists.py optional: Euro/Copa/AFCON assists from StatsBomb Open Data
│   ├── fetch_club_data.py         optional: clubs since 2012/13 from transfermarkt-datasets
│   └── check.py                   runs every check (pytest, UI typecheck + unit tests + build)
│
├── data_extra/               Committed outputs of the fetch_* scripts (optional build inputs)
│   ├── wikipedia_goals.csv   statsbomb_assists.csv
│   └── clubs.csv  club_player_stats.csv  club_seasons.csv  player_profiles.csv
│
├── src/                      STAGE 1 (the original knowledge-graph project)
│   ├── build_kg.py           CSVs (+ data_extra) -> NetworkX graph + Neo4j export
│   └── explore_kg.py         Command-line showcase queries on the graph
│
├── server/                   STAGES 2 + 3 (backend)
│   ├── build_db.py           Graph -> DuckDB tables, Elo, confederations, club tables
│   ├── store.py              All queries: search, profiles, expand, paths, H2H, discover, home views,
│   │                         team-mates, player compare, quiz, safe SQL
│   ├── agent.py              Groq LLM agent: system prompt, tools, streaming loop
│   ├── media.py              Flags, crests, photos, club logos (Wikidata/Wikipedia), image proxy
│   └── main.py               FastAPI app: /api/* endpoints + static UI
│
├── tests/                    Backend tests (pytest) — see §11½
│   ├── conftest.py           fixtures (store, client, graph), offline mode, skip-if-not-built
│   ├── test_graph.py         graph schema, ID conventions, structural invariants
│   ├── test_db.py            data-integrity invariants of the app database
│   ├── test_store.py         every Store feature + SQL safety
│   ├── test_api.py           every endpoint, error codes, image-proxy allow-list, chat without a key
│   ├── test_agent.py         tool validation/execution without the LLM
│   └── test_scripts.py       import-script parsing/matching rules on synthetic inputs
│
├── web/                      Frontend (React + TypeScript + Vite; Vitest for unit tests)
│   ├── index.html  vite.config.ts  tsconfig.json  package.json
│   └── src/
│       ├── main.tsx          React entry point
│       ├── App.tsx           Layout + app state + all user actions + deep links
│       ├── routes.ts         URL hash <-> view (parseRoute / routeHash), unit-tested
│       ├── api.ts            HTTP client + chat stream reader
│       ├── types.ts          Shared TypeScript types (mirror the API JSON)
│       ├── theme.ts          Colours, node shapes, sizes, layout homes, canvas helpers
│       ├── media.ts          Client store for flags, photos, club logos
│       ├── useGraph.ts       Canvas graph state (nodes/links kept stable)
│       ├── useTheme.ts       Light/dark mode (saved in localStorage)
│       ├── styles.css        All styling, theme tokens for light + dark
│       ├── *.test.ts         Vitest unit tests (routes, theme)
│       └── components/
│           ├── GraphCanvas.tsx    Force-directed canvas: drawing, physics, clicks, the three home views
│           ├── Inspector.tsx      Entity details panel (per node type)
│           ├── H2HPanel.tsx       Team head to head
│           ├── ComparePanel.tsx   Player vs player
│           ├── QuizPanel.tsx      "Guess the player" game (Play tab)
│           ├── DiscoverPanel.tsx  Insight cards (rankings, upsets, rivalries…)
│           ├── EraBar.tsx         Decade selector + play button
│           ├── ChatPanel.tsx      Agent chat: streaming, tool steps, entity links
│           ├── SearchBox.tsx      Debounced search (optionally one entity type) with keyboard navigation
│           ├── EntityMedia.tsx    Photos, bios, club careers, flags and crests in the Inspector
│           ├── Flag.tsx           Flag / avatar images
│           ├── ErrorBoundary.tsx  Contains a crash to one panel instead of blanking the app
│           └── charts.tsx         LineChart, ResultBar, ProbBar (SVG)
│
├── data/                     (git-ignored) raw dataset; never edited by code
├── football_kg.gpickle       (git-ignored) Stage 1 output
├── neo4j_import/             (git-ignored) Stage 1 Neo4j export
└── app_data/                 (git-ignored) football.duckdb, media cache, downloads
```

---

## 5. Stage 1: building the knowledge graph (`src/build_kg.py`)

### Input files

| File | Contents |
|---|---|
| `results.csv` | One row per match: date, home/away team, scores, tournament, city, country, neutral venue |
| `goalscorers.csv` | One row per goal: match key, team, scorer, minute, own goal, penalty, assist |
| `shootouts.csv` | Penalty-shootout winners |
| `former_names.csv` | Historical team names (e.g. Dahomey → Benin) with date ranges |

### Entity resolution: stable IDs

Each real-world thing gets **one ID** so that every mention of it links to the same node:

| Node | ID format | Example |
|---|---|---|
| Team | `T:<slug>` | `T:brazil` |
| Player | `P:<name-slug>@<team-slug>` | `P:lionel_messi@argentina` |
| Match | `M<row index in results.csv>` | `M48836` |
| Goal | `G<n>` | `G47812` |
| Tournament | `TR:<slug>` | `TR:fifa_world_cup` |
| City | `CI:<city>@<country>` | `CI:london@england` |
| Country | `C:<slug>` | `C:england` |
| FormerName | `FN:<slug>` | `FN:dahomey` |

*slug* = lowercase, spaces → underscores, accents kept (`P:pelé@brazil`).

Player IDs include the team because **names are not unique**: many players called "Ronaldo"
exist. Goals are linked to matches by the key `(date, home_team, away_team)`.

### Graph schema

```
(Player)-[:SCORED]->(Goal)-[:IN_MATCH]->(Match)-[:PART_OF]->(Tournament)
(Player)-[:ASSISTED]->(Goal)-[:FOR_TEAM]->(Team)
(Player)-[:PLAYS_FOR]->(Team)-[:PLAYED_IN {side, goals}]->(Match)
(Match)-[:WON_BY]->(Team)          (Team)-[:WON_SHOOTOUT]->(Match)
(Match)-[:HELD_IN]->(City)-[:LOCATED_IN]->(Country)<-[:REPRESENTS]-(Team)
(Team)-[:FORMERLY_KNOWN_AS {start, end}]->(FormerName)
(Player)-[:PLAYED_FOR_CLUB {apps, goals, assists, start, end}]->(Club)-[:BASED_IN]->(Country)
```

Illustration: one World Cup goal by Messi links to the rest of the graph like this:

```
 Lionel Messi ──SCORED──► Goal ──IN_MATCH──► Match ──PART_OF──► FIFA World Cup
      │                    │                  │  ▲
  PLAYS_FOR            FOR_TEAM            HELD_IN └──PLAYED_IN── Argentina, the opponent
      ▼                    ▼                  ▼
  Argentina            Argentina           City ──LOCATED_IN──► Country
```

### Optional input: goals from Wikipedia goal lists

`goalscorers.csv` has no scorers for friendlies (18,283 matches in `results.csv`) and none for many
regional competitions either; India, for example, has no scorers for any SAFF Cup, Nehru Cup or Asian Cup
qualifier, so Sunil Chhetri showed 14 dataset goals instead of 95. `scripts/fetch_wikipedia_goals.py`
fills part of that gap:

1. finds Wikipedia's ~165 "List of international goals scored by …" pages (prolific scorers only),
2. downloads each once (HTML cached in `app_data/wiki_goal_lists/`, throttled like all Wikimedia calls),
3. reads the goal table (one row per goal: date, opponent, competition). Only the page's largest
   table is used, plus extra tables that are per-team goal lists (players who scored for two national
   teams). Hat-trick, youth and summary tables repeat goals and are skipped, as are rows in youth sections
   or against U15-U23 sides. A header row that the HTML parser read as data is promoted back to a header,
4. matches each goal to a match in `results.csv` by date (±1 day as a fallback) + both teams
   (historical names like "West Germany" resolve through `former_names.csv` and `OPPONENT_ALIASES`;
   exact team names always win, because `results.csv` keeps Yugoslavia and Czechoslovakia as teams),
5. per player and match, adds only the goals the dataset does not already credit to that player, so a
   goal is never counted twice. It drops a match's goals if the team's dataset plus imported goals would
   exceed its actual score,
6. writes `data_extra/wikipedia_goals.csv` in goalscorers.csv format plus `source` / `source_page`.

Result: about 2,480 goals for 95 players. Totals were checked against each player's Wikipedia list:
52 match exactly (Messi 126, Ronaldo 146, Kane, Lewandowski, Gerd Müller, Muriqi) and 72 are within 2.
Two are 1 over because of the source dataset: a Villa goal his list doesn't have, and a 1992 goal by
Pierre Aubameyang (the father) credited to Pierre-Emerick. The large shortfalls (Hussein Saeed, Kamamoto,
Ahmed Faras, Mokhtar Dahari) are Olympic qualifiers and early Merdeka Cup games that are not in
`results.csv`, so there is no match to attach them to.

### Optional input: assists from StatsBomb Open Data

`goalscorers.csv` has assists **only for FIFA World Cup matches**. No free, licensed source publishes
official career international assists: Opta is commercial, and FBref (which lost its Opta feed in January
2026) and Transfermarkt forbid scraping. [StatsBomb Open Data](https://github.com/statsbomb/open-data)
is free for analysis with attribution and has full event data for Euro 2020 and 2024, Copa América
2024 and AFCON 2023. `scripts/fetch_statsbomb_assists.py`:

1. downloads each match's events and lineups once, keeping only the goals (`app_data/statsbomb/`),
2. matches StatsBomb matches to `results.csv` (date ±1 day + both teams) and each goal to the
   dataset's goal (same team, minute within 2),
3. takes the assist from the pass marked `goal_assist` and writes the assister under the spelling the
   dataset uses for that team (e.g. "Lionel Andrés Messi Cuccittini" -> "Lionel Messi"),
4. writes `data_extra/statsbomb_assists.csv`. `build_kg.py` fills only empty assists from it;
   `goals.assist_source` says where each assist came from.

The 2018 and 2022 World Cups are run through the same matching purely to validate it against the
dataset's own assists (the agreement rate is printed); the dataset's World Cup assists stay as they are.
Credit "StatsBomb" when sharing anything built on this data.

`build_kg.py` appends these rows after the dataset's goals (so Goal IDs stay stable) and stores
`source` on each Goal node. The build still works when the file is absent. Because only stars have
Wikipedia goals, `players.goals` stays dataset-only (fair for rankings); `players.wiki_goals` and
`players.total_goals` (dataset + Wikipedia) give one player's full tally, which the Inspector shows.

### Optional input: club data from transfermarkt-datasets

[transfermarkt-datasets](https://github.com/dcaribou/transfermarkt-datasets) is a CC0 (public domain)
dataset that its author collects from Transfermarkt. It covers ~800 clubs in the top leagues (Premier
League, LaLiga, Bundesliga, Serie A, Ligue 1, Eredivisie, Liga Portugal, MLS, Saudi Pro League and more),
their cups and the European competitions, with every appearance's goals, assists and minutes. Coverage
starts in **2012/13** and ends in July 2026 (upstream updates are paused). `scripts/fetch_club_data.py`:

1. downloads the dataset's DuckDB file once to `app_data/transfermarkt/` (~200 MB, not committed),
2. links Transfermarkt players to graph players: same name (ignoring word order and hyphens, so
   "Heung-min Son" = "Son Heung-min") and national team (nationality or current national team), or a
   unique surname + first initial. Every link must pass an **era check**: the player's median
   international-goal year must fall between ages 16 and 40. Namesakes from other eras are common
   (Bebeto, Ali Karimi, two Abdoulaye Fayes),
3. writes small committed CSVs to `data_extra/`: `clubs.csv`, `club_player_stats.csv` (one row per
   player per club, all ~55k pairs, with `kg_player_id` when linked), `club_seasons.csv` (W/D/L and
   goals per club, season and competition) and `player_profiles.csv` (caps, position, date of birth).

`build_kg.py` adds a `Club` node for every club (`CL:<slug>`, linked to its Country) and
`PLAYED_FOR_CLUB` edges for the ~3.9k linked players. `build_db.py` loads the CSVs into `clubs`,
`club_careers` and `club_seasons` and adds `caps`, `position` and `date_of_birth` to `players`. Without
the CSVs those tables are created empty, so nothing breaks.

### Outputs

- `football_kg.gpickle`: the NetworkX `MultiDiGraph` (117,180 nodes, 404,151 edges).
- `neo4j_import/`: one CSV per node label and per relationship type, plus `load.cypher`
  (needs Neo4j 5.23+). The app does not use these; they are there for Neo4j users.

### Data facts you must know (they affect every answer)

- `goalscorers.csv` has **no friendlies** and covers only ~15.5k of ~46k matches with goals.
  Scorer totals mean "goals in competitive matches with recorded scorers", **not** career totals.
- Assists exist for only ~2k goals.
- One duplicate match key exists (1974 Tahiti v New Caledonia); goals link to the first one.
- Results always use **current** team names; old names are only in `former_names.csv`.
- **Own goals:** in `goalscorers.csv`, `team` is the team the goal *counted for*. For an own goal
  the scorer actually played for the opponent, so `build_kg.py` attaches that player to the wrong
  national team. The app excludes own goals from scorer rankings to work around this.

---

## 6. Stage 2: building the app database (`server/build_db.py`)

```bash
python -m server.build_db     # football_kg.gpickle -> app_data/football.duckdb
```

You rarely run this by hand: `Store()` calls it automatically when the database is missing or older
than the graph file (`needs_rebuild()`). It writes to a temporary file and swaps it in at the end, so
a failed build never leaves a half-written database.

### Why a second format?

The NetworkX graph is good for traversal but slow for "group by / count / rank" questions, and
loading it costs ~3 s and a lot of RAM per process. DuckDB answers aggregate queries over 50k matches
in milliseconds, opens instantly, and can be opened **read-only**.

### Tables

| Table | One row per | Key columns |
|---|---|---|
| `nodes` | graph node | `id, label, name, name_norm` (lowercase, accents stripped, for search), `degree` |
| `edges` | graph edge | `src, dst, type, side, goals, start_date, end_date` |
| `matches` | match | `match_id, date, home_team, away_team, home_score, away_score, tournament, city, country, neutral, winner` (NULL = draw), `shootout_winner`, `*_id` columns, **Elo:** `home_elo, away_elo, home_win_exp, winner_win_exp, upset` |
| `goals` | goal | `goal_id, match_id, date, tournament, team, opponent, player_id, scorer, minute, penalty, own_goal, assist_player_id, assister, assist_source` |
| `players` | player | `player_id, name, team, goals` (dataset, excl. own goals), `wiki_goals, total_goals, friendly_goals, penalty_goals, own_goals, assists, matches_scored_in, first_goal_date, last_goal_date` |
| `teams` | team | `team_id, name, matches, wins, draws, losses, goals_for, goals_against, first_match, last_match, confederation` (UEFA, CONMEBOL, CONCACAF, CAF, AFC, OFC or Non-FIFA: the confederation of the team's most recent continental competition, from `add_confederations`) |
| `tournaments` | tournament | `tournament_id, name, matches, first_match, last_match` |
| `former_names` | old name | `former_id, former_name, team, team_id, start_date, end_date` |
| `elo_history` | team × match | `team_id, date, match_id, elo` (rating after the match) |
| `elo_yearly` | team × year | `team_id, team, year, elo` (year-end), `rank` (world rank that year) |
| `clubs` | club | `club_id` (`CL:…`), `tm_club_id, name, league, country, stadium, stadium_seats, coach, squad_size` |
| `club_careers` | player × club | `player_id` (graph id or NULL), `tm_player_id, player_name, nationality, club_id, club, apps, goals, assists, minutes, league_*, europe_*` (Champions/Europa/Conference League), `first_app, last_app`; since 2012/13 |
| `club_seasons` | club × season × competition | `season, competition, competition_type, played, wins, draws, losses, goals_for, goals_against` |

Note: `winner` is derived from the scores, not from `WON_BY` edges, so an unplayed fixture with no
score can never be counted as a win.

### How the Elo ratings work (`add_elo`)

Elo is a rating system: each team has a number; beating a stronger team gains more points than
beating a weaker one. All 50,099 matches are replayed in date order:

1. Every team starts at **1500**.
2. Before a match, the home side's **win expectancy** is
   `E = 1 / (1 + 10^(-(R_home + H - R_away) / 400))`, where `H = 100` for home advantage
   (0 at a neutral venue).
3. The result `W` is 1 (home win), 0.5 (draw) or 0 (away win).
4. Ratings change by `Δ = K × G × (W − E)`: the home side gains Δ and the away side loses Δ.
   - **K** (match importance): World Cup 60 · continental finals 50 · qualifiers / Nations League
     40 · other tournaments 30 · friendlies 20.
   - **G** (goal difference): 1 for a 0–1 goal margin, 1.5 for 2 goals, `(11 + margin) / 8` for 3+.
5. A match is an **upset** when the winner's pre-match expectancy was **below 25%**.

The results pass a sanity check against football history: Hungary was #1 at the end of 1954,
Brazil in 1970, Spain in 2010, and the biggest World Cup upsets found are Cameroon–Brazil 2022,
Saudi Arabia–Argentina 2022 and USA–England 1950.

---

## 7. Stage 3: the backend API (`server/`)

### `store.py`: the data-access layer

`Store` opens `football.duckdb` once:

```python
duckdb.connect(path, read_only=True,
               config={"enable_external_access": False, "lock_configuration": True})
```

Every method takes a fresh **cursor** (`self.q(...)`), which makes it safe to call from FastAPI's
thread pool. Expensive results that never change (`meta`, `discover`, `schema_text`)
are cached with `@cached_property`.

| Method | What it does | How |
|---|---|---|
| `search(text, label)` | Find entities by name | All words must appear in `name_norm`; ranked exact → prefix/word-prefix → contains, then by `degree` |
| `entity(id)` | Full profile for the Inspector | Dispatches to `_detail_<label>()` (team record + Elo series, player goals, match goals + Elo expectancy, …) plus relationship counts |
| `expand(id, limit, type, direction)` | Neighbours of a node | One SQL with a window function: at most `limit` neighbours **per relationship group**, matches newest first, others by degree; reports totals that were cut (`truncated`) |
| `subgraph(ids)` | Nodes + all edges among them | Used for "add this to the canvas" |
| `subgraph_with_context(ids)` | `subgraph` plus anchors | Adds player→team, match→teams/tournament, goal→scorer/match; for 2–3 teams adds their latest meetings. Used for agent drawings |
| `overview(top, min_meetings, start, end, backbone)` | The "football universe" for an era | Top `top` teams by matches in the era (200 all-time, 140 per decade). Edges: pairs that met `min_meetings` times (default ≈ 10% of the era's years, min 4) **plus** each team's `backbone` (3) most frequent opponents, flagged `backbone: true`, so no team floats alone. Nodes carry `conf` (confederation) and `group` (its fixed palette slot), Elo at era end for sizing; the response lists the confederations with team counts |
| `h2h(a, b)` / `h2h_graph` | Head-to-head data | Summary, results by decade, biggest wins, recent meetings, competitions, both Elo series |
| `discover` | Insight cards | Current Elo top 10 (+ 4-year change), all-time peaks, biggest upsets (competitive, both teams rated above 1600), most-played rivalries, most different opponents |
| `find_path(src, dst, via_teams)` | Shortest connection | Builds an undirected NetworkX graph lazily (first call only) without hub labels (Tournament/City/Country/FormerName); bidirectional BFS. `via_teams=False` hides Team nodes, so players link only through shared matches |
| `run_sql(sql)` | Agent SQL tool | See security below |
| `schema_text` | Table list for the agent prompt | Read from `information_schema` |

**Why are hubs excluded from paths?** Every match is "part of" the *Friendly* tournament or "held
in" some city, so through those hubs everything would be 2 hops apart and the path would mean
nothing.

#### SQL safety (`run_sql`)

Several layers protect against harmful or runaway agent SQL:

1. Comments are stripped; only **one statement** is allowed (no `;` in the middle).
2. It must start with `SELECT`, `WITH`, `FROM`, `SUMMARIZE`, `DESCRIBE` or `SHOW`.
3. The connection is **read-only**: no INSERT/UPDATE/DROP can succeed.
4. `enable_external_access=false`: no `read_csv('C:/…')`, no `COPY … TO`, no HTTP.
5. `lock_configuration=true`: the query cannot `SET` those options back.
6. A **timeout** (20 s): a timer calls `cursor.interrupt()`.
7. A **row cap**: at most 200 rows for API callers, 60 for the agent.

### `main.py`: HTTP endpoints

| Method | Path | Returns |
|---|---|---|
| GET | `/api/meta` | Counts per label/relationship, date span, agent model, whether a key is set |
| GET | `/api/search?q=&label=&limit=` | Search hits |
| GET | `/api/entity/{id}` | Entity profile |
| GET | `/api/expand/{id}?limit=&type=&direction=` | Neighbourhood subgraph |
| POST | `/api/subgraph` `{ids: [...]}` | Nodes + edges among them |
| GET | `/api/overview?start=&end=&top=&min_meetings=&backbone=` | Era "universe" + confederations |
| GET | `/api/clubs/universe` | Club universe: clubs by league, linked by shared players |
| GET | `/api/clubs/pipeline` | Nations → clubs rings |
| GET | `/api/h2h?a=&b=` | Head-to-head data + its graph |
| GET | `/api/compare/players?a=&b=` | Player vs player (404 unless both are players) |
| GET | `/api/quiz/new?level=easy\|medium\|hard` | A new "guess the player" round: token + 8 clues |
| POST | `/api/quiz/guess` `{token, guess, reveal}` | `{correct}`, plus the answer when correct or revealed |
| GET | `/api/media/teams`, `/api/media/clubs` | Flags/crests and club logos (proxied URLs) |
| POST | `/api/media/players` `{ids}` | Player photos (queues lookups it doesn't have yet) |
| GET | `/api/media/entity/{id}` | Bio, club career or team summary for the Inspector |
| GET | `/api/img?u=` | Disk-cached proxy for Wikimedia images only |
| GET | `/api/discover` | Insight cards |
| GET | `/api/path?src=&dst=&via_teams=` | Shortest path subgraph |
| POST | `/api/chat` `{message, conversation_id}` | **Server-Sent Events** stream (see §8) |
| DELETE | `/api/chat/{conversation_id}` | Forget a conversation |
| GET | `/*` | The built React app from `web/dist` (single-page-app fallback to `index.html`) |

Try them in a browser or with curl; FastAPI also serves interactive docs at
**http://localhost:8000/docs**.

```bash
curl "http://localhost:8000/api/search?q=messi"
curl "http://localhost:8000/api/h2h?a=T:brazil&b=T:argentina"
```

All graph payloads share one shape:

```json
{ "nodes": [{"id": "T:brazil", "label": "Team", "name": "Brazil", "degree": 2000, "elo": 2070, "group": 6}],
  "edges": [{"id": "T:brazil|PLAYED_IN|M100", "source": "T:brazil", "target": "M100", "type": "PLAYED_IN"}] }
```

---

## 8. The AI agent (`server/agent.py`)

### What it is

A **tool-calling loop**: the language model cannot see the database. It can only ask the server to
run one of five tools, read the results, and decide what to do next, until it can answer.

```
 user question
      │
      ▼
 ┌─────────────────────────────────────────────────────────────┐
 │  messages = [system prompt, ...history, user question]      │◄──────────┐
 │  Groq chat.completions.create(tools=TOOLS, stream=True)     │           │
 └───────────────┬─────────────────────────────────────────────┘           │
                 │ streamed tokens                                         │
      ┌──────────┴───────────┐                                             │
      ▼                      ▼                                             │
  text → UI          tool calls?  ── no ──► done (answer complete)         │
                         │ yes                                             │
                         ▼                                                 │
            validate arguments → run Store method                          │
            send tool_start / tool_result / graph events to the UI         │
            append results to messages ────────────────────────────────────┘
```

### The five tools

| Tool | Backed by | Purpose |
|---|---|---|
| `search_entities(query, label?)` | `Store.search` | Turn names into IDs (e.g. "pele" → `P:pelé@brazil`) |
| `get_entity(id)` | `Store.entity` | Read a profile |
| `run_sql(sql)` | `Store.run_sql` | Any statistics question over the tables |
| `find_path(from_id, to_id, via_teams)` | `Store.find_path` | "How are X and Y connected?"; also draws the path |
| `show_in_graph(node_ids, caption, focus_id?)` | `Store.subgraph_with_context` | Draw entities on the canvas |

### The system prompt

`SYSTEM_TEMPLATE` tells the model:

- the graph schema and the ID conventions,
- the **live table schema** (generated from the database, so it never goes stale),
- notes on tricky columns (draws are NULL winners, own goals, Elo columns, tournament names),
- the **data caveats** it must mention (scorer coverage, sparse assists),
- how to work (resolve names first, prefer one good query) and how to answer (direct answer first,
  short tables, entity links in the form `[Lionel Messi](<entity:P:lionel_messi@argentina>)`).

### Streaming protocol (Server-Sent Events)

`/api/chat` keeps the HTTP response open and sends one `data: {json}` line per event:

| Event | Meaning | UI reaction |
|---|---|---|
| `conversation` `{id}` | Conversation ID for follow-ups | Stored; sent with the next question |
| `thinking` `{delta}` | Model reasoning (if the model exposes it) | Collapsible "Reasoning" box |
| `text` `{delta}` | A piece of the answer (markdown) | Appended live |
| `tool_start` `{id, name, input}` | A tool is about to run | Step row with spinner |
| `tool_result` `{id, ok, summary, result?}` | Tool finished (`result` = SQL rows) | Step shows summary, SQL and table preview |
| `graph` `{nodes, edges, caption, focus?, path?}` | Something to draw | Canvas replaced, path highlighted |
| `done` `{usage}` | Finished | Input re-enabled |
| `error` `{message}` | Readable error | Red box in the chat |

### Robustness features

- **Conversation memory** lives on the server (`Agent.conversations`, an in-memory dict). Restarting
  the server forgets conversations.
- **One answer at a time per conversation** (a lock); a second request gets a friendly error.
- **Failed turns roll back**: if anything fails mid-answer, the history is cut back to before the
  question, so the conversation is never left half-finished.
- **Argument validation**: tool arguments are parsed and type-checked before running; bad ones go
  back to the model as an error it can fix.
- **Groq `tool_use_failed`** (a garbled tool call) is retried up to 2 times.
- **Tool budget**: after 10 tool rounds the model is forced to answer (`tool_choice="none"`).
- **Free-tier friendly**: SQL results are capped (60 rows, 9,000 characters), and after each answer
  older tool results are shortened (`_compact_turn`) so follow-ups stay small. The SDK automatically
  waits and retries when Groq rate-limits (`max_retries=4`).
- **Automatic drawing**: if the model links entities but never calls `show_in_graph`, the server
  draws them itself. The UI also hides any tool JSON the model pastes into its text.

### Configuration (`.env`)

| Variable | Default | Notes |
|---|---|---|
| `GROQ_API_KEY` | (required) | Free key from console.groq.com |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Any Groq model with tool calling (`openai/gpt-oss-20b`, `qwen/qwen3.8-27b`) |
| `GROQ_REASONING_EFFORT` | `medium` | `low` / `medium` / `high` (gpt-oss models only) |

---

## 8½. Media enrichment: photos, flags, crests, clubs (`server/media.py`)

The dataset has no images and no club information, so the app enriches it from **Wikidata** and
**Wikipedia** at runtime and caches everything locally.

| What | Source | When fetched |
|---|---|---|
| Team flag + federation crest | One Wikidata SPARQL query over all national teams (matched by label, e.g. "Brazil men's national football team") | Once, in a background thread at startup |
| Team summary | Wikipedia intro extract | When you open a team |
| Player photo, short bio, birth year, position | Wikipedia page (exact title first, then search), validated as a footballer page | When a player appears on the canvas or in the Inspector |
| Club career + club logos | Wikidata `P54` (member of sports team) with start/end dates; logos from `P154` | Together with the player |

**Matching tricks.** Wikidata lists England's *country* as the United Kingdom, so territory and
home-nation teams take the flag of the place named like the team instead; a few teams Wikidata can't
resolve have fixed flag files (`FLAG_FILES`). National and youth sides are filtered out of club
careers. Some club logos are non-free (only on English Wikipedia, not Commons); those clubs show a
monogram badge.

**Caching.** Metadata lives in `app_data/media.sqlite`, image files in `app_data/img/`. Each lookup
and each image is downloaded once; failures are not cached, so they retry later.

**Image proxy.** The browser loads images from `/api/img?u=…`, which downloads them once (only from
`upload.wikimedia.org`, `thumb.wikimedia.org` and `commons.wikimedia.org`, to prevent the proxy being
abused) and serves them from disk. Images coming from our own origin keep the canvas exportable as
PNG (cross-origin images would "taint" it).

**Rate limits (important).** Wikimedia limits API clients per minute: **10 requests/min** if the
User-Agent has no contact info, **200/min** if it includes an email or URL. The client therefore:

- sends all API calls one at a time through a global throttle (`_get_json`), spaced
  6.5 s apart (anonymous) or 0.4 s apart (with `WIKIMEDIA_CONTACT` set in `.env`);
- honours `Retry-After` on HTTP 429, else waits at least 5 s and backs off;
- runs player lookups in **one background worker** with a priority queue: what you are looking at
  (priority 0) goes ahead of the startup warm-up of top scorers (priority 5; 40 players anonymous,
  300 with contact info).

The API answers `/api/media/players` immediately with cached photos plus a `pending` list; the UI
(`web/src/media.ts`) re-polls pending ones every 2.5 s, so photos pop in one by one.

**Frontend.** `media.ts` keeps team media, player photos and loaded `HTMLImageElement`s, and exposes
`useMediaVersion()` which bumps whenever something new is available. `GraphCanvas` clips flags into
team circles and photos into player circles (with a coloured rim, so community/type colour stays
visible); `EntityMedia.tsx` renders the Inspector hero (photo, bio, club career / flag banner, crest,
summary); `Flag.tsx` puts flags in search results, Discover and head-to-head.

**Endpoints:** `GET /api/media/teams` · `POST /api/media/players {ids}` ·
`GET /api/media/entity/{id}` · `GET /api/img?u=`.

Images are free-licensed Wikimedia Commons files (mostly CC BY-SA); the UI credits the source next
to them.

---

## 9. The frontend (`web/`)

### How it is served

- **Development:** `npm run dev` starts Vite on **:5173** with hot reload; Vite proxies every
  `/api/*` call to the FastAPI server on **:8000** (`vite.config.ts`).
- **Production:** `npm run build` writes static files to `web/dist/`; FastAPI serves them on :8000.
  One process, one port.

### Component tree

```
App.tsx  ── owns all state and actions
├── header: brand · SearchBox · KPI counts · theme toggle
├── stage (left)
│   ├── GraphCanvas        the force-directed graph
│   ├── caption pill       what you are looking at
│   ├── toolbar            overview · fit · freeze · PNG · clear
│   ├── legend             node types / communities / H2H result colours
│   ├── home switcher      Nations · Clubs · Pipeline (overview only)
│   ├── EraBar             decades + play (Nations view only)
│   ├── Inspector  OR  H2HPanel  OR  ComparePanel   (floating panel, inside an ErrorBoundary)
│   └── toast              short notices
└── side (right)  tabs:  ChatPanel  |  DiscoverPanel  |  QuizPanel   (each inside an ErrorBoundary)
```

### State management

There is no Redux or similar library: `App.tsx` holds state with `useState`, and two custom hooks
manage the rest.

**`useGraph.ts`** holds what is on the canvas. The force simulation stores positions (`x`, `y`)
*inside* node objects, so the hook keeps node and link objects **stable** in `Map`s (refs) and only
hands React new arrays. That is why expanding a node doesn't reshuffle the graph.

- `merge(subgraph, anchorId?)` adds nodes/edges; new nodes start next to the anchor, so the graph
  "blooms" out from the node you expanded.
- `replace(subgraph, keepPositions?)` swaps the contents; with `keepPositions` surviving nodes keep
  their place (used to morph between eras).
- `remove`, `clear`, `has`, `ids`, `ungroup` (drops overview colours/sizes).

> **Pitfall (already hit once):** the hook returns a new object each render. Depend on its
> individual functions (`merge`, `replace`, …), never on the whole object, or effects re-run on
> every render and cause an infinite request loop.

**`useTheme.ts`** stores `light`/`dark`, writes `data-theme` on `<html>`, and remembers the choice
in `localStorage`.

### Rendering the graph (`GraphCanvas.tsx`)

`react-force-graph-2d` runs a d3-force physics simulation and draws on an HTML canvas. We replace
its default node drawing with `paintNode`:

1. **Shape** by node type (`LABEL_SHAPE` in `theme.ts`, drawn by `drawShape`).
2. **Colour** by node type, or by confederation (overview) or result (head-to-head) when `node.group`
   is set.
3. **Size** by `log(degree)`, or by Elo in the overview (`nodeRadius`).
4. **Surface-coloured ring** around each node so overlapping nodes stay distinguishable.
5. **Team code** (BRA, ARG) inside large team circles.
6. **Label pill** under the node when zoomed in enough, or when selected, hovered or on a path.
   Match and goal labels only appear when zoomed in close, to avoid clutter.
7. **Dimming**: hovering a node dims everything except it and its neighbours; a highlighted path
   dims the rest, and its edges carry moving particles.

Physics (`d3Force('charge')`, `d3Force('link')`) is tuned differently for the overview and for
exploration (compact). After a layout settles, the view auto-fits (our own `fit`, which keeps clear of a
floating panel on the left via `insetLeft` and caps the zoom).

**The overview ("football universe")** is drawn in four layers:

- **Layout**: every confederation has a *home* roughly where it sits on a world map (`CONF_HOME` in
  `theme.ts`). Two custom d3 forces are added only in this mode: `home` pulls each team gently
  towards its home, and `collide` stops flags overlapping. Links still pull rivals together, so teams
  that play across confederations (Australia, Israel, the USA and Mexico) settle between clusters.
  New nodes are seeded near their home, so the view "blooms" outward when it opens.
- **Backdrop** (`onRenderFramePre`): a soft radial "nebula" per confederation, sized from its nodes'
  spread, plus its title (`UEFA · Europe · 57`), above the cluster for northern confederations and
  below it for southern ones.
- **Links** (`linkCanvasObject`, virtual edges only): curved arcs with a colour gradient from one
  confederation to the other, opacity and width by number of meetings. The dark theme uses
  `lighter` (additive) blending so busy areas glow; light uses `multiply`.
- **Nodes and labels**: flags with a confederation-coloured rim, a gloss highlight and a glow for
  strong (high-Elo) teams. Labels are collected while painting nodes and drawn last
  (`onRenderFramePost`), strongest first, skipping any that would overlap: no flag covers a name and
  names never pile up.

Hovering a team dims everything but its rivals; clicking a confederation in the legend spotlights
it (`spotlight` prop).

**Three home views.** A switcher at the top of the canvas (`HOMES` in `App.tsx`) swaps between:

| View | Endpoint / method | Nodes | Links | Layout |
|---|---|---|---|---|
| Nations | `/api/overview`, `Store.overview` | 200 national teams, size = Elo | meetings | confederation homes |
| Clubs | `/api/clubs/universe`, `Store.club_universe` | clubs of the 7 big leagues with 4+ seasons since 2012/13, plus the 24 winningest elsewhere; size = win rate (`strength`) | players shared (5+ games at both) + each club's 2 strongest bonds | league homes in `CONF_HOME`, roughly a map of Europe |
| Pipeline | `/api/clubs/pipeline`, `Store.pipeline` | 40 nations with the most internationals in club data + the 64 clubs that used most of them | internationals of that nation who played for that club | two fixed rings (`hx`/`hy` from the server), links bundled towards the centre |

All three reuse the same canvas code. Every overview node has a `conf` (cluster key: confederation or
league), and the canvas works out each node's home from `hx`/`hy` or `CONF_HOME[conf]`. On the rings, link
forces are switched off and a strong home force keeps nodes in place. In the Pipeline, spotlighting a
confederation also keeps the clubs its players went to lit. Club logos (`Media.warm_clubs`, 789 of 796 clubs) are
found in three steps, all serial and throttled:

1. Wikidata, matched exactly by Transfermarkt team ID (P7223): the free logo on Commons (P154). About 280 clubs
   have one.
2. Otherwise, the main image of the club's English Wikipedia article (`prop=pageimages`, `pilicense=any`).
   Most club crests are non-free files hosted on en.wikipedia under fair use, so this finds nearly all the
   rest. Only crest-like images (SVG/PNG, or "logo"/"crest"/"badge" in the file name) are kept.
3. Otherwise, a Wikipedia search for the club's name. The result's title must contain the club's first
   distinctive word, so "Metalist Kharkiv" can't pick up "FC Kharkiv".

Clubs still without a logo get a crest with their initials. Results are cached; delete the `__clubs_v2__`
row from `app_data/media.sqlite` to run the lookup again. Opening an entity from the overview starts a clean canvas around it.

Interactions: click = select, double-click or right-click = expand, drag = pin, background click =
deselect.

### Colours and theme (`theme.ts`, `styles.css`)

- All UI colours are **CSS variables** defined twice: under `:root` (light) and under
  `:root[data-theme="dark"]`. Components only use variables like `var(--panel)` or
  `var(--series-1)`.
- Node colours come from an **8-slot categorical palette** with separate light and dark steps,
  validated for colour-blind separation. Because 8 colours are too many to tell apart reliably,
  **shapes** are a second encoding, so a node type is never identified by colour alone.
- The canvas cannot read CSS variables cheaply on every frame, so `CANVAS[mode]` in `theme.ts` holds
  the canvas inks (label text, rings, edges) for each mode.
- Overview colours follow the confederation in a fixed slot order (UEFA, CONMEBOL, CONCACAF, CAF, AFC,
  OFC; Non-FIFA neutral), so Brazil keeps the same colour in every decade.

### Charts (`charts.tsx`)

Small SVG components, no library:

- **`LineChart`**: one or two series, crosshair + tooltip on hover, legend and end-of-line labels
  for two series, optional peak marker. Used for team Elo and head-to-head Elo.
- **`ResultBar`**: wins / draws / wins as one stacked bar with 2px gaps; hover shows the numbers.
- **`ProbBar`**: the pre-match win expectancy split.

### Chat (`ChatPanel.tsx` + `api.ts`)

`streamChat()` POSTs the question and reads the response body as a stream, splitting on blank lines
into SSE events. Each event updates the current assistant message, which is a list of **parts**
(`text`, `thinking`, `tool`, `graph`) rendered in order. Markdown links starting with `entity:`
become buttons that call `openEntity(id)`, which puts that entity on the canvas and opens its
Inspector.

---

### Player features (`store.py` + `Inspector.tsx`, `ComparePanel.tsx`, `QuizPanel.tsx`)

| Feature | Backend | Notes |
|---|---|---|
| Club team-mates | `Store.club_teammates` | players whose spells at the same club overlapped (2012/13+), longest together first |
| Scored together | `Store.scored_with` | national-team team-mates who scored in the same match |
| Player vs player | `Store.compare_players`, `GET /api/compare/players` | stats for both, goals per year, shared clubs, common team-mates, matches both scored in |
| Guess the player | `Store.quiz_new` / `quiz_guess`, `/api/quiz/*` | the answer stays on the server (keyed by a random token) so it can't be read in the browser; score and streak live in `localStorage["fkg.quiz"]` |

### Deep links (`routes.ts`)

The URL hash mirrors what is on screen: `#/nations`, `#/clubs`, `#/pipeline`, `#/e/<id>`,
`#/h2h/<team>/<team>`, `#/vs/<player>/<player>`. `App.tsx` computes the current `Route` from its
state and `pushState`s it when it changes. On load and on browser back/forward, `applyRoute` parses the
hash and opens that view. `parseRoute` never throws: a malformed link such as `#/e/%E0` just falls back
to the home view.

### Robustness

- **Error boundaries**: each floating panel and each side tab sits in an `ErrorBoundary`, so a crash in
  one shows a small "Something went wrong · Retry" card instead of a blank page.
- **Offline mode** (`FKG_OFFLINE=1`): no Wikimedia calls at all (`_get_json` refuses and the image proxy
  serves only cached files), and the media warm-up is skipped. Tests and CI use it; you can also use it to
  work without internet.
- **Read-only data access**: the runtime DuckDB connection is read-only with external file access
  disabled, and the agent's SQL is a single SELECT/WITH with a time limit (all covered by tests).
- **Deterministic imports**: the `fetch_*` scripts sort their output, so re-running them on the same
  input gives byte-identical CSVs and clean git diffs. Ties between namesakes are broken by fixed rules.

---

## 10. What happens when… (end-to-end walkthroughs)

### …you open the app

1. `App` mounts → `api.meta()` (header counts) and `loadOverview(ALL_TIME)`.
2. `GET /api/overview` → `Store.overview()` returns 200 teams, ~850 rivalry edges, each team's
   confederation and Elo.
3. `replace()` puts them on the canvas, seeded near their confederation's home; the clusters bloom
   outward, the links fade in, and the view fits to the screen (clear of the welcome card).

### …you search "messi" and press Enter

1. `SearchBox` waits 140 ms after typing, then calls `GET /api/search?q=messi`.
2. `openEntity("P:lionel_messi@argentina")`: the node isn't on the canvas, so the app requests
   `POST /api/subgraph` with it plus everything already shown (to find edges to existing nodes).
3. If it arrives unconnected, the app also fetches a small neighbourhood (`/api/expand`) for context.
4. The Inspector opens and calls `GET /api/entity/…` → `Store._detail_player()`.

### …you double-click a team

1. `GraphCanvas` detects two clicks within 320 ms → `onExpand`.
2. `GET /api/expand/T:brazil?limit=12` → at most 12 neighbours per relationship type.
3. `merge(sub, "T:brazil")`: new nodes appear around Brazil; a toast tells you how many were left
   out (e.g. "first 12 of 1,065 PLAYED_IN").

### …you press ▶ on the era bar

1. `EraBar` steps through the decades every 2.6 s, calling `changeEra(decade)`.
2. Each step loads `/api/overview?start=1950&end=1959` and calls `replace(sub, keepPositions=true)`,
   so teams that exist in both decades glide to their new positions instead of jumping.

### …you ask "How has Brazil fared against Argentina?"

1. `ChatPanel` → `POST /api/chat`. The server starts the agent loop and streams events.
2. The model calls `search_entities` twice (→ `T:brazil`, `T:argentina`), then `run_sql` with a
   query on `matches`. Each call appears as a step in the chat.
3. The answer streams in as markdown with entity links.
4. No `show_in_graph` call was made, so the server draws the linked teams plus their latest
   head-to-head matches (`subgraph_with_context`). The canvas updates.

---

## 11. Development workflow

### Running in development

```bash
# terminal 1 — API with auto-reload on Python changes
uvicorn server.main:app --reload --port 8000

# terminal 2 — UI with hot module reload
cd web && npm run dev          # open http://localhost:5173
```

### Useful commands

| Task | Command |
|---|---|
| Update the dataset | `python scripts/get_data.py` |
| Rebuild the graph | `python src/build_kg.py` |
| Rebuild the app database | `python -m server.build_db` (stop the server first on Windows) |
| CLI showcase queries | `python src/explore_kg.py` |
| **Run every check** | `python scripts/check.py` (add `--rebuild` to rebuild graph + DB first) |
| Backend tests only | `python -m pytest` (`-k compare` for a subset, `-x` to stop at the first failure) |
| UI unit tests | `cd web && npm test` |
| Type-check the UI | `cd web && npm run typecheck` |
| Production UI build | `cd web && npm run build` (`npm run check` = typecheck + unit tests + build) |
| Work without internet | set `FKG_OFFLINE=1` before starting the server |
| API docs | http://localhost:8000/docs |

### Poking at the data directly

```python
import duckdb
c = duckdb.connect("app_data/football.duckdb", read_only=True)
c.sql("SELECT team, elo FROM elo_yearly WHERE year = 2026 ORDER BY elo DESC LIMIT 10").show()
```

```python
from server.store import Store
s = Store()
s.search("zidane")
s.find_path("P:pelé@brazil", "P:lionel_messi@argentina", via_teams=False)["path"]
```

Testing the agent without the UI:

```python
import json
from fastapi.testclient import TestClient
from server.main import app
with TestClient(app).stream("POST", "/api/chat", json={"message": "Top 5 World Cup scorers?"}) as r:
    for line in r.iter_lines():
        if line.startswith("data: "):
            print(json.loads(line[6:]))
```

---

## 11½. Testing and quality

### What is tested

| Suite | Where | What it guards |
|---|---|---|
| Graph | `tests/test_graph.py` | every node has a known label and a conventional ID; every edge follows the schema in `CLAUDE.md`; one Match per results row; each match has one home + one away side; each goal has exactly one scorer, match and team; winners follow the score |
| Data integrity | `tests/test_db.py` | all tables exist; imported goals never exceed a team's score; goals belong to a team in the match; player goal columns add up; `winner` and `upset` agree with the numbers; Elo is plausible; every team has a confederation; assists have a known source; club tables are referentially sound; linked players fit their era; known namesakes link to the right person |
| Store | `tests/test_store.py` | search ranking and accents, every entity type, expand limits, paths, H2H, the three home views, discover, compare, quiz rounds, and SQL safety (no writes, no multiple statements, no file access, read-only connection) |
| API | `tests/test_api.py` | every GET endpoint answers; bad input gets 404/422 (not 500); subgraph limits; the quiz flow; the image proxy only serves Wikimedia; chat without a key returns a readable error |
| Agent | `tests/test_agent.py` | tool-argument validation, every tool against the real store, entity-link parsing, history compaction, the system prompt lists the tables |
| Import scripts | `tests/test_scripts.py` | Wikipedia table selection (main table only; hat-trick, youth and summary tables ignored), youth sections, name normalisation, the club linker's name key and era check, StatsBomb minute rounding |
| UI logic | `web/src/*.test.ts` (Vitest) | deep-link parsing (including malformed links) and round-trips; node sizes, colours and layout tables |

The backend tests run against the **built** database and graph. Tests marked `needs_build` are skipped,
with a message, when those are missing. Everything runs offline: `conftest.py` sets `FKG_OFFLINE=1` and
an empty `GROQ_API_KEY`, so no test ever calls Wikimedia or the LLM.

### Running

```bash
python scripts/check.py              # everything, like CI
python -m pytest -k "quiz or compare"  # a subset
cd web && npx vitest                 # UI tests in watch mode
```

CI (`.github/workflows/ci.yml`) does the full pipeline on every push: clone the dataset, build the
graph and the database, run pytest, then `npm ci && npm run check`.

### Known source-data faults

The invariant tests encode a few faults of the **source** dataset as explicit exceptions, for example
`KNOWN_OVER_SCORE` in `test_db.py` (Switzerland 2-0 Romania, 1933, lists two Romanian goals) and the
Aubameyang father/son goal. `data/` is never edited. If a new fault appears after a dataset update, a
test will name it. Then decide: either it is a real bug in our code, or it is source data and gets
added to the list with a comment.

### When you add something

- New endpoint → a case in `test_api.py` (happy path + bad input).
- New store method → a test in `test_store.py` asserting structure and one known fact.
- New table or column → an invariant in `test_db.py` (keys unique, references valid, values in range).
- New import rule → a synthetic-input test in `test_scripts.py`.
- New pure UI logic → a `*.test.ts` next to it.

---

## 12. How to extend the project

### Add an API endpoint

1. Write the query as a `Store` method in `server/store.py` (use `self.q(sql, params)` with `?`
   placeholders, never string-formatting user input into SQL).
2. Expose it in `server/main.py` with `@app.get("/api/…")`.
3. Add the response type to `web/src/types.ts` and a function to `web/src/api.ts`.
4. Call it from a component.

### Give the agent a new tool

1. Implement the logic in `Store`.
2. In `server/agent.py`: add a `_tool(...)` entry to `TOOLS` (name, description, JSON schema), its
   required argument types to `_REQUIRED_TYPES`, and a branch in `_run_tool` that returns
   `(result_for_model, one_line_summary, graph_event_or_None)`.
3. Optionally add a label/icon in `TOOL_META` in `web/src/components/ChatPanel.tsx`.
4. Mention when to use it in `SYSTEM_TEMPLATE`.

### Add a new derived table (like Elo)

1. Compute it in `server/build_db.py` (pattern: compute in pandas, `con.register(...)`,
   `CREATE TABLE … AS SELECT …`).
2. Add the table name to the list in `Store.schema_text` so the agent can see it, and describe
   non-obvious columns in the agent prompt notes.
3. Delete `app_data/` (or run `python -m server.build_db`) to rebuild.

### Change the graph itself (new node or edge type)

1. Edit `src/build_kg.py` and keep the schema block in `CLAUDE.md` in sync.
2. Rebuild: `python src/build_kg.py`; the app database rebuilds automatically on the next server start.
3. If it's a new node **label**: add it to `Label` in `types.ts`, and give it a slot, shape and base
   radius in `theme.ts`, plus `LABELS` in `store.py` and a `_detail_<label>` method if it needs a
   profile.
4. Add the edge type to `SCHEMA` (and the label to `ID_PATTERN`) in `tests/test_graph.py`. The
   schema test fails until you do, which is how the test suite keeps `CLAUDE.md` and the build in sync.
5. Run `python scripts/check.py --rebuild`.

### Tune the Elo model

Edit `_elo_k`, `CONTINENTAL_FINALS`, the home advantage (`adv`) or the goal multiplier in
`add_elo`, then rebuild the database. The upset threshold (25%) is in the `upset` SQL expression.

### Switch the LLM model

Set `GROQ_MODEL` in `.env` and restart. The model must support tool calling. To move to a different
provider, `Agent._loop` is the only place that talks to the LLM API.

---

## 13. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| UI says "API is not reachable" | The FastAPI server isn't running on :8000. Start `uvicorn server.main:app --port 8000`. |
| `PermissionError` rebuilding the DB on Windows | The running server holds the file open. Stop the server, then rebuild. |
| Chat: "GROQ_API_KEY is not set" | Add the key to `.env` (project root) and restart the server. |
| Chat is slow or says rate limit reached | Groq free-tier tokens-per-minute limit. Wait a minute, ask shorter questions, or try `GROQ_MODEL=openai/gpt-oss-20b`. |
| Chat answer has odd JSON in it | The model pasted a tool call into text; the UI hides blocks containing `node_ids`, and the graph is drawn anyway. |
| UI changes don't show at :8000 | You're viewing the old build. Run `npm run build` in `web/`, or use the dev server on :5173. |
| `football_kg.gpickle not found` | Run `python src/build_kg.py` first (and `scripts/get_data.py` before that). |
| Graph looks like one tangled ball | Press **Fit**, or toggle **Freeze/Resume** to let the physics settle again. |
| Requests in an endless loop (browser `ERR_INSUFFICIENT_RESOURCES`) | An effect depends on the whole `useGraph()` object; see the pitfall in §9. |
| Tests are all "skipped" | The graph/database aren't built. Run `python scripts/check.py --rebuild`. |
| A `test_db.py` invariant fails after `get_data.py` | The dataset changed. Read the failure: fix our import, or add the new source fault to the known list with a comment (see §11½). |
| A panel shows "Something went wrong" | An error boundary caught a crash. The browser console has the stack trace; Retry closes the panel. |
| Club re-import changed many rows | Run `fetch_club_data.py` twice: the output must be byte-identical. If it isn't, a tie-break became non-deterministic. |

---

## 14. Known limitations and roadmap

**Limitations**

- Dataset scorers cover only part of the matches (no friendlies, few regional cups). Wikipedia fills
  this for ~95 star players only. Assists cover World Cups, Euro 2020/2024, Copa América 2024 and
  AFCON 2023 only; they are not career totals.
- Own-goal scorers are attached to the wrong national team in the graph (see §5).
- Conversations are kept in memory only and vanish on restart.
- The agent cannot open the head-to-head or compare panels by itself yet.
- Club data covers 2012/13 to July 2026 only (upstream updates are paused).
- The free Groq tier limits how fast multi-step questions can be answered.

**Roadmap** (from `CLAUDE.md`)

1. ✅ Ingestion, IDs, NetworkX graph, Neo4j export
2. ⬜ Load into Neo4j and verify counts
3. ◐ Derived knowledge: ✅ Elo + upsets · ⬜ `RIVAL_OF` edges (50+ meetings), `TEAMMATE_OF`
4. ⬜ Enrich players/teams from Wikidata (photos, birth dates, positions)
5. ◐ Graph analytics: ✅ confederation clustering · ⬜ community detection, link prediction
6. ✅ Interactive web explorer + agent

Done since the first version: club data, the Clubs and Pipeline views, player compare, team-mates, the
quiz, shareable deep links, a test suite and CI.

Further ideas: club match data (club head-to-heads, title races, club Elo), an all-time XI builder,
"on this day" stories in Discover, transfer-flow animation in the Clubs view, `RIVAL_OF` / `TEAMMATE_OF`
edges for richer paths, persisted conversations, and code-splitting the 660 kB UI bundle.

---

## 15. Glossary

| Term | Meaning |
|---|---|
| **Knowledge graph** | Data stored as entities (nodes) and typed relationships (edges) instead of flat tables |
| **Node / edge** | An entity (Team, Player…) / a relationship between two entities (SCORED, PLAYED_IN…) |
| **Label** | A node's type (Team, Player, Match…) |
| **Degree** | How many edges a node has; used as a measure of importance |
| **Entity resolution** | Deciding which mentions refer to the same real thing, and giving it one ID |
| **Subgraph** | A selection of nodes plus the edges between them |
| **Force-directed layout** | Nodes repel each other and edges pull like springs, so connected things cluster |
| **Elo rating** | A strength rating updated after every match based on the result versus the expectation |
| **Win expectancy** | The probability the Elo model gives a team before the match |
| **Upset** | A win by a team whose pre-match win expectancy was under 25% |
| **Tool calling** | An LLM asks the program to run a named function with arguments, then reads the result |
| **SSE (Server-Sent Events)** | A simple way for a server to stream many small messages over one HTTP response |
| **DuckDB** | An embedded analytical SQL database stored in a single file |
