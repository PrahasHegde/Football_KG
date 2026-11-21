import { Swords, X } from "lucide-react";
import { fmt } from "../theme";
import type { H2H, MatchRow } from "../types";
import { LineChart, ResultBar } from "./charts";
import { Flag } from "./Flag";

interface Props {
  data: H2H;
  onClose: () => void;
  onOpen: (id: string) => void;
}

export function H2HPanel({ data, onClose, onOpen }: Props) {
  const { a, b, summary: s } = data;
  const maxDecade = Math.max(1, ...data.by_decade.map((d) => d.a_wins + d.draws + d.b_wins));
  const match = (m: MatchRow) => (
    <button className="link row-link" onClick={() => onOpen(m.id)}>
      <span className="mono muted">{m.date.slice(0, 4)}</span>
      <span className="grow">
        {m.home_team} <b>{m.home_score}–{m.away_score}</b> {m.away_team}
      </span>
      {m.upset && <span className="tag tag--upset">upset</span>}
      <span className="muted small ellipsis">{m.tournament}</span>
    </button>
  );

  return (
    <aside className="panel h2h" aria-label="Head to head">
      <header className="panel__head">
        <span className="eyebrow"><Swords size={13} /> Head to head</span>
        <h2>
          <button className="link" onClick={() => onOpen(a.id)}><i className="key key--a" /><Flag id={a.id} size={15} />{a.name}</button>
          <span className="muted vs">vs</span>
          <button className="link" onClick={() => onOpen(b.id)}><i className="key key--b" /><Flag id={b.id} size={15} />{b.name}</button>
        </h2>
        <p className="muted small">
          {s.matches} meetings since {s.first?.slice(0, 4)} · goals {fmt(s.a_goals)}–{fmt(s.b_goals)}
        </p>
        <button className="icon-btn panel__close" onClick={onClose} aria-label="Close head to head">
          <X size={16} />
        </button>
      </header>
      <div className="panel__body">
        <ResultBar a={s.a_wins} draws={s.draws} b={s.b_wins} aName={a.name} bName={b.name} height={16} />

        <section className="section">
          <h3>Elo rating over the rivalry</h3>
          <LineChart
            ariaLabel={`Elo ratings of ${a.name} and ${b.name} by year`}
            series={[
              { name: a.name, color: "var(--series-1)", points: data.elo.a.map((p) => ({ x: p.year, y: p.elo })) },
              { name: b.name, color: "var(--series-2)", points: data.elo.b.map((p) => ({ x: p.year, y: p.elo })) },
            ]}
          />
        </section>

        <section className="section">
          <h3>Results by decade</h3>
          <div className="decades">
            {data.by_decade.map((d) => {
              const n = d.a_wins + d.draws + d.b_wins;
              return (
                <div key={d.decade} className="decades__row">
                  <span className="mono muted">{d.decade}s</span>
                  <div style={{ width: `${(n / maxDecade) * 100}%` }}>
                    <ResultBar a={d.a_wins} draws={d.draws} b={d.b_wins} aName={a.name} bName={b.name} height={8} showLabels={false} />
                  </div>
                  <span className="small decades__n">
                    {d.a_wins}–{d.draws}–{d.b_wins}
                  </span>
                </div>
              );
            })}
          </div>
          <p className="footnote">Bar length = meetings that decade; segments = {a.name} wins · draws · {b.name} wins.</p>
        </section>

        <section className="section">
          <h3>Biggest wins</h3>
          <ul className="rows">
            {[...data.biggest_wins.a.slice(0, 2), ...data.biggest_wins.b.slice(0, 2)].map((m) => (
              <li key={m.id}>{match(m)}</li>
            ))}
          </ul>
        </section>

        <section className="section">
          <h3>Latest meetings</h3>
          <ul className="rows">
            {data.recent.map((m) => (
              <li key={m.id}>{match(m)}</li>
            ))}
          </ul>
        </section>

        <section className="section">
          <h3>Where they met</h3>
          <ul className="rows">
            {data.competitions.map((c) => (
              <li key={c.name} className="row-flex">
                {c.name}
                <span className="num">{c.matches}</span>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </aside>
  );
}
