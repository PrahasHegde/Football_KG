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
pip install -r requirements.txt

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
├── README.md                 How to install and run
├── DEVELOPER_GUIDE.md        This file
├── CLAUDE.md                 Project rules/notes for Claude Code
├── requirements.txt          Python dependencies
├── .env.example              Template for GROQ_API_KEY and model settings
├── .gitignore
│
├── scripts/
│   └── get_data.py           git clone / pull of the source dataset into data/
│
├── src/                      STAGE 1 (the original knowledge-graph project)
│   ├── build_kg.py           CSVs -> NetworkX graph + Neo4j export
│   └── explore_kg.py         Command-line showcase queries on the graph
│
├── server/                   STAGES 2 + 3 (backend)
│   ├── __init__.py
│   ├── build_db.py           Graph -> DuckDB tables, Elo ratings
│   ├── store.py              All queries: search, profiles, expand, paths, H2H, discover, safe SQL
│   ├── agent.py              Groq LLM agent: system prompt, tools, streaming loop
│   └── main.py               FastAPI app: /api/* endpoints + static UI
│
├── web/                      Frontend (React + TypeScript + Vite)
│   ├── index.html            HTML shell, fonts, favicon
│   ├── vite.config.ts        Dev server on :5173, proxies /api to :8000
│   ├── tsconfig.json
│   ├── package.json
│   └── src/
│       ├── main.tsx          React entry point
│       ├── App.tsx           Layout + app state + all user actions
│       ├── api.ts            HTTP client + chat stream reader
│       ├── types.ts          Shared TypeScript types (mirror the API JSON)
│       ├── theme.ts          Colours, node shapes, sizes, canvas drawing helpers
│       ├── useGraph.ts       Canvas graph state (nodes/links kept stable)
│       ├── useTheme.ts       Light/dark mode (saved in localStorage)
│       ├── styles.css        All styling, theme tokens for light + dark
│       └── components/
│           ├── GraphCanvas.tsx    Force-directed canvas: drawing, physics, clicks
│           ├── Inspector.tsx      Entity details panel (per node type)
│           ├── H2HPanel.tsx       Head-to-head comparison panel
│           ├── DiscoverPanel.tsx  Insight cards (rankings, upsets, rivalries…)
│           ├── EraBar.tsx         Decade selector + play button
│           ├── ChatPanel.tsx      Agent chat: streaming, tool steps, entity links
│           ├── SearchBox.tsx      Debounced search with keyboard navigation
│           └── charts.tsx         LineChart, ResultBar, ProbBar (SVG)
│
├── data/                     (git-ignored) raw dataset; never edited by code
├── football_kg.gpickle       (git-ignored) Stage 1 output
├── neo4j_import/             (git-ignored) Stage 1 Neo4j export
└── app_data/                 (git-ignored) Stage 2 output: football.duckdb
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
```

Illustration: one World Cup goal by Messi links to the rest of the graph like this:

```
 Lionel Messi ──SCORED──► Goal ──IN_MATCH──► Match ──PART_OF──► FIFA World Cup
      │                    │                  │  ▲
  PLAYS_FOR            FOR_TEAM            HELD_IN └──PLAYED_IN── Argentina, the opponent
      ▼                    ▼                  ▼
  Argentina            Argentina           City ──LOCATED_IN──► Country
```

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
| `goals` | goal | `goal_id, match_id, date, tournament, team, opponent, player_id, scorer, minute, penalty, own_goal, assist_player_id, assister` |
| `players` | player | `player_id, name, team, goals` (excl. own goals), `penalty_goals, own_goals, assists, matches_scored_in, first_goal_date, last_goal_date` |
| `teams` | team | `team_id, name, matches, wins, draws, losses, goals_for, goals_against, first_match, last_match` |
| `tournaments` | tournament | `tournament_id, name, matches, first_match, last_match` |
| `former_names` | old name | `former_id, former_name, team, team_id, start_date, end_date` |
| `elo_history` | team × match | `team_id, date, match_id, elo` (rating after the match) |
| `elo_yearly` | team × year | `team_id, team, year, elo` (year-end), `rank` (world rank that year) |

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
thread pool. Expensive results that never change (`meta`, `communities`, `discover`, `schema_text`)
are cached with `@cached_property`.

| Method | What it does | How |
|---|---|---|
| `search(text, label)` | Find entities by name | All words must appear in `name_norm`; ranked exact → prefix/word-prefix → contains, then by `degree` |
| `entity(id)` | Full profile for the Inspector | Dispatches to `_detail_<label>()` (team record + Elo series, player goals, match goals + Elo expectancy, …) plus relationship counts |
| `expand(id, limit, type, direction)` | Neighbours of a node | One SQL with a window function: at most `limit` neighbours **per relationship group**, matches newest first, others by degree; reports totals that were cut (`truncated`) |
| `subgraph(ids)` | Nodes + all edges among them | Used for "add this to the canvas" |
| `subgraph_with_context(ids)` | `subgraph` plus anchors | Adds player→team, match→teams/tournament, goal→scorer/match; for 2–3 teams adds their latest meetings. Used for agent drawings |
| `communities` | Team "regions" | Louvain community detection on the all-time meeting graph (pairs that met 5+ times); top 7 communities named after their 3 most active teams; the rest → "Other" |
| `overview(top, min_meetings, start, end)` | Rivalry network for an era | Top 80 teams by matches in the era, linked when they met `min_meetings` times (default scales with the era length), Elo at era end for sizing |
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
| GET | `/api/overview?start=&end=&top=&min_meetings=` | Era rivalry network + community names |
| GET | `/api/h2h?a=&b=` | Head-to-head data + its graph |
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
│   ├── EraBar             decades + play (overview only)
│   ├── Inspector          OR  H2HPanel   (floating panel)
│   └── toast              short notices
└── side (right)  tabs:  ChatPanel  |  DiscoverPanel
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
2. **Colour** by node type, or by community (overview) or result (head-to-head) when `node.group`
   is set.
3. **Size** by `log(degree)`, or by Elo in the overview (`nodeRadius`).
4. **Surface-coloured ring** around each node so overlapping nodes stay distinguishable.
5. **Team code** (BRA, ARG) inside large team circles.
6. **Label pill** under the node when zoomed in enough, or when selected, hovered or on a path.
   Match and goal labels only appear when zoomed in close, to avoid clutter.
7. **Dimming**: hovering a node dims everything except it and its neighbours; a highlighted path
   dims the rest, and its edges carry moving particles.

Physics (`d3Force('charge')`, `d3Force('link')`) is tuned differently for the overview (spread
clusters, edge strength by number of meetings) and for exploration (compact). After a layout settles,
the view auto-fits, with a zoom cap so tiny graphs don't zoom absurdly far.

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
- Community colours are assigned once over all time (`Store.communities`), so Brazil keeps the same
  colour in every decade.

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

## 10. What happens when… (end-to-end walkthroughs)

### …you open the app

1. `App` mounts → `api.meta()` (header counts) and `loadOverview(ALL_TIME)`.
2. `GET /api/overview` → `Store.overview()` returns 80 teams + "met 20+ times" edges, community IDs
   and Elo values.
3. `replace()` puts them on the canvas; physics spreads the clusters; the view fits to the screen.

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
| Type-check the UI | `cd web && npm run typecheck` |
| Production UI build | `cd web && npm run build` |
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

---

## 14. Known limitations and roadmap

**Limitations**

- Scorer data covers competitive matches only (no friendlies) and only part of them; assist data is
  sparse.
- Own-goal scorers are attached to the wrong national team in the graph (see §5).
- Conversations are kept in memory only and vanish on restart.
- The agent cannot open the head-to-head panel by itself yet.
- The free Groq tier limits how fast multi-step questions can be answered.

**Roadmap** (from `CLAUDE.md`)

1. ✅ Ingestion, IDs, NetworkX graph, Neo4j export
2. ⬜ Load into Neo4j and verify counts
3. ◐ Derived knowledge: ✅ Elo + upsets · ⬜ `RIVAL_OF` edges (50+ meetings), `TEAMMATE_OF`
4. ⬜ Enrich players/teams from Wikidata (photos, birth dates, positions)
5. ◐ Graph analytics: ✅ community detection · ⬜ link prediction
6. ✅ Interactive web explorer + agent

Further ideas: let the agent open head-to-head views, add a team "form guide" (last 10 results),
persist conversations, and add shareable links to a view.

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
| **Louvain communities** | An algorithm that finds groups of nodes more connected to each other than to the rest |
| **Elo rating** | A strength rating updated after every match based on the result versus the expectation |
| **Win expectancy** | The probability the Elo model gives a team before the match |
| **Upset** | A win by a team whose pre-match win expectancy was under 25% |
| **Tool calling** | An LLM asks the program to run a named function with arguments, then reads the result |
| **SSE (Server-Sent Events)** | A simple way for a server to stream many small messages over one HTTP response |
| **DuckDB** | An embedded analytical SQL database stored in a single file |
