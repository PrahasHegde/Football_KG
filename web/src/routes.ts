/*
 * Deep links: the URL hash mirrors what is on screen, so a link (or the back button) restores it.
 *
 *   #/nations  #/clubs  #/pipeline      a home view
 *   #/e/<id>                            an entity's panel        (e.g. #/e/P%3Alionel_messi%40argentina)
 *   #/h2h/<team>/<team>                 head to head of two teams
 *   #/vs/<player>/<player>              player comparison
 */

export type Home = "nations" | "clubs" | "pipeline";

export type Route =
  | { kind: "home"; home: Home }
  | { kind: "entity"; id: string }
  | { kind: "h2h"; a: string; b: string }
  | { kind: "vs"; a: string; b: string };

const HOMES: readonly string[] = ["nations", "clubs", "pipeline"];

/** Parse a location hash; null for anything unknown or malformed (never throws). */
export function parseRoute(hash: string): Route | null {
  let parts: string[];
  try {
    parts = hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  } catch {
    return null; // malformed %-escape
  }
  const [kind, a, b] = parts;
  if (HOMES.includes(kind) && parts.length === 1) return { kind: "home", home: kind as Home };
  if (kind === "e" && a) return { kind: "entity", id: parts.slice(1).join("/") };
  if ((kind === "h2h" || kind === "vs") && a && b && parts.length === 3) return { kind, a, b };
  return null;
}

/** The hash for a route (inverse of parseRoute). */
export function routeHash(r: Route): string {
  const e = encodeURIComponent;
  switch (r.kind) {
    case "home":
      return `#/${r.home}`;
    case "entity":
      return `#/e/${e(r.id)}`;
    case "h2h":
    case "vs":
      return `#/${r.kind}/${e(r.a)}/${e(r.b)}`;
  }
}
