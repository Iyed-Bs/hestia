#pragma once
// =============================================================================
// Turning a verified command (the "cmd" JSON string of an envelope) into a
// change of controller state, plus the production-permit lease.
//
// Commands the gateway sends (runtime/links.py, domain/commands.py):
//   {"kind":"set_mode","mode":"AUTO"|"MANUAL"|"SAFE_SHUTDOWN"}
//   {"kind":"reset_h2_alarm","reason":"what was checked and found"}
//   {"kind":"set_actuator","actuator":"electrolyser"|"cooling_pump"|"koh_dosing"|"water_makeup"|"ventilation","on":true}
//   {"kind":"set_threshold","name":"koh_low_pct"|"koh_high_pct"|"temp_alert_c"|"h2_warning_ppm"|"h2_alarm_ppm","value":26}
//   {"kind":"set_permit","permit":true,"valid_for_s":120}
// =============================================================================
#include <stdint.h>

#include "hestia_controller.h"

namespace hestia {

// The production permit is a lease, not a switch: the gateway re-sends it
// every third of `valid_for_s`, and the device drops it by itself when it is
// not refreshed in time. A gateway that crashes, loses power or loses the
// network therefore stops production within two minutes, without anyone
// having to notice.
class PermitLease {
   public:
    static constexpr uint32_t MAX_VALID_S = 900;

    void grant(bool permit, uint32_t valid_for_s, uint32_t now_ms);
    bool active(uint32_t now_ms) const;
    void revoke() { granted_ = false; }

   private:
    bool granted_ = false;
    uint32_t until_ms_ = 0;
};

// Parse and apply one command. Returns what to acknowledge to the gateway.
CommandResult apply_command_json(State& state, PermitLease& permit, const char* cmd_json, uint32_t now_ms);

}  // namespace hestia
