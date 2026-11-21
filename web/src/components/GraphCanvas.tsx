import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import ForceGraph2D, { type ForceGraphMethods, type LinkObject, type NodeObject } from "react-force-graph-2d";
import { loadedImage, nodeImageUrl, requestPlayerPhotos, useMediaVersion } from "../media";
import { CANVAS, LABEL_SHAPE, drawShape, nodeColor, nodeRadius, teamCode, type Mode } from "../theme";
import type { GEdge, GNode, Label } from "../types";
import { idOf, type GraphData } from "../useGraph";

type N = NodeObject<GNode>;
type L = LinkObject<GNode, GEdge>;

export interface GraphHandle {
  fit: (ms?: number) => void;
  focus: (id: string) => void;
  reheat: () => void;
  fitAfterLayout: () => void;
  exportPng: (filename: string) => void;
}

interface Props {
  data: GraphData;
  mode: Mode;
  selectedId: string | null;
  highlight: Set<string>;
  hiddenLabels: Set<Label>;
  frozen: boolean;
  onSelect: (node: GNode | null) => void;
  onExpand: (node: GNode) => void;
}

export const GraphCanvas = forwardRef<GraphHandle, Props>(function GraphCanvas(
  { data, mode, selectedId, highlight, hiddenLabels, frozen, onSelect, onExpand },
  ref,
) {
  const fg = useRef<ForceGraphMethods<N, L> | undefined>(undefined);
  const wrap = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 800, h: 600 });
  const [hoverId, setHoverId] = useState<string | null>(null);
  const pendingFit = useRef(true);
  const lastClick = useRef<{ id: string; t: number } | null>(null);
  const ink = CANVAS[mode];
  const mediaVersion = useMediaVersion();

  // fetch photos for the most connected players on the canvas
  useEffect(() => {
    const players = data.nodes.filter((n) => n.label === "Player").sort((a, b) => (b.degree ?? 0) - (a.degree ?? 0));
    if (players.length) requestPlayerPhotos(players.slice(0, 40).map((n) => n.id));
  }, [data]);

  useEffect(() => {
    if (!wrap.current) return;
    const ro = new ResizeObserver(([e]) => setSize({ w: e.contentRect.width, h: e.contentRect.height }));
    ro.observe(wrap.current);
    return () => ro.disconnect();
  }, []);

  // zoomToFit on a tiny graph zooms in absurdly far; cap it.
  const fit = useCallback((ms = 500) => {
    const g = fg.current;
    if (!g) return;
    g.zoomToFit(ms, 80);
    window.setTimeout(() => {
      if (g.zoom() > 2.6) g.zoom(2.6, 250);
    }, ms + 30);
  }, []);

  // physics tuned for a few hundred nodes; re-applied whenever the data changes
  useEffect(() => {
    const g = fg.current;
    if (!g) return;
    const overview = data.links.length > 0 && data.links.every((l) => l.virtual);
    const maxW = Math.max(1, ...data.links.map((l) => l.weight ?? 1));
    g.d3Force("charge")?.strength((n: N) => (overview ? -150 : -80 - nodeRadius(n) * 12)).distanceMax(overview ? 420 : 420);
    g.d3Force("link")
      ?.distance((l: L) => (l.virtual ? 95 - 55 * ((l.weight ?? 1) / maxW) : 42))
      .strength((l: L) => (l.virtual ? 0.04 + 0.5 * ((l.weight ?? 1) / maxW) : 0.6));
    g.d3ReheatSimulation();
    const t = window.setTimeout(() => {
      if (pendingFit.current) fit(600);
    }, 1400);
    return () => window.clearTimeout(t);
  }, [data, fit]);

  useImperativeHandle(ref, () => ({
    fit,
    focus: (id: string) => {
      const n = data.nodes.find((x) => x.id === id);
      if (n?.x !== undefined && n.y !== undefined) {
        fg.current?.centerAt(n.x, n.y, 600);
        fg.current?.zoom(2.4, 600);
      }
    },
    reheat: () => fg.current?.d3ReheatSimulation(),
    fitAfterLayout: () => {
      pendingFit.current = true;
    },
    exportPng: (filename: string) => {
      const src = wrap.current?.querySelector("canvas");
      if (!src) return;
      const out = document.createElement("canvas");
      out.width = src.width;
      out.height = src.height;
      const ctx = out.getContext("2d")!;
      ctx.fillStyle = mode === "light" ? "#fcfcfb" : "#141413";
      ctx.fillRect(0, 0, out.width, out.height);
      ctx.drawImage(src, 0, 0);
      const a = document.createElement("a");
      a.download = filename;
      a.href = out.toDataURL("image/png");
      a.click();
    },
  }));

  const neighbours = useMemo(() => {
    const m = new Map<string, Set<string>>();
    for (const l of data.links) {
      const a = idOf(l.source), b = idOf(l.target);
      if (!m.has(a)) m.set(a, new Set());
      if (!m.has(b)) m.set(b, new Set());
      m.get(a)!.add(b);
      m.get(b)!.add(a);
    }
    return m;
  }, [data]);

  const focusSet = useMemo(() => {
    if (!hoverId) return null;
    return new Set([hoverId, ...(neighbours.get(hoverId) ?? [])]);
  }, [hoverId, neighbours]);

  const isPathLink = useCallback(
    (l: L) => highlight.size > 0 && highlight.has(idOf(l.source as string | GNode)) && highlight.has(idOf(l.target as string | GNode)),
    [highlight],
  );

  const paintNode = useCallback(
    (node: N, ctx: CanvasRenderingContext2D, scale: number) => {
      const x = node.x ?? 0, y = node.y ?? 0;
      const r = nodeRadius(node);
      const color = nodeColor(node, mode);
      const shape = LABEL_SHAPE[node.label];
      const selected = node.id === selectedId;
      const hi = highlight.has(node.id);
      const dim = (focusSet && !focusSet.has(node.id)) || (highlight.size > 0 && !hi && !selected && !focusSet);

      ctx.globalAlpha = dim ? 0.16 : 1;

      // halo for selection / highlighted path
      if (selected || hi) {
        drawShape(ctx, shape, x, y, r + (selected ? 7 : 5) / Math.sqrt(scale));
        ctx.fillStyle = selected ? ink.halo : `${color}33`;
        ctx.fill();
      }

      // body + a surface-coloured ring so overlapping marks stay separable
      drawShape(ctx, shape, x, y, r);
      if (node.label === "FormerName") {
        ctx.fillStyle = ink.ring;
        ctx.fill();
        ctx.setLineDash([2 / scale, 2 / scale]);
        ctx.lineWidth = 1.6 / scale;
        ctx.strokeStyle = color;
        ctx.stroke();
        ctx.setLineDash([]);
      } else {
        ctx.fillStyle = color;
        ctx.fill();
        ctx.lineWidth = (selected ? 2.4 : 1.4) / scale;
        ctx.strokeStyle = selected ? ink.textStrong : ink.ring;
        ctx.stroke();
      }
      // flag (teams) or photo (players) clipped into the node; a coloured rim keeps the
      // type / community colour readable around the picture
      const img = r * scale >= 4 ? loadedImage(nodeImageUrl(node)) : undefined;
      if (img) {
        const rim = Math.max(1.4 / scale, r * 0.16);
        const inner = r - rim;
        ctx.save();
        ctx.beginPath();
        ctx.arc(x, y, inner, 0, 2 * Math.PI);
        ctx.clip();
        const s = Math.max((2 * inner) / img.naturalWidth, (2 * inner) / img.naturalHeight);
        const w = img.naturalWidth * s, h = img.naturalHeight * s;
        // photos: bias towards the top, where faces are; flags: centred
        const dy = node.label === "Player" ? Math.min(0, -(h - 2 * inner) * 0.18) : -(h - 2 * inner) / 2;
        ctx.drawImage(img, x - w / 2, y - inner + dy, w, h);
        ctx.restore();
      } else if (shape === "ring") {
        // players without a photo: a small inner dot reads as a "person" marker
        ctx.beginPath();
        ctx.arc(x, y, r * 0.38, 0, 2 * Math.PI);
        ctx.fillStyle = ink.ring;
        ctx.fill();
      }

      // team code inside big team circles (when there is no flag)
      if (node.label === "Team" && !img && r * scale >= 10) {
        const fs = Math.min(r * 0.78, 9);
        ctx.font = `700 ${fs}px Inter, system-ui, sans-serif`;
        ctx.fillStyle = "#ffffff";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(teamCode(node.name), x, y + fs * 0.05);
      }

      const minor = node.label === "Match" || node.label === "Goal";
      const showLabel =
        selected || hi || node.id === hoverId || (focusSet?.has(node.id) ?? false) ||
        (minor ? scale >= 3.4 : scale > 1.9 || r * scale >= 7);
      if (showLabel) {
        const fontSize = Math.max(10.5 / scale, 1.5);
        ctx.font = `${selected || hi ? 600 : 500} ${fontSize}px Inter, system-ui, sans-serif`;
        const text = node.name.length > 34 ? `${node.name.slice(0, 32)}…` : node.name;
        const w = ctx.measureText(text).width;
        const ty = y + r * 1.15 + 3 / scale;
        const padX = 3.5 / scale, padY = 1.5 / scale;
        ctx.fillStyle = ink.pill;
        ctx.beginPath();
        ctx.roundRect(x - w / 2 - padX, ty - padY, w + padX * 2, fontSize + padY * 2, 4 / scale);
        ctx.fill();
        ctx.fillStyle = selected ? ink.textStrong : ink.text;
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        ctx.fillText(text, x, ty);
      }
      ctx.globalAlpha = 1;
    },
    // mediaVersion: repaint when flags/photos finish loading
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selectedId, highlight, focusSet, hoverId, mode, ink, mediaVersion],
  );

  const linkColor = useCallback(
    (l: L) => {
      const a = idOf(l.source as string | GNode), b = idOf(l.target as string | GNode);
      if (focusSet) return a === hoverId || b === hoverId ? ink.accent : ink.edgeDim;
      if (highlight.size) return isPathLink(l) ? ink.accent : ink.edgeDim;
      return ink.edge;
    },
    [focusSet, hoverId, highlight, isPathLink, ink],
  );

  const handleClick = useCallback(
    (node: N) => {
      const now = Date.now();
      if (lastClick.current && lastClick.current.id === node.id && now - lastClick.current.t < 320) {
        lastClick.current = null;
        onExpand(node);
        return;
      }
      lastClick.current = { id: node.id, t: now };
      onSelect(node);
    },
    [onExpand, onSelect],
  );

  return (
    <div ref={wrap} className="graph-canvas">
      <ForceGraph2D<GNode, GEdge>
        ref={fg}
        width={size.w}
        height={size.h}
        graphData={data}
        backgroundColor="rgba(0,0,0,0)"
        nodeId="id"
        nodeRelSize={1}
        nodeVal={(n) => nodeRadius(n) ** 2}
        nodeLabel={(n) =>
          `<div class="tip"><b>${escapeHtml(n.name)}</b><span>${n.label}${n.elo ? ` · Elo ${Math.round(n.elo)}` : ""}</span></div>`
        }
        nodeCanvasObject={paintNode}
        nodeVisibility={(n) => !hiddenLabels.has(n.label)}
        linkVisibility={(l) => {
          const s = l.source as GNode | string, t = l.target as GNode | string;
          return !(typeof s === "object" && hiddenLabels.has(s.label)) && !(typeof t === "object" && hiddenLabels.has(t.label));
        }}
        linkColor={linkColor}
        linkWidth={(l) => (isPathLink(l) ? 2.2 : l.virtual ? Math.min(4, 0.4 + Math.log10(l.weight ?? 1) * 1.3) : 1)}
        linkCurvature={(l) => (l.virtual ? 0.12 : 0)}
        linkDirectionalArrowLength={(l) => (l.virtual ? 0 : 3.2)}
        linkDirectionalArrowRelPos={1}
        linkDirectionalParticles={(l) => (isPathLink(l) ? 3 : 0)}
        linkDirectionalParticleSpeed={0.007}
        linkDirectionalParticleWidth={3.2}
        linkDirectionalParticleColor={() => ink.accent}
        linkLabel={(l) =>
          `<div class="tip"><b>${l.type}</b>${l.weight ? `<span>${l.weight} meetings</span>` : ""}${l.side ? `<span>${l.side}, ${l.goals ?? 0} goals</span>` : ""}</div>`
        }
        cooldownTicks={frozen ? 0 : 300}
        cooldownTime={6000}
        d3VelocityDecay={0.32}
        onEngineStop={() => {
          if (pendingFit.current) {
            pendingFit.current = false;
            fit(500);
          }
        }}
        onNodeClick={handleClick}
        onNodeRightClick={(n) => onExpand(n)}
        onNodeHover={(n) => setHoverId(n?.id ?? null)}
        onNodeDragEnd={(n) => {
          n.fx = n.x;
          n.fy = n.y;
        }}
        onBackgroundClick={() => onSelect(null)}
      />
    </div>
  );
});

function escapeHtml(s: string) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}
