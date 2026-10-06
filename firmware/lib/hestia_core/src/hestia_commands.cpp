#include "hestia_commands.h"

#include <ArduinoJson.h>
#include <stdio.h>
#include <string.h>

namespace hestia {

void PermitLease::grant(bool permit, uint32_t valid_for_s, uint32_t now_ms) {
    if (valid_for_s == 0) valid_for_s = 1;
    if (valid_for_s > MAX_VALID_S) valid_for_s = MAX_VALID_S;
    granted_ = permit;
    until_ms_ = now_ms + valid_for_s * 1000UL;
}

bool PermitLease::active(uint32_t now_ms) const {
    // Signed difference: stays correct when millis() wraps after 49 days.
    return granted_ && int32_t(until_ms_ - now_ms) > 0;
}

static CommandResult refuse(const char* message) {
    CommandResult r;
    r.ok = false;
    snprintf(r.message, sizeof r.message, "%s", message);
    return r;
}

CommandResult apply_command_json(State& state, PermitLease& permit, const char* cmd_json, uint32_t now_ms) {
    JsonDocument doc;
    if (cmd_json == nullptr || deserializeJson(doc, cmd_json) != DeserializationError::Ok || !doc.is<JsonObject>()) {
        return refuse("Command is not a JSON object");
    }
    const char* kind = doc["kind"] | "";

    if (strcmp(kind, "set_mode") == 0) {
        Mode mode;
        if (!parse_mode(doc["mode"] | "", mode)) return refuse("Unknown mode");
        return set_mode(state, mode);
    }
    if (strcmp(kind, "reset_h2_alarm") == 0) {
        if (!doc["reason"].is<const char*>()) return refuse("A written reason of 10 to 500 characters is required");
        return reset_h2_alarm(state, doc["reason"].as<const char*>());
    }
    if (strcmp(kind, "set_actuator") == 0) {
        Actuator actuator;
        if (!parse_actuator(doc["actuator"] | "", actuator)) return refuse("Unknown actuator");
        if (!doc["on"].is<bool>()) return refuse("'on' must be true or false");
        return set_actuator(state, actuator, doc["on"].as<bool>());
    }
    if (strcmp(kind, "set_threshold") == 0) {
        if (!doc["value"].is<float>()) return refuse("value must be a finite number");
        return set_threshold(state, doc["name"] | "", doc["value"].as<float>());
    }
    if (strcmp(kind, "set_permit") == 0) {
        if (!doc["permit"].is<bool>()) return refuse("'permit' must be true or false");
        const bool granted = doc["permit"].as<bool>();
        permit.grant(granted, doc["valid_for_s"] | 120U, now_ms);
        CommandResult r;
        r.ok = true;
        snprintf(r.message, sizeof r.message, "Production permit %s", granted ? "granted" : "withdrawn");
        return r;
    }
    return refuse("Unknown command");
}

}  // namespace hestia
