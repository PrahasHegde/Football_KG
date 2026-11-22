# Football Knowledge Graph

<p align="center">
  <img src="https://img.shields.io/badge/Python-50.2%25-blue" alt="Python">
  <img src="https://img.shields.io/badge/TypeScript-39.4%25-3178c6" alt="TypeScript">
  <img src="https://img.shields.io/badge/FastAPI-009688" alt="FastAPI">
  <img src="https://img.shields.io/badge/React-19-61dafb" alt="React">
  <img src="https://img.shields.io/badge/DuckDB-FFF000" alt="DuckDB">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License">
</p>

An interactive knowledge graph of international men’s football from **1872 to the present**, containing roughly **50,000 matches, 48,000 goals, and 16,000 players**.

Explore rivalries, trace connections between players and clubs, replay football history by decade, compare teams and players, calculate Elo ratings, and ask an AI agent questions in natural language.

Built from [martj42/international_results](https://github.com/martj42/international_results).

## Features

### The Football Universe

View the 200 most active national teams, grouped by confederation and positioned according to geographic homes.

- Node size represents Elo rating
- Link thickness represents meeting frequency
- Confederations form distinct colour-coded clusters
- Replay the universe decade by decade
- Hover to highlight rivals
- Double-click entities to expand their relationships

### Club Universe

Explore clubs from the world’s major leagues and the winningest teams from other competitions.

- Win rate determines node size
- Shared players create links
- Club crests are loaded from Wikidata and Wikipedia
- Compare clubs across leagues and countries

### Talent Pipeline

See where international players develop their club careers:

- Nations form an outer ring
- Clubs form an inner ring
- Links show how many national-team players appeared for a club
- Ring positions are organised by confederation and league

### Player and Team Analysis

- Team records and top opponents
- Elo history and head-to-head records
- Player goals, caps, club careers, and assists
- Shared clubs and national-team team-mates
- Players who scored together
- Club appearance, goal, and assist totals
- Biggest wins, latest meetings, and tournament histories

### Ask the Graph

A Groq-powered tool-calling agent can search entities, inspect profiles, execute read-only SQL, find paths, and place relevant entities onto the graph.

Example questions:

- “How has Brazil fared against Argentina?”
- “Who is the most successful team at the FIFA World Cup?”
- “Find the shortest connection between Pelé and Lionel Messi.”
- “Which team has the biggest Elo peak?”
- “Show me Lionel Messi’s club career.”

## Tech Stack

| Layer | Technology |
|---|---|
| Knowledge graph | Python, pandas, NetworkX |
| Analytical database | DuckDB |
| Backend API | FastAPI, Uvicorn, Pydantic |
| AI assistant | Groq tool-calling API |
| Frontend | React 19, TypeScript, Vite |
| Graph rendering | `react-force-graph-2d`, d3-force |
| Media | Wikidata, Wikipedia, Wikimedia Commons |
| Testing | pytest, Vitest |

## Project Architecture

```text
martj42/international_results
              │
              ▼
      scripts/get_data.py
              │
              ▼
        src/build_kg.py
              │
     ┌────────┴────────┐
     ▼                 ▼
NetworkX graph   Neo4j CSV export
football_kg.gpickle
     │
     ▼
server/build_db.py
     │
     ▼
app_data/football.duckdb
     │
     ├── server/store.py      Queries and analytical access
     ├── server/agent.py      Groq tool-calling agent
     ├── server/media.py      Wikimedia/Wikidata enrichment
     └── server/main.py       FastAPI and static frontend
                                      │
                                      ▼
                           React graph application
```

The NetworkX graph is the source of the Neo4j and DuckDB representations. DuckDB provides fast analytical queries, while the graph tables remain available for graph exploration and path finding.

## Quick Start

### 1. Create a Python environment

```bash
python -m venv .venv
source .venv/bin/activate
# Windows:
# .venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
cd web && npm install && cd ..
```

For development and testing:

```bash
pip install -r requirements-dev.txt
```

### 3. Download the football data

```bash
python scripts/get_data.py
```

This clones the source dataset into `data/international_results/`.

### 4. Build the knowledge graph

```bash
python src/build_kg.py
python -m server.build_db
```

The graph build creates:

- `football_kg.gpickle`
- `neo4j_import/`
- `app_data/football.duckdb`

The server also rebuilds the DuckDB database automatically when the graph is newer.

### 5. Configure the AI agent

Create the environment file:

```bash
cp .env.example .env
```

Add a free Groq API key:

```dotenv
GROQ_API_KEY=your_key_here
GROQ_MODEL=openai/gpt-oss-120b
GROQ_REASONING_EFFORT=medium
```

Get a key from [Groq Console](https://console.groq.com/keys).

`GROQ_API_KEY` is optional for the rest of the app, but required for “Ask the Graph”.

An optional Wikimedia contact value can reduce media rate limiting:

```dotenv
WIKIMEDIA_CONTACT=you@example.com
```

### 6. Build and run the app

```bash
cd web
npm run build
cd ..

uvicorn server.main:app --port 8000
```

Open:

```text
http://localhost:8000
```

## Development

### Run the API with hot reload

```bash
uvicorn server.main:app --reload --port 8000
```

### Run the frontend development server

```bash
cd web
npm run dev
```

Open:

```text
http://localhost:5173
```

Vite proxies `/api` requests to the FastAPI server on port 8000.

### Run type checking

```bash
cd web
npm run typecheck
```

### Run all checks

```bash
python scripts/check.py
```

This runs:

- Backend pytest suite
- Frontend TypeScript checks
- Vitest unit tests
- Production frontend build

Use `--rebuild` to rebuild the graph and database before testing:

```bash
python scripts/check.py --rebuild
```

Stop the server before rebuilding on Windows because DuckDB may lock the database file.

## Optional Data Enrichment

### Wikipedia Goals

Fills missing goal lists for prolific international players:

```bash
python scripts/fetch_wikipedia_goals.py
```

This process makes serial, throttled Wikimedia requests.

### StatsBomb Assists

Downloads assists for Euro 2020, Euro 2024, Copa América 2024, and AFCON 2023:

```bash
python scripts/fetch_statsbomb_assists.py
```

### Club Data

Downloads approximately 200 MB of club performance data:

```bash
python scripts/fetch_club_data.py
```

The resulting data covers roughly 800 clubs and club seasons beginning in 2012/13.

## Elo Ratings

Every match is replayed in chronological order.

- Every team starts at **1500 Elo**
- Home advantage is **100 Elo points**
- K-factors vary by competition:
  - World Cup: 60
  - Continental finals: 50
  - Qualifiers and Nations League: 40
  - Other tournaments: 30
  - Friendlies: 20
- Goal difference adjusts rating movement
- A result is marked as an upset when the winner’s pre-match expectancy is below 25%

The app exposes pre-match expectations, post-match Elo history, year-end rankings, historical peaks, and upset lists.

## Data Caveats

The underlying source dataset is excellent but not complete.

- `goalscorers.csv` does not contain goals from friendlies
- Scorer rankings use only goals recorded in the dataset
- Own goals are excluded from player scoring totals
- Assist data is not available as a complete career statistic
- Team names generally use current names
- Historical names are available through `former_names`
- Wikipedia enrichment only fills selected prolific player lists
- Club data begins in the 2012/13 season

These limitations are respected by the agent and test suite rather than hidden or silently estimated.

## Neo4j Export

Every build also produces Neo4j CSV files and a Cypher loading script:

```text
neo4j_import/
├── nodes_*.csv
├── rels_*.csv
└── load.cypher
```

Neo4j is optional; the web app runs entirely with NetworkX and DuckDB.

## Repository Guide

```text
src/            Knowledge graph construction
server/         FastAPI app, DuckDB store, AI agent, and media
web/            React and TypeScript frontend
scripts/        Data download and verification utilities
tests/          Graph, database, API, store, agent, and script tests
data_extra/     Optional enriched goal, assist, club, and profile data
DEVELOPER_GUIDE.md
                Detailed architecture and extension documentation
CLAUDE.md       Schema, conventions, caveats, and roadmap
```

## API

While the app is running, explore the API at:

```text
http://localhost:8000/docs
```

Useful endpoints include:

| Endpoint | Description |
|---|---|
| `/api/meta` | Graph totals and date range |
| `/api/search?q=messi` | Search teams, players, clubs, and tournaments |
| `/api/entity/{id}` | Inspect an entity |
| `/api/overview` | National-team universe |
| `/api/clubs/universe` | Club universe |
| `/api/clubs/pipeline` | National-team-to-club pipeline |
| `/api/h2h?a=T:brazil&b=T:argentina` | Team head-to-head |
| `/api/compare/players?a=&b=` | Player comparison |
| `/api/discover` | Rankings and discoveries |
| `/api/path` | Shortest graph connection |
| `/api/chat` | Streaming AI agent |

## Offline Mode

Disable all Wikimedia and Groq network calls:

```bash
FKG_OFFLINE=1
```

Tests and CI run in offline mode:

```bash
python scripts/check.py
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Run `python scripts/check.py`
5. Open a pull request

The project’s detailed extension guidelines are available in [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md).

## Documentation

- [Developer Guide](DEVELOPER_GUIDE.md)
- [Schema and Project Notes](CLAUDE.md)
- [International Results Source](https://github.com/martj42/international_results)
- [StatsBomb Open Data](https://github.com/statsbomb/open-data)
- [transfermarkt-datasets](https://github.com/dcaribou/transfermarkt-datasets)

## License

Licensed under the [MIT License](LICENSE).

Please review the licenses of the source datasets and enriched third-party data before redistributing derived data or media.
