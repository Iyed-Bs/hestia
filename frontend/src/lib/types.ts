// Shapes of the gateway's API (backend/src/hestia/api/routes.py, api/sim_routes.py,
// runtime/engine.py, domain/telemetry.py). Kept by hand: they are few and stable.

export type Role = "viewer" | "operator" | "admin";
export type Level = "ok" | "degraded" | "untrusted";
export type Mode = "AUTO" | "MANUAL" | "SAFE_SHUTDOWN";
export type Phase = "ELECTROLYSIS" | "COOLING" | "CONDITIONING";
export type HvacMode = "off" | "heat" | "cool";
export type EndUse = "fuel_cell" | "boiler";
export type SensorName = "h2" | "temperature" | "electrolyte" | "link";

export interface User {
  id: number;
  username: string;
  display_name: string;
  role: Role;
  disabled: boolean;
}

export interface Me {
  user: User;
  csrf: string;
  mode: "twin" | "device";
  site_name: string;
  version: string;
}

// One message from the controller (the ESP32, or its twin).
export interface Telemetry {
  device_id: string;
  seq: number;
  ts: string;
  uptime_s: number;
  firmware: string;
  electrolyte_c: number;
  koh_wt_pct: number;
  level_low: boolean;
  h2_ppm: number;
  tank_bar: number;
  temp_valid: boolean;
  koh_valid: boolean;
  h2_valid: boolean;
  tank_valid: boolean;
  mode: Mode;
  phase: Phase;
  reason: string;
  electrolyser: boolean;
  cooling_pump: boolean;
  koh_dosing: boolean;
  water_makeup: boolean;
  ventilation: boolean;
  h2_relay_closed: boolean;
  h2_warning: boolean;
  h2_alarm_latched: boolean;
  production_permit: boolean;
  koh_low_pct: number;
  koh_high_pct: number;
  temp_alert_c: number;
  h2_warning_ppm: number;
  h2_alarm_ppm: number;
  storage_mawp_bar: number;
}

// Energy, building and storage around the controller. Fields a bench does not
// have (no building, no tank) are null.
export interface Site {
  source: "twin" | "simulation" | "estimated" | "metered";
  timestamp: string;
  local_time: string;
  outdoor_c: number;
  humidity_pct: number;
  irradiance_wpm2: number;
  pv_w: number;
  electrolyser_w: number;
  available_w: number;
  load_w: number | null;
  heat_pump_w: number | null;
  fuel_cell_w: number | null;
  battery_w: number | null; // > 0 charging
  battery_soc_pct: number | null;
  grid_w: number | null; // > 0 importing, < 0 exporting
  curtailed_w: number | null;
  h2_tank_kg: number | null;
  h2_tank_pct: number | null;
  tank_bar: number | null;
  tank_mawp_bar: number | null;
  h2_vented_kg: number | null;
  indoor_c: number | null;
  hvac_mode: HvacMode | null;
  hvac_w: number | null; // thermal, > 0 heating, < 0 cooling
  fuel_cell_heat_w: number | null;
  boiler_w: number | null;
  end_use: EndUse | null;
  room_h2_ppm: number | null;
  dispatch?: string; // where the power went this step, and why (simulator)
}

export interface Finding {
  level: Level;
  code: string;
  message: string;
}

export interface SensorHealth {
  name: string;
  level: Level;
  score: number; // 0–100
  value: number | null;
  findings: Finding[];
  baseline_ppm?: number;
  drift_ppm?: number;
  received?: number;
  gaps?: number;
  reboots?: number;
}

export interface Trust {
  overall: Level;
  h2_trusted: boolean;
  h2_why: string;
  sensors: Record<SensorName, SensorHealth>;
}

export interface Hvac {
  mode: HvacMode;
  setpoint_c: number;
  allow_h2: boolean;
  emergency: boolean;
  reason: string;
}

export interface Energy {
  permit: boolean;
  permit_reason: string;
  hvac: Hvac;
}

export interface Advice {
  available: boolean;
  pv_forecast_w: number | null;
  heat_demand_forecast_w: number | null;
  production_level: number | null;
  production_label: string | null;
  anomaly: boolean;
  anomaly_severity: number;
}

