import { describe, expect, it } from "vitest";
import {
  CONF_HOME, CONF_NAME, LABEL_ORDER, LABEL_SHAPE, communityColor, labelColor, nodeColor, nodeRadius, rgba, teamCode,
} from "./theme";
import type { GNode, Label } from "./types";

const node = (over: Partial<GNode>): GNode => ({ id: "x", label: "Team", name: "X", ...over });

describe("nodeRadius", () => {
  it("sizes overview nodes by strength, clamped to 4..17", () => {
    expect(nodeRadius(node({ strength: 0 }))).toBe(4);
    expect(nodeRadius(node({ strength: 1 }))).toBe(17);
    expect(nodeRadius(node({ strength: 5 }))).toBe(17);
    expect(nodeRadius(node({ strength: -1 }))).toBe(4);
  });

  it("sizes by Elo in the nations view, clamped", () => {
    expect(nodeRadius(node({ elo: 500 }))).toBe(4);
    expect(nodeRadius(node({ elo: 3000 }))).toBe(17);
    expect(nodeRadius(node({ elo: 2000 }))).toBeGreaterThan(nodeRadius(node({ elo: 1500 })));
  });

  it("never returns an invalid radius for any label", () => {
    for (const label of LABEL_ORDER) {
      for (const degree of [0, 1, 10, 1e6]) {
        const r = nodeRadius(node({ label, degree }));
        expect(Number.isFinite(r) && r > 0 && r <= 16).toBe(true);
      }
    }
  });
});

describe("colours and shapes", () => {
  it("covers every label", () => {
    for (const label of LABEL_ORDER as Label[]) {
      expect(LABEL_SHAPE[label]).toBeTruthy();
      expect(labelColor(label, "light")).toMatch(/^#[0-9a-f]{6}$/);
      expect(labelColor(label, "dark")).toMatch(/^#[0-9a-f]{6}$/);
    }
  });

  it("uses the neutral slot for 'Other' groups and the group colour over the label colour", () => {
    expect(communityColor(7, "light")).toBe(communityColor(99, "light"));
    expect(nodeColor(node({ group: 2 }), "dark")).toBe(communityColor(2, "dark"));
  });

  it("converts hex to rgba", () => {
    expect(rgba("#2a78d6", 0.5)).toBe("rgba(42,120,214,0.5)");
    expect(rgba("#000000", 0)).toBe("rgba(0,0,0,0)");
  });
});

describe("overview layout tables", () => {
  it("has a home and a display name for every cluster key", () => {
    for (const key of Object.keys(CONF_HOME)) expect(CONF_NAME[key]).toBeTruthy();
    for (const k of ["UEFA", "CONMEBOL", "CONCACAF", "CAF", "AFC", "OFC", "Non-FIFA",
      "Premier League", "LaLiga", "Bundesliga", "Serie A", "Ligue 1", "Eredivisie", "Liga Portugal", "Rest of world"]) {
      expect(CONF_HOME[k]).toHaveLength(2);
    }
  });
});

describe("teamCode", () => {
  it("abbreviates names", () => {
    expect(teamCode("Brazil")).toBe("BRA");
    expect(teamCode("South Korea")).toBe("SKO");
    expect(teamCode("")).toBe("");
  });
});
