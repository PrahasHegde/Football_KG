# Football Knowledge Graph

Knowledge graph of international men's football results (1872–present), built from
https://github.com/martj42/international_results.

## Commands
- Get data: `python scripts/get_data.py` (clones the dataset into `data/international_results/`)
- Wikipedia goals (optional, slow, stop the server first): `python scripts/fetch_wikipedia_goals.py`
- StatsBomb assists (optional, slow ~320 downloads): `python scripts/fetch_statsbomb_assists.py`
- Club data (optional, downloads ~200 MB once): `python scripts/fetch_club_data.py`
- Build graph: `python src/build_kg.py` → writes `football_kg.gpickle` and `neo4j_import/`
- Explore: `python src/explore_kg.py` → runs the showcase queries
- Install deps: `pip install -r requirements.txt`; UI deps: `cd web && npm install`
- App DB: `python -m server.build_db` → `app_data/football.duckdb` (auto-built on server start if stale)
- Run app: `cd web && npm run build`, then `uvicorn server.main:app --port 8000` → http://localhost:8000
- UI dev: `uvicorn server.main:app --reload` + `cd web && npm run dev` (:5173 proxies /api to :8000)
- Typecheck UI: `cd web && npm run typecheck`
- **All checks** (run before finishing any change): `python scripts/check.py` (`--rebuild` rebuilds graph + DB
  first). = `python -m pytest` (backend, offline, needs the built DB) + `cd web && npm run check` (tsc + Vitest +
  build). Dev deps: `pip install -r requirements-dev.txt`. CI: `.github/workflows/ci.yml`.
- When adding a feature, add tests (see DEVELOPER_GUIDE §11½). New edge types/labels must be added to
  `SCHEMA`/`ID_PATTERN` in tests/test_graph.py; source-data faults go in the known lists in tests/test_db.py.
- `FKG_OFFLINE=1` disables every Wikimedia call (tests/CI set it).

## Web app
- `server/` FastAPI app. `store.py` is the only data access layer (read-only DuckDB, external file access
  disabled); `agent.py` is a Groq tool-calling loop streamed as SSE (event types documented at the top).
- LLM provider is **Groq** (free tier), not Claude: key `GROQ_API_KEY` in `.env`, model via `GROQ_MODEL`
  (default `openai/gpt-oss-120b`). Free-tier tokens-per-minute limits are tight, so keep tool results
  compact (`SQL_MAX_ROWS`, `MAX_RESULT_CHARS`) and system prompt lean.
- Media (`server/media.py`): flags/crests/photos/club careers from Wikidata + Wikipedia, cached in
  `app_data/media.sqlite` + `app_data/img/`, served via the `/api/img` proxy (Wikimedia hosts only).
  Wikimedia API calls MUST stay serial and throttled (10 req/min without `WIKIMEDIA_CONTACT`, 200/min with);
  never parallelise them. Never put the user's email in the User-Agent unless they set it themselves.
- The agent links entities as `[Name](<entity:ID>)`; the UI makes these clickable. Answers that link
  entities are drawn on the canvas automatically if the model didn't call `show_in_graph`.
- DuckDB tables: graph `nodes`/`edges` plus tidy `matches`, `goals`, `players`, `teams`, `tournaments`,
  `former_names`, `elo_history`, `elo_yearly`, `clubs`, `club_careers`, `club_seasons` (see `server/build_db.py`). Winners are derived from scores, not `WON_BY`.
- Elo (`add_elo` in build_db.py): start 1500, home advantage 100, K = 60 World Cup / 50 continental finals /
  40 qualifiers + Nations League / 30 other / 20 friendlies, goal-difference multiplier. `matches` gets
  `home_elo`, `away_elo` (pre-match), `home_win_exp`, `winner_win_exp`, `upset` (winner expectancy < 25%).
- UI colours: validated 8-slot categorical palette in `web/src/theme.ts` (light + dark steps); node types also
  differ by shape. Charts read `--series-1/2`, `--neutral` CSS tokens.
