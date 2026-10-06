#pragma once
// =============================================================================
// HARDWARE AND SITE CONFIGURATION (not secret: credentials go in secrets.h)
//
// Everything that depends on how a particular installation is wired or named
// lives here. Safety limits do NOT: they are fixed in
// lib/hestia_core/src/hestia_controller.h because they come from the stack's
// datasheet, and the gateway enforces the same ones.
// =============================================================================

#define FIRMWARE_VERSION "hestia-fw-2.1.0"

// ── Identity (must match the gateway's .env) ──────────────────────────────────
// Topics are  <TOPIC_PREFIX>/<SITE_ID>/<DEVICE_ID>/{telemetry,cmd,ack,status}
#define TOPIC_PREFIX "hestia"  // HESTIA_TOPIC_PREFIX
#define SITE_ID "site1"        // HESTIA_SITE_ID   (letters, digits, - and _)
#define DEVICE_ID "esp32_1"    // HESTIA_DEVICE_ID (letters, digits, - and _)

// ── GPIO assignment (ESP32 DevKit v1) ─────────────────────────────────────────
// Inputs
#define PIN_ONEWIRE 13  // DS18B20 electrolyte temperature (4.7 kΩ pull-up to 3.3 V)
#define PIN_DENSITY 34  // electrolyte density transmitter, 4-20 mA across the shunt (ADC1, input only)
#define PIN_H2 35       // MQ-8 analog output through a 5 V → 3.3 V divider (ADC1, input only)
#define PIN_TANK 36     // storage pressure transmitter, 4-20 mA across the shunt (ADC1 VP, input only)
#define PIN_LEVEL 32    // float switch in the electrolyte tank, to GND (INPUT_PULLUP)
// Outputs (relay board, active HIGH)
#define PIN_ELECTROLYSER 25  // relay 3: electrolyser power
#define PIN_COOLING_PUMP 33  // relay 4: cooling circulation pump (NO contact)
#define PIN_KOH_DOSING 26    // relay 1: peristaltic pump, 45 wt% KOH concentrate
#define PIN_WATER_MAKEUP 27  // relay 2: deionised water from the reserve tank
#define PIN_VENTILATION 17   // relay 5: extraction fan. Supply it from OUTSIDE the
                             // electrolyser bus: it must keep running when the
                             // emergency relay has cut everything else.
// Emergency relay on the electrolyser power bus. Wire it so that the bus is
// powered only while the relay coil is energised: if the ESP32 resets, hangs
// or loses power, the bus opens by itself (fail-safe).
#define PIN_H2_RELAY 16

// ── Timing ────────────────────────────────────────────────────────────────────
#define CONTROL_PERIOD_MS 2000UL  // one control cycle + telemetry; = HESTIA_DEVICE_PERIOD_S
#define WATCHDOG_S 8              // reboot if the loop stalls this long
#define WIFI_RETRY_MS 10000UL
#define MQTT_RETRY_MS 5000UL

// ── Clock (needed to check command timestamps) ───────────────────────────────
// On a site without internet, point the first server at the gateway host.
#define NTP_SERVER_1 "pool.ntp.org"
#define NTP_SERVER_2 "time.google.com"

// ── 4-20 mA inputs ───────────────────────────────────────────────────────────
// Each loop current goes through a precision shunt to ground: 150 Ω turns
// 4-20 mA into 0.60-3.00 V. NAMUR NE43: outside 3.8-20.5 mA the transmitter
// is signalling a fault (or the loop is broken) and the reading is invalid.
#define SHUNT_OHMS 150.0f
#define LOOP_MIN_VALID_MA 3.8f
#define LOOP_MAX_VALID_MA 20.5f
#define ANALOG_SAMPLES 5  // median of 5 readings rejects relay switching spikes

// ── Electrolyte (KOH strength from density) ──────────────────────────────────
// A density transmitter in the electrolyte loop (vibrating element or
// differential pressure). KOH strength follows from density and temperature
// (lib/hestia_core: koh_wt_pct_from_density). A pH probe cannot be used at
// pH 14, and a conductivity cell cannot tell 26 from 28 wt% (it peaks there).
// Check it in 20 and 30 wt% standards and record the check in Hestia
// (Safety → Record a check → Calibration → Electrolyte); correct a constant
// error with the offset below.
#define DENSITY_AT_4MA 1.000f   // kg/L
#define DENSITY_AT_20MA 1.500f  // kg/L
#define DENSITY_OFFSET_KG_L 0.0f

// ── Pressurised storage (optional) ───────────────────────────────────────────
// The tank's maximum allowable working pressure, from its nameplate. Leave 0
// on a bench that vents its gas: the pressure interlock is then off. With a
// tank, production stops at 95 % of this pressure, or if the transmitter fails.
#define STORAGE_MAWP_BAR 0.0f
#define TANK_BAR_AT_20MA 40.0f  // transmitter range 0-40 bar (gauge)

// ── H₂ sensor (MQ-8) ──────────────────────────────────────────────────────────
// The heater needs time before readings mean anything: until then h2_valid is
// false, and the controller refuses to produce (an undetectable leak is not
// allowed to happen). Calibrate the clean-air voltage, then check the span
// with a bump test recorded in the dashboard.
#define H2_WARMUP_MS (180UL * 1000UL)
#define H2_CLEAN_AIR_VOLTS 0.40f
#define H2_VOLTS_PER_100PPM 0.03f
#define H2_DIVIDER_RATIO 1.5f     // 5 V sensor → 3.3 V ADC (e.g. 10 kΩ over 20 kΩ)
#define H2_MIN_VALID_VOLTS 0.05f  // below: wire cut or heater dead
#define H2_MAX_VALID_VOLTS 3.20f  // above: shorted or saturated
