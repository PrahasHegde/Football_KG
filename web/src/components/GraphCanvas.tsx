import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import ForceGraph2D, { type ForceGraphMethods, type LinkObject, type NodeObject } from "react-force-graph-2d";
import { loadedImage, nodeImageUrl, requestPlayerPhotos, useMediaVersion } from "../media";
import {
  CANVAS, CONF_HOME, CONF_NAME, LABEL_SHAPE, drawShape, nodeColor, nodeRadius, rgba, teamCode, type Mode,
} from "../theme";
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
  /** overview only: confederation to spotlight (others recede) */
  spotlight?: string | null;
  /** screen pixels on the left covered by a floating panel; fitting keeps the graph clear of it */
  insetLeft?: number;
  onSelect: (node: GNode | null) => void;
  onExpand: (node: GNode) => void;
}

const CURVATURE = 0.14;
const INTRO_MS = 1600;

/** Where an overview node belongs: its own ring position (pipeline) or its cluster's home. */
const homeOf = (n: GNode): [number, number] | undefined =>
  n.hx !== undefined && n.hy !== undefined ? [n.hx, n.hy] : n.conf ? CONF_HOME[n.conf] : undefined;

/** Pull every overview node towards its home. */
function homeForce(strength: number) {
  let nodes: N[] = [];
  const force = (alpha: number) => {
    for (const n of nodes) {
      const home = homeOf(n);
      if (!home || n.x === undefined || n.y === undefined) continue;
      n.vx = (n.vx ?? 0) + (home[0] - n.x) * strength * alpha;
      n.vy = (n.vy ?? 0) + (home[1] - n.y) * strength * alpha;
    }
  };
  force.initialize = (ns: N[]) => {
    nodes = ns;
  };
  return force;
}

/** Keep nodes from overlapping (O(n²), fine for the few hundred nodes of the overview). */
function collideForce(pad: number) {
  let nodes: N[] = [];
  const force = () => {
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        const dx = (b.x ?? 0) - (a.x ?? 0), dy = (b.y ?? 0) - (a.y ?? 0);
        const min = nodeRadius(a) + nodeRadius(b) + pad;
        const d2 = dx * dx + dy * dy;
        if (d2 >= min * min || d2 === 0) continue;
        const d = Math.sqrt(d2), push = ((min - d) / d) * 0.5;
        a.x! -= dx * push; a.y! -= dy * push;
        b.x! += dx * push; b.y! += dy * push;
      }
    }
  };
  force.initialize = (ns: N[]) => {
    nodes = ns;
  };
  return force;
}

/** Same control point force-graph uses for curved links, so hover areas match what we draw. */
function controlPoint(s: N, t: N, curvature: number) {
  const dx = t.x! - s.x!, dy = t.y! - s.y!;
  const l = Math.sqrt(dx * dx + dy * dy), a = Math.atan2(dy, dx), d = l * curvature;
  return { x: (s.x! + t.x!) / 2 + d * Math.cos(a - Math.PI / 2), y: (s.y! + t.y!) / 2 + d * Math.sin(a - Math.PI / 2) };
}

interface LabelJob {
  text: string;
  x: number;
  y: number;
  r: number;
  alpha: number;
  forced: boolean;
  strong: boolean;
  big: boolean;
  ink: string;
}

const ease = (x: number) => 1 - Math.pow(1 - Math.min(1, Math.max(0, x)), 3);

