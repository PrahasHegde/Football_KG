# Football Knowledge Graph

A knowledge graph of 150 years of international football (50k matches, 48k goals, 16k players),
built from [martj42/international_results](https://github.com/martj42/international_results),
with a web app to explore it visually and an AI agent that answers questions from the data.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/get_data.py          # download the dataset
python src/build_kg.py              # build the graph -> football_kg.gpickle
cp .env.example .env                # then put your Groq API key in .env
```

## Run the app

```bash
cd web && npm install && npm run build && cd ..
uvicorn server.main:app --port 8000      # open http://localhost:8000
```

For frontend development with hot reload, run the API as above (add `--reload`) and in another terminal
`cd web && npm run dev`, then open http://localhost:5173 (API calls are proxied to :8000).

On first start the server builds `app_data/football.duckdb` from the graph (about 7 s). It rebuilds
automatically whenever `football_kg.gpickle` is newer. To force a rebuild, run `python -m server.build_db`.

## What's in the app

- **Graph canvas** (light theme by default, dark toggle top-right): opens on the *football universe*,
  the 200 most active nations laid out like a world map. Each confederation forms a glowing cluster,
  and the rivalries they played most are drawn as gradient arcs between them. Size = Elo rating; hover a
  nation to light up its rivals, click a confederation in the legend to spotlight it. Node types also have their own shapes (team ●, player ◉, match ▢, goal ◆,
  tournament ⬢, country ■, city ▲). Search (Ctrl K), click for details, double-click to expand,
  drag to pin, download the canvas as PNG.
- **Era time-travel**: replay the universe decade by decade (▶ plays 1900s → 2020s and morphs
  between them).
- **Elo ratings** computed from every match since 1872: rating-history chart per team, pre-match win
  expectancy on every match, and upset detection.
- **Head to head**: Compare any two teams. Shows the W/D/L split, both Elo curves, results by decade,
  biggest wins and latest meetings. The canvas colours each match by who won.
- **Discover**: current world Elo ranking, biggest upsets, greatest rivalries, highest peaks, and the
  teams with the most different opponents.
- **Inspector**: per-entity profiles, relationship-by-type expansion, and **Connect** for shortest
  paths (animated along the path).
- **Three home views** (switcher at the top of the canvas): **Nations** (the universe above), **Clubs**
  (192 clubs clustered by league like a map of Europe, linked by the players they shared, sized by win rate,
  with club crests) and **Pipeline** (nations on an outer ring, clubs on an inner ring: where each country's
  internationals play). Hover anything to light up its connections; click a legend chip to spotlight a group.
- **Player vs player**: compare any two players side by side (goals, caps, assists, club numbers, goals
  over time) and see how they connect: shared clubs, team-mates in common, matches where both scored.
- **Team-mates**: every player panel lists their longest club team-mates and the countrymen they scored
  alongside.
- **Guess the player** (Play tab): clues from the graph are revealed one at a time; three levels, points,
  streaks.
- **Shareable links**: the URL follows the view (`#/clubs`, `#/e/P:lionel_messi@argentina`,
  `#/vs/<player>/<player>`), and the browser back button works.
- **Club stats** for ~800 clubs in the top leagues since 2012/13 (transfermarkt-datasets, CC0):
  club nodes in the graph, a club panel (stadium, coach, win rate by season, top scorers, assisters and
  appearances) and a club table on player panels (apps, goals, assists per club). International players
  also get caps and position.
- **Photos, flags & clubs** from Wikipedia/Wikidata: flags inside team nodes, player photos on the
  canvas, and in the Inspector a player's bio and club career with club logos, or a team's flag banner,
  federation crest and summary. Cached locally after the first load. Set `WIKIMEDIA_CONTACT` in `.env`
  (an email or URL) to load photos ~20x faster under Wikimedia's rate limits.
- **Ask the graph**: an agent (Groq, `openai/gpt-oss-120b` by default) that resolves entities, writes
  read-only SQL (including the Elo tables), finds paths and draws its answers on the canvas.

## Architecture

```
src/build_kg.py      raw CSVs -> NetworkX graph (football_kg.gpickle) + Neo4j export
server/build_db.py   graph -> DuckDB (graph tables + tidy analytical tables)
server/store.py      read-only queries: search, entity profiles, expansion, paths, guarded SQL
server/agent.py      Groq tool-calling loop, streamed to the browser as server-sent events
server/main.py       FastAPI: /api/* and the built UI
web/                 React + TypeScript + Vite, react-force-graph-2d canvas
```

For a full explanation of how everything is built and works, read
[DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md). Explore from the command line with `python src/explore_kg.py`. See `CLAUDE.md` for the schema,
ID conventions, data caveats and roadmap.
