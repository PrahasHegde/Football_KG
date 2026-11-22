/* eslint-disable @typescript-eslint/no-explicit-any */
import { Route, Users, X } from "lucide-react";
import { useEffect } from "react";
import { requestPlayerPhotos } from "../media";
import { fmt } from "../theme";
import { LineChart } from "./charts";
import { Avatar, Flag } from "./Flag";

const num = (v: number | null | undefined) => (v == null ? "–" : Number.isInteger(v) ? fmt(v) : v.toFixed(2));

interface Props {
  data: any; // /api/compare/players
  onClose: () => void;
  onOpen: (id: string) => void;
  onPath: (a: string, b: string) => void;
}

/** Player vs player: mirrored stat bars, goals over time, and everything that connects the two. */
export function ComparePanel({ data, onClose, onOpen, onPath }: Props) {
  const a = data.a, b = data.b;
  const pa = a.profile ?? {}, pb = b.profile ?? {};
  useEffect(() => {
    requestPlayerPhotos([a.id, b.id]);
  }, [a.id, b.id]);

  const rows: [string, number | null, number | null][] = [
    ["International goals", pa.total_goals, pb.total_goals],
    ["Caps", pa.caps, pb.caps],
    ["Goals per cap", pa.caps ? +(pa.total_goals / pa.caps).toFixed(2) : null, pb.caps ? +(pb.total_goals / pb.caps).toFixed(2) : null],
    ["Hat-tricks", a.hat_tricks, b.hat_tricks],
    ["Penalties", pa.penalty_goals, pb.penalty_goals],
    ["Intl assists*", pa.assists, pb.assists],
    ["Club games†", a.club_totals.apps, b.club_totals.apps],
    ["Club goals†", a.club_totals.goals, b.club_totals.goals],
    ["Club assists†", a.club_totals.assists, b.club_totals.assists],
  ];
  const cumulative = (side: any) => {
    let sum = 0;
    return side.by_year.map((y: any) => ({ x: y.year, y: (sum += y.goals) }));
  };

  return (
    <aside className="panel h2h compare" aria-label="Player comparison">
      <header className="panel__head">
        <span className="eyebrow"><Users size={13} /> Player vs player</span>
        <div className="compare__heads">
          {[a, b].map((s: any, i) => (
            <button key={s.id} className={`compare__head compare__head--${i ? "b" : "a"}`} onClick={() => onOpen(s.id)}>
              <span className="compare__photo"><Avatar id={s.id} size={54} /></span>
              <b>{s.profile?.name ?? s.id}</b>
              <span className="muted small"><Flag id={s.profile?.team_id ?? ""} size={11} />{s.profile?.team}{s.profile?.position ? ` · ${s.profile.position}` : ""}</span>
            </button>
          ))}
          <span className="compare__vs">vs</span>
        </div>
        <button className="icon-btn panel__close" onClick={onClose} aria-label="Close comparison">
          <X size={16} />
        </button>
      </header>
      <div className="panel__body">
        <div className="duel">
          {rows.filter(([, x, y]) => (x ?? 0) > 0 || (y ?? 0) > 0).map(([label, x, y]) => {
            const max = Math.max(x ?? 0, y ?? 0, 1e-9);
            return (
              <div key={label} className="duel__row">
                <span className={`duel__val ${(x ?? 0) > (y ?? 0) ? "is-lead" : ""}`}>{num(x)}</span>
                <span className="duel__bar duel__bar--a"><i style={{ width: `${((x ?? 0) / max) * 100}%` }} /></span>
                <span className="duel__label">{label}</span>
                <span className="duel__bar duel__bar--b"><i style={{ width: `${((y ?? 0) / max) * 100}%` }} /></span>
                <span className={`duel__val ${(y ?? 0) > (x ?? 0) ? "is-lead" : ""}`}>{num(y)}</span>
              </div>
            );
          })}
        </div>
        <p className="footnote">* Assists only for World Cups, Euro 2020/24, Copa 2024, AFCON 2023. † Club data since 2012/13.</p>

        {(a.by_year.length > 1 || b.by_year.length > 1) && (
          <section className="section">
            <h3>International goals over time (cumulative)</h3>
            <LineChart
              ariaLabel="Cumulative international goals by year"
              height={140}
              series={[
                { name: pa.name ?? "A", color: "var(--series-1)", points: cumulative(a) },
                { name: pb.name ?? "B", color: "var(--series-2)", points: cumulative(b) },
              ]}
            />
          </section>
        )}

        <section className="section">
          <h3>How they connect</h3>
          <ul className="connect-list">
            {data.shared_clubs.length > 0 ? (
              data.shared_clubs.map((c: any) => (
                <li key={c.id}>
                  <button className="link" onClick={() => onOpen(c.id)}><b>{c.name}</b></button>
                  <span className="muted small"> · {c.together ? "team-mates there" : "both played there, at different times"}</span>
                </li>
              ))
            ) : (
              <li className="muted small">Never at the same club (since 2012/13).</li>
            )}
            <li>
              <b>{fmt(data.common_teammates)}</b> club team-mates in common
              {data.common_teammates_sample.length > 0 && <span className="muted small"> · e.g. {data.common_teammates_sample.slice(0, 4).join(", ")}</span>}
            </li>
            <li>
              Scored in <b>{data.shared_matches.length}</b> of the same matches
              {data.shared_matches.some((m: any) => !m.same_side) && <span className="muted small"> (incl. against each other)</span>}
            </li>
          </ul>
          {data.shared_matches.length > 0 && (
            <ul className="rows">
              {data.shared_matches.map((m: any) => (
                <li key={m.id}>
                  <button className="link row-link" onClick={() => onOpen(m.id)}>
                    <span className="mono muted">{String(m.date).slice(0, 4)}</span>
                    <span className="grow">{m.home_team} <b>{m.home_score}–{m.away_score}</b> {m.away_team}</span>
                    <span className="tag">{m.same_side ? "together" : "rivals"}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          <button className="btn btn--sm compare__path" onClick={() => onPath(a.id, b.id)}>
            <Route size={14} /> Show the shortest connection on the graph
          </button>
        </section>

        <div className="compare__cols">
          {[a, b].map((s: any) => (
            <section key={s.id} className="section">
              <h3>{s.profile?.name?.split(" ").slice(-1)[0]} · top competitions</h3>
              <ul className="rows">
                {s.by_tournament.map((t: any) => (
                  <li key={t.name}><span className="row-flex small">{t.name}<span className="num">{t.goals}</span></span></li>
                ))}
              </ul>
              {s.clubs.length > 0 && (
                <>
                  <h3 className="compare__sub">Clubs since 2012</h3>
                  <ul className="rows">
                    {s.clubs.map((c: any) => (
                      <li key={c.id}>
                        <button className="link row-flex small" onClick={() => onOpen(c.id)}>
                          {c.name}<span className="num">{c.goals}g · {c.assists}a</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </section>
          ))}
        </div>
      </div>
    </aside>
  );
}
