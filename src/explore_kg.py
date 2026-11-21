"""Showcase queries on the football knowledge graph."""
import pickle, collections, networkx as nx
G = pickle.load(open("football_kg.gpickle", "rb"))
L = lambda n: G.nodes[n]["label"]; N = lambda n: G.nodes[n].get("name", n)
out = lambda n, t: [v for _, v, d in G.out_edges(n, data=True) if d["type"] == t]
inn = lambda n, t: [u for u, _, d in G.in_edges(n, data=True) if d["type"] == t]
players = [n for n in G if L(n) == "Player"]

print("1) All-time top scorers (no own goals)")
tally = {p: sum(not G.nodes[g]["own_goal"] for g in out(p, "SCORED")) for p in players}
for p, c in sorted(tally.items(), key=lambda x: -x[1])[:5]:
    print(f"   {c:3d}  {N(p)} ({N(out(p,'PLAYS_FOR')[0])})")

print("\n2) Hat-trick kings (3+ goals in one match)")
ht = collections.Counter()
for p in players:
    per_match = collections.Counter(out(g, "IN_MATCH")[0] for g in out(p, "SCORED")
                                    if not G.nodes[g]["own_goal"])
    ht[p] = sum(c >= 3 for c in per_match.values())
for p, c in ht.most_common(5): print(f"   {c:2d}  {N(p)}")
 
print("\n3) Most-played rivalries")
pairs = collections.Counter()
for m in (n for n in G if L(n) == "Match"):
    pairs[tuple(sorted(N(t) for t in inn(m, "PLAYED_IN")))] += 1
for (a, b), c in pairs.most_common(5): print(f"   {c:3d}  {a} vs {b}")

print("\n4) Penalty-shootout specialists (wins)")
sw = collections.Counter({N(t): len(out(t, "WON_SHOOTOUT")) for t in G if L(t) == "Team"})
print("   " + ", ".join(f"{t} {c}" for t, c in sw.most_common(5)))

print("\n5) 'Six degrees of football' — link two scorers through matches they both scored in")
# projected graph: player -- match (via goal)
P = nx.Graph()
for g in (n for n in G if L(n) == "Goal"):
    P.add_edge(inn(g, "SCORED")[0], out(g, "IN_MATCH")[0])
def find(name, team):
    return next(p for p in players if N(p) == name and N(out(p, "PLAYS_FOR")[0]) == team)
for a, b in [(("Pelé","Brazil"), ("Lionel Messi","Argentina")),
             (("Ferenc Puskás","Hungary"), ("Cristiano Ronaldo","Portugal"))]:
    try:
        path = nx.shortest_path(P, find(*a), find(*b))
        print("   " + "\n     -> ".join(N(n) for n in path))
    except (nx.NetworkXNoPath, StopIteration) as e:
        print("   no path", a, b, type(e).__name__)
