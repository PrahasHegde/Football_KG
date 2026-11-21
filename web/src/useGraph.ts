import { useCallback, useRef, useState } from "react";
import type { GEdge, GNode, Subgraph } from "./types";

export const idOf = (end: string | GNode) => (typeof end === "string" ? end : end.id);

export interface GraphData {
  nodes: GNode[];
  links: GEdge[];
}

/**
 * Canvas contents. Node and link objects are kept stable across updates so the force
 * simulation keeps their positions; React only sees fresh arrays.
 */
export function useGraph() {
  const nodes = useRef(new Map<string, GNode>());
  const links = useRef(new Map<string, GEdge>());
  const [data, setData] = useState<GraphData>({ nodes: [], links: [] });

  const commit = useCallback(() => {
    setData({ nodes: [...nodes.current.values()], links: [...links.current.values()] });
  }, []);

  const merge = useCallback(
    (sub: Subgraph, anchorId?: string) => {
      const anchor = anchorId ? nodes.current.get(anchorId) : undefined;
      for (const n of sub.nodes) {
        const existing = nodes.current.get(n.id);
        if (existing) {
          existing.name = n.name;
          existing.degree = n.degree ?? existing.degree;
          if (n.group !== undefined) existing.group = n.group;
          if (n.elo !== undefined) existing.elo = n.elo;
          continue;
        }
        const fresh: GNode = { ...n };
        if (anchor?.x !== undefined && anchor.y !== undefined) {
          // start new neighbours next to the node they came from, so expansion "blooms" out
          fresh.x = anchor.x + (Math.random() - 0.5) * 30;
          fresh.y = anchor.y + (Math.random() - 0.5) * 30;
        }
        nodes.current.set(n.id, fresh);
      }
      for (const e of sub.edges) {
        if (links.current.has(e.id)) continue;
        if (!nodes.current.has(idOf(e.source)) || !nodes.current.has(idOf(e.target))) continue;
        links.current.set(e.id, { ...e, source: idOf(e.source), target: idOf(e.target) });
      }
      commit();
    },
    [commit],
  );

  /** Swap the canvas contents. With keepPositions, nodes that survive keep their place (smooth morphs). */
  const replace = useCallback(
    (sub: Subgraph, keepPositions = false) => {
      const old = keepPositions ? new Map(nodes.current) : null;
      nodes.current.clear();
      links.current.clear();
      if (old) {
        for (const n of sub.nodes) {
          const prev = old.get(n.id);
          nodes.current.set(n.id, prev ? Object.assign(prev, n) : { ...n });
        }
      }
      merge(sub);
    },
    [merge],
  );

  const remove = useCallback(
    (id: string) => {
      nodes.current.delete(id);
      for (const [k, l] of links.current) {
        if (idOf(l.source) === id || idOf(l.target) === id) links.current.delete(k);
      }
      commit();
    },
    [commit],
  );

  const clear = useCallback(() => replace({ nodes: [], edges: [] }), [replace]);
  const has = useCallback((id: string) => nodes.current.has(id), []);
  const ids = useCallback(() => [...nodes.current.keys()], []);
  /** Drop overview community colours so nodes fall back to label colours. */
  const ungroup = useCallback(() => {
    for (const n of nodes.current.values()) {
      delete n.group;
      delete n.elo;
    }
    commit();
  }, [commit]);

  return { data, merge, replace, remove, clear, has, ids, ungroup };
}
