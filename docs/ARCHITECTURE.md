# Architecture

Hestia has three parts that run in three places, and one specification that
binds two of them.

```
┌────────────── Building network ──────────────────────────────────────────────┐
│                                                                               │
│  ESP32 controller            Gateway (mini-PC / Pi, Docker)        Browsers    │
│  ┌────────────────┐  MQTT    ┌──────────────────────────────┐ HTTPS ┌───────┐ │
│  │ safety rules   │──TLS────▶│ engine ─ trust layer ─ ML    │◀──────│ React │ │
│  │ (hestia_core)  │◀─signed──│ energy manager ─ journal     │──SSE─▶│ dash- │ │
│  │ sensors/relays │ commands │ planner ─ API ─ users        │       │ board │ │
│  └────────────────┘          └──────────────────────────────┘       └───────┘ │
│          ▲                              ▲                                     │
│          └──── spec/controller_vectors.json, spec/command_vectors.json ───────┘
│               (same rules, same envelopes, tested on both sides)              │
└───────────────────────────────────────────────────────────────────────────────┘
```

On a demo, the controller is replaced by the **digital twin**: a virtual
ESP32 running the same controller (in Python) over a physical model of the
plant, driven by a real measured year of weather. Everything above the link
(engine, trust layer, dashboard) runs unchanged.

The twin has two models:

- **the bench** (`BenchModel`): the échantillon as it runs on its lab
  supply, with no building and no storage. This is what the live page shows.
- **a whole site** (`SiteModel`): a building around a scaled-up stack, with
  PV, battery, a pressurised tank, a fuel cell or a hydrogen boiler, a heat
  pump that heats and cools, and the grid. Each signed-in session can run a
  private one (`sim/`): its own engine, journal and trust layer, in memory,
  with e-mail alerts off. Nothing in a simulation touches the live gateway.

## Who decides what

Decisions are layered so that each layer can only make the one below it
*more* careful, never less.

