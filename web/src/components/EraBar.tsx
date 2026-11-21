import { Pause, Play } from "lucide-react";
import { useEffect, useRef } from "react";

export interface Era {
  start: number;
  end: number;
  label: string;
}

export const ALL_TIME: Era = { start: 1872, end: 2026, label: "All time" };
export const DECADES: Era[] = Array.from({ length: 13 }, (_, i) => {
  const start = 1900 + i * 10;
  return { start, end: start + 9, label: `${start}s` };
});

interface Props {
  era: Era;
  playing: boolean;
  onChange: (era: Era) => void;
  onPlay: (playing: boolean) => void;
}

/** Time-travel through the rivalry network decade by decade. */
export function EraBar({ era, playing, onChange, onPlay }: Props) {
  const timer = useRef<number | null>(null);

  useEffect(() => {
    if (!playing) return;
    const idx = DECADES.findIndex((d) => d.start === era.start && d.end === era.end);
    if (idx === -1) {
      onChange(DECADES[0]);
      return;
    }
    timer.current = window.setTimeout(() => {
      if (idx === DECADES.length - 1) onPlay(false);
      else onChange(DECADES[idx + 1]);
    }, 2600);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [playing, era, onChange, onPlay]);

  const active = (e: Era) => e.start === era.start && e.end === era.end;
  return (
    <div className="erabar" role="toolbar" aria-label="Era">
      <button className={`erabar__play ${playing ? "is-on" : ""}`} onClick={() => onPlay(!playing)} aria-label={playing ? "Pause" : "Play through the decades"}>
        {playing ? <Pause size={14} /> : <Play size={14} />}
      </button>
      <div className="erabar__chips">
        <button className={active(ALL_TIME) ? "is-active" : ""} onClick={() => { onPlay(false); onChange(ALL_TIME); }}>
          All time
        </button>
        {DECADES.map((d) => (
          <button key={d.start} className={active(d) ? "is-active" : ""} onClick={() => { onPlay(false); onChange(d); }}>
            {String(d.start).slice(2)}s
          </button>
        ))}
      </div>
    </div>
  );
}
