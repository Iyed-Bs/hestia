import { describe, expect, it } from "vitest";
import { dictionaries } from "./i18n";

const placeholders = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();

describe("translations", () => {
  const en = dictionaries.en;
  const fr = dictionaries.fr;

  it("has a non-empty French string for every English key", () => {
    for (const key of Object.keys(en) as (keyof typeof en)[]) {
      expect(fr[key], key).toBeTruthy();
    }
  });

  it("keeps the same {placeholders} in both languages", () => {
    for (const key of Object.keys(en) as (keyof typeof en)[]) {
      expect(placeholders(fr[key]), key).toEqual(placeholders(en[key]));
    }
  });
});
