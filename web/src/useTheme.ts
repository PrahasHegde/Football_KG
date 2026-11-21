import { useEffect, useState } from "react";
import type { Mode } from "./theme";

const KEY = "football-kg-theme";

function initial(): Mode {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    /* storage unavailable */
  }
  return "light";
}

export function useTheme() {
  const [mode, setMode] = useState<Mode>(initial);
  useEffect(() => {
    document.documentElement.dataset.theme = mode;
    try {
      localStorage.setItem(KEY, mode);
    } catch {
      /* storage unavailable */
    }
  }, [mode]);
  return { mode, toggle: () => setMode((m) => (m === "light" ? "dark" : "light")) };
}