- Overview ("football universe", `Store.overview` + `GraphCanvas`): top 200 teams (140 per decade), edges =
  pairs that met often + each team's 3 most frequent opponents (`backbone`). Grouped/coloured by
  `teams.confederation` (derived in `add_confederations`: confederation of the team's latest continental
  competition; CONMEBOL listed explicitly; else 'Non-FIFA') in fixed slot order. Layout = geographic homes
  (`CONF_HOME` in theme.ts) via custom `home`/`collide` d3 forces; nebulae + titles in `onRenderFramePre`,
  gradient arcs via `linkCanvasObject`, labels de-cluttered in `onRenderFramePost`.
- Home switcher (App.tsx `HOMES`): **Nations** (above), **Clubs** (`Store.club_universe`, `/api/clubs/universe`:
  big-7-league clubs with 4+ seasons + 24 winningest elsewhere, grouped by league via `conf` + `CONF_HOME` league
  homes, size = win rate via `strength`, edges = players shared with 5+ apps at both) and **Pipeline**
  (`Store.pipeline`, `/api/clubs/pipeline`: 40 nations on an outer ring by confederation, 64 clubs on an inner
  ring, fixed ring positions sent as `hx/hy`, edge = internationals of that nation who played for that club).
  Club logos (`Media.warm_clubs`, 789/796): Wikidata by Transfermarkt ID (P7223) -> free Commons logo (P154);
  else the club's enwiki article page image (`pilicense=any`: most crests are NON-FREE fair-use files on
  en.wikipedia) kept only if crest-like (`CREST_FILE`); else a Wikipedia search whose title must contain the
  club's first distinctive word. Re-run by deleting `__clubs_v2__` from app_data/media.sqlite.
- Player features: `Store.club_teammates` (overlapping club spells, 2012+), `Store.scored_with` (scored in the
  same match for the same national team), `Store.compare_players` (`/api/compare/players`, `ComparePanel.tsx`),
  "Guess the player" game (`Store.quiz_new`/`quiz_guess`, `/api/quiz/*`, `QuizPanel.tsx`, Play tab; answer kept
  server-side by token, stats in localStorage `fkg.quiz`).
- Deep links (App.tsx `applyRoute`/`route`): `#/nations|clubs|pipeline`, `#/e/<id>`, `#/h2h/<a>/<b>`,
  `#/vs/<a>/<b>`; pushState on change, popstate re-applies (browser back works).
- `web/` React + TS + Vite, canvas via `react-force-graph-2d`. Graph state lives in `useGraph.ts`;
  node objects are kept stable so the simulation keeps positions. Keep hook callbacks off the
  per-render `useGraph()` object (doing so caused an infinite fetch loop once).

## Schema (keep this in sync when you change build_kg.py)
```
(Player)-[:SCORED]->(Goal)-[:IN_MATCH]->(Match)-[:PART_OF]->(Tournament)
(Player)-[:ASSISTED]->(Goal)-[:FOR_TEAM]->(Team)
(Player)-[:PLAYS_FOR]->(Team)-[:PLAYED_IN {side, goals}]->(Match)
(Match)-[:WON_BY]->(Team)          (Team)-[:WON_SHOOTOUT]->(Match)
(Match)-[:HELD_IN]->(City)-[:LOCATED_IN]->(Country)<-[:REPRESENTS]-(Team)
(Team)-[:FORMERLY_KNOWN_AS {start, end}]->(FormerName)
(Player)-[:PLAYED_FOR_CLUB {apps, goals, assists, start, end}]->(Club)-[:BASED_IN]->(Country)
```

## Node ID conventions
- Team `T:<slug>`, Tournament `TR:<slug>`, Country `C:<slug>`, City `CI:<city>@<country>`
- Match `M<row index in results.csv>`, Goal `G<n>`, FormerName `FN:<slug>`, Club `CL:<slug of club name>`
- Player `P:<name>@<team>`: names alone are NOT unique, so the national team is part of the ID
- slug = lowercase, spaces → underscores

## Known data facts (verified)
- results.csv has ~50k matches; one duplicate key (1974-02-17 Tahiti v New Caledonia), so goals link to the first one.
- results.csv DOES include friendlies (18,283 matches), but goalscorers.csv contains NO friendly scorers
  and covers only ~15.5k of the ~46k matches with goals; many regional competitions have no scorers at all
  (e.g. India: none for SAFF Cup, Nehru Cup, Intercontinental Cup, Asian Cup qualifiers).
