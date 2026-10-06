# Devlog

## 0 · The PFA prototype (spring 2026, team of three)

**Idea.** Solar surplus -> electrolyser -> stored hydrogen -> heat for the
building. First sketch was a three-layer microgrid: Raspberry Pi for the AI,
a PLC for real-time control, sensors in the field. PEM stack, hydrogen
boiler, NN / LSTM / RL on top. Open question at the start: full simulation,
mock-up, or both.

**main.ino.** Went hybrid. ESP32 on Wi-Fi, DS18B20, pH probe, level switch,
pumps and a fan on relays, MQTT to Node-RED, e-mail alerts. Threshold rules,
on/off.

Limits once it ran: no operating modes, no sensor validation, an alert
e-mail every 2 s for as long as a fault lasted, telemetry split across too
many topics.

**hydrogen-esp32_project.**
- AUTO / MANUAL / SAFE state machine, commands over MQTT (mode, forced
  outputs, thresholds)
- plausibility gate on every sensor, 5-sample median filter on pH
- alert cooldown with edge detection, Wi-Fi watchdog reboot
- one JSON telemetry message, heartbeat + LWT
- ML layer: PV forecast (NN), heat demand (LSTM), production policy (RL),
  leak anomaly model; a year of synthetic weather to train on; an
  electrochemical model replacing the bell-curve stack

**h2_project.** Firmware split into modules (PlatformIO). Flask backend whose
control logic mirrors the firmware rules, physics-based twin, two dashboards
(simulation, real bench). Report and demo end of May.

Summer: parked during an internship.

## 1 · From bench to product (autumn 2026)

The bench worked. Would it hold up in a real building, and would anyone
trust it?

### What happens when something fails?
- Controller rules pulled out of the firmware into a spec with test vectors,
  run by both the Python twin and the C++ firmware. Two bugs surfaced:
  manual mode skipped every interlock, over-temp also cut the cooling pump.
- pH control dropped: 30 % KOH sits above pH 14. Electrolyte held at
  25-32 wt% by density, dosing only while stopped.
- Two-stage H2 detection: 500 ppm stops production, extraction until 5 min
  clean; 2 000 ppm cuts power and latches, fan keeps running.
- Cooling loop holds 50-55 °C while producing. The old 60 -> 40 °C cycle
  would never restart on a 40 °C day.
- Storage interlock at 95 % MAWP, relief journalled.
- Signed commands with replay protection, production permit as a lease that
  expires if the gateway goes quiet.

### Can the detector still be believed?
- Integrity checks per sensor: drift, frozen, dead, poisoned.
- Bump tests and calibrations on a schedule, pass/fail computed from the
  readings.
- Hash-chained HMAC journal, printable report for an inspector or an
  insurer.
- No trusted H2 sensor, no permit.

### Are the numbers real?
- Constants replaced with published models: HDKR + PVWatts for tilted PV,
  alkaline cell voltage and Faraday efficiency, KOH properties, Abel-Noble
  tank pressure, heat pump, two-node building.
- PV had been ~18 % high. KOH conductivity peaks inside the target band, so
  conductivity can't resolve it: density instead.
- Twin replays a measured Tunis year (Open-Meteo). First winter run: the
  battery was driving the stack at night. Now it only bridges clouds.
- Bench model (the PFA bench on its lab supply) and a whole-site model
  (PV, battery, tank, fuel cell or H2 boiler, reversible heat pump, grid).
- Season jumps froze the anti-short-cycling timer: twin gets a monotonic
  clock.

### Where should the sun go?
- Merit order: kg CO2 avoided x 300 €/t (UBA) + € saved. Tunis: battery,
  grid (export capped at 30 % of production, law 2015-12), hydrogen. Paris:
  battery, hydrogen, grid. Curtailment last.
- Fuel cell beats the H2 boiler once there's a heat pump.
- Hydrogen kept for heating, a full tank or an outage.

### Does it pay?
- Hourly year on the simulator's physics, four set-ups, size sweep, net
  value over 20 years.
- STEG tariffs and local install costs. Hydrogen rarely pays on cost alone;
  the verdict says so. Tank volume shown with every size.

### Who uses it?
- Three people in mind: the owner, the installer, the inspector.
- React + TypeScript, EN/FR, ISA-101: neutral by default, colour for
  abnormal states, every state in words.
- Annunciator strip, P&ID with faceplates, trends against their limits.
- Public home page, one-click demo, private simulator per visitor with 13
  scenarios and fault injection.
- Control-panel look.

### How does it ship?
- One loop: link -> trust -> energy manager -> journal -> SSE.
- ML models to ONNX, SHA-256 manifest checked before loading.
- Docker (Caddy, Mosquitto with TLS), CI with CodeQL, gitleaks and Trivy.
- Threat model caught two leaks: env values in config errors, input echoed
  in validation errors.
- E-mail alerts opt-in, credentials only in `.env`.

### Next
- Certified detector and pressure vessel in place of the bench parts.
- Certification path: ISO 22734 for the generator, ISO 26142 and
  IEC 60079-29-2 for detection and its upkeep. Interlocks, maintenance plan
  and journal already point that way.