export const GraphCanvas = forwardRef<GraphHandle, Props>(function GraphCanvas(
  { data, mode, selectedId, highlight, hiddenLabels, frozen, spotlight = null, insetLeft = 0, onSelect, onExpand },
  ref,
) {
  const fg = useRef<ForceGraphMethods<N, L> | undefined>(undefined);
  const wrap = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 800, h: 600 });
  const [hoverId, setHoverId] = useState<string | null>(null);
  const pendingFit = useRef(true);
  const lastClick = useRef<{ id: string; t: number } | null>(null);
  const introAt = useRef(0);
  const labelJobs = useRef<LabelJob[]>([]);
  const inset = useRef(insetLeft);
  inset.current = insetLeft;
  const ink = CANVAS[mode];
  const mediaVersion = useMediaVersion();

  const overview = data.nodes.length > 0 && data.nodes.every((n) => n.conf !== undefined);
  // pipeline: nations and clubs on two fixed rings
  const ring = overview && data.nodes.some((n) => n.hx !== undefined);
  const maxW = useMemo(() => Math.max(1, ...data.links.map((l) => l.weight ?? 1)), [data]);

  // Seed brand-new overview nodes at their confederation's home, so the universe "blooms" out of
  // six small clusters instead of one tangle. (Runs during render, before the simulation sees them.)
  useMemo(() => {
    let seeded = 0;
    for (const n of data.nodes) {
      if (n.x !== undefined || !n.conf) continue;
      const home = homeOf(n) ?? [0, 0];
      const a = Math.random() * 2 * Math.PI, r = Math.random() * 40;
      n.x = home[0] * 0.35 + Math.cos(a) * r;
      n.y = home[1] * 0.35 + Math.sin(a) * r;
      seeded++;
    }
    if (seeded > data.nodes.length / 2) introAt.current = performance.now();
  }, [data]);

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
  // Our own zoom-to-fit: keeps clear of a floating panel on the left and caps the zoom
  // (zoomToFit on a tiny graph zooms in absurdly far).
  const fit = useCallback((ms = 500) => {
    const g = fg.current;
    const el = wrap.current;
    if (!g || !el) return;
    const ns = data.nodes.filter((n) => n.x !== undefined && n.y !== undefined);
    if (!ns.length) return;
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const n of ns) {
      const r = nodeRadius(n);
      x0 = Math.min(x0, n.x! - r); x1 = Math.max(x1, n.x! + r);
      y0 = Math.min(y0, n.y! - r); y1 = Math.max(y1, n.y! + r * 3); // room for labels below
    }
    const w = el.clientWidth, h = el.clientHeight;
    if (overview) {
      y0 -= 40;
      y1 += 30;
    }
    const left = Math.min(inset.current, w * 0.45), pad = 56;
    const top = overview ? 90 : 0; // home switcher + caption
    const bottom = overview ? 50 : 0; // legend
    const k = Math.min(2.6, (w - left - 2 * pad) / Math.max(1, x1 - x0),
      (h - 2 * pad - 40 - top - bottom) / Math.max(1, y1 - y0));
    g.zoom(k, ms);
    g.centerAt((x0 + x1) / 2 - left / 2 / k, (y0 + y1) / 2 + 10 / k - (top - bottom) / 2 / k, ms);
  }, [data, overview]);

  // physics: a geographic "universe" for the overview, a classic force layout for exploring
  useEffect(() => {
    const g = fg.current;
    if (!g) return;
    if (ring) {
      // the rings are the layout: links carry no force, nodes just settle into their places
      g.d3Force("charge")?.strength(-10).distanceMax(60);
      g.d3Force("link")?.strength(0);
      g.d3Force("home", homeForce(0.3) as never);
      g.d3Force("collide", collideForce(3) as never);
    } else if (overview) {
      const conf = new Map(data.nodes.map((n) => [n.id, n.conf]));
      g.d3Force("charge")?.strength(-70).distanceMax(260);
      g.d3Force("link")
        ?.distance((l: L) => 44 + 50 * (1 - Math.sqrt((l.weight ?? 1) / maxW)))
        .strength((l: L) => {
          const same = conf.get(idOf(l.source as string | GNode)) === conf.get(idOf(l.target as string | GNode));
          return (same ? 0.05 : 0.012) + 0.22 * ((l.weight ?? 1) / maxW);
        });
      g.d3Force("home", homeForce(0.055) as never);
      g.d3Force("collide", collideForce(5) as never);
    } else {
      g.d3Force("home", null as never);
      g.d3Force("collide", null as never);
      g.d3Force("charge")?.strength((n: N) => -80 - nodeRadius(n) * 12).distanceMax(420);
      g.d3Force("link")?.distance(42).strength(0.6);
    }
    g.d3ReheatSimulation();
    const t = window.setTimeout(() => {
      if (pendingFit.current) fit(600);
    }, 1400);
    return () => window.clearTimeout(t);
  }, [data, fit, overview, ring, maxW]);

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
      ctx.fillStyle = ink.bg;
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

  // pipeline spotlight: a confederation's nations AND the clubs their players went to
  const spotSet = useMemo(() => {
    if (!spotlight || !ring) return null;
    const s = new Set<string>();
    for (const n of data.nodes) {
      if (n.conf !== spotlight) continue;
      s.add(n.id);
      neighbours.get(n.id)?.forEach((m) => s.add(m));
    }
    return s;
  }, [spotlight, ring, data, neighbours]);

  const isDim = useCallback(
    (node: GNode) => {
      if (focusSet) return !focusSet.has(node.id);
      if (spotSet) return !spotSet.has(node.id) && node.id !== selectedId;
      if (spotlight && overview) return node.conf !== spotlight && node.id !== selectedId;
      return highlight.size > 0 && !highlight.has(node.id) && node.id !== selectedId;
    },
    [focusSet, spotSet, spotlight, overview, highlight, selectedId],
  );

  // ------------------------------------------------------------ overview backdrop: confederation nebulae
  const paintBackdrop = useCallback(
    (ctx: CanvasRenderingContext2D, scale: number) => {
      labelJobs.current = [];
      if (!overview) return;
      const groups = new Map<string, { xs: number; ys: number; n: number; nodes: GNode[]; color: string }>();
      for (const n of data.nodes) {
        if (n.x === undefined || n.y === undefined || !n.conf) continue;
        const g = groups.get(n.conf) ?? { xs: 0, ys: 0, n: 0, nodes: [], color: nodeColor(n, mode) };
        g.xs += n.x; g.ys += n.y; g.n++; g.nodes.push(n);
        groups.set(n.conf, g);
      }
      const intro = introAt.current ? ease((performance.now() - introAt.current) / INTRO_MS) : 1;
      if (ring) {
        paintRings(ctx, scale, groups, intro);
        return;
      }
      for (const [conf, g] of groups) {
        const cx = g.xs / g.n, cy = g.ys / g.n;
        let spread = 0;
        for (const n of g.nodes) spread += (n.x! - cx) ** 2 + (n.y! - cy) ** 2;
        spread = Math.sqrt(spread / g.n);
        const faded = (spotlight && spotlight !== conf) || (focusSet !== null);
        const strength = (mode === "dark" ? 0.2 : 0.13) * (faded ? 0.35 : 1) * intro;
        const R = spread * 1.9 + 50;
        const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, R);
        grad.addColorStop(0, rgba(g.color, strength));
        grad.addColorStop(0.55, rgba(g.color, strength * 0.45));
        grad.addColorStop(1, rgba(g.color, 0));
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(cx, cy, R, 0, 2 * Math.PI);
        ctx.fill();

        // confederation title above the cluster, constant size on screen
        if (g.n >= 3) {
          const fs = 12 / scale;
          let top = Infinity, bottom = -Infinity;
          for (const n of g.nodes) {
            top = Math.min(top, n.y! - nodeRadius(n));
            bottom = Math.max(bottom, n.y! + nodeRadius(n));
          }
          const below = (CONF_HOME[conf]?.[1] ?? 0) > 0;
          ctx.save();
          ctx.globalAlpha = (faded ? 0.3 : 0.9) * intro;
          ctx.font = `700 ${fs}px Inter, system-ui, sans-serif`;
          try {
            (ctx as CanvasRenderingContext2D & { letterSpacing: string }).letterSpacing = `${2.4 / scale}px`;
          } catch { /* older browsers */ }
          ctx.textAlign = "center";
          ctx.textBaseline = "bottom";
          const ty = below ? bottom + 16 / scale + fs : top - 14 / scale - fs * 1.1;
          ctx.fillStyle = g.color;
          ctx.fillText(conf.toUpperCase(), cx, ty);
          ctx.font = `500 ${fs * 0.86}px Inter, system-ui, sans-serif`;
          try {
            (ctx as CanvasRenderingContext2D & { letterSpacing: string }).letterSpacing = "0px";
          } catch { /* older browsers */ }
          ctx.fillStyle = ink.text;
          ctx.globalAlpha *= 0.75;
          ctx.fillText(`${CONF_NAME[conf] ?? conf} · ${g.n}`, cx, ty + fs * 1.25);
          ctx.restore();
        }
      }
    },
    [overview, ring, data, mode, spotlight, focusSet, ink],
  );

  /** Pipeline backdrop: two faint orbits and each group's name just outside (nations) or inside (clubs). */
  const paintRings = useCallback(
    (ctx: CanvasRenderingContext2D, scale: number,
     groups: Map<string, { xs: number; ys: number; n: number; nodes: GNode[]; color: string }>, intro: number) => {
      const radii = new Set<number>();
      for (const n of data.nodes) if (n.hx !== undefined) radii.add(Math.round(Math.hypot(n.hx, n.hy ?? 0)));
      ctx.save();
      ctx.globalAlpha = 0.5 * intro;
      ctx.strokeStyle = ink.edge;
      ctx.setLineDash([3 / scale, 6 / scale]);
      ctx.lineWidth = 1 / scale;
      for (const r of radii) {
        ctx.beginPath();
        ctx.arc(0, 0, r, 0, 2 * Math.PI);
        ctx.stroke();
      }
      ctx.setLineDash([]);
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      for (const [conf, g] of groups) {
        let ax = 0, ay = 0;
        for (const n of g.nodes) { ax += n.hx ?? 0; ay += n.hy ?? 0; }
        const ang = Math.atan2(ay, ax);
        const r0 = Math.hypot(g.nodes[0].hx ?? 0, g.nodes[0].hy ?? 0);
        const club = g.nodes[0].label === "Club";
        if (club || g.n < 3) continue; // tiny groups: the nation's own label says enough
        const r = r0 + 105;
        const faded = spotlight !== null && spotlight !== conf && !club;
        ctx.globalAlpha = (faded ? 0.3 : 0.9) * intro;
        const fs = (club ? 10.5 : 12) / scale;
        ctx.font = `700 ${fs}px Inter, system-ui, sans-serif`;
        ctx.fillStyle = club ? ink.text : g.color;
        ctx.fillText(conf.toUpperCase(), Math.cos(ang) * r, Math.sin(ang) * r);
        ctx.font = `500 ${fs * 0.85}px Inter, system-ui, sans-serif`;
        ctx.fillStyle = ink.text;
        ctx.globalAlpha *= 0.7;
        ctx.fillText(club ? `${g.n} clubs` : `${CONF_NAME[conf] ?? conf} · ${g.n}`, Math.cos(ang) * r, Math.sin(ang) * r + fs * 1.3);
      }
      ctx.restore();
    },
    [data, ink, spotlight],
  );

  // ------------------------------------------------------------ overview links: glowing gradient arcs
  const paintLink = useCallback(
    (l: L, ctx: CanvasRenderingContext2D, scale: number) => {
      const s = l.source as N, t = l.target as N;
      if (typeof s !== "object" || typeof t !== "object" || s.x === undefined || t.x === undefined) return;
      const w = Math.sqrt((l.weight ?? 1) / maxW);
      const touchesHover = hoverId !== null && (s.id === hoverId || t.id === hoverId);
      let alpha = (l.backbone ? 0.12 : 0.16 + 0.42 * w) * (mode === "light" ? 1.25 : 1);
      if (focusSet) alpha = touchesHover ? 0.95 : 0.025;
      else if (spotSet) alpha = spotSet.has(s.id) && spotSet.has(t.id) && (s.conf === spotlight || t.conf === spotlight) ? alpha * 1.4 : 0.02;
      else if (spotlight) alpha = s.conf === spotlight && t.conf === spotlight ? alpha * 1.3 : s.conf === spotlight || t.conf === spotlight ? alpha * 0.6 : 0.02;
      else if (highlight.size) alpha = isPathLink(l) ? 0.95 : 0.03;
      if (ring && !focusSet && !spotSet) alpha *= 0.55; // bundled links overlap a lot on the rings
      if (introAt.current) alpha *= ease((performance.now() - introAt.current - 250) / INTRO_MS);
      if (alpha <= 0.005) return;

      const cs = nodeColor(s, mode), ct = nodeColor(t, mode);
      let stroke: string | CanvasGradient;
      if (cs === ct) stroke = rgba(cs, alpha);
      else {
        const grad = ctx.createLinearGradient(s.x!, s.y!, t.x!, t.y!);
        grad.addColorStop(0, rgba(cs, alpha));
        grad.addColorStop(1, rgba(ct, alpha));
        stroke = grad;
      }
      // rings: pull every curve towards the centre so links bundle like a chord diagram
      const cp = ring ? { x: (s.x! + t.x!) * 0.18, y: (s.y! + t.y!) * 0.18 } : controlPoint(s, t, CURVATURE);
      ctx.save();
      ctx.globalCompositeOperation = mode === "dark" ? "lighter" : "multiply";
      ctx.strokeStyle = stroke;
      ctx.lineCap = "round";
      ctx.lineWidth = ((touchesHover ? 1.6 : 0.5) + 2.6 * w) / Math.pow(scale, 0.7);
      ctx.beginPath();
      ctx.moveTo(s.x!, s.y!);
      ctx.quadraticCurveTo(cp.x, cp.y, t.x!, t.y!);
      ctx.stroke();
      ctx.restore();
    },
    [maxW, hoverId, focusSet, spotSet, spotlight, highlight, isPathLink, mode, ring],
  );

  const paintNode = useCallback(
    (node: N, ctx: CanvasRenderingContext2D, scale: number) => {
      const x = node.x ?? 0, y = node.y ?? 0;
      const r = nodeRadius(node);
      const color = nodeColor(node, mode);
      const shape = LABEL_SHAPE[node.label];
      const selected = node.id === selectedId;
      const hi = highlight.has(node.id);
      const dim = isDim(node);

      ctx.globalAlpha = dim ? (overview ? 0.12 : 0.16) : 1;

      // overview: strong teams glow in their confederation colour
      if (overview && !dim && r >= 9) {
        const R = r * (2 + (r - 9) / 8);
        const glow = ctx.createRadialGradient(x, y, r * 0.6, x, y, R);
        glow.addColorStop(0, rgba(color, mode === "dark" ? 0.5 : 0.32));
        glow.addColorStop(1, rgba(color, 0));
        ctx.fillStyle = glow;
        ctx.beginPath();
        ctx.arc(x, y, R, 0, 2 * Math.PI);
        ctx.fill();
      }

      // halo for selection / highlighted path / hover
      if (selected || hi || node.id === hoverId) {
        drawShape(ctx, shape, x, y, r + (selected ? 7 : 5) / Math.sqrt(scale));
        ctx.fillStyle = selected ? ink.halo : rgba(color, 0.25);
        ctx.fill();
      }

      // body + a surface-coloured ring so overlapping marks stay separable (clubs draw their own crest)
      drawShape(ctx, shape, x, y, r);
      if (node.label === "Club") {
        /* drawn below with the logo */
      } else if (node.label === "FormerName") {
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
      const img = r * scale >= 3.5 ? loadedImage(nodeImageUrl(node)) : undefined;
      if (node.label === "Club") {
        // a light crest carrying the club logo (never cropped), rimmed in the league colour
        drawShape(ctx, shape, x, y, r);
        ctx.fillStyle = mode === "dark" ? "#f4f3ef" : "#ffffff";
        ctx.fill();
        ctx.lineWidth = Math.max(1.4 / scale, r * 0.14);
        ctx.strokeStyle = color;
        ctx.stroke();
        if (img) {
          const box = r * 1.25;
          const s = Math.min(box / img.naturalWidth, box / img.naturalHeight);
          const w = img.naturalWidth * s, h = img.naturalHeight * s;
          ctx.drawImage(img, x - w / 2, y - h / 2 + r * 0.02, w, h);
        } else if (r * scale >= 7) {
          const fs = Math.min(r * 0.62, 9);
          ctx.font = `800 ${fs}px Inter, system-ui, sans-serif`;
          ctx.fillStyle = color;
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillText(teamCode(node.name.replace(/^(FC|AFC|SC|SV|AC|AS|SS|US|RC|CD|CA|UD|SD|RCD|Real)\s+/i, "")), x, y);
        }
      } else if (img) {
        const rim = Math.max(1.4 / scale, r * (overview ? 0.2 : 0.16));
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
        // a soft highlight makes flags read as little glossy "planets"
        if (overview) {
          const gloss = ctx.createRadialGradient(x - inner * 0.4, y - inner * 0.5, 0, x, y, inner * 1.1);
          gloss.addColorStop(0, "rgba(255,255,255,0.35)");
          gloss.addColorStop(0.5, "rgba(255,255,255,0)");
          gloss.addColorStop(1, "rgba(0,0,0,0.22)");
          ctx.fillStyle = gloss;
          ctx.fillRect(x - inner, y - inner, inner * 2, inner * 2);
        }
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
      const inFocus = focusSet?.has(node.id) ?? false;
      const showLabel = overview
        ? !dim && (selected || hi || inFocus || scale > 2.2 ||
            (ring && node.label === "Club" ? r >= 12.5 : r >= 11.5 || r * scale >= 8))
        : selected || hi || node.id === hoverId || inFocus || (minor ? scale >= 3.4 : scale > 1.9 || r * scale >= 7);
      if (showLabel) {
        // drawn after all nodes (paintLabels) so flags never cover a name, and de-cluttered there
        const big = overview && r >= 11;
        labelJobs.current.push({
          text: node.name.length > 34 ? `${node.name.slice(0, 32)}…` : node.name,
          x, y: y + r * 1.15 + 3 / scale, r, alpha: ctx.globalAlpha,
          forced: selected || hi || node.id === hoverId, strong: selected || hi || big, big,
          ink: selected || big ? ink.textStrong : ink.text,
        });
      }
      ctx.globalAlpha = 1;
    },
    // mediaVersion: repaint when flags/photos finish loading
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selectedId, highlight, focusSet, hoverId, mode, ink, mediaVersion, overview, isDim],
  );

  const paintLabels = useCallback(
    (ctx: CanvasRenderingContext2D, scale: number) => {
      const jobs = labelJobs.current.sort((a, b) => Number(b.forced) - Number(a.forced) || b.r - a.r);
      const boxes: [number, number, number, number][] = [];
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      for (const j of jobs) {
        const fontSize = Math.max((j.big ? 11.5 : 10.5) / scale, 1.5);
        ctx.font = `${j.strong ? 650 : 500} ${fontSize}px Inter, system-ui, sans-serif`;
        const w = ctx.measureText(j.text).width;
        const box: [number, number, number, number] = [j.x - w / 2 - 2 / scale, j.y, j.x + w / 2 + 2 / scale, j.y + fontSize * 1.15];
        if (!j.forced && boxes.some((b) => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1])) continue;
        boxes.push(box);
        ctx.globalAlpha = j.alpha;
        if (overview) {
          // haloed text instead of pills keeps the universe airy
          ctx.lineJoin = "round";
          ctx.lineWidth = 3.4 / scale;
          ctx.strokeStyle = ink.ring;
          ctx.strokeText(j.text, j.x, j.y);
        } else {
          const padX = 3.5 / scale, padY = 1.5 / scale;
          ctx.fillStyle = ink.pill;
          ctx.beginPath();
          ctx.roundRect(j.x - w / 2 - padX, j.y - padY, w + padX * 2, fontSize + padY * 2, 4 / scale);
          ctx.fill();
        }
        ctx.fillStyle = j.ink;
        ctx.fillText(j.text, j.x, j.y);
      }
      ctx.globalAlpha = 1;
      labelJobs.current = [];
    },
    [overview, ink],
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
    <div ref={wrap} className={`graph-canvas ${overview ? "is-universe" : ""}`}>
      <ForceGraph2D<GNode, GEdge>
        ref={fg}
        width={size.w}
        height={size.h}
        graphData={data}
        backgroundColor="rgba(0,0,0,0)"
        nodeId="id"
        nodeRelSize={1}
        nodeVal={(n) => nodeRadius(n) ** 2}
        nodeLabel={(n) => tooltip(n)}
        nodeCanvasObject={paintNode}
        nodeVisibility={(n) => !hiddenLabels.has(n.label)}
        onRenderFramePre={paintBackdrop}
        onRenderFramePost={paintLabels}
        linkVisibility={(l) => {
          const s = l.source as GNode | string, t = l.target as GNode | string;
          return !(typeof s === "object" && hiddenLabels.has(s.label)) && !(typeof t === "object" && hiddenLabels.has(t.label));
        }}
        linkCanvasObjectMode={(l) => (l.virtual ? "replace" : undefined)}
        linkCanvasObject={paintLink}
        linkColor={linkColor}
        linkWidth={(l) => (isPathLink(l) ? 2.2 : 1)}
        linkCurvature={(l) => (l.virtual ? CURVATURE : 0)}
        linkDirectionalArrowLength={(l) => (l.virtual ? 0 : 3.2)}
        linkDirectionalArrowRelPos={1}
        linkDirectionalParticles={(l) => (isPathLink(l) && !l.virtual ? 3 : 0)}
        linkDirectionalParticleSpeed={0.007}
        linkDirectionalParticleWidth={3.2}
        linkDirectionalParticleColor={() => ink.accent}
        linkLabel={(l) =>
          `<div class="tip"><b>${l.virtual ? linkTitle(l) : l.type}</b>${l.weight ? `<span>${l.weight} ${l.unit ?? "meetings"}</span>` : ""}${l.side ? `<span>${l.side}, ${l.goals ?? 0} goals</span>` : ""}</div>`
        }
        cooldownTicks={frozen ? 0 : 320}
        cooldownTime={7000}
        d3VelocityDecay={overview ? 0.38 : 0.32}
        autoPauseRedraw={!overview}
        onEngineStop={() => {
          introAt.current = 0;
          if (pendingFit.current) {
            pendingFit.current = false;
            fit(700);
          }
        }}
        onNodeClick={handleClick}
        onNodeRightClick={(n) => onExpand(n)}
        onNodeHover={(n) => {
          setHoverId(n?.id ?? null);
          if (wrap.current) wrap.current.style.cursor = n ? "pointer" : "";
        }}
        onNodeDragEnd={(n) => {
          n.fx = n.x;
          n.fy = n.y;
        }}
        onBackgroundClick={() => onSelect(null)}
      />
    </div>
  );
});

