/*
 * Client-side media store: team flags/crests and player photos from Wikidata/Wikipedia.
 *
 * Images are loaded once into HTMLImageElements (the canvas draws them directly). Components that
 * care subscribe with useMediaVersion(), which bumps whenever new media or a newly loaded image
 * arrives, so the canvas repaints exactly when there is something new to show.
 */
import { useSyncExternalStore } from "react";
import type { GNode } from "./types";

export interface TeamMedia {
  flag: string | null;
  crest: string | null;
}

const teams: Record<string, TeamMedia> = {};
const playerPhotos: Record<string, string | null> = {};
const requested = new Set<string>();
const images = new Map<string, HTMLImageElement>();
const listeners = new Set<() => void>();
let version = 0;
let bumpScheduled = false;

function bump() {
  if (bumpScheduled) return;
  bumpScheduled = true;
  // batch many image loads into one repaint
  window.setTimeout(() => {
    bumpScheduled = false;
    version++;
    listeners.forEach((l) => l());
  }, 60);
}

export function useMediaVersion(): number {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => version,
  );
}

/** Load team flags/crests once; retries while the server is still doing its first-ever lookup. */
export async function loadTeamMedia(): Promise<void> {
  for (let attempt = 0; attempt < 30; attempt++) {
    try {
      const r = await fetch("/api/media/teams");
      const body = (await r.json()) as { ready: boolean; teams: Record<string, TeamMedia> };
      if (body.ready) {
        Object.assign(teams, body.teams);
        bump();
        return;
      }
    } catch {
      /* server not ready */
    }
    await new Promise((res) => window.setTimeout(res, 4000));
  }
}

/**
 * Ask the server for photos of these players (only ones not asked for before). The server answers
 * with what it already has and queues the rest; pending ones are re-polled until they resolve, so
 * photos pop in one by one.
 */
export async function requestPlayerPhotos(ids: string[]): Promise<void> {
  let todo = ids.filter((id) => id.startsWith("P:") && !requested.has(id)).slice(0, 40);
  if (!todo.length) return;
  todo.forEach((id) => requested.add(id));
  for (let round = 0; round < 25 && todo.length; round++) {
    try {
      const r = await fetch("/api/media/players", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: todo }),
      });
      const body = (await r.json()) as { photos: Record<string, string | null>; pending: string[] };
      const fresh = Object.entries(body.photos);
      for (const [id, photo] of fresh) playerPhotos[id] = photo;
      if (fresh.length) bump();
      todo = body.pending;
    } catch {
      todo.forEach((id) => requested.delete(id));
      return;
    }
    if (todo.length) await new Promise((res) => window.setTimeout(res, 2500));
  }
}

export const teamMedia = (id: string): TeamMedia | undefined => teams[id];
export const playerPhoto = (id: string): string | null | undefined => playerPhotos[id];

/** The picture that represents a node on the canvas, if any. */
export function nodeImageUrl(n: GNode): string | null {
  if (n.label === "Team") return teams[n.id]?.flag ?? null;
  if (n.label === "Player") return playerPhotos[n.id] ?? null;
  return null;
}

/** A loaded image ready to draw, or undefined (and starts loading it). */
export function loadedImage(url: string | null): HTMLImageElement | undefined {
  if (!url) return undefined;
  let img = images.get(url);
  if (!img) {
    img = new Image();
    img.decoding = "async";
    img.onload = bump;
    img.src = url;
    images.set(url, img);
  }
  return img.complete && img.naturalWidth > 0 ? img : undefined;
}
