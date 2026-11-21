import { useEffect, useMemo, useRef, useState } from "react";

/* Charts read their colours from CSS tokens (--series-1, --series-2, --neutral …) so they follow the theme. */

export interface Series {
  name: string;
  color: string; // a CSS colour or var(--token)
  points: { x: number; y: number }[];
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(320);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

function niceTicks(min: number, max: number, count = 4) {
  const span = max - min || 1;
  const step0 = span / count;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0) ?? step0;
  const ticks = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) ticks.push(v);
  return ticks;
}

/** Line chart with crosshair hover. One series: no legend (the title names it). Two: legend + end labels. */
export function LineChart({
  series,
  height = 150,
  formatY = (v: number) => Math.round(v).toString(),
  marker,
  ariaLabel,
}: {
  series: Series[];
  height?: number;
  formatY?: (v: number) => string;
  marker?: { x: number; y: number; label: string };
  ariaLabel: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hoverX, setHoverX] = useState<number | null>(null);
  const multi = series.length > 1;
  const pad = { l: 38, r: multi ? 64 : 12, t: 10, b: 22 };

  const { xs, x0, x1, y0, y1 } = useMemo(() => {
    const all = series.flatMap((s) => s.points);
    const xs = [...new Set(all.map((p) => p.x))].sort((a, b) => a - b);
    const ys = all.map((p) => p.y);
    const lo = Math.min(...ys), hi = Math.max(...ys);
    const padY = (hi - lo) * 0.08 || 50;
    return { xs, x0: xs[0], x1: xs[xs.length - 1], y0: lo - padY, y1: hi + padY };
  }, [series]);

  if (!xs.length) return null;
  const iw = Math.max(10, width - pad.l - pad.r), ih = height - pad.t - pad.b;
  const sx = (x: number) => pad.l + ((x - x0) / (x1 - x0 || 1)) * iw;
  const sy = (y: number) => pad.t + (1 - (y - y0) / (y1 - y0 || 1)) * ih;
  const yTicks = niceTicks(y0, y1, 3);
  const xTicks = niceTicks(x0, x1, Math.max(2, Math.floor(iw / 70)));
  const path = (pts: Series["points"]) => pts.map((p, i) => `${i ? "L" : "M"}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join("");

  const valueAt = (s: Series, x: number) => s.points.find((p) => p.x === x)?.y;
  const tipLeft = hoverX !== null ? Math.min(Math.max(sx(hoverX) - 60, 0), width - 130) : 0;

  return (
    <div className="chart" ref={ref}>
      {multi && (
        <div className="chart__legend">
          {series.map((s) => (
            <span key={s.name}>
              <i style={{ background: s.color }} /> {s.name}
            </span>
          ))}
        </div>
      )}
      <svg
        width={width}
        height={height}
        role="img"
        aria-label={ariaLabel}
        onMouseMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const x = x0 + ((e.clientX - rect.left - pad.l) / iw) * (x1 - x0);
          const nearest = xs.reduce((a, b) => (Math.abs(b - x) < Math.abs(a - x) ? b : a));
          setHoverX(nearest);
        }}
        onMouseLeave={() => setHoverX(null)}
      >
        {yTicks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={pad.l + iw} y1={sy(t)} y2={sy(t)} className="chart__grid" />
            <text x={pad.l - 6} y={sy(t)} className="chart__tick" textAnchor="end" dominantBaseline="middle">
              {formatY(t)}
            </text>
          </g>
        ))}
        {xTicks.map((t) => (
          <text key={t} x={sx(t)} y={height - 6} className="chart__tick" textAnchor="middle">
            {t}
          </text>
        ))}
        {series.map((s) => (
          <path key={s.name} d={path(s.points)} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        ))}
        {multi &&
          series.map((s) => {
            const last = s.points[s.points.length - 1];
            return (
              <text key={s.name} x={sx(last.x) + 6} y={sy(last.y)} className="chart__direct" dominantBaseline="middle">
                {s.name.length > 10 ? `${s.name.slice(0, 9)}…` : s.name}
              </text>
            );
          })}
        {marker && hoverX === null && (
          <g>
            <circle cx={sx(marker.x)} cy={sy(marker.y)} r={4} fill={series[0].color} className="chart__ring" />
            <text
              x={Math.min(sx(marker.x), pad.l + iw - 40)}
              y={sy(marker.y) - 9}
              className="chart__annot"
              textAnchor="middle"
            >
              {marker.label}
            </text>
          </g>
        )}
        {hoverX !== null && (
          <g>
            <line x1={sx(hoverX)} x2={sx(hoverX)} y1={pad.t} y2={pad.t + ih} className="chart__crosshair" />
            {series.map((s) => {
              const v = valueAt(s, hoverX);
              return v === undefined ? null : <circle key={s.name} cx={sx(hoverX)} cy={sy(v)} r={4} fill={s.color} className="chart__ring" />;
            })}
          </g>
        )}
        <rect x={pad.l} y={pad.t} width={iw} height={ih} fill="transparent" />
      </svg>
      {hoverX !== null && (
        <div className="chart__tip" style={{ left: tipLeft }}>
          <b>{hoverX}</b>
          {series.map((s) => {
            const v = valueAt(s, hoverX);
            return (
              <span key={s.name}>
                <i style={{ background: s.color }} />
                {multi ? `${s.name} ` : ""}
                <strong>{v === undefined ? "–" : formatY(v)}</strong>
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** Wins / draws / wins as one stacked bar, 2px gaps between segments, rounded outer ends. */
export function ResultBar({
  a,
  draws,
  b,
  aName,
  bName,
  height = 14,
  showLabels = true,
}: {
  a: number;
  draws: number;
  b: number;
  aName: string;
  bName: string;
  height?: number;
  showLabels?: boolean;
}) {
  const [hover, setHover] = useState<string | null>(null);
  const total = a + draws + b || 1;
  const segs = [
    { key: "a", v: a, color: "var(--series-1)", label: `${aName} won ${a}` },
    { key: "d", v: draws, color: "var(--neutral)", label: `${draws} draws` },
    { key: "b", v: b, color: "var(--series-2)", label: `${bName} won ${b}` },
  ].filter((s) => s.v > 0);
  return (
    <div className="resultbar">
      <div className="resultbar__track" style={{ height }} onMouseLeave={() => setHover(null)}>
        {segs.map((s) => (
          <div
            key={s.key}
            className="resultbar__seg"
            style={{ flexGrow: s.v / total, background: s.color }}
            onMouseEnter={() => setHover(`${s.label} (${Math.round((s.v / total) * 100)}%)`)}
          />
        ))}
      </div>
      {showLabels && (
        <div className="resultbar__labels">
          {hover ? (
            <span className="muted">{hover}</span>
          ) : (
            <>
              <span><b>{a}</b> {aName}</span>
              <span className="muted">{draws} draws</span>
              <span>{bName} <b>{b}</b></span>
            </>
          )}
        </div>
      )}
    </div>
  );
}

/** Two-way probability split (pre-match Elo expectancy). */
export function ProbBar({ home, homeName, awayName }: { home: number; homeName: string; awayName: string }) {
  return (
    <div className="resultbar">
      <div className="resultbar__track" style={{ height: 10 }}>
        <div className="resultbar__seg" style={{ flexGrow: home, background: "var(--series-1)" }} title={`${homeName} ${Math.round(home * 100)}%`} />
        <div className="resultbar__seg" style={{ flexGrow: 1 - home, background: "var(--series-2)" }} title={`${awayName} ${Math.round((1 - home) * 100)}%`} />
      </div>
      <div className="resultbar__labels">
        <span><b>{Math.round(home * 100)}%</b> {homeName}</span>
        <span>{awayName} <b>{Math.round((1 - home) * 100)}%</b></span>
      </div>
    </div>
  );
}
