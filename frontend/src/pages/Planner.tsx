// The sizing & ROI planner. Before anyone buys a hydrogen system for a
// building, they need an honest answer to "is it worth it here, and how big?".
// The gateway simulates a full year of real weather, hour by hour, with the
// simulator's physics, for four set-ups (today / solar / solar + battery /
// solar + battery + hydrogen) and sweeps electrolyser and tank sizes.
//
// How to use it:
//   1. Pick a country preset (tariffs, grid CO₂, how homes are heated), or
//      "elsewhere" and type a latitude/longitude.
//   2. Describe the building, how it is heated and cooled today, and the
//      system you are considering, including what the hydrogen is used by.
//   3. Simulate. Click any cell of the size map to try that size.
//   Every price and efficiency is editable: the defaults are illustrative,
//   and the planner says so.
import { useEffect, useMemo, useState } from "react";
import { CaretDownIcon, LightbulbIcon, PlayIcon } from "@phosphor-icons/react";
import { useI18n, type Key } from "../lib/i18n";
import { api, ApiError, post } from "../lib/api";
import { Card } from "../components/ui";
import type { MeritRow } from "../lib/types";
import { Merit } from "../components/Merit";
import { SeasonChart } from "../components/SeasonChart";

type Values = Record<string, number | string | boolean | null>;

interface Economics {
  capex: number;
  yearly_cost: number;
  yearly_savings: number;
  payback_years: number;
  npv: number;
  co2_kg: number;
  co2_avoided_kg: number;
  self_sufficiency: number;
  self_consumption: number;
}

interface Scenario {
  sizes: { pv_kwp: number; battery_kwh: number; electrolyser_kw: number; tank_kg: number };
  flows: Record<string, number> & {
    monthly_tank_kg: number[];
    monthly_h2_kwh: number[];
    monthly_heat_kwh: number[];
    monthly_cool_kwh: number[];
  };
  economics: Economics;
}

interface SweepRow extends Economics {
  electrolyser_kw: number;
  tank_kg: number;
  h2_useful_kwh: number;
  tank_volume_m3?: number;
}

interface Plan {
  weather_source: string;
  scenarios: Record<"today" | "solar" | "solar_battery" | "hestia", Scenario>;
  sweep: SweepRow[];
  recommended: SweepRow;
  tank_volume_m3: number;
  pv_yield_kwh_per_kwp: number;
  h2_cost_per_kwh: number | null;
  grid_price_per_kwh: number;
  chain: { stack_kwh_per_kg: number; to_hydrogen: number; electricity: number; heat: number; useful: number };
  merit: MeritRow[];
  verdict: string[];
}

// Number fields, by group, in form order. Every limit is enforced again by
// the gateway (PlannerInputs in backend/src/hestia/planner/model.py).
const BUILDING = ["floor_area_m2", "electricity_kwh_per_m2_year", "heat_setpoint_c", "cool_setpoint_c"] as const;
const SYSTEM = ["pv_kwp", "battery_kwh", "pv_tilt_deg", "pv_azimuth_deg", "electrolyser_kw", "tank_kg"] as const;
const PRICES = [
  "import_price",
  "export_price",
  "export_cap_share",
  "gas_price",
  "grid_kgco2_per_kwh",
  "pv_eur_per_kwp",
  "battery_eur_per_kwh",
  "electrolyser_eur_per_kw",
  "storage_eur_per_kg",
  "fuel_cell_eur_per_kw",
  "h2_boiler_eur",
  "installation_share",
  "om_share_per_year",
  "lifetime_years",
  "discount_rate",
  "battery_round_trip",
  "battery_reserve",
  "tank_mawp_bar",
  "fuel_cell_electrical_efficiency",
  "fuel_cell_thermal_efficiency",
  "h2_boiler_efficiency",
  "h2_reserve_pct",
  "gas_boiler_efficiency",
  "carbon_price_eur_per_t",
] as const;
const STEP: Record<string, number> = {
  floor_area_m2: 10,
  electricity_kwh_per_m2_year: 1,
  heat_setpoint_c: 0.5,
  cool_setpoint_c: 0.5,
  pv_kwp: 0.5,
  pv_tilt_deg: 5,
  pv_azimuth_deg: 5,
  battery_kwh: 1,
  electrolyser_kw: 0.5,
  tank_kg: 1,
  fuel_cell_kw: 0.5,
  h2_boiler_kw: 0.5,
  lifetime_years: 1,
  tank_mawp_bar: 5,
  h2_reserve_pct: 1,
  carbon_price_eur_per_t: 10,
  pv_eur_per_kwp: 50,
  battery_eur_per_kwh: 50,
  electrolyser_eur_per_kw: 100,
  storage_eur_per_kg: 50,
  fuel_cell_eur_per_kw: 100,
  h2_boiler_eur: 100,
};
const CHOICES = ["preset", "insulation", "heating", "end_use", "h2_use", "strategy", "objective", "timezone"];
const NUMERIC = [...BUILDING, ...SYSTEM, ...PRICES, "latitude", "longitude", "fuel_cell_kw", "h2_boiler_kw"];

