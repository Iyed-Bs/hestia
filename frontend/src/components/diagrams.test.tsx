import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { I18nProvider } from "../lib/i18n";
import { BenchDiagram } from "./BenchDiagram";
import { SiteDiagram } from "./SiteDiagram";
import { annunciate } from "./Annunciator";
import { siteSnapshot, snapshot } from "../test-fixtures";

const draw = (node: ReactNode) => render(<I18nProvider>{node}</I18nProvider>).container;
const fmt = (v: number | null | undefined) => String(v ?? "—");
function need<T>(value: T | null | undefined): T {
  if (value === null || value === undefined) throw new Error("fixture incomplete");
  return value;
}
const tel = need(snapshot().telemetry);

describe("bench diagram", () => {
  it("animates the power and hydrogen lines only while the stack produces", () => {
    const producing = draw(<BenchDiagram snap={snapshot()} />);
    expect(producing.querySelector(".flow.h2.active")).not.toBeNull();
    expect(producing.querySelector(".producing")).not.toBeNull();

    const base = snapshot();
    const idle = draw(<BenchDiagram snap={snapshot({ site: { ...need(base.site), electrolyser_w: 0 } })} />);
    expect(idle.querySelector(".flow.h2.active")).toBeNull();
    expect(idle.querySelector(".producing")).toBeNull();
  });

  it("moves water through the cooler only while the cooling loop runs", () => {
    const off = draw(<BenchDiagram snap={snapshot()} />);
    const on = draw(<BenchDiagram snap={snapshot({ telemetry: { ...tel, cooling_pump: true } })} />);
    expect(on.querySelectorAll(".flow.water.active").length).toBeGreaterThan(off.querySelectorAll(".flow.water.active").length);
  });

  it("says in words when the H₂ sensor is not trusted, not only in colour", () => {
    const base = snapshot();
    const trust = need(base.trust);
    const untrusted = draw(
      <BenchDiagram snap={snapshot({ trust: { ...trust, h2_trusted: false, sensors: { ...trust.sensors, h2: { ...trust.sensors.h2, level: "untrusted" } } } })} />,
    );
    expect(untrusted.textContent).toMatch(/NOT trusted|NON fiable/);
  });

  it("shows the emergency relay open and the extraction running during an alarm", () => {
    const alarm = draw(
      <BenchDiagram snap={snapshot({ telemetry: { ...tel, h2_alarm_latched: true, h2_relay_closed: false, ventilation: true, h2_ppm: 2600 } })} />,
    );
    expect(alarm.textContent).toMatch(/relay OPEN|relais OUVERT/);
    expect(alarm.textContent).toMatch(/ALARM/);
    expect(alarm.querySelector(".flow.air.active")).not.toBeNull();
  });
});

describe("site diagram", () => {
  it("draws export towards the grid and cooling to the building", () => {
    const site = draw(<SiteDiagram snap={siteSnapshot()} />);
    expect(site.textContent).toMatch(/export|vente/);
    expect(site.textContent).toMatch(/cooling|rafraîchissement/);
    expect(site.querySelector(".flow.water.active")).not.toBeNull(); // heat pump → building, cooling
  });

  it("says when the grid is down", () => {
    const base = siteSnapshot();
    const down = draw(<SiteDiagram snap={{ ...base, twin: { ...need(base.twin), faults: ["grid_outage"] } }} />);
    expect(down.textContent).toMatch(/GRID DOWN|RÉSEAU COUPÉ/);
  });
});

describe("annunciator", () => {
  it("is all normal on a healthy bench", () => {
    expect(annunciate(snapshot(), fmt).every((tile) => tile.severity === "normal")).toBe(true);
  });

  it("ranks the gas stages: warning, then critical", () => {
    const warn = annunciate(snapshot({ telemetry: { ...tel, h2_warning: true, h2_ppm: 600 } }), fmt);
    expect(warn.find((x) => x.name === "ann.gas")?.severity).toBe("warning");
    const alarm = annunciate(snapshot({ telemetry: { ...tel, h2_warning: true, h2_alarm_latched: true, h2_ppm: 2500 } }), fmt);
    expect(alarm.find((x) => x.name === "ann.gas")?.severity).toBe("critical");
  });

  it("flags the storage interlock and a silent controller", () => {
    const full = annunciate(siteSnapshot({ telemetry: { ...tel, storage_mawp_bar: 30, tank_bar: 28.6 } }), fmt);
    expect(full.find((x) => x.name === "ann.storage")?.state).toBe("ann.storage.interlock");
    const silent = annunciate(snapshot({ online: false }), fmt);
    expect(silent.find((x) => x.name === "ann.link")?.severity).toBe("critical");
  });
});