function linkTitle(l: L) {
  const s = l.source as GNode, t = l.target as GNode;
  return typeof s === "object" && typeof t === "object" ? `${escapeHtml(s.name)} – ${escapeHtml(t.name)}` : "MET";
}

function tooltip(n: GNode) {
  const img = n.label === "Team" || n.label === "Player" || n.label === "Club" ? nodeImageUrl(n) : undefined;
  if (n.subtitle) {
    return `<div class="tip tip--rich">${img ? `<img src="${img}" alt="" class="${n.label === "Club" ? "is-logo" : ""}" />` : ""}<div><b>${escapeHtml(n.name)}</b><span>${escapeHtml(n.subtitle)}</span></div></div>`;
  }
  const meta = [
    n.conf ? `${n.conf} · ${CONF_NAME[n.conf] ?? ""}` : n.label,
    n.elo ? `Elo ${Math.round(n.elo)}` : "",
    n.conf && n.degree ? `${n.degree.toLocaleString("en-US")} matches` : "",
  ].filter(Boolean);
  return `<div class="tip tip--rich">${img ? `<img src="${img}" alt="" />` : ""}<div><b>${escapeHtml(n.name)}</b><span>${meta.join(" · ")}</span></div></div>`;
}

function escapeHtml(s: string) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}