| Layer | Where | Decides | Can be overruled by |
|---|---|---|---|
| Safety controller | ESP32 (`firmware/lib/hestia_core`) / twin (`domain/control.py`) | Every relay, every 2 s: two-stage gas detection and extraction, thermal cycle and cooling loop, electrolyte band, storage pressure, manual mode | Nothing |
| Trust layer | Gateway (`trust/`) | Whether the H₂ sensor can be believed: integrity checks + maintenance standing | Nothing: an untrusted sensor withdraws the permit |
| Energy manager | Gateway (`domain/energy.py`) | The production permit (available power against the stack's minimum load, tank, short-cycling) and comfort: heat, cool, and whether stored hydrogen may be used. Surplus goes to battery, grid and hydrogen in the site's merit order (`domain/merit.py`) | The two layers above |
| ML advisor | Gateway (`ml/runtime.py`) | Forecasts and a suggested production level | Everything: it can delay a start, nothing more |

The permit reaches the controller as a **lease** (`permit_valid_s`, refreshed
every third of it). If the gateway disappears, production stops on its own.

## Repository map

```
backend/src/hestia/
  physics/     solar.py, electrolyser.py, electrolyte.py, storage.py, conversion.py,
               building.py: every model with its source, shared by twin and planner
  domain/      control.py (the controller spec), commands.py, energy.py, merit.py, telemetry.py
  trust/       journal.py (hash chain + HMAC), maintenance.py, integrity.py, report.py
  runtime/     engine.py (the loop), links.py (twin | MQTT), envelope.py (signatures),
               site.py (live weather), notifier.py (e-mail)
  twin/        plant.py (bench and site models), device.py (virtual ESP32), params.py,
               weather.py, faults.py
  sim/         config.py (a visitor's site), scenarios.py, session.py, manager.py
  planner/     model.py (hourly year on the same physics, four scenarios, size sweep),
               presets.py, weather.py
  ml/          runtime.py (ONNX advisor), models/ (ONNX + manifest with SHA-256)
  security/    auth.py (argon2id, sessions, throttling), headers.py (CSP, HSTS)
  api/         routes.py, sim_routes.py, deps.py (roles, CSRF)
  storage/     db.py (SQLite, WAL, migrations)
  main.py, settings.py, cli.py
frontend/src/  pages (Landing, Overview, Live, Simulator, Planner, Safety, Report, Users),
               components (annunciator, bench and site diagrams, controller panel, charts),
               lib (api, i18n, live streams)
firmware/      lib/hestia_core (portable C++), src (hardware glue), test (native tests)
ml/            training and ONNX export (not needed to run the gateway)
spec/          the shared controller and command vectors
deploy/        Caddy, Mosquitto (TLS, ACL), setup scripts
```

## One cycle of the engine

`runtime/engine.py`, every time the link delivers a reading (every 2 s on a
real controller, every simulated step on the twin):

1. **Telemetry** arrives through the link (`TwinLink` or `MqttLink`), already
   validated against `domain/telemetry.py` (strict bounds, unknown fields refused).
2. **Trust:** `integrity.observe()` feeds the sensor histories;
   `integrity.assess()` returns a level per sensor (ok / degraded / untrusted)
   with its findings, combined with the maintenance standing.
3. **Advice:** the ML advisor updates its forecasts (if its models loaded).
4. **Decision:** `EnergyManager.decide()` returns the permit and the comfort
   intent (heat, cool or nothing, and whether hydrogen may be used), with a
   sentence explaining each.
5. **Action:** the permit is sent (signed) to the controller; the comfort
   intent to the site (the simulated building, or a retained MQTT topic for
   a real heating system).
6. **Record:** transitions (alarm latched, sensor trust changed, controller
   offline, rejected messages…) are journalled and, when serious, e-mailed.
7. **Broadcast:** a snapshot goes to every open dashboard (Server-Sent Events,
   newest-only for slow clients).

## Two clocks

The twin's weather position jumps when an operator picks a scenario ("winter
night"). Durations (anti short-cycling, sensor windows, fault onset) are
measured on a separate forward-only clock, so a jump cannot freeze or skip a
timer. On a real site both are the monotonic clock.

## Storage

One SQLite database in WAL mode (`/data/hestia.sqlite3`): `journal`,
`maintenance`, `users`, `login_failures`, `settings`, with versioned
migrations. A building gateway serves a handful of people; SQLite keeps the
whole state in one file that is trivial to back up with `.env`.

## Testing

| What | How |
|---|---|
| Controller rules, both implementations | `spec/controller_vectors.json`: pytest and PlatformIO native |
| Command signatures, both implementations | `spec/command_vectors.json`: pytest and PlatformIO native |
| Physics | known values: equinox declination, PVGIS-range yield, stack voltage and Faraday efficiency, KOH density, real-gas pressure, EN 14511 COP/EER |
| Twin and energy logic | the bench's cooling loop, leaks with and without extraction, cooling failure; a site's energy balance closing to the watt, winter fuel cell and boiler, heat wave, grid outage, tank interlock and relief |
| Simulator | private per sign-in, never reaches the live journal or e-mail, scenarios rebuild equipment, fast-forward, eviction |
| Trust layer | journal tampering, maintenance pass/fail, each sensor fault |
| API | end to end on the twin: roles, CSRF, throttling, alarm reset, tampering, SPA serving |
| Planner | electricity and heat balances close, steady year, local time, fuel cell beats boiler with a heat pump, presets |
| Dashboard | Vitest: API client (CSRF, 401), history, diagram flows, annunciator priorities, translations complete |
| Image | CI builds it, starts it read-only, checks its health, scans it with Trivy |

See [DESIGN-DECISIONS.md](DESIGN-DECISIONS.md) for why things are the way
they are, and [../SECURITY.md](../SECURITY.md) for the threat model.
