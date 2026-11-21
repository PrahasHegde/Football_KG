import { Compass, Download, Eraser, Globe2, Maximize2, Moon, Pause, Play, Sparkles, Sun } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { loadTeamMedia } from "./media";
import { ChatPanel } from "./components/ChatPanel";
import { DiscoverPanel } from "./components/DiscoverPanel";
import { ALL_TIME, EraBar, type Era } from "./components/EraBar";
import { GraphCanvas, type GraphHandle } from "./components/GraphCanvas";
import { H2HPanel } from "./components/H2HPanel";
import { Inspector } from "./components/Inspector";
import { SearchBox } from "./components/SearchBox";
import { LABEL_ORDER, LABEL_SHAPE, communityColor, fmt, labelColor } from "./theme";
import type { AgentEvent, GNode, H2H, Label, Meta, Subgraph } from "./types";
import { useGraph } from "./useGraph";
import { useTheme } from "./useTheme";

type ViewMode = "overview" | "explore";
type Tab = "ask" | "discover";

export default function App() {
  const { data, merge, replace, remove, clear, has, ids, ungroup } = useGraph();
  const { mode: theme, toggle: toggleTheme } = useTheme();
  const canvas = useRef<GraphHandle>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [highlight, setHighlight] = useState<Set<string>>(new Set());
  const [caption, setCaption] = useState("");
  const [view, setView] = useState<ViewMode>("overview");
  const [era, setEra] = useState<Era>(ALL_TIME);
  const [playing, setPlaying] = useState(false);
  const [communities, setCommunities] = useState<string[]>([]);
  const [hidden, setHidden] = useState<Set<Label>>(new Set());
  const [frozen, setFrozen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("ask");
  const [h2h, setH2H] = useState<H2H | null>(null);

  const notify = useCallback((msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast((t) => (t === msg ? null : t)), 4200);
  }, []);

  const show = useCallback(
    (sub: Subgraph, opts: { caption: string; view?: ViewMode; highlight?: string[]; select?: string | null; morph?: boolean }) => {
      replace(sub, opts.morph);
      if (!opts.morph) canvas.current?.fitAfterLayout();
      setView(opts.view ?? "explore");
      setCaption(opts.caption);
      setHighlight(new Set(opts.highlight ?? []));
      setSelectedId(opts.select ?? null);
      setFrozen(false);
      if (opts.view !== "overview") setPlaying(false);
    },
    [replace],
  );

  const loadOverview = useCallback(
    async (e: Era, morph = false) => {
      try {
        const sub = await api.overview(e === ALL_TIME ? undefined : e);
        setCommunities(sub.communities ?? []);
        setEra(e);
        show(sub, {
          view: "overview",
          morph,
          caption:
            e === ALL_TIME
              ? "Rivalry network · 80 most active teams, linked when they met 20+ times"
              : `80 most active teams of the decade, linked when they met ${sub.era?.min_meetings}+ times`,
        });
      } catch (err) {
        notify(`Could not load the graph: ${(err as Error).message}`);
      }
    },
    [show, notify],
  );

  useEffect(() => {
    api.meta().then(setMeta).catch(() => notify("API is not reachable. Is the server running on :8000?"));
    loadOverview(ALL_TIME);
    loadTeamMedia();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const changeEra = useCallback((e: Era) => loadOverview(e, view === "overview"), [loadOverview, view]);

  const leaveOverview = useCallback(() => {
    if (view !== "overview") return;
    ungroup();
    setPlaying(false);
    setView("explore");
    setCaption("Exploring · double-click a node to expand it");
  }, [view, ungroup]);

  /** Put an entity on the canvas (connected to whatever is already there) and select it. */
  const openEntity = useCallback(
    async (id: string) => {
      try {
        if (!has(id)) {
          const sub = await api.subgraph([id, ...ids()].slice(0, 500));
          if (!sub.nodes.some((n) => n.id === id)) {
            notify(`Unknown entity ${id}`);
            return;
          }
          leaveOverview();
          merge(sub);
          if (!sub.edges.some((e) => e.source === id || e.target === id)) {
            const ctx = await api.expand(id, { limit: 8 });
            merge(ctx, id);
          }
        }
        setH2H(null);
        setSelectedId(id);
        window.setTimeout(() => canvas.current?.focus(id), 350);
      } catch (e) {
        notify((e as Error).message);
      }
    },
    [has, ids, merge, leaveOverview, notify],
  );

  const expand = useCallback(
    async (id: string, type?: string, direction?: string) => {
      try {
        const sub = await api.expand(id, { limit: type ? 30 : 12, type, direction });
        leaveOverview();
        merge(sub, id);
        setFrozen(false);
        canvas.current?.reheat();
        const t = Object.entries(sub.truncated ?? {});
        if (t.length) {
          notify(
            `Showing the first ${type ? 30 : 12} of ` +
              t.map(([k, n]) => `${fmt(n)} ${k.split(":")[0]}`).join(", ") +
              ". Use Relationships in the panel to load more of one type.",
          );
        }
      } catch (e) {
        notify((e as Error).message);
      }
    },
    [merge, leaveOverview, notify],
  );

  const findPath = useCallback(
    async (from: string, to: string, viaTeams: boolean) => {
      const res = await api.path(from, to, viaTeams);
      if (!res.found) {
        notify(`No connection found (${res.reason}).`);
        return;
      }
      const names = res.nodes.map((n) => n.name);
      show(res, { caption: `${names[0]} → ${names[names.length - 1]} · ${res.path!.length - 1} hops`, highlight: res.path, select: from });
    },
    [show, notify],
  );

  const openH2H = useCallback(
    async (a: string, b: string) => {
      try {
        const res = await api.h2h(a, b);
        const winner = new Map(res.graph.edges.filter((e) => e.type === "WON_BY").map((e) => [e.source as string, e.target as string]));
        for (const n of res.graph.nodes) {
          if (n.id === res.a.id) n.group = 0;
          else if (n.id === res.b.id) n.group = 1;
          else if (n.label === "Match") n.group = winner.get(n.id) === res.a.id ? 0 : winner.get(n.id) === res.b.id ? 1 : 7;
        }
        show(res.graph, { caption: `${res.a.name} vs ${res.b.name} · latest ${res.graph.nodes.length - 2} meetings` });
        setH2H(res);
      } catch (e) {
        notify((e as Error).message);
      }
    },
    [show, notify],
  );

  const onAgentGraph = useCallback(
    (e: Extract<AgentEvent, { type: "graph" }>) => {
      setH2H(null);
      show({ nodes: e.nodes, edges: e.edges }, { caption: e.caption, highlight: e.path ?? [], select: e.focus ?? null });
    },
    [show],
  );

  const onSelect = useCallback((n: GNode | null) => {
    setSelectedId(n?.id ?? null);
    if (n) setH2H(null);
  }, []);
  const onExpandNode = useCallback((n: GNode) => expand(n.id), [expand]);

  const counts = useMemo(() => {
    const c = new Map<Label, number>();
    for (const n of data.nodes) c.set(n.label, (c.get(n.label) ?? 0) + 1);
    return c;
  }, [data]);

  const toggleLabel = (l: Label) =>
    setHidden((h) => {
      const next = new Set(h);
      if (next.has(l)) next.delete(l);
      else next.add(l);
      return next;
    });

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <svg viewBox="0 0 32 32" width="24" height="24" aria-hidden>
            <circle cx="16" cy="16" r="13" fill="none" stroke="var(--accent)" strokeWidth="2.5" />
            <circle cx="16" cy="16" r="3.5" fill="var(--accent)" />
            <path d="M16 3v26M3 16h26" stroke="var(--accent)" strokeWidth="1.2" opacity=".45" />
          </svg>
          <span>
            Football <em>KG</em>
          </span>
        </div>
        <SearchBox shortcut onPick={(h) => openEntity(h.id)} />
        {meta && (
          <div className="kpis">
            <span><b>{fmt(meta.nodes)}</b> nodes</span>
            <span><b>{fmt(meta.edges)}</b> edges</span>
            <span><b>{fmt(meta.labels.find((l) => l.label === "Match")?.count)}</b> matches</span>
            <span><b>{meta.first_match.slice(0, 4)}–{meta.last_match.slice(0, 4)}</b></span>
          </div>
        )}
        <button className="icon-btn" onClick={toggleTheme} aria-label="Toggle colour theme" title={theme === "light" ? "Dark mode" : "Light mode"}>
          {theme === "light" ? <Moon size={17} /> : <Sun size={17} />}
        </button>
      </header>

      <main className="workspace">
        <section className="stage">
          <GraphCanvas
            ref={canvas}
            data={data}
            mode={theme}
            selectedId={selectedId}
            highlight={highlight}
            hiddenLabels={hidden}
            frozen={frozen}
            onSelect={onSelect}
            onExpand={onExpandNode}
          />

          {caption && (
            <div className="caption">
              {view === "overview" && era !== ALL_TIME && <span className="caption__era">{era.label}</span>}
              {caption}
            </div>
          )}

          <div className="toolbar" role="toolbar">
            <button className={`icon-btn ${view === "overview" ? "is-on" : ""}`} onClick={() => loadOverview(era)} title="Rivalry network">
              <Globe2 size={16} />
            </button>
            <button className="icon-btn" onClick={() => canvas.current?.fit()} title="Fit to screen">
              <Maximize2 size={16} />
            </button>
            <button className="icon-btn" onClick={() => setFrozen((f) => !f)} title={frozen ? "Resume layout" : "Freeze layout"}>
              {frozen ? <Play size={16} /> : <Pause size={16} />}
            </button>
            <button className="icon-btn" onClick={() => canvas.current?.exportPng(`football-kg-${Date.now()}.png`)} title="Download as PNG">
              <Download size={16} />
            </button>
            <button
              className="icon-btn"
              onClick={() => {
                clear();
                setCaption("");
                setSelectedId(null);
                setHighlight(new Set());
                setH2H(null);
                setView("explore");
                setPlaying(false);
              }}
              title="Clear canvas"
            >
              <Eraser size={16} />
            </button>
          </div>

          <div className="legend">
            {h2h ? (
              <>
                <span className="legend__item"><span className="dot" style={{ background: communityColor(0, theme) }} />{h2h.a.name} win</span>
                <span className="legend__item"><span className="dot" style={{ background: communityColor(7, theme) }} />Draw</span>
                <span className="legend__item"><span className="dot" style={{ background: communityColor(1, theme) }} />{h2h.b.name} win</span>
              </>
            ) : view === "overview" ? (
              <>
                {communities.map((c, i) => (
                  <span key={c} className="legend__item" title={c}>
                    <span className="dot" style={{ background: communityColor(i, theme) }} />
                    <span className="legend__text" title={c}>{c}</span>
                  </span>
                ))}
                <span className="legend__note">Size = Elo at the end of the era · width = meetings</span>
              </>
            ) : (
              LABEL_ORDER.filter((l) => counts.has(l)).map((l) => (
                <button key={l} className={`legend__item ${hidden.has(l) ? "is-off" : ""}`} onClick={() => toggleLabel(l)} title="Show / hide">
                  <ShapeIcon label={l} color={labelColor(l, theme)} />
                  {l} <span className="muted">{counts.get(l)}</span>
                </button>
              ))
            )}
          </div>

          {view === "overview" && <EraBar era={era} playing={playing} onChange={changeEra} onPlay={setPlaying} />}

          {data.nodes.length === 0 && (
            <div className="stage__empty">
              <p>Search for a team or player, explore Discover, or ask the agent a question.</p>
            </div>
          )}

          {h2h ? (
            <H2HPanel data={h2h} onClose={() => setH2H(null)} onOpen={openEntity} />
          ) : (
            selectedId && (
              <Inspector
                id={selectedId}
                mode={theme}
                onCompare={openH2H}
                onClose={() => setSelectedId(null)}
                onOpen={openEntity}
                onExpand={expand}
                onPath={findPath}
                onRemove={(id) => {
                  remove(id);
                  setSelectedId(null);
                }}
                onFocus={(id) => canvas.current?.focus(id)}
              />
            )
          )}

          {toast && <div className="toast">{toast}</div>}
        </section>

        <aside className="side">
          <nav className="tabs" role="tablist">
            <button role="tab" aria-selected={tab === "ask"} className={tab === "ask" ? "is-active" : ""} onClick={() => setTab("ask")}>
              <Sparkles size={14} /> Ask the graph
            </button>
            <button role="tab" aria-selected={tab === "discover"} className={tab === "discover" ? "is-active" : ""} onClick={() => setTab("discover")}>
              <Compass size={14} /> Discover
            </button>
          </nav>
          <div className="side__body" hidden={tab !== "ask"}>
            <ChatPanel meta={meta} onGraph={onAgentGraph} onEntity={openEntity} />
          </div>
          {tab === "discover" && (
            <div className="side__body side__scroll">
              <DiscoverPanel onOpen={openEntity} onH2H={openH2H} />
            </div>
          )}
        </aside>
      </main>
    </div>
  );
}

function ShapeIcon({ label, color }: { label: Label; color: string }) {
  const s = LABEL_SHAPE[label];
  const hollow = label === "FormerName";
  const common = { fill: hollow ? "none" : color, stroke: color, strokeWidth: hollow ? 1.5 : 0 };
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden>
      {s === "square" && <rect x="1.5" y="1.5" width="9" height="9" {...common} />}
      {s === "rounded" && <rect x="1.5" y="1.5" width="9" height="9" rx="2.5" {...common} />}
      {s === "diamond" && <path d="M6 0.5 11.5 6 6 11.5 0.5 6Z" {...common} />}
      {s === "triangle" && <path d="M6 1 11 10.5H1Z" {...common} />}
      {s === "hexagon" && <path d="M6 0.6 10.8 3.3v5.4L6 11.4 1.2 8.7V3.3Z" {...common} />}
      {(s === "circle" || s === "ring") && <circle cx="6" cy="6" r="5" {...common} strokeDasharray={hollow ? "2 1.5" : undefined} />}
      {s === "ring" && <circle cx="6" cy="6" r="1.9" fill="var(--surface)" />}
    </svg>
  );
}
