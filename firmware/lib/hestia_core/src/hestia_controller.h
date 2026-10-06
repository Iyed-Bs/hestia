#pragma once
// =============================================================================
// The Hestia safety controller, in portable C++.
//
// This is a line-by-line port of backend/src/hestia/domain/control.py and
// domain/commands.py. Both run the same scenario file
// (spec/controller_vectors.json): the Python one in the gateway's tests, this
// one in firmware/test/test_core (PlatformIO "native" environment, in CI).
// Change a rule here and not there, and CI fails.
//
// Nothing in this library touches Arduino or the hardware, so it compiles on
// a PC for the tests and on the ESP32 for the real thing.
//
// Thermal cycle of the alkaline stack (30 wt% KOH, 15-70 °C):
//   ELECTROLYSIS ─(≥ 60 °C)─▶ COOLING ─(≤ 50 °C)─▶ CONDITIONING ─(KOH in band)─▶ ELECTROLYSIS
//   ELECTROLYSIS ─(KOH below its band)─▶ CONDITIONING
// While producing, the cooling loop holds 50-55 °C; the stack stops to cool
// only when the loop cannot keep up.
//
// Hydrogen detection in two stages: > 500 ppm stops production and runs the
// extraction fan until the air has been clean for 5 minutes; > 2 000 ppm
// latches, cuts the power and keeps the fan running until a reasoned reset.
// =============================================================================
#include <stddef.h>
#include <stdint.h>

