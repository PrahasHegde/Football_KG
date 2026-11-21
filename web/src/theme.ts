import type { GNode, Label } from "./types";

export type Mode = "light" | "dark";

/*
 * Categorical colours come from a validated 8-slot palette (light & dark steps of the same hues,
 * checked for colour-blind separation). Node types also get distinct SHAPES, so identity never
 * rests on colour alone.
 */
const SLOTS: Record<Mode, string[]> = {
  light: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
  dark: ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#0ca30c", "#9085e9", "#e66767"],
};
const NEUTRAL: Record<Mode, string> = { light: "#8f8d86", dark: "#8a897f" };

const LABEL_SLOT: Record<Label, number | null> = {
  Team: 0,
  Player: 1,
  City: 2,
  FormerName: 4,
  Country: 5,
  Tournament: 6,
  Goal: 7,
  Match: null, // the most numerous type recedes to neutral
};

export const LABEL_ORDER: Label[] = ["Team", "Player", "Match", "Goal", "Tournament", "Country", "City", "FormerName"];

export type Shape = "circle" | "ring" | "square" | "rounded" | "diamond" | "hexagon" | "triangle";
export const LABEL_SHAPE: Record<Label, Shape> = {
  Team: "circle",
  Player: "ring",
  Match: "rounded",
  Goal: "diamond",
  Tournament: "hexagon",
  Country: "square",
  City: "triangle",
  FormerName: "circle",
};

export function labelColor(label: Label, mode: Mode): string {
  const slot = LABEL_SLOT[label];
  return slot === null ? NEUTRAL[mode] : SLOTS[mode][slot];
}

/** Community colours: slots 1-7 in fixed order; slot 8 ("Other") is neutral. */
export function communityColor(group: number, mode: Mode): string {
  return group >= 7 ? NEUTRAL[mode] : SLOTS[mode][group];
}

/** Two-series colours (head-to-head): slot 1 and slot 2. */
export const seriesColor = (i: 0 | 1, mode: Mode) => SLOTS[mode][i];

export function nodeColor(n: GNode, mode: Mode): string {
  if (n.group !== undefined) return communityColor(n.group, mode);
  return labelColor(n.label, mode);
}

const BASE_RADIUS: Record<Label, number> = {
  Team: 6.5,
  Player: 4.8,
  Match: 3.6,
  Goal: 3,
  Tournament: 7,
  Country: 5.5,
  City: 4.2,
  FormerName: 4.2,
};

export function nodeRadius(n: GNode): number {
  if (n.elo) {
    // era overview: size by strength
    return Math.max(4, Math.min(17, 4 + ((n.elo - 1250) / 1000) * 13));
  }
  const extra = n.label === "Match" || n.label === "Goal" ? 0 : Math.log10(1 + (n.degree ?? 1)) * 1.6;
  return Math.min(16, BASE_RADIUS[n.label] + extra);
}

/** Canvas ink for each mode (the canvas can't read CSS variables cheaply per frame). */
export const CANVAS: Record<
  Mode,
  { text: string; textStrong: string; pill: string; ring: string; edge: string; edgeDim: string; accent: string; halo: string }
> = {
  light: {
    text: "#3d3c38",
    textStrong: "#0b0b0b",
    pill: "rgba(252,252,251,0.88)",
    ring: "#fcfcfb",
    edge: "rgba(110,108,100,0.28)",
    edgeDim: "rgba(110,108,100,0.07)",
    accent: "#0b8a5e",
    halo: "rgba(11,11,11,0.10)",
  },
  dark: {
    text: "#c9c8bf",
    textStrong: "#ffffff",
    pill: "rgba(24,24,23,0.85)",
    ring: "#1a1a19",
    edge: "rgba(200,198,188,0.22)",
    edgeDim: "rgba(200,198,188,0.05)",
    accent: "#34d399",
    halo: "rgba(255,255,255,0.14)",
  },
};

/** Short code drawn inside team nodes: "Brazil" -> "BRA", "South Korea" -> "SKO". */
export function teamCode(name: string): string {
  const words = name.replace(/[^\p{L}\s]/gu, "").split(/\s+/).filter((w) => w && w[0] === w[0].toUpperCase());
  if (words.length >= 2) return (words[0][0] + words[1].slice(0, 2)).toUpperCase();
  return name.slice(0, 3).toUpperCase();
}

export function drawShape(ctx: CanvasRenderingContext2D, shape: Shape, x: number, y: number, r: number) {
  ctx.beginPath();
  switch (shape) {
    case "square":
      ctx.rect(x - r * 0.88, y - r * 0.88, r * 1.76, r * 1.76);
      break;
    case "rounded":
      ctx.roundRect(x - r * 0.9, y - r * 0.9, r * 1.8, r * 1.8, r * 0.45);
      break;
    case "diamond":
      ctx.moveTo(x, y - r * 1.15);
      ctx.lineTo(x + r * 1.15, y);
      ctx.lineTo(x, y + r * 1.15);
      ctx.lineTo(x - r * 1.15, y);
      ctx.closePath();
      break;
    case "triangle":
      ctx.moveTo(x, y - r * 1.15);
      ctx.lineTo(x + r * 1.05, y + r * 0.75);
      ctx.lineTo(x - r * 1.05, y + r * 0.75);
      ctx.closePath();
      break;
    case "hexagon":
      for (let i = 0; i < 6; i++) {
        const a = (Math.PI / 3) * i + Math.PI / 6;
        const px = x + r * 1.05 * Math.cos(a), py = y + r * 1.05 * Math.sin(a);
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      }
      ctx.closePath();
      break;
    default:
      ctx.arc(x, y, r, 0, 2 * Math.PI);
  }
}

export const fmt = (n: number | null | undefined) =>
  n === null || n === undefined ? "–" : Math.round(n).toLocaleString("en-US");

export const pct = (x: number | null | undefined) => (x === null || x === undefined ? "–" : `${Math.round(x * 100)}%`);
