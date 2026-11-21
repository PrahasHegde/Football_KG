"""
Football Knowledge Graph builder
Source: github.com/martj42/international_results
Outputs:
  1. football_kg.gpickle      -> NetworkX MultiDiGraph (explore in Python)
  2. neo4j_import/*.csv       -> node & relationship files for Neo4j
  3. neo4j_import/load.cypher -> script that loads them
"""
import sys, pickle, pandas as pd, networkx as nx
from pathlib import Path

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else "data/international_results")
OUT = Path("neo4j_import"); OUT.mkdir(exist_ok=True)

results = pd.read_csv(SRC / "results.csv")
goals   = pd.read_csv(SRC / "goalscorers.csv")
shoots  = pd.read_csv(SRC / "shootouts.csv")
former  = pd.read_csv(SRC / "former_names.csv")

# ---------- 1. Entity resolution: stable IDs ----------
slug = lambda s: str(s).strip().lower().replace(" ", "_")
results = results.reset_index().rename(columns={"index": "row"})
results["match_id"] = "M" + results["row"].astype(str)
key = ["date", "home_team", "away_team"]
# one duplicate key exists in the source -> keep first for goal linking
match_lookup = results.drop_duplicates(key).set_index(key)["match_id"]

goals = goals.dropna(subset=["scorer"]).copy()
goals["match_id"] = [match_lookup[tuple(k)] for k in goals[key].values]
goals["goal_id"] = "G" + goals.reset_index().index.astype(str)
# A player is identified by name + national team (names alone collide)
pid = lambda name, team: f"P:{slug(name)}@{slug(team)}"
goals["player_id"] = [pid(n, t) for n, t in zip(goals.scorer, goals.team)]

# ---------- 2. Build the graph ----------
G = nx.MultiDiGraph()
def node(i, label, **p): G.add_node(i, label=label, **p)

teams = set(results.home_team) | set(results.away_team)
for t in teams:            node(f"T:{slug(t)}", "Team", name=t)
for tr in results.tournament.unique(): node(f"TR:{slug(tr)}", "Tournament", name=tr)
for c in results.country.unique():     node(f"C:{slug(c)}", "Country", name=c)
for (city, c) in results[["city", "country"]].drop_duplicates().values:
    cid = f"CI:{slug(city)}@{slug(c)}"
    node(cid, "City", name=city)
    G.add_edge(cid, f"C:{slug(c)}", type="LOCATED_IN")
for c in set(results.country) & teams:      # host country <-> national team
    G.add_edge(f"T:{slug(c)}", f"C:{slug(c)}", type="REPRESENTS")

for r in results.itertuples():
    m, h, a = r.match_id, f"T:{slug(r.home_team)}", f"T:{slug(r.away_team)}"
    node(m, "Match", date=r.date, home_score=r.home_score, away_score=r.away_score,
         neutral=bool(r.neutral), name=f"{r.home_team} {r.home_score}-{r.away_score} {r.away_team} ({r.date})")
    G.add_edge(h, m, type="PLAYED_IN", side="home", goals=r.home_score)
    G.add_edge(a, m, type="PLAYED_IN", side="away", goals=r.away_score)
    G.add_edge(m, f"TR:{slug(r.tournament)}", type="PART_OF")
    G.add_edge(m, f"CI:{slug(r.city)}@{slug(r.country)}", type="HELD_IN")
    if r.home_score != r.away_score:
        G.add_edge(m, h if r.home_score > r.away_score else a, type="WON_BY")

for s in shoots.itertuples():
    m = match_lookup.get((s.date, s.home_team, s.away_team))
    if m: G.add_edge(f"T:{slug(s.winner)}", m, type="WON_SHOOTOUT")

