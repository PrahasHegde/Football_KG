import type { AgentEvent, Discover, Entity, H2H, Meta, PathResult, SearchHit, Subgraph } from "./types";

async function get<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail ?? `${r.status} ${r.statusText}`);
  }
  return r.json();
}

const enc = encodeURIComponent;

export const api = {
  meta: () => get<Meta>("/api/meta"),
  search: (q: string, label?: string) =>
    get<SearchHit[]>(`/api/search?q=${enc(q)}${label ? `&label=${label}` : ""}`),
  entity: (id: string) => get<Entity>(`/api/entity/${enc(id)}`),
  expand: (id: string, opts: { limit?: number; type?: string; direction?: string } = {}) => {
    const p = new URLSearchParams({ limit: String(opts.limit ?? 12) });
    if (opts.type) p.set("type", opts.type);
    if (opts.direction) p.set("direction", opts.direction);
    return get<Subgraph>(`/api/expand/${enc(id)}?${p}`);
  },
  clubUniverse: () => get<Subgraph>("/api/clubs/universe"),
  pipeline: () => get<Subgraph>("/api/clubs/pipeline"),
  overview: (era?: { start: number; end: number }) =>
    get<Subgraph>(`/api/overview?top=${era ? 140 : 200}${era ? `&start=${era.start}&end=${era.end}` : ""}`),
  h2h: (a: string, b: string) => get<H2H>(`/api/h2h?a=${enc(a)}&b=${enc(b)}`),
  discover: () => get<Discover>("/api/discover"),
  path: (src: string, dst: string, viaTeams: boolean) =>
    get<PathResult>(`/api/path?src=${enc(src)}&dst=${enc(dst)}&via_teams=${viaTeams}`),
  subgraph: async (ids: string[]) => {
    const r = await fetch("/api/subgraph", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return (await r.json()) as Subgraph;
  },
  resetChat: (conversationId: string) =>
    fetch(`/api/chat/${enc(conversationId)}`, { method: "DELETE" }),
};

/** POST a question and invoke `onEvent` for every server-sent event until the stream ends. */
export async function streamChat(
  body: { message: string; conversation_id: string | null },
  onEvent: (e: AgentEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const r = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!r.ok || !r.body) throw new Error(`Chat request failed (${r.status})`);
  const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += value;
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const data = chunk
        .split("\n")
        .filter((l) => l.startsWith("data: "))
        .map((l) => l.slice(6))
        .join("\n");
      if (data) onEvent(JSON.parse(data) as AgentEvent);
    }
  }
}

/** Mirrors the Python slug in src/build_kg.py. */
export const slug = (s: string) => s.trim().toLowerCase().replaceAll(" ", "_");
export const teamId = (name: string) => `T:${slug(name)}`;