const DEFAULTS: Values = {
  preset: "tunisia",
  latitude: 36.8,
  longitude: 10.2,
  timezone: "Africa/Tunis",
  floor_area_m2: 120,
  insulation: "average",
  electricity_kwh_per_m2_year: 30,
  heat_setpoint_c: 20,
  cool_setpoint_c: 26,
  heating: "heat_pump",
  cooling: true,
  gas_boiler_efficiency: 0.9,
  pv_kwp: 8,
  pv_tilt_deg: 30,
  pv_azimuth_deg: 0,
  battery_kwh: 10,
  battery_round_trip: 0.92,
  battery_reserve: 0.2,
  electrolyser_kw: 2,
  tank_kg: 10,
  tank_mawp_bar: 30,
  end_use: "fuel_cell",
  fuel_cell_kw: 1.5,
  fuel_cell_electrical_efficiency: 0.45,
  fuel_cell_thermal_efficiency: 0.4,
  h2_boiler_kw: 3,
  h2_boiler_efficiency: 0.9,
  h2_use: "auto",
  h2_reserve_pct: 5,
  strategy: "value",
  carbon_price_eur_per_t: 300,
  objective: "balanced",
};

const SCENARIOS = ["today", "solar", "solar_battery", "hestia"] as const;

export function PlannerPage() {
  const { t } = useI18n();
  const [presets, setPresets] = useState<Record<string, Values>>({});
  const [values, setValues] = useState<Values>(DEFAULTS);
  const [result, setResult] = useState<Plan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const run = async (v: Values) => {
    setBusy(true);
    setError("");
    try {
      // Only send the fields the gateway knows (presets also carry a label and a currency).
      const body: Values = { cooling: Boolean(v.cooling) };
      for (const k of [...NUMERIC, ...CHOICES]) {
        const value = v[k];
        if (value === undefined || value === "") continue;
        body[k] = NUMERIC.includes(k) ? Number(value) : value;
      }
      setResult(await post<Plan>("/api/planner/run", body));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  // Load the presets, apply the default one and run once, so the page opens on an answer.
  useEffect(() => {
    let alive = true;
    void api<{ presets: Record<string, Values> }>("/api/planner/presets").then((r) => {
      if (!alive) return;
      setPresets(r.presets);
      const start = { ...DEFAULTS, ...r.presets.tunisia, preset: "tunisia" };
      setValues(start);
      void run(start);
    });
    return () => {
      alive = false;
    };
  }, []);

  const applyPreset = (name: string) => {
    const preset = presets[name];
    // Elsewhere: no time-zone name, the gateway uses solar time from the longitude.
    setValues(preset ? { ...values, ...preset, preset: name } : { ...values, preset: "custom", timezone: null });
  };

  const set = (key: string, value: string | boolean) => setValues({ ...values, [key]: value });
  const trySize = (kw: number, kg: number) => {
    const next = { ...values, electrolyser_kw: kw, tank_kg: kg };
    setValues(next);
    void run(next);
  };
  const seg = (key: string, options: string[], label: (o: string) => string) => (
    <div className="field">
      <span className="label">{t(`planner.f.${key}` as Key)}</span>
      <div className="segmented wrap" role="group" aria-label={t(`planner.f.${key}` as Key)}>
        {options.map((o) => (
          <button type="button" key={o} aria-pressed={values[key] === o} onClick={() => set(key, o)}>
            {label(o)}
          </button>
        ))}
      </div>
    </div>
  );

  return (
    <div className="stack">
      <header className="topbar">
        <div className="title">
          <h1>{t("planner.title")}</h1>
          <span className="dim">{t("planner.lead")}</span>
        </div>
      </header>

      <div className="planner">
        <Card title={t("planner.inputs")} className="planner-form">
          <form className="stack" onSubmit={(e) => {
            e.preventDefault();
            void run(values);
          }}>
            <div className="field">
              <span className="label">{t("planner.where")}</span>
              <div className="segmented" role="group" aria-label={t("planner.where")}>
                {["tunisia", "france", "custom"].map((p) => (
                  <button type="button" key={p} aria-pressed={values.preset === p} onClick={() => applyPreset(p)}>
                    {t(`planner.preset.${p}` as Key)}
                  </button>
                ))}
              </div>
              {values.preset === "custom" && (
                <div className="grid grid-2" style={{ gap: 8 }}>
                  <NumberField name="latitude" values={values} set={set} step={0.1} />
                  <NumberField name="longitude" values={values} set={set} step={0.1} />
                </div>
              )}
            </div>

            <fieldset>
              <legend>{t("planner.building")}</legend>
              <div className="grid grid-2" style={{ gap: 8 }}>
                {BUILDING.map((k) => (
                  <NumberField key={k} name={k} values={values} set={set} />
                ))}
              </div>
              {seg("insulation", ["poor", "average", "good"], (o) => t(`ins.${o}` as Key))}
            </fieldset>

            <fieldset>
              <legend>{t("planner.comfort")}</legend>
              {seg("heating", ["heat_pump", "gas_boiler"], (o) => t(`planner.heating.${o}` as Key))}
              <label className="row check">
                <input type="checkbox" checked={Boolean(values.cooling)} onChange={(e) => set("cooling", e.target.checked)} />
                {t("planner.f.cooling")}
              </label>
            </fieldset>

            <fieldset>
              <legend>{t("planner.system")}</legend>
              <div className="grid grid-2" style={{ gap: 8 }}>
                {SYSTEM.map((k) => (
                  <NumberField key={k} name={k} values={values} set={set} />
                ))}
              </div>
            </fieldset>

            <fieldset>
              <legend>{t("planner.hydrogen")}</legend>
              {seg("end_use", ["fuel_cell", "boiler"], (o) => t(`opt.${o}` as Key))}
              {values.end_use === "fuel_cell" ? (
                <NumberField name="fuel_cell_kw" values={values} set={set} />
              ) : (
                <NumberField name="h2_boiler_kw" values={values} set={set} />
              )}
              {seg("h2_use", ["auto", "heating_only", "grid_backup"], (o) => t(`planner.use.${o}` as Key))}
              {seg("strategy", ["value", "battery_first", "hydrogen_first"], (o) => t(`opt.${o}` as Key))}
            </fieldset>

            <div className="field">
              <span className="label">{t("planner.objective")}</span>
              <div className="segmented" role="group" aria-label={t("planner.objective")}>
                {["balanced", "self_sufficiency", "value"].map((o) => (
                  <button type="button" key={o} aria-pressed={values.objective === o} onClick={() => set("objective", o)}>
                    {t(`planner.objective.${o}` as Key)}
                  </button>
                ))}
              </div>
              <span className="hint">{t(`planner.objective.${String(values.objective)}.hint` as Key)}</span>
            </div>

            <details>
              <summary className="row">
                <CaretDownIcon size={14} /> {t("planner.prices")}
              </summary>
              <p className="hint">{t("planner.prices.hint")}</p>
              <div className="grid grid-2" style={{ gap: 8 }}>
                {PRICES.map((k) => (
                  <NumberField key={k} name={k} values={values} set={set} step={STEP[k] ?? 0.001} />
                ))}
              </div>
            </details>

            <button className="btn primary" type="submit" disabled={busy}>
              <PlayIcon size={15} weight="fill" /> {busy ? t("planner.running") : t("planner.run")}
            </button>
            {error && <div className="error">{error}</div>}
          </form>
        </Card>

        <div className="stack planner-results" aria-busy={busy}>
          {result ? <Results plan={result} onTry={trySize} busy={busy} /> : <Card><p className="dim">{t("planner.running")}</p></Card>}
        </div>
      </div>
    </div>
  );
}

function NumberField({ name, values, set, step }: { name: string; values: Values; set: (k: string, v: string) => void; step?: number }) {
  const { t } = useI18n();
  return (
    <div className="field">
      <label htmlFor={`p-${name}`}>{t(`planner.f.${name}` as Key)}</label>
      <input
        id={`p-${name}`}
        className="input num"
        type="number"
        step={step ?? STEP[name] ?? "any"}
        min={name === "latitude" ? -90 : name === "longitude" ? -180 : name === "pv_azimuth_deg" ? -180 : 0}
        value={String(values[name] ?? "")}
        onChange={(e) => set(name, e.target.value)}
      />
    </div>
  );
}

function Results({ plan, onTry, busy }: { plan: Plan; onTry: (kw: number, kg: number) => void; busy: boolean }) {
  const { t, fmt } = useI18n();
  const h = plan.scenarios.hestia;
  const eur = (v: number) => `${fmt(v)} €`;
  return (
    <>
      <Card title={t("planner.answer")} className="verdict">
        <div className="row" style={{ alignItems: "start", flexWrap: "nowrap" }}>
          <LightbulbIcon size={26} color="var(--solar)" weight="duotone" style={{ flex: "none" }} />
          <ul className="stack" style={{ gap: 6, margin: 0, paddingLeft: 18 }}>
            {plan.verdict.map((v) => (
              <li key={v}>{v}</li>
            ))}
          </ul>
        </div>
      </Card>

      <div className="grid grid-4 scenarios">
        {SCENARIOS.map((name) => {
          const s = plan.scenarios[name];
          const e = s.economics;
          return (
            <div className={`card scenario ${name === "hestia" ? "featured" : ""}`} key={name}>
              <span className="label">{t(`planner.scenario.${name}`)}</span>
              <span className="faint num">
                {name === "today"
                  ? t("planner.scenario.today.sizes")
                  : `${fmt(s.sizes.pv_kwp, 1)} kWp${s.sizes.battery_kwh ? ` · ${fmt(s.sizes.battery_kwh)} kWh` : ""}${
                      s.sizes.electrolyser_kw ? ` · ${fmt(s.sizes.electrolyser_kw, 1)} kW · ${fmt(s.sizes.tank_kg)} kg` : ""
                    }`}
              </span>
              <span className="big num">{fmt(e.self_sufficiency * 100)} %</span>
              <span className="faint">{t("planner.selfsufficiency")}</span>
              <div className="bar">
                <span style={{ width: `${e.self_sufficiency * 100}%`, background: name === "hestia" ? "var(--accent)" : "var(--solar)" }} />
              </div>
              <dl>
                <dt>{t("planner.capex")}</dt>
                <dd className="num">{eur(e.capex)}</dd>
                <dt>{t("planner.yearly_cost")}</dt>
                <dd className="num">{eur(e.yearly_cost)}</dd>
                <dt>{t("planner.payback")}</dt>
                <dd className="num">
                  {name === "today" ? "—" : e.payback_years < 0 ? t("planner.never") : `${fmt(e.payback_years, 1)} ${t("planner.years")}`}
                </dd>
                <dt>{t("planner.npv")}</dt>
                <dd className={`num ${e.npv < 0 ? "neg" : ""}`}>{name === "today" ? "—" : eur(e.npv)}</dd>
                <dt>{t("planner.co2")}</dt>
                <dd className="num">{fmt(e.co2_kg / 1000, 2)} t</dd>
              </dl>
            </div>
          );
        })}
      </div>

      <Card title={t("planner.season")}>
        <SeasonChart heat={h.flows.monthly_heat_kwh} cool={h.flows.monthly_cool_kwh} fromH2={h.flows.monthly_h2_kwh}
          tankKg={h.flows.monthly_tank_kg} tankSize={h.sizes.tank_kg} />
        <p className="hint">{t("planner.season.hint")}</p>
      </Card>

      <Card title={t("planner.sweep")} actions={busy ? <span className="pill accent">{t("planner.running")}</span> : undefined}>
        <SweepMap plan={plan} onTry={onTry} />
      </Card>

      <Card title={t("planner.assumptions")}>
        <dl className="assumptions">
          <dt>{t("planner.weather")}</dt>
          <dd>{plan.weather_source}</dd>
          <dt>{t("planner.pv_yield")}</dt>
          <dd className="num">{fmt(plan.pv_yield_kwh_per_kwp)} kWh/kWp</dd>
          <dt>{t("planner.stack")}</dt>
          <dd className="num">{fmt(plan.chain.stack_kwh_per_kg, 1)} kWh/kg</dd>
          <dt>{t("planner.chain")}</dt>
          <dd className="num">
            {t("planner.chain.value")
              .replace("{h2}", fmt(plan.chain.to_hydrogen, 2))
              .replace("{el}", fmt(plan.chain.electricity, 2))
              .replace("{heat}", fmt(plan.chain.heat, 2))}
          </dd>
          <dt>{t("planner.h2_cost")}</dt>
          <dd className="num">
            {plan.h2_cost_per_kwh === null ? "—" : `${fmt(plan.h2_cost_per_kwh, 2)} €`} · {t("planner.h2_cost.vs")} {fmt(plan.grid_price_per_kwh, 3)} €
          </dd>
          <dt>{t("planner.h2_year")}</dt>
          <dd className="num">
            {fmt(h.flows.h2_produced_kg, 1)} kg · {fmt(h.flows.h2_useful_kwh)} kWh
          </dd>
          <dt>{t("planner.tank")}</dt>
          <dd className="num">{fmt(plan.tank_volume_m3, 2)} m³</dd>
        </dl>
        <Merit rows={plan.merit} />
        <p className="hint">{t("planner.assumptions.hint")}</p>
      </Card>
    </>
  );
}

function SweepMap({ plan, onTry }: { plan: Plan; onTry: (kw: number, kg: number) => void }) {
  const { t, fmt } = useI18n();
  const kws = useMemo(() => [...new Set(plan.sweep.map((r) => r.electrolyser_kw))].sort((a, b) => a - b), [plan]);
  const kgs = useMemo(() => [...new Set(plan.sweep.map((r) => r.tank_kg))].sort((a, b) => a - b), [plan]);
  const values = plan.sweep.map((r) => r.self_sufficiency);
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const cell = (kw: number, kg: number) => plan.sweep.find((r) => r.electrolyser_kw === kw && r.tank_kg === kg);
  const current = plan.scenarios.hestia.sizes;

  return (
    <div className="stack">
      <div className="sweep" style={{ gridTemplateColumns: `90px repeat(${kgs.length}, minmax(0, 1fr))` }}>
        <span className="faint corner">kW ↓ · kg →</span>
        {kgs.map((kg) => (
          <span key={kg} className="faint num head">
            {fmt(kg)} kg
          </span>
        ))}
        {kws.map((kw) => (
          <div key={kw} style={{ display: "contents" }}>
            <span className="faint num head">{fmt(kw, 1)} kW</span>
            {kgs.map((kg) => {
              const r = cell(kw, kg);
              if (!r) return <span key={kg} />;
              const share = hi > lo ? (r.self_sufficiency - lo) / (hi - lo) : 1;
              const rec = r.electrolyser_kw === plan.recommended.electrolyser_kw && r.tank_kg === plan.recommended.tank_kg;
              const cur = r.electrolyser_kw === current.electrolyser_kw && r.tank_kg === current.tank_kg;
              return (
                <button
                  key={kg}
                  className={`heat-cell ${rec ? "rec" : ""} ${cur ? "cur" : ""}`}
                  style={{
                    background: `color-mix(in oklab, var(--accent) ${Math.round(15 + share * 80)}%, var(--surface-3))`,
                    color: share > 0.55 ? "#04201d" : "var(--text)",
                  }}
                  title={`${fmt(kw, 1)} kW · ${fmt(kg)} kg — ${fmt(r.self_sufficiency * 100)} % · ${fmt(r.capex)} € · NPV ${fmt(r.npv)} €`}
                  onClick={() => onTry(kw, kg)}
                >
                  {fmt(r.self_sufficiency * 100)}%
                </button>
              );
            })}
          </div>
        ))}
      </div>
      <p className="hint" style={{ margin: 0 }}>
        {t("planner.sweep.hint")}
      </p>
    </div>
  );
}