for g in goals.itertuples():
    team = f"T:{slug(g.team)}"
    if g.player_id not in G:
        node(g.player_id, "Player", name=g.scorer)
        G.add_edge(g.player_id, team, type="PLAYS_FOR")
    minute = None if pd.isna(g.minute) else int(g.minute)
    node(g.goal_id, "Goal", minute=minute, penalty=bool(g.penalty), own_goal=bool(g.own_goal))
    G.add_edge(g.player_id, g.goal_id, type="SCORED")
    G.add_edge(g.goal_id, g.match_id, type="IN_MATCH")
    G.add_edge(g.goal_id, team, type="FOR_TEAM")
    if isinstance(g.assist, str):
        a = pid(g.assist, g.team)
        if a not in G:
            node(a, "Player", name=g.assist); G.add_edge(a, team, type="PLAYS_FOR")
        G.add_edge(a, g.goal_id, type="ASSISTED")

for f in former.itertuples():               # historical names as aliases
    fid = f"FN:{slug(f.former)}"
    node(fid, "FormerName", name=f.former)
    G.add_edge(f"T:{slug(f.current)}", fid, type="FORMERLY_KNOWN_AS",
               start=f.start_date, end=f.end_date)

with open("football_kg.gpickle", "wb") as fh: pickle.dump(G, fh)

# ---------- 3. Export for Neo4j ----------
nodes = pd.DataFrame([{"id": n, **d} for n, d in G.nodes(data=True)])
for label, df in nodes.groupby("label"):
    df.dropna(axis=1, how="all").to_csv(OUT / f"nodes_{label}.csv", index=False)
edges = pd.DataFrame([{"src": u, "dst": v, "src_label": G.nodes[u]["label"],
                       "dst_label": G.nodes[v]["label"], **d} for u, v, d in G.edges(data=True)])
groups = list(edges.groupby(["type", "src_label", "dst_label"]))
for (t, sl, dl), df in groups:
    df.drop(columns=["src_label", "dst_label", "type"]).dropna(axis=1, how="all") \
      .to_csv(OUT / f"rels_{t}_{sl}_{dl}.csv", index=False)

cy = ["// Copy neo4j_import/*.csv into Neo4j's import folder, then run:",
      "//   cypher-shell -u neo4j -p <password> -f load.cypher"]
for label in sorted(nodes.label.unique()):
    cy.append(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE;")
for label in sorted(nodes.label.unique()):
    cy.append(f"LOAD CSV WITH HEADERS FROM 'file:///nodes_{label}.csv' AS row "
              f"CALL (row) {{ CREATE (n:{label}) SET n = row }} IN TRANSACTIONS OF 5000 ROWS;")
cy.append("// typed numbers / dates")
cy.append("MATCH (m:Match) SET m.date = date(m.date), m.home_score = toInteger(m.home_score), "
          "m.away_score = toInteger(m.away_score), m.neutral = m.neutral = 'True';")
cy.append("MATCH (g:Goal) SET g.minute = toInteger(g.minute), g.penalty = g.penalty = 'True', "
          "g.own_goal = g.own_goal = 'True';")
for (t, sl, dl), df in groups:
    extra = [c for c in df.columns if c not in ("src", "dst", "src_label", "dst_label", "type")
             and df[c].notna().any()]
    props = ", ".join(f"{c}: row.{c}" for c in extra)
    cy.append(f"LOAD CSV WITH HEADERS FROM 'file:///rels_{t}_{sl}_{dl}.csv' AS row "
              f"CALL (row) {{ MATCH (a:{sl} {{id: row.src}}) MATCH (b:{dl} {{id: row.dst}}) "
              f"CREATE (a)-[:{t}{' {'+props+'}' if props else ''}]->(b) }} IN TRANSACTIONS OF 5000 ROWS;")
(OUT / "load.cypher").write_text("\n".join(cy) + "\n")

print(f"Nodes: {G.number_of_nodes():,}   Edges: {G.number_of_edges():,}")
print(nodes.label.value_counts().to_string())
print(edges.type.value_counts().to_string())