namespace hestia {

enum class Mode : uint8_t { AUTO, MANUAL, SAFE_SHUTDOWN };
enum class Phase : uint8_t { ELECTROLYSIS, COOLING, CONDITIONING };

const char* to_string(Mode mode);
const char* to_string(Phase phase);
bool parse_mode(const char* text, Mode& out);
bool parse_phase(const char* text, Phase& out);

// ── Fixed design limits ──────────────────────────────────────────────────────
// They come from the hardware (alkaline stack, 30 wt% KOH, 15-70 °C) and from
// gas-safety practice, not from preferences, so no command can move them.
constexpr float ELECTROLYTE_MAX_C = 70.0f;  // absolute maximum of the stack
constexpr float REGULATE_ON_C = 55.0f;      // cooling loop on while producing
constexpr float REGULATE_OFF_C = 50.0f;     // cooling loop off while producing
constexpr float COOL_AT_C = 60.0f;          // the loop could not hold: stop and cool
constexpr float RESUME_AT_C = 50.0f;        // cool enough to run again
constexpr int H2_DEBOUNCE_READINGS = 3;     // consecutive readings above a level before it counts
constexpr uint32_t VENTILATION_PURGE_MS = 300000UL;  // extraction runs 5 min after the gas has gone
constexpr float STORAGE_INTERLOCK_FRACTION = 0.95f;  // of the tank's maximum allowable working pressure

struct Limits {
    float minimum;
    float maximum;
    bool contains(float v) const { return v >= minimum && v <= maximum; }
};

// Operator-adjustable thresholds and the only ranges they may take. A value
// outside its range is refused, never clamped, so the journal shows exactly
// what was asked.
constexpr Limits KOH_LOW_LIMITS{20.0f, 30.0f};   // wt%
constexpr Limits KOH_HIGH_LIMITS{28.0f, 35.0f};  // wt%
constexpr float KOH_MIN_BAND = 2.0f;             // the band must stay at least this wide
constexpr Limits TEMP_ALERT_LIMITS{COOL_AT_C + 2.0f, ELECTROLYTE_MAX_C};  // stricter, never looser
constexpr Limits H2_WARNING_LIMITS{100.0f, 1000.0f};  // ppm (0.25-2.5 % of the LEL)
constexpr Limits H2_ALARM_LIMITS{500.0f, 4000.0f};    // ppm, at most 10 % of the LEL

struct Thresholds {
    float koh_low_pct = 25.0f;               // below: dose KOH concentrate before the next run
    float koh_high_pct = 32.0f;              // above: add pure water
    float temp_alert_c = ELECTROLYTE_MAX_C;  // emergency stop at or above this
    float h2_warning_ppm = 500.0f;           // stage 1
    float h2_alarm_ppm = 2000.0f;            // stage 2
};

// One instant of sensor data. The *_valid flags are the device's own
// plausibility checks; the deeper integrity checks (drift, frozen sensor…)
// run on the gateway and act through the production permit.
struct Reading {
    float electrolyte_c = 0.0f;
    float koh_wt_pct = 30.0f;  // from the density transmitter
    bool level_low = false;
    float h2_ppm = 0.0f;
    uint32_t t_ms = 0;      // monotonic clock (millis), for the ventilation purge
    float tank_bar = 0.0f;  // storage pressure; ignored without pressurised storage
    bool temp_valid = true;
    bool koh_valid = true;
    bool h2_valid = true;
    bool tank_valid = true;
};

// Everything the controller remembers between two cycles.
struct State {
    Mode mode = Mode::AUTO;
    Phase phase = Phase::ELECTROLYSIS;
    // Granted by the gateway's energy manager. Starts false: nothing is
    // produced until the gateway has positively allowed it (fail-safe).
    bool production_permit = false;
    Thresholds thresholds;
    // Maximum allowable working pressure of the storage tank; 0 when the gas
    // is not stored under pressure (the bench vents it).
    float storage_mawp_bar = 0.0f;
    // Gas detection.
    bool h2_warning = false;
    int h2_warning_count = 0;
    bool h2_alarm_latched = false;
    int h2_alarm_count = 0;
    uint32_t gas_seen_ms = 0;  // last time a warning-level concentration was read
    // Cooling loop while producing (hysteresis between REGULATE_OFF_C and REGULATE_ON_C).
    bool cooling_on = false;
    // Manual-mode requests (only read in MANUAL mode).
    bool manual_electrolyser = false;
    bool manual_cooling_pump = false;
    bool manual_koh_dosing = false;
    bool manual_water_makeup = false;
    bool manual_ventilation = false;
};

constexpr size_t REASON_LEN = 200;  // telemetry allows 200 characters

struct Outputs {
    bool electrolyser = false;
    bool cooling_pump = false;
    bool koh_dosing = false;    // peristaltic pump, 45 wt% KOH concentrate
    bool water_makeup = false;  // deionised water from the reserve tank
    bool ventilation = false;   // extraction fan (ATEX-rated, on its own supply)
    // true = emergency relay closed (normal). false = tripped: it cuts the
    // electrolyser power bus in hardware, whatever the software does next.
    bool h2_relay_closed = true;
    Phase phase = Phase::ELECTROLYSIS;
    char reason[REASON_LEN] = "";
};

// Run both detection stages. Returns true while the extraction fan must run.
bool update_gas_detection(const Reading& reading, State& state);

// Decide every actuator for one control cycle. Mutates `state`.
Outputs compute_outputs(const Reading& reading, State& state);

// ── Commands (domain/commands.py) ────────────────────────────────────────────

enum class Actuator : uint8_t { ELECTROLYSER, COOLING_PUMP, KOH_DOSING, WATER_MAKEUP, VENTILATION };
bool parse_actuator(const char* text, Actuator& out);
const char* to_string(Actuator actuator);

struct CommandResult {
    bool ok = false;
    char message[160] = "";
};

CommandResult set_mode(State& state, Mode mode);
// `reason` is what the operator checked and found: 10 to 500 characters.
CommandResult reset_h2_alarm(State& state, const char* reason);
CommandResult set_actuator(State& state, Actuator actuator, bool on);
CommandResult set_threshold(State& state, const char* name, float value);

// KOH mass fraction (wt%) from the electrolyte's density and temperature: the
// inverse of physics/electrolyte.py density_kg_per_l. Conductivity cannot be
// used here: it peaks inside the operating band (26 and 28 wt% read the same).
float koh_wt_pct_from_density(float density_kg_l, float temp_c);

// Number of characters (not bytes) in a UTF-8 string, as Python counts them.
size_t utf8_length(const char* text);

}  // namespace hestia
