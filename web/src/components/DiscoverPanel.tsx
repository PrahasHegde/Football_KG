import { Crown, Globe2, Mountain, Swords, TrendingDown, TrendingUp, Zap } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api } from "../api";
import { fmt } from "../theme";
import type { Discover } from "../types";
import { Flag } from "./Flag";

interface Props {
  onOpen: (id: string) => void;
  onH2H: (a: string, b: string) => void;
}

export function DiscoverPanel({ onOpen, onH2H }: Props) {
  const [d, setD] = useState<Discover | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api.discover().then(setD).catch((e) => setErr(String(e.message ?? e)));
  }, []);

  if (err) return <p className="discover__msg muted">{err}</p>;
  if (!d) return <p className="discover__msg muted">Crunching 150 years of results…</p>;
  const maxElo = Math.max(...d.top_elo.map((t) => t.elo));
  const minElo = Math.min(...d.top_elo.map((t) => t.elo)) - 120;

  return (
    <div className="discover">
      <Card icon={<Crown size={15} />} title={`World Elo ranking · ${d.year}`} note="Elo replayed over every match since 1872. Arrow = change over 4 years.">
        <ol className="ranking">
          {d.top_elo.map((t, i) => (
            <li key={t.id}>
              <button className="link ranking__row" onClick={() => onOpen(t.id)}>
                <span className="ranking__pos">{i + 1}</span>
                <span className="ranking__name"><Flag id={t.id} size={13} />{t.name}</span>
                <span className="ranking__bar" aria-hidden>
                  <span style={{ width: `${((t.elo - minElo) / (maxElo - minElo)) * 100}%` }} />
                </span>
                <span className="num">{fmt(t.elo)}</span>
                <span className={`delta ${t.change >= 0 ? "delta--up" : "delta--down"}`}>
                  {t.change >= 0 ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
                  {t.change >= 0 ? "+" : ""}
                  {fmt(t.change)}
                </span>
              </button>
            </li>
          ))}
        </ol>
      </Card>

      <Card icon={<Zap size={15} />} title="Biggest upsets" note="Competitive matches between established sides, ranked by the winner's pre-match chance.">
        <ul className="rows">
          {d.upsets.map((m) => (
            <li key={m.id}>
              <button className="link upset" onClick={() => onOpen(m.id)}>
                <span className="upset__odds">{Math.round((m.winner_win_exp ?? 0) * 100)}%</span>
                <span className="grow">
                  <span className="upset__score">
                    {m.home_team} <b>{m.home_score}–{m.away_score}</b> {m.away_team}
                  </span>
                  <span className="muted small">
                    {m.date.slice(0, 4)} · {m.tournament}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </Card>

      <Card icon={<Swords size={15} />} title="Greatest rivalries" note="Most-played fixtures. Opens a head-to-head comparison.">
        <ul className="rows">
          {d.rivalries.map((r) => (
            <li key={`${r.a}-${r.b}`}>
              <button className="link row-link" onClick={() => onH2H(r.a, r.b)}>
                <span className="grow rivalry">
                  <Flag id={r.a} size={12} />{r.a_name} <span className="muted">vs</span> <Flag id={r.b} size={12} />{r.b_name}
                </span>
                <span className="muted small">since {r.since}</span>
                <span className="num">{r.meetings}</span>
              </button>
            </li>
          ))}
        </ul>
      </Card>

      <Card icon={<Mountain size={15} />} title="Highest peaks ever" note="Each team's best-ever Elo rating and when they reached it.">
        <ul className="rows">
          {d.peaks.map((p) => (
            <li key={p.id}>
              <button className="link row-link" onClick={() => onOpen(p.id)}>
                <span className="grow rivalry"><Flag id={p.id} size={12} />{p.name}</span>
                <span className="muted small mono">{p.date}</span>
                <span className="num">{fmt(p.elo)}</span>
              </button>
            </li>
          ))}
        </ul>
      </Card>

      <Card icon={<Globe2 size={15} />} title="Globetrotters" note="Most different opponents faced: the best-connected nodes in the team network.">
        <ul className="rows">
          {d.central.map((c) => (
            <li key={c.id}>
              <button className="link row-link" onClick={() => onOpen(c.id)}>
                <span className="grow rivalry"><Flag id={c.id} size={12} />{c.name}</span>
                <span className="num">{c.score}</span>
              </button>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

function Card({ icon, title, note, children }: { icon: ReactNode; title: string; note: string; children: ReactNode }) {
  return (
    <section className="card">
      <header>
        <h3>
          {icon} {title}
        </h3>
        <p className="muted small">{note}</p>
      </header>
      {children}
    </section>
  );
}
