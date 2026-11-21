# Football Knowledge Graph

Knowledge graph of international men's football results (1872–present), built from
https://github.com/martj42/international_results.

## Commands
- Get data: `python scripts/get_data.py` (clones the dataset into `data/international_results/`)
- Build graph: `python src/build_kg.py` → writes `football_kg.gpickle` and `neo4j_import/`
- Explore: `python src/explore_kg.py` → runs the showcase queries
- Install deps: `pip install -r requirements.txt`; UI deps: `cd web && npm install`
- App DB: `python -m server.build_db` → `app_data/football.duckdb` (auto-built on server start if stale)
- Run app: `cd web && npm run build`, then `uvicorn server.main:app --port 8000` → http://localhost:8000
- UI dev: `uvicorn server.main:app --reload` + `cd web && npm run dev` (:5173 proxies /api to :8000)
- Typecheck UI: `cd web && npm run typecheck`

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
  `former_names`, `elo_history`, `elo_yearly` (see `server/build_db.py`). Winners are derived from scores, not `WON_BY`.
- Elo (`add_elo` in build_db.py): start 1500, home advantage 100, K = 60 World Cup / 50 continental finals /
  40 qualifiers + Nations League / 30 other / 20 friendlies, goal-difference multiplier. `matches` gets
  `home_elo`, `away_elo` (pre-match), `home_win_exp`, `winner_win_exp`, `upset` (winner expectancy < 25%).
- UI colours: validated 8-slot categorical palette in `web/src/theme.ts` (light + dark steps); node types also
  differ by shape. Overview communities are computed once over all time (`Store.communities`) so colours
  don't shift between eras. Charts read `--series-1/2`, `--neutral` CSS tokens.
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
```

## Node ID conventions
- Team `T:<slug>`, Tournament `TR:<slug>`, Country `C:<slug>`, City `CI:<city>@<country>`
- Match `M<row index in results.csv>`, Goal `G<n>`, FormerName `FN:<slug>`
- Player `P:<name>@<team>`: names alone are NOT unique, so the national team is part of the ID
- slug = lowercase, spaces → underscores

## Known data facts (verified)
- results.csv has ~50k matches; one duplicate key (1974-02-17 Tahiti v New Caledonia), so goals link to the first one.
- goalscorers.csv contains NO friendlies and covers only ~15.5k of the ~46k matches with goals.
  Scorer totals are "competitive matches with recorded scorers", not official career totals.
- `assist` is filled for only ~2k goals.
- Former team names never appear in results.csv (results use current names).
- In goalscorers.csv `team` is the team credited with the goal, so for own goals the scorer actually
  played for the opponent; build_kg.py therefore attaches own-goal scorers to the wrong national team.

## Conventions
- Python 3.10+, pandas + networkx. Keep the build reproducible from the raw CSVs alone.
- Never edit files in `data/`; derive everything in code.
- Neo4j load script requires Neo4j 5.23+ (`CALL (row) {...} IN TRANSACTIONS`).

## Roadmap
1. [done] Ingestion, IDs, NetworkX graph, Neo4j export
2. Load into Neo4j and verify counts match the build output
3. Derived knowledge: [done] Elo ratings over time + upset flags; todo RIVAL_OF edges, TEAMMATE_OF
4. Enrich players/teams from Wikidata
5. Graph analytics: PageRank, community detection, link prediction
6. [done] Interactive web explorer + agent (NL → SQL/graph tools over DuckDB, Groq LLM)
