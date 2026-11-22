import { describe, expect, it } from "vitest";
import { parseRoute, routeHash, type Route } from "./routes";

describe("parseRoute", () => {
  it("reads the three home views", () => {
    expect(parseRoute("#/nations")).toEqual({ kind: "home", home: "nations" });
    expect(parseRoute("#/clubs")).toEqual({ kind: "home", home: "clubs" });
    expect(parseRoute("#pipeline")).toEqual({ kind: "home", home: "pipeline" });
  });

  it("decodes entity ids with special characters", () => {
    expect(parseRoute("#/e/P%3Alionel_messi%40argentina")).toEqual({ kind: "entity", id: "P:lionel_messi@argentina" });
    expect(parseRoute("#/e/P%3Ap%C3%A9l%C3%A9%40brazil")).toEqual({ kind: "entity", id: "P:pélé@brazil" });
  });

  it("reads two-entity routes", () => {
    expect(parseRoute("#/h2h/T%3Abrazil/T%3Aargentina")).toEqual({ kind: "h2h", a: "T:brazil", b: "T:argentina" });
    expect(parseRoute("#/vs/P%3Aa%40x/P%3Ab%40y")).toEqual({ kind: "vs", a: "P:a@x", b: "P:b@y" });
  });

  it("returns null (never throws) for unknown or malformed hashes", () => {
    for (const bad of ["", "#", "#/", "#/nope", "#/e", "#/e/", "#/h2h/T%3Aa", "#/vs/a/b/c", "#/e/%E0%A4%A", "#/nations/extra"]) {
      expect(parseRoute(bad)).toBeNull();
    }
  });
});

describe("routeHash", () => {
  it("round-trips through parseRoute", () => {
    const routes: Route[] = [
      { kind: "home", home: "clubs" },
      { kind: "entity", id: "CL:fc_barcelona" },
      { kind: "entity", id: "CI:são_paulo@brazil" },
      { kind: "h2h", a: "T:england", b: "T:scotland" },
      { kind: "vs", a: "P:son_heung-min@south_korea", b: "P:harry_kane@england" },
    ];
    for (const r of routes) expect(parseRoute(routeHash(r))).toEqual(r);
  });

  it("escapes ids so they cannot break the path", () => {
    expect(routeHash({ kind: "entity", id: "a/b" })).toBe("#/e/a%2Fb");
  });
});
