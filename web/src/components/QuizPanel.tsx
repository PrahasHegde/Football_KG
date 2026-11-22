import { Calendar, Flag as FlagIcon, Gamepad2, Globe2, Goal, Lightbulb, RotateCcw, Shield, Target, Trophy, Type, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { requestPlayerPhotos } from "../media";
import type { SearchHit } from "../types";
import { Avatar } from "./Flag";
import { SearchBox } from "./SearchBox";

type Level = "easy" | "medium" | "hard";
interface Clue {
  icon: string;
  label: string;
  text: string;
}
interface Quiz {
  token: string;
  level: Level;
  clues: Clue[];
}
interface Answer {
  id: string;
  name: string;
  team: string;
}
interface Stats {
  score: number;
  streak: number;
  best: number;
  played: number;
  won: number;
}

const ICONS: Record<string, typeof Globe2> = {
  globe: Globe2, calendar: Calendar, trophy: Trophy, target: Target, goal: Goal, shield: Shield, flag: FlagIcon, type: Type,
};
const KEY = "fkg.quiz";
const EMPTY: Stats = { score: 0, streak: 0, best: 0, played: 0, won: 0 };

function loadStats(): Stats {
  try {
    return { ...EMPTY, ...JSON.parse(localStorage.getItem(KEY) ?? "{}") };
  } catch {
    return EMPTY;
  }
}

/** "Who am I?": clues from the graph are revealed one by one; fewer clues and wrong guesses = more points. */
export function QuizPanel({ onOpen }: { onOpen: (id: string) => void }) {
  const [level, setLevel] = useState<Level>("easy");
  const [quiz, setQuiz] = useState<Quiz | null>(null);
  const [shown, setShown] = useState(1);
  const [wrong, setWrong] = useState<string[]>([]);
  const [result, setResult] = useState<{ correct: boolean; answer: Answer; points: number } | null>(null);
  const [stats, setStats] = useState<Stats>(loadStats);
  const [error, setError] = useState<string | null>(null);
  const [shake, setShake] = useState(false);

  const save = (s: Stats) => {
    setStats(s);
    try {
      localStorage.setItem(KEY, JSON.stringify(s));
    } catch {
      /* storage unavailable */
    }
  };

  const start = useCallback(async (lv: Level) => {
    setError(null);
    setResult(null);
    setWrong([]);
    setShown(1);
    try {
      setQuiz((await api.quizNew(lv)) as Quiz);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    start(level);
  }, [level, start]);

  const points = Math.max(1, 9 - shown - wrong.length);

  const finish = (correct: boolean, answer: Answer) => {
    requestPlayerPhotos([answer.id]);
    const gained = correct ? points : 0;
    setResult({ correct, answer, points: gained });
    const streak = correct ? stats.streak + 1 : 0;
    save({
      score: stats.score + gained, streak, best: Math.max(stats.best, streak),
      played: stats.played + 1, won: stats.won + (correct ? 1 : 0),
    });
  };

  const guess = async (h: SearchHit) => {
    if (!quiz || result) return;
    const r = await api.quizGuess(quiz.token, h.id);
    if (r.correct && r.answer) finish(true, r.answer);
    else {
      setWrong((w) => [...w, h.name]);
      setShake(true);
      window.setTimeout(() => setShake(false), 450);
      if (shown < (quiz.clues.length ?? 8)) setShown((n) => n + 1); // a wrong guess also reveals a clue
    }
  };

  const giveUp = async () => {
    if (!quiz || result) return;
    const r = await api.quizGuess(quiz.token, null, true);
    if (r.answer) finish(false, r.answer);
  };

  return (
    <div className="quiz">
      <header className="quiz__head">
        <h3><Gamepad2 size={16} /> Guess the player</h3>
        <div className="quiz__levels" role="tablist">
          {(["easy", "medium", "hard"] as Level[]).map((lv) => (
            <button key={lv} role="tab" aria-selected={level === lv} className={level === lv ? "is-active" : ""} onClick={() => setLevel(lv)}>
              {lv}
            </button>
          ))}
        </div>
      </header>
      <div className="quiz__stats">
        <span><b>{stats.score}</b> points</span>
        <span><b>{stats.streak}</b> streak</span>
        <span><b>{stats.best}</b> best</span>
        <span><b>{stats.played ? Math.round((100 * stats.won) / stats.played) : 0}%</b> solved</span>
      </div>

      {error && <p className="msg__error">{error}</p>}

      {quiz && (
        <ol className="clues">
          {quiz.clues.map((c, i) => {
            const Icon = ICONS[c.icon] ?? Lightbulb;
            const open = i < shown || result !== null;
            return (
              <li key={i} className={`clue ${open ? "is-open" : ""}`} style={{ animationDelay: `${i * 40}ms` }}>
                <span className="clue__icon"><Icon size={14} /></span>
                <span className="clue__body">
                  <span className="clue__label">Clue {i + 1} · {c.label}</span>
                  <span className="clue__text">{open ? c.text : "Hidden"}</span>
                </span>
              </li>
            );
          })}
        </ol>
      )}

      {quiz && !result && (
        <div className={`quiz__guess ${shake ? "is-shaking" : ""}`}>
          <SearchBox compact label="Player" placeholder="Your guess: type a player's name…" onPick={guess} />
          {wrong.length > 0 && (
            <div className="quiz__wrong">
              {wrong.map((w, i) => <span key={i} className="chip-x"><X size={11} /> {w}</span>)}
            </div>
          )}
          <div className="quiz__actions">
            <button className="btn btn--sm" disabled={shown >= quiz.clues.length} onClick={() => setShown((n) => n + 1)}>
              <Lightbulb size={14} /> Next clue
            </button>
            <button className="btn btn--sm btn--ghost" onClick={giveUp}>Give up</button>
            <span className="muted small quiz__worth">worth <b>{points}</b> pts</span>
          </div>
        </div>
      )}

      {result && (
        <div className={`quiz__result ${result.correct ? "is-win" : "is-loss"}`}>
          <span className="quiz__avatar"><Avatar id={result.answer.id} size={56} /></span>
          <div className="grow">
            <p className="quiz__verdict">{result.correct ? `Correct! +${result.points}` : "Out of guesses"}</p>
            <p><b>{result.answer.name}</b> <span className="muted">· {result.answer.team}</span></p>
          </div>
          <div className="quiz__result-actions">
            <button className="btn btn--sm" onClick={() => onOpen(result.answer.id)}>Open</button>
            <button className="btn btn--sm is-on" onClick={() => start(level)}><RotateCcw size={14} /> Next</button>
          </div>
        </div>
      )}
    </div>
  );
}
