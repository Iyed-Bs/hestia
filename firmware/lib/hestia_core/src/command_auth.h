#pragma once
// =============================================================================
// Checks that a command really comes from the gateway, and is fresh.
//
// Mirror of backend/src/hestia/runtime/envelope.py. A command arrives as
//   {"seq": 1790861234567, "ts": 1790861234, "cmd": "{...}", "mac": "9f2c…"}
// and is accepted only if, in this order:
//   1. mac == HMAC-SHA256(key, "<seq>|<ts>|<cmd>") (lower-case hex), compared
//      in constant time: someone on the broker without the key cannot drive
//      the electrolyser;
//   2. seq is larger than any seq accepted before: replays are refused;
//   3. ts is within ±30 s of the device clock (NTP): a message captured
//      before a reboot cannot be replayed after it either.
// Without a synchronised clock every command is refused: the device then
// simply runs without a production permit, which is the safe state.
// =============================================================================
#include <stddef.h>
#include <stdint.h>

namespace hestia {

constexpr int64_t MAX_SKEW_S = 30;
constexpr int64_t CLOCK_VALID_AFTER = 1700000000;  // 2023-11-14: before this, NTP has not answered
constexpr size_t COMMAND_KEY_BYTES = 32;

enum class Verdict : uint8_t { OK, MALFORMED, BAD_SIGNATURE, REPLAYED, STALE, NO_CLOCK };
const char* to_string(Verdict verdict);

class CommandVerifier {
   public:
    // `key_hex`: 64 hexadecimal characters (COMMAND_HMAC_KEY in secrets.h,
    // HESTIA_DEVICE_COMMAND_KEY on the gateway). Returns false if malformed.
    bool set_key_hex(const char* key_hex);
    bool has_key() const { return has_key_; }

    Verdict verify(uint64_t seq, int64_t ts, const char* cmd, const char* mac_hex, int64_t now_s);
    uint64_t last_seq() const { return last_seq_; }

    // Exposed for the tests: the signature the gateway would send.
    void sign(uint64_t seq, int64_t ts, const char* cmd, char out_hex[65]) const;

   private:
    uint8_t key_[COMMAND_KEY_BYTES] = {0};
    bool has_key_ = false;
    uint64_t last_seq_ = 0;
};

// Decimal text of an unsigned/signed 64-bit integer (newlib-nano printf lacks %llu).
void u64_to_text(uint64_t value, char out[21]);
void i64_to_text(int64_t value, char out[22]);

}  // namespace hestia
