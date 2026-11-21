export type Label =
  | "Team"
  | "Player"
  | "Match"
  | "Goal"
  | "Tournament"
  | "City"
  | "Country"
  | "FormerName";

export interface GNode {
  id: string;
  label: Label;
  name: string;
  degree?: number;
  group?: number;
  elo?: number | null;
  // set by the force simulation
  x?: number;
  y?: number;
  fx?: number;
  fy?: number;
}

export interface GEdge {
  id: string;
  source: string | GNode;
  target: string | GNode;
  type: string;
  side?: string;
  goals?: number;
  weight?: number;
  virtual?: boolean;
}

export interface Subgraph {
  nodes: GNode[];
  edges: GEdge[];
  truncated?: Record<string, number>;
  communities?: string[];
  era?: { start: number; end: number; min_meetings: number };
}

export interface MatchRow {
  id: string;
  date: string;
  home_team: string;
  away_team: string;
  home_score: number;
  away_score: number;
  tournament: string;
  upset?: boolean;
  winner?: string | null;
  winner_win_exp?: number | null;
}

export interface H2H {
  a: { id: string; name: string };
  b: { id: string; name: string };
  summary: { matches: number; a_wins: number; b_wins: number; draws: number; a_goals: number; b_goals: number; first: string; last: string };
  by_decade: { decade: number; a_wins: number; draws: number; b_wins: number }[];
  biggest_wins: { a: MatchRow[]; b: MatchRow[] };
  recent: MatchRow[];
  competitions: { name: string; matches: number }[];
  elo: { a: { year: number; elo: number }[]; b: { year: number; elo: number }[] };
  graph: Subgraph;
}

export interface Discover {
  year: number;
  top_elo: { id: string; name: string; elo: number; change: number }[];
  peaks: { id: string; name: string; elo: number; date: string }[];
  upsets: MatchRow[];
  rivalries: { a: string; b: string; a_name: string; b_name: string; meetings: number; since: number }[];
  central: { id: string; name: string; score: number }[];
}

export interface PathResult extends Subgraph {
  found: boolean;
  reason?: string;
  path?: string[];
}

export interface SearchHit {
  id: string;
  label: Label;
  name: string;
  degree: number;
  team?: string | null;
}

export interface RelCount {
  type: string;
  direction: "in" | "out";
  count: number;
}

export interface Entity {
  id: string;
  label: Label;
  name: string;
  degree: number;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  details: Record<string, any>;
  relationships: RelCount[];
}

export interface Meta {
  nodes: number;
  edges: number;
  labels: { label: Label; count: number }[];
  relationships: { type: string; count: number }[];
  first_match: string;
  last_match: string;
  agent: { model: string; provider?: string; api_key_set: boolean };
}

export interface SqlResult {
  columns: string[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
}

export type ChatPart =
  | { kind: "text"; text: string }
  | { kind: "thinking"; text: string }
  | {
      kind: "tool";
      id: string;
      name: string;
      input: Record<string, unknown>;
      status: "running" | "ok" | "error";
      summary?: string;
      result?: SqlResult | null;
    }
  | { kind: "graph"; caption: string; nodes: number };

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text?: string;
  parts: ChatPart[];
  pending?: boolean;
  error?: string;
}

export type AgentEvent =
  | { type: "conversation"; id: string }
  | { type: "text"; delta: string }
  | { type: "thinking"; delta: string }
  | { type: "tool_start"; id: string; name: string; input: Record<string, unknown> }
  | { type: "tool_result"; id: string; ok: boolean; summary: string; result?: SqlResult | null }
  | { type: "graph"; nodes: GNode[]; edges: GEdge[]; caption: string; focus?: string | null; path?: string[] }
  | { type: "done"; usage: Record<string, number> }
  | { type: "error"; message: string };