- Gap-filling goals for ~95 prolific players come from Wikipedia goal lists via
  `python scripts/fetch_wikipedia_goals.py` -> `data_extra/wikipedia_goals.csv` (committed; optional build
  input). Per player and match it adds only the goals the dataset doesn't already credit to that player
  (skipped if the dataset covers the match under an unmatched spelling), and the team's dataset + imported
  goals may not exceed its score. Parsing: the page's largest goal table, plus extra tables only if they are
  per-team lists (a "team" column, no goals column) - hat-trick, youth and summary tables repeat goals; rows in
  youth sections / vs U15-U23 opponents are dropped; header rows parsed as data are promoted. Exact team names
  beat aliases: results.csv has Yugoslavia and Czechoslovakia as their own teams. Dates may differ by ±1 day. These goals have `source = 'wikipedia'`
  (dataset: `'dataset'`), no minute and unknown penalty flag. `players.goals` = dataset only (use for
  rankings), `wiki_goals`, `total_goals` (= one player's tally), `friendly_goals` (goals in friendlies).
  Never rank all players by total_goals/friendly_goals (only stars have Wikipedia goals). Checked: 52/95
  players match their Wikipedia list exactly, 72 within 2; over by 1: Villa (dataset goal not on his list) and
  Aubameyang (the dataset credits his father Pierre's 1992 goal to him). Big shortfalls (Hussein Saeed,
  Kamamoto, Faras, Dahari) = Olympic qualifiers / old Merdeka games absent from results.csv.
- goalscorers.csv `assist` exists ONLY for FIFA World Cup matches (~2.1k goals). StatsBomb Open Data adds
  Euro 2020/2024, Copa America 2024, AFCON 2023 via `scripts/fetch_statsbomb_assists.py` ->
  `data_extra/statsbomb_assists.csv` (fills empty assists only; WC 2018/2022 used to validate the matching).
  `goals.assist_source` = 'dataset' | 'statsbomb'. No free, licensed source has career international assists
  (Opta is commercial; FBref lost Opta in Jan 2026 and, like Transfermarkt, forbids scraping). StatsBomb's
  terms require crediting "StatsBomb" when sharing results.
- Club data = transfermarkt-datasets (github.com/dcaribou/transfermarkt-datasets, CC0; collected from
  Transfermarkt by its author). ~800 clubs in top leagues + cups + European competitions, ONLY 2012/13 ->
  July 2026 (updates paused upstream). `scripts/fetch_club_data.py` downloads the DuckDB file to
  app_data/transfermarkt/ and writes committed CSVs: data_extra/clubs.csv, club_player_stats.csv (all ~55k
  player-club pairs, `kg_player_id` when linked), club_seasons.csv, player_profiles.csv (caps, position, DOB).
  Linking to graph players: same name (order/hyphen-insensitive) + national team (nationality or current
  national team), else unique surname + first initial; ALWAYS with an era check (median international goal
  year at age 16-40 from the DOB) - namesakes from other eras are common (Bebeto, Ali Karimi). ~3.9k linked.
  Namesake tie-break (several TM players -> one graph player): exact name > surname match, then highest peak
  market value, then age fit, then lower id (caps are mostly empty in the snapshot).
  Club nodes for all clubs; PLAYED_FOR_CLUB edges only for linked players. Transfermarkt's
  `international_goals` is stale for some players (Chhetri 89 vs 95): not used.
- Former team names never appear in results.csv (results use current names).
- In goalscorers.csv `team` is the team credited with the goal, so for own goals the scorer actually
  played for the opponent; build_kg.py therefore attaches own-goal scorers to the wrong national team.

## Conventions
- Python 3.10+, pandas + networkx. Keep the build reproducible from the raw CSVs alone.
- Never edit files in `data/`; derive everything in code.
- Import scripts must stay deterministic (sorted output, fixed tie-breaks): re-running gives identical CSVs.
- Neo4j load script requires Neo4j 5.23+ (`CALL (row) {...} IN TRANSACTIONS`).

## Roadmap
1. [done] Ingestion, IDs, NetworkX graph, Neo4j export
2. Load into Neo4j and verify counts match the build output
3. Derived knowledge: [done] Elo ratings over time + upset flags; todo RIVAL_OF edges, TEAMMATE_OF
4. Enrich players/teams from Wikidata
5. Graph analytics: PageRank, community detection, link prediction
6. [done] Interactive web explorer + agent (NL → SQL/graph tools over DuckDB, Groq LLM)