export interface StackInfo {
  rated_w: number;
  min_w: number;
  cells: number;
  cell_voltage: number;
  current_a: number;
  faraday_efficiency: number;
  heat_w: number;
  kwh_per_kg: number | null;
  h2_g_per_h: number;
}

export interface TwinInfo {
  model: "bench" | "site";
  speed: number;
  paused: boolean;
  faults: string[];
  available_faults: string[];
  sim_time: string | null;
  local_time: string;
  totals: Record<string, number>;
  electrolyte_l: number;
  electrolyte_wt_pct: number;
  conductivity_s_per_cm: number;
  stack: StackInfo;
}

export interface SimConfig {
  location: "tunis" | "paris";
  floor_area_m2: number;
  insulation: "poor" | "average" | "good";
  heat_setpoint_c: number;
  cool_setpoint_c: number;
  heat_pump_kw: number;
  pv_kwp: number;
  pv_tilt_deg: number;
  pv_azimuth_deg: number;
  battery_kwh: number;
  battery_kw: number;
  grid_connected: boolean;
  export_limit_kw: number;
  stack_kw: number;
  tank_kg: number;
  tank_mawp_bar: number;
  end_use: EndUse;
  fuel_cell_kw: number;
  boiler_kw: number;
  strategy: "value" | "battery_first" | "hydrogen_first";
  h2_use: "auto" | "grid_backup" | "heating_only" | "never";
  h2_reserve_pct: number;
}

export type Destination = "battery" | "export" | "hydrogen";

// What a kWh of surplus is worth in each destination, best first (domain/merit.py).
export interface MeritRow {
  destination: Destination;
  kg_co2: number;
  eur: number;
  score: number;
}

export interface SimInfo {
  owner: string;
  config: SimConfig;
  scenario: string | null;
  weather_source: string;
  all_faults: string[];
  busy: boolean;
  merit: MeritRow[];
  surplus_order: Destination[];
  tank: { capacity_kg: number; volume_m3: number; mawp_bar: number };
}

export interface Snapshot {
  version: string;
  mode: "twin" | "device";
  site_name: string;
  updated_at: string;
  online: boolean;
  telemetry: Telemetry | null;
  site: Site | null;
  trust: Trust | null;
  energy: Energy | null;
  advice: Advice;
  twin: TwinInfo | null;
  journal_head: string;
  sim: SimInfo | null;
}

export interface ScenarioCard {
  id: string;
  group: "energy" | "comfort" | "safety" | "trust";
  title: { en: string; fr: string };
  story: { en: string; fr: string };
  when: { month: number; day: number; hour: number };
  speed: number;
  faults: string[];
  config: Partial<SimConfig>;
}

export interface SimCatalog {
  scenarios: ScenarioCard[];
  defaults: SimConfig;
  locations: Record<string, { name: string; latitude: number; longitude: number }>;
  faults: string[];
}

export interface JournalEntry {
  id: number;
  ts: string;
  kind: string;
  severity: "info" | "warning" | "critical";
  actor: string;
  summary: string;
  details: Record<string, unknown>;
  hash: string;
}

export interface CheckStatus {
  kind: "bump_test" | "calibration" | "inspection";
  sensor: string;
  state: "ok" | "due_soon" | "overdue" | "never" | "failed";
  last_at: string | null;
  next_due: string | null;
  last_result: string | null;
  days_overdue: number;
}

export interface TrustPage {
  integrity: Trust | null;
  maintenance: CheckStatus[];
  h2_standing: { level: string; why: string };
  plan?: { bump_test_days: number; calibration_days: number; inspection_days: number; grace_days: number };
  baseline_ppm: number;
  journal_head?: string;
  ml?: { loaded: string[]; loading: string[]; errors: Record<string, string>; exported_at: string } | null;
}

export interface AlertStatus {
  enabled: boolean;
  provider: "gmail" | "smtp";
  sender: string;
  recipients: string[];
  cooldown_minutes: number;
  last_sent_at: string | null;
  last_error: string;
}

export interface Verification {
  ok: boolean;
  checked: number;
  head_hash: string;
  first_broken_id: number | null;
  problem: string;
}
