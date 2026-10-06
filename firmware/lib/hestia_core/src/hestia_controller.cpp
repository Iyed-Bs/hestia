// Port of backend/src/hestia/domain/control.py and domain/commands.py.
// Keep the two in step: spec/controller_vectors.json checks both.
#include "hestia_controller.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

namespace hestia {

// ── Names ────────────────────────────────────────────────────────────────────

const char* to_string(Mode mode) {
    switch (mode) {
        case Mode::AUTO: return "AUTO";
        case Mode::MANUAL: return "MANUAL";
        default: return "SAFE_SHUTDOWN";
    }
}

const char* to_string(Phase phase) {
    switch (phase) {
        case Phase::ELECTROLYSIS: return "ELECTROLYSIS";
        case Phase::COOLING: return "COOLING";
        default: return "CONDITIONING";
    }
}

const char* to_string(Actuator actuator) {
    switch (actuator) {
        case Actuator::ELECTROLYSER: return "electrolyser";
        case Actuator::COOLING_PUMP: return "cooling_pump";
        case Actuator::KOH_DOSING: return "koh_dosing";
        case Actuator::WATER_MAKEUP: return "water_makeup";
        default: return "ventilation";
    }
}

bool parse_mode(const char* text, Mode& out) {
    if (text == nullptr) return false;
    if (strcmp(text, "AUTO") == 0) { out = Mode::AUTO; return true; }
    if (strcmp(text, "MANUAL") == 0) { out = Mode::MANUAL; return true; }
    if (strcmp(text, "SAFE_SHUTDOWN") == 0) { out = Mode::SAFE_SHUTDOWN; return true; }
    return false;
}

bool parse_phase(const char* text, Phase& out) {
    if (text == nullptr) return false;
    if (strcmp(text, "ELECTROLYSIS") == 0) { out = Phase::ELECTROLYSIS; return true; }
    if (strcmp(text, "COOLING") == 0) { out = Phase::COOLING; return true; }
    if (strcmp(text, "CONDITIONING") == 0) { out = Phase::CONDITIONING; return true; }
    return false;
}

bool parse_actuator(const char* text, Actuator& out) {
    if (text == nullptr) return false;
    if (strcmp(text, "electrolyser") == 0) { out = Actuator::ELECTROLYSER; return true; }
    if (strcmp(text, "cooling_pump") == 0) { out = Actuator::COOLING_PUMP; return true; }
    if (strcmp(text, "koh_dosing") == 0) { out = Actuator::KOH_DOSING; return true; }
    if (strcmp(text, "water_makeup") == 0) { out = Actuator::WATER_MAKEUP; return true; }
    if (strcmp(text, "ventilation") == 0) { out = Actuator::VENTILATION; return true; }
    return false;
}

float koh_wt_pct_from_density(float density_kg_l, float temp_c) {
    return (density_kg_l / (1.0f - 0.0005f * (temp_c - 25.0f)) - 0.9971f) / 0.00973f;
}

size_t utf8_length(const char* text) {
    size_t n = 0;
    for (const unsigned char* p = reinterpret_cast<const unsigned char*>(text); p && *p; ++p) {
        if ((*p & 0xC0) != 0x80) ++n;  // count every byte that starts a character
    }
    return n;
}

// ── Controller ───────────────────────────────────────────────────────────────

static void append(char* buffer, size_t size, const char* text) {
    size_t used = strlen(buffer);
    if (used + 1 < size) snprintf(buffer + used, size - used, "%s", text);
}

// Stage 2 latches and stays latched until a reasoned reset. Stage 1 comes on
// after three readings and goes off once the air has been clean for the purge
// time. Neither reacts to a single noisy reading.
bool update_gas_detection(const Reading& r, State& s) {
    const Thresholds& thr = s.thresholds;
    const bool valid = r.h2_valid;

    if (valid && r.h2_ppm > thr.h2_alarm_ppm) {
        s.h2_alarm_count += 1;
        if (s.h2_alarm_count >= H2_DEBOUNCE_READINGS && !s.h2_alarm_latched) {
            s.h2_alarm_latched = true;
            s.mode = Mode::SAFE_SHUTDOWN;
        }
    } else if (!s.h2_alarm_latched) {
        s.h2_alarm_count = 0;
    }

    if (valid && r.h2_ppm > thr.h2_warning_ppm) {
        s.h2_warning_count += 1;
        if (s.h2_warning_count >= H2_DEBOUNCE_READINGS) s.h2_warning = true;
        if (s.h2_warning) s.gas_seen_ms = r.t_ms;
    } else {
        s.h2_warning_count = 0;
        // An unreadable sensor never ends a warning: only clean, valid air does.
        // Unsigned difference: correct across the millis() wrap.
        if (s.h2_warning && valid && uint32_t(r.t_ms - s.gas_seen_ms) >= VENTILATION_PURGE_MS) {
            s.h2_warning = false;
        }
    }
    return s.h2_warning || s.h2_alarm_latched;
}

static void advance_phase(const Reading& r, State& s) {
    const Thresholds& thr = s.thresholds;
    if (s.phase == Phase::ELECTROLYSIS && r.electrolyte_c >= COOL_AT_C) {
        s.phase = Phase::COOLING;
    } else if (s.phase == Phase::ELECTROLYSIS && r.koh_wt_pct < thr.koh_low_pct) {
        // Weak electrolyte: stop, dose concentrate, then run again.
        s.phase = Phase::CONDITIONING;
    } else if (s.phase == Phase::COOLING && r.electrolyte_c <= RESUME_AT_C) {
        s.phase = Phase::CONDITIONING;
    } else if (s.phase == Phase::CONDITIONING && r.koh_wt_pct >= thr.koh_low_pct &&
               r.koh_wt_pct <= thr.koh_high_pct && !r.level_low) {
        s.phase = Phase::ELECTROLYSIS;
    }
}

// Reasons the stack may not run, whatever the mode, as one comma-separated list.
static void interlocks(const Reading& r, const State& s, bool overheated, char* out, size_t size) {
    out[0] = '\0';
    auto block = [out, size](const char* why) {
        if (out[0] != '\0') append(out, size, ", ");
        append(out, size, why);
    };
    if (overheated) block("electrolyte over-temperature");
    if (r.level_low) block("electrolyte level low");
    // A leak that cannot be detected cannot be allowed to happen.
    if (!r.h2_valid) block("H\xE2\x82\x82 sensor unavailable");
    if (s.h2_warning) block("hydrogen detected in the room");
    if (s.storage_mawp_bar > 0.0f) {
        if (!r.tank_valid) {
            block("storage pressure unknown");
        } else if (r.tank_bar >= STORAGE_INTERLOCK_FRACTION * s.storage_mawp_bar) {
            char why[64];
            snprintf(why, sizeof why, "storage at %.1f bar (limit %.0f bar)", r.tank_bar, s.storage_mawp_bar);
            block(why);
        }
    }
}

Outputs compute_outputs(const Reading& r, State& s) {
    const Thresholds& thr = s.thresholds;
    const bool ventilate = update_gas_detection(r, s);
    Outputs out;
    out.phase = s.phase;

    // 1. Stage-2 hydrogen alarm: everything off, relay tripped, extraction on.
    if (s.h2_alarm_latched) {
        out.ventilation = true;
        out.h2_relay_closed = false;
        snprintf(out.reason, sizeof out.reason,
                 "H\xE2\x82\x82 alarm latched (%.0f ppm): power cut, extraction running; inspect, then reset", r.h2_ppm);
        return out;
    }

    // 2. Temperature or electrolyte unreadable: nothing can be run safely.
    if (!r.temp_valid || !r.koh_valid) {
        out.ventilation = ventilate;
        snprintf(out.reason, sizeof out.reason, "Temperature or electrolyte sensor fault: cannot trust readings");
        return out;
    }

    // 3. Operator shutdown.
    if (s.mode == Mode::SAFE_SHUTDOWN) {
        out.ventilation = ventilate;
        snprintf(out.reason, sizeof out.reason, "Safe shutdown requested by an operator");
        return out;
    }

    const bool overheated = r.electrolyte_c >= thr.temp_alert_c;
    char blockers[REASON_LEN - 40];
    interlocks(r, s, overheated, blockers, sizeof blockers);

    // 4. Manual mode: the operator drives, but every interlock still applies.
    if (s.mode == Mode::MANUAL) {
        out.electrolyser = s.manual_electrolyser && blockers[0] == '\0';
        if (s.manual_electrolyser && blockers[0] != '\0') {
            snprintf(out.reason, sizeof out.reason, "Manual control: electrolyser blocked (%s)", blockers);
        } else {
            snprintf(out.reason, sizeof out.reason, "Manual control");
        }
        // Cooling can always be forced on in an overheat, never forced off.
        out.cooling_pump = s.manual_cooling_pump || overheated;
        out.koh_dosing = s.manual_koh_dosing && !overheated;
        out.water_makeup = s.manual_water_makeup && !overheated;
        // Ventilation can be switched on by hand, never off while gas is present.
        out.ventilation = s.manual_ventilation || ventilate;
        return out;
    }

    // 5. AUTO: thermal cycle.
    advance_phase(r, s);
    out.phase = s.phase;
    out.ventilation = ventilate;
    // Topping up with water keeps both the level and the KOH strength.
    out.water_makeup = r.level_low || r.koh_wt_pct > thr.koh_high_pct;
    // Concentrate is only dosed while the stack is stopped.
    out.koh_dosing = s.phase != Phase::ELECTROLYSIS && r.koh_wt_pct < thr.koh_low_pct;

    if (s.phase == Phase::ELECTROLYSIS) {
        if (r.electrolyte_c >= REGULATE_ON_C) {
            s.cooling_on = true;
        } else if (r.electrolyte_c <= REGULATE_OFF_C) {
            s.cooling_on = false;
        }
        if (!s.production_permit) {
            if (blockers[0] != '\0') append(blockers, sizeof blockers, ", ");
            append(blockers, sizeof blockers, "no production permit from the energy manager");
        }
        out.electrolyser = blockers[0] == '\0';
        out.cooling_pump = s.cooling_on;
        if (out.electrolyser) {
            snprintf(out.reason, sizeof out.reason, "%s",
                     s.cooling_on ? "Producing H\xE2\x82\x82, cooling loop on" : "Producing H\xE2\x82\x82");
        } else {
            snprintf(out.reason, sizeof out.reason, "Electrolyser off: %s", blockers);
        }
    } else if (s.phase == Phase::COOLING) {
        s.cooling_on = false;  // the loop restarts from its own hysteresis after the stop
        out.cooling_pump = true;
        snprintf(out.reason, sizeof out.reason, "Cooling the electrolyte");
    } else {  // CONDITIONING
        s.cooling_on = false;
        snprintf(out.reason, sizeof out.reason, "Bringing the electrolyte back into its band before the next run");
    }

    // 6. Over-temperature override: stop production and dosing, but keep the
    //    cooling pump running (the prototype stopped it too, which removed the
    //    one thing that brings the temperature down).
    if (overheated) {
        Outputs hot;
        hot.cooling_pump = true;
        hot.ventilation = ventilate;
        hot.phase = s.phase;
        snprintf(hot.reason, sizeof hot.reason,
                 "Over-temperature (%.1f \xC2\xB0" "C \xE2\x89\xA5 %.0f \xC2\xB0" "C): electrolyser stopped, cooling forced on",
                 r.electrolyte_c, thr.temp_alert_c);
        return hot;
    }
    return out;
}

// ── Commands ─────────────────────────────────────────────────────────────────

static CommandResult result(bool ok, const char* message) {
    CommandResult r;
    r.ok = ok;
    snprintf(r.message, sizeof r.message, "%s", message);
    return r;
}

CommandResult set_mode(State& s, Mode mode) {
    if (s.h2_alarm_latched && mode != Mode::SAFE_SHUTDOWN) {
        return result(false, "H\xE2\x82\x82 alarm latched: reset it before changing mode");
    }
    s.mode = mode;
    if (mode != Mode::MANUAL) {
        // Leaving manual mode never leaves an actuator latched on.
        s.manual_electrolyser = s.manual_cooling_pump = s.manual_koh_dosing = false;
        s.manual_water_makeup = s.manual_ventilation = false;
    }
    CommandResult r;
    r.ok = true;
    snprintf(r.message, sizeof r.message, "Mode set to %s", to_string(mode));
    return r;
}

CommandResult reset_h2_alarm(State& s, const char* reason) {
    const size_t length = utf8_length(reason);
    if (reason == nullptr || length < 10 || length > 500) {
        return result(false, "A written reason of 10 to 500 characters is required");
    }
    if (!s.h2_alarm_latched) return result(false, "No H\xE2\x82\x82 alarm is latched");
    s.h2_alarm_latched = false;
    s.h2_alarm_count = 0;
    // Back to a safe, explicit state: the operator chooses AUTO again. The
    // stage-1 warning, if gas is still around, keeps ventilating.
    s.mode = Mode::SAFE_SHUTDOWN;
    return result(true, "H\xE2\x82\x82 alarm reset; system held in SAFE_SHUTDOWN until an operator selects a mode");
}

CommandResult set_actuator(State& s, Actuator actuator, bool on) {
    if (s.mode != Mode::MANUAL) return result(false, "Actuators can only be driven by hand in MANUAL mode");
    switch (actuator) {
        case Actuator::ELECTROLYSER: s.manual_electrolyser = on; break;
        case Actuator::COOLING_PUMP: s.manual_cooling_pump = on; break;
        case Actuator::KOH_DOSING: s.manual_koh_dosing = on; break;
        case Actuator::WATER_MAKEUP: s.manual_water_makeup = on; break;
        case Actuator::VENTILATION: s.manual_ventilation = on; break;
    }
    CommandResult r;
    r.ok = true;
    snprintf(r.message, sizeof r.message, "%s %s (manual)", to_string(actuator), on ? "ON" : "OFF");
    return r;
}

CommandResult set_threshold(State& s, const char* name, float value) {
    if (!isfinite(value)) return result(false, "value must be a finite number");
    Thresholds& thr = s.thresholds;
    const Limits* limits = nullptr;
    float* target = nullptr;
    if (name && strcmp(name, "koh_low_pct") == 0) {
        limits = &KOH_LOW_LIMITS;
        target = &thr.koh_low_pct;
    } else if (name && strcmp(name, "koh_high_pct") == 0) {
        limits = &KOH_HIGH_LIMITS;
        target = &thr.koh_high_pct;
    } else if (name && strcmp(name, "temp_alert_c") == 0) {
        limits = &TEMP_ALERT_LIMITS;
        target = &thr.temp_alert_c;
    } else if (name && strcmp(name, "h2_warning_ppm") == 0) {
        limits = &H2_WARNING_LIMITS;
        target = &thr.h2_warning_ppm;
    } else if (name && strcmp(name, "h2_alarm_ppm") == 0) {
        limits = &H2_ALARM_LIMITS;
        target = &thr.h2_alarm_ppm;
    } else {
        return result(false, "Unknown threshold");
    }
    CommandResult r;
    if (!limits->contains(value)) {
        snprintf(r.message, sizeof r.message, "%s must be between %g and %g", name,
                 static_cast<double>(limits->minimum), static_cast<double>(limits->maximum));
        return r;
    }
    // Related thresholds stay coherent: the KOH band cannot invert, stage 1 stays below stage 2.
    if (target == &thr.koh_low_pct && value > thr.koh_high_pct - KOH_MIN_BAND) {
        return result(false, "koh_low_pct must stay at least 2 below koh_high_pct");
    }
    if (target == &thr.koh_high_pct && value < thr.koh_low_pct + KOH_MIN_BAND) {
        return result(false, "koh_high_pct must stay at least 2 above koh_low_pct");
    }
    if (target == &thr.h2_warning_ppm && value >= thr.h2_alarm_ppm) {
        return result(false, "h2_warning_ppm must stay below h2_alarm_ppm");
    }
    if (target == &thr.h2_alarm_ppm && value <= thr.h2_warning_ppm) {
        return result(false, "h2_alarm_ppm must stay above h2_warning_ppm");
    }
    *target = value;
    r.ok = true;
    snprintf(r.message, sizeof r.message, "%s set to %g", name, static_cast<double>(value));
    return r;
}

}  // namespace hestia
