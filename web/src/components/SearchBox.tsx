import { Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { labelColor, type Mode } from "../theme";
import type { SearchHit } from "../types";
import { Avatar } from "./Flag";

interface Props {
  placeholder?: string;
  onPick: (hit: SearchHit) => void;
  autoFocus?: boolean;
  compact?: boolean;
  shortcut?: boolean;
}

export function SearchBox({ placeholder = "Search teams, players, tournaments…", onPick, autoFocus, compact, shortcut }: Props) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!q.trim()) {
      setHits([]);
      return;
    }
    const ctrl = { cancelled: false };
    const t = setTimeout(() => {
      api.search(q).then((r) => {
        if (!ctrl.cancelled) {
          setHits(r);
          setActive(0);
        }
      }).catch(() => {});
    }, 140);
    return () => {
      ctrl.cancelled = true;
      clearTimeout(t);
    };
  }, [q]);

  useEffect(() => {
    if (!shortcut) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && document.activeElement?.tagName !== "INPUT" && document.activeElement?.tagName !== "TEXTAREA")) {
        e.preventDefault();
        input.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [shortcut]);

  const pick = (h: SearchHit) => {
    onPick(h);
    setQ("");
    setHits([]);
    setOpen(false);
    input.current?.blur();
  };

  return (
    <div className={`search ${compact ? "search--compact" : ""}`}>
      <Search size={15} className="search__icon" />
      <input
        ref={input}
        value={q}
        autoFocus={autoFocus}
        placeholder={placeholder}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") setActive((a) => Math.min(a + 1, hits.length - 1));
          else if (e.key === "ArrowUp") setActive((a) => Math.max(a - 1, 0));
          else if (e.key === "Enter" && hits[active]) pick(hits[active]);
          else if (e.key === "Escape") input.current?.blur();
        }}
      />
      {shortcut && !q && <kbd className="search__kbd">Ctrl K</kbd>}
      {open && hits.length > 0 && (
        <ul className="search__results" role="listbox">
          {hits.map((h, i) => (
            <li
              key={h.id}
              role="option"
              aria-selected={i === active}
              className={i === active ? "is-active" : ""}
              onMouseDown={(e) => {
                e.preventDefault();
                pick(h);
              }}
              onMouseEnter={() => setActive(i)}
            >
              <span className="search__icon-slot">
                <Avatar id={h.id} size={20} />
                <span className="dot" style={{ background: labelColor(h.label, (document.documentElement.dataset.theme as Mode) ?? "light") }} />
              </span>
              <span className="search__name">{h.name}</span>
              <span className="search__meta">{h.team ? `${h.team} · ` : ""}{h.label}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
