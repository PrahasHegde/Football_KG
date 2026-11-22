/* eslint-disable @typescript-eslint/no-explicit-any */
import { Crosshair, Network, Route, Swords, Trash2, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api, teamId } from "../api";
import { fmt, labelColor, type Mode } from "../theme";
import type { Entity, SearchHit } from "../types";
import { LineChart, ProbBar } from "./charts";
import { EntityMedia } from "./EntityMedia";
import { SearchBox } from "./SearchBox";

interface Props {
  id: string;
  mode: Mode;
  onCompare: (a: string, b: string) => void;
  onClose: () => void;
  onOpen: (id: string) => void;
  onExpand: (id: string, type?: string, direction?: string) => void;
  onPath: (from: string, to: string, viaTeams: boolean) => void;
  onRemove: (id: string) => void;
  onFocus: (id: string) => void;
}

export function Inspector({ id, mode, onCompare, onClose, onOpen, onExpand, onPath, onRemove, onFocus }: Props) {
  const [entity, setEntity] = useState<Entity | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pathMode, setPathMode] = useState(false);
  const [compareMode, setCompareMode] = useState(false);
  const [viaTeams, setViaTeams] = useState(true);

  useEffect(() => {
    let live = true;
    setEntity(null);
    setError(null);
    setPathMode(false);
    setCompareMode(false);
    api.entity(id).then((e) => live && setEntity(e)).catch((e) => live && setError(String(e.message ?? e)));
    return () => {
      live = false;
    };
  }, [id]);

  return (
    <aside className="panel inspector" aria-label="Entity details">
      <header className="panel__head">
        {entity ? (
          <>
            <span className="chip" style={{ "--chip": labelColor(entity.label, mode) } as React.CSSProperties}>
              {entity.label}
            </span>
            <h2>{entity.name}</h2>
            <code className="inspector__id">{entity.id}</code>
          </>
        ) : (
          <h2 className="muted">{error ?? "Loading…"}</h2>
        )}
        <button className="icon-btn panel__close" onClick={onClose} aria-label="Close details">
          <X size={16} />
        </button>
      </header>

      {entity && (
        <div className="inspector__actions">
          <button className="btn btn--sm" onClick={() => onExpand(id)} title="Add neighbours to the graph (or double-click the node)">
            <Network size={14} /> Expand
          </button>
          <button className="btn btn--sm" onClick={() => onFocus(id)}>
            <Crosshair size={14} /> Focus
          </button>
          {["Player", "Team", "Match", "Goal"].includes(entity.label) && (
            <button className={`btn btn--sm ${pathMode ? "is-on" : ""}`} onClick={() => { setPathMode((v) => !v); setCompareMode(false); }}>
              <Route size={14} /> Connect
            </button>
          )}
          {entity.label === "Team" && (
            <button className={`btn btn--sm ${compareMode ? "is-on" : ""}`} onClick={() => { setCompareMode((v) => !v); setPathMode(false); }}>
              <Swords size={14} /> Compare
            </button>
          )}
          <button className="btn btn--sm btn--ghost" onClick={() => onRemove(id)} title="Remove from canvas">
            <Trash2 size={14} />
          </button>
        </div>
      )}

      {entity && pathMode && (
        <div className="inspector__path">
          <p className="muted small">Shortest connection from <b>{entity.name}</b> to:</p>
          <SearchBox compact autoFocus placeholder="Pick a player or team…" onPick={(h: SearchHit) => onPath(id, h.id, viaTeams)} />
          <label className="toggle small">
            <input type="checkbox" checked={!viaTeams} onChange={(e) => setViaTeams(!e.target.checked)} />
            Only through goals scored in shared matches
          </label>
        </div>
      )}

      {entity && compareMode && (
        <div className="inspector__path">
          <p className="muted small">Head to head: <b>{entity.name}</b> vs</p>
          <SearchBox compact autoFocus placeholder="Pick another team…" onPick={(h: SearchHit) => h.label === "Team" && onCompare(id, h.id)} />
        </div>
      )}

      {entity && (
        <div className="panel__body">
          <EntityMedia entity={entity} />
          <Details entity={entity} open={onOpen} />
          <Section title="Relationships">
            <ul className="rels">
              {entity.relationships.map((r) => (
                <li key={`${r.type}-${r.direction}`}>
                  <button onClick={() => onExpand(id, r.type, r.direction)} title="Add these to the graph">
                    <span className="rels__dir">{r.direction === "out" ? "→" : "←"}</span>
                    <span className="rels__type">{r.type}</span>
                    <span className="rels__count">{fmt(r.count)}</span>
                  </button>
                </li>
              ))}
            </ul>
          </Section>
        </div>
      )}
    </aside>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="section">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

function Stats({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="stats">
      {items.map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function Rows({ rows, render }: { rows: any[]; render: (r: any) => ReactNode }) {
  if (!rows?.length) return <p className="muted small">None recorded.</p>;
  return <ul className="rows">{rows.map((r, i) => <li key={r.id ?? i}>{render(r)}</li>)}</ul>;
}

function Link({ id, open, children }: { id: string; open: (id: string) => void; children: ReactNode }) {
  return (
    <button className="link" onClick={() => open(id)}>
      {children}
    </button>
  );
}

const year = (d?: string | null) => (d ? d.slice(0, 4) : "–");
const pct = (a: number, b: number) => (b ? `${Math.round((a / b) * 100)}%` : "–");

function MatchRow({ m, open }: { m: any; open: (id: string) => void }) {
  return (
    <Link id={m.id ?? m.match_id} open={open}>
      <span className="mono muted">{m.date}</span> {m.home_team} <b>{m.home_score}–{m.away_score}</b> {m.away_team}
    </Link>
  );
}

function Details({ entity, open }: { entity: Entity; open: (id: string) => void }) {
  const d = entity.details;
  switch (entity.label) {
    case "Team": {
      const r = d.record;
      return (
        <>
          {r && (
            <Stats
              items={[
                ["Matches", fmt(r.matches)],
                ["Win rate", pct(r.wins, r.matches)],
                ["W · D · L", `${fmt(r.wins)} · ${fmt(r.draws)} · ${fmt(r.losses)}`],
                ["Goals", `${fmt(r.goals_for)} : ${fmt(r.goals_against)}`],
                ["Active", `${year(r.first_match)}–${year(r.last_match)}`],
                ["Shootout wins", fmt(d.shootout_wins)],
              ]}
            />
          )}
          {d.elo?.length > 1 && (
            <Section title={`Elo rating · now #${d.elo[d.elo.length - 1].rank} in the world`}>
              <LineChart
                ariaLabel={`Elo rating of ${entity.name} by year`}
                height={130}
                series={[{ name: entity.name, color: "var(--series-1)", points: d.elo.map((p: any) => ({ x: p.year, y: p.elo })) }]}
                marker={d.elo_peak?.elo ? { x: Number(String(d.elo_peak.date).slice(0, 4)), y: d.elo_peak.elo, label: `Peak ${fmt(d.elo_peak.elo)}` } : undefined}
              />
            </Section>
          )}
          <Section title="Top scorers">
            <Rows rows={d.top_scorers} render={(p) => <Link id={p.id} open={open}>{p.name}<span className="num">{p.goals}</span></Link>} />
          </Section>
          <Section title="Most frequent opponents">
            <Rows
              rows={d.top_opponents}
              render={(o) => (
                <Link id={o.id} open={open}>
                  {o.name}
                  <span className="num muted small">{o.wins}W {o.draws}D {o.meetings - o.wins - o.draws}L</span>
                  <span className="num">{o.meetings}</span>
                </Link>
              )}
            />
          </Section>
          <Section title="Recent matches">
            <Rows rows={d.recent_matches} render={(m) => <MatchRow m={m} open={open} />} />
          </Section>
          <Section title="Competitions">
            <Rows rows={d.tournaments} render={(t) => <Link id={t.id} open={open}>{t.name}<span className="num">{t.matches}</span></Link>} />
          </Section>
          {d.former_names?.length > 0 && (
            <Section title="Former names">
              <Rows rows={d.former_names} render={(f) => <span>{f.name} <span className="muted small">{year(f.start_date)}–{year(f.end_date)}</span></span>} />
            </Section>
          )}
        </>
      );
    }
    case "Player": {
      const p = d.profile;
      return (
        <>
          {p && (
            <Stats
              items={[
                ["Team", <Link id={p.team_id} open={open}>{p.team}</Link>],
                ...(p.friendly_goals > 0
                  ? ([
                      ["Total goals", fmt(p.goals + p.friendly_goals)],
                      ["Competitive*", fmt(p.goals)],
                      ["Friendlies†", fmt(p.friendly_goals)],
                    ] as [string, ReactNode][])
                  : ([["Goals*", fmt(p.goals)]] as [string, ReactNode][])),
                ["Penalties", fmt(p.penalty_goals)],
                ["Assists*", fmt(p.assists)],
                ["Hat-tricks", fmt(d.hat_tricks)],
                ["Scoring span", `${year(p.first_goal_date)}–${year(p.last_goal_date)}`],
              ]}
            />
          )}
          <p className="footnote">
            * Competitive matches with recorded scorers; assists are sparsely recorded.
            {p?.friendly_goals > 0 && " † Friendly goals from this player's Wikipedia goal list."}
          </p>
          <Section title="Goals by competition">
            <Rows rows={d.by_tournament} render={(t) => <span className="row-flex">{t.name}<span className="num">{t.goals}</span></span>} />
          </Section>
          <Section title="Favourite opponents">
            <Rows rows={d.victims} render={(t) => <Link id={teamId(t.name)} open={open}>{t.name}<span className="num">{t.goals}</span></Link>} />
          </Section>
          <Section title="Latest goals">
            <Rows
              rows={d.goals}
              render={(g) => (
                <Link id={g.match_id} open={open}>
                  <span className="mono muted">{g.date}</span> {g.source === "wikipedia" ? "" : `${g.minute ?? "?"}′ `}vs {g.opponent}
                  {g.source === "wikipedia" && <span className="tag">friendly</span>}
                  {g.penalty && <span className="tag">pen</span>}
                  {g.own_goal && <span className="tag">o.g.</span>}
                </Link>
              )}
            />
          </Section>
        </>
      );
    }
    case "Match": {
      const m = d.match;
      if (!m) return null;
      return (
        <>
          <div className="scoreline">
            <Link id={m.home_team_id} open={open}>{m.home_team}</Link>
            <span className="scoreline__score">{m.home_score} – {m.away_score}</span>
            <Link id={m.away_team_id} open={open}>{m.away_team}</Link>
          </div>
          {m.shootout_winner && <p className="center small">{m.shootout_winner} won on penalties</p>}
          {m.upset && (
            <p className="upset-banner">
              Upset: {m.winner} had a {Math.round((m.winner_win_exp ?? 0) * 100)}% pre-match chance
            </p>
          )}
          {m.home_win_exp !== null && m.home_win_exp !== undefined && (
            <Section title={`Pre-match Elo ${fmt(m.home_elo)} vs ${fmt(m.away_elo)} · win expectancy`}>
              <ProbBar home={m.home_win_exp} homeName={m.home_team} awayName={m.away_team} />
            </Section>
          )}
          <Stats
            items={[
              ["Date", m.date],
              ["Competition", <Link id={m.tournament_id} open={open}>{m.tournament}</Link>],
              ["Venue", <Link id={m.city_id} open={open}>{m.city}, {m.country}</Link>],
              ["Neutral", m.neutral ? "Yes" : "No"],
            ]}
          />
          <Section title="Goals">
            <Rows
              rows={d.goals}
              render={(g) => (
                <Link id={g.player_id} open={open}>
                  <span className="mono muted">{g.source === "wikipedia" ? "–" : `${g.minute ?? "?"}′`}</span> {g.scorer}
                  <span className="muted small"> ({g.team})</span>
                  {g.penalty && <span className="tag">pen</span>}
                  {g.own_goal && <span className="tag">o.g.</span>}
                  {g.assister && <span className="muted small"> · assist {g.assister}</span>}
                </Link>
              )}
            />
            {!d.goals?.length && <p className="footnote">Scorers are not recorded for this match.</p>}
            {d.goals?.some((g: any) => g.source === "wikipedia") && (
              <p className="footnote">Friendly goals from players' Wikipedia goal lists; other scorers in this match may be missing.</p>
            )}
          </Section>
        </>
      );
    }
    case "Goal": {
      const g = d.goal;
      if (!g) return null;
      return (
        <Stats
          items={[
            ["Scorer", <Link id={g.player_id} open={open}>{g.scorer}</Link>],
            ["For", <Link id={g.team_id} open={open}>{g.team}</Link>],
            ["Minute", g.minute ?? "?"],
            ["Type", g.own_goal ? "Own goal" : g.penalty ? "Penalty" : "Open play"],
            ["Match", <Link id={g.match_id} open={open}>vs {g.opponent}, {g.date}</Link>],
            ["Assist", g.assister ? <Link id={g.assist_player_id} open={open}>{g.assister}</Link> : "–"],
          ]}
        />
      );
    }
    case "Tournament": {
      const s = d.summary;
      return (
        <>
          {s && <Stats items={[["Matches", fmt(s.matches)], ["Played", `${year(s.first_match)}–${year(s.last_match)}`]]} />}
          <Section title="Most wins">
            <Rows rows={d.most_wins} render={(t) => <Link id={teamId(t.name)} open={open}>{t.name}<span className="num">{t.wins}</span></Link>} />
          </Section>
          <Section title="Top scorers">
            <Rows rows={d.top_scorers} render={(p) => <Link id={p.id} open={open}>{p.name}<span className="muted small"> {p.team}</span><span className="num">{p.goals}</span></Link>} />
          </Section>
          <Section title="Host countries">
            <Rows rows={d.hosts} render={(h) => <span className="row-flex">{h.name}<span className="num">{h.matches}</span></span>} />
          </Section>
        </>
      );
    }
    case "Country":
      return (
        <>
          <Stats items={[["Matches hosted", fmt(d.hosted?.matches)], ["Since", d.hosted?.first ?? "–"]]} />
          <Section title="Host cities">
            <Rows rows={d.cities} render={(c) => <Link id={c.id} open={open}>{c.name}<span className="num">{c.matches}</span></Link>} />
          </Section>
        </>
      );
    case "City":
      return (
        <>
          <Stats items={[["Country", d.hosted?.country ?? "–"], ["Matches hosted", fmt(d.hosted?.matches)], ["First", d.hosted?.first ?? "–"]]} />
          <Section title="Recent matches">
            <Rows rows={d.recent_matches} render={(m) => <MatchRow m={m} open={open} />} />
          </Section>
        </>
      );
    case "FormerName": {
      const f = d.former;
      return f ? (
        <Stats items={[["Now", <Link id={f.team_id} open={open}>{f.team}</Link>], ["Used", `${f.start_date ?? "?"} → ${f.end_date ?? "?"}`]]} />
      ) : null;
    }
    default:
      return null;
  }
}
