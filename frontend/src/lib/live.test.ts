import { describe, expect, it } from "vitest";
import { empty, HISTORY_POINTS, push } from "./live";
import { siteSnapshot, snapshot } from "../test-fixtures";

const at = (iso: string) => {
  const s = snapshot();
  return snapshot({ site: s.site ? { ...s.site, timestamp: iso } : null });
};

describe("live history", () => {
  it("appends readings in time order", () => {
    let h = empty();
    h = push(h, at("2025-07-10T11:00:00Z"));
    h = push(h, at("2025-07-10T11:00:05Z"));
    expect(h.t).toHaveLength(2);
    expect(h.electrolyser).toEqual([1000, 1000]);
    expect(h.electrolyte).toEqual([52.4, 52.4]);
  });

  it("leaves a gap, not a zero, where a bench has no building", () => {
    const h = push(empty(), at("2025-07-10T11:00:00Z"));
    expect(Number.isNaN(h.indoor[0])).toBe(true);
    const site = push(empty(), siteSnapshot());
    expect(site.indoor[0]).toBe(25.8);
    expect(site.grid[0]).toBe(-1050);
  });

  it("ignores a duplicate snapshot", () => {
    let h = push(empty(), at("2025-07-10T11:00:00Z"));
    h = push(h, at("2025-07-10T11:00:00Z"));
    expect(h.t).toHaveLength(1);
  });

  it("starts a fresh chart when the clock jumps back in time", () => {
    let h = push(empty(), at("2025-07-10T11:00:00Z"));
    h = push(h, at("2025-07-10T11:00:05Z"));
    h = push(h, at("2025-01-15T18:00:00Z"));
    expect(h.t).toEqual([Date.parse("2025-01-15T18:00:00Z") / 1000]);
  });

  it("keeps a bounded window", () => {
    let h = empty();
    const start = Date.parse("2025-07-10T00:00:00Z");
    for (let i = 0; i < HISTORY_POINTS + 50; i++) h = push(h, at(new Date(start + i * 1000).toISOString()));
    expect(h.t).toHaveLength(HISTORY_POINTS);
    expect(h.pv).toHaveLength(HISTORY_POINTS);
  });
});
