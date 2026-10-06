#include "command_auth.h"

#include <string.h>

#include "sha256.h"

namespace hestia {

const char* to_string(Verdict verdict) {
    switch (verdict) {
        case Verdict::OK: return "ok";
        case Verdict::MALFORMED: return "rejected: malformed envelope";
        case Verdict::BAD_SIGNATURE: return "rejected: bad signature";
        case Verdict::REPLAYED: return "rejected: replayed sequence number";
        case Verdict::STALE: return "rejected: timestamp outside the allowed window";
        default: return "rejected: device clock not synchronised yet";
    }
}

static int hex_value(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

bool CommandVerifier::set_key_hex(const char* key_hex) {
    if (key_hex == nullptr || strlen(key_hex) != 2 * COMMAND_KEY_BYTES) return false;
    for (size_t i = 0; i < COMMAND_KEY_BYTES; ++i) {
        const int hi = hex_value(key_hex[2 * i]);
        const int lo = hex_value(key_hex[2 * i + 1]);
        if (hi < 0 || lo < 0) return false;
        key_[i] = uint8_t(hi * 16 + lo);
    }
    has_key_ = true;
    return true;
}

void u64_to_text(uint64_t value, char out[21]) {
    char reversed[21];
    size_t n = 0;
    do {
        reversed[n++] = char('0' + value % 10);
        value /= 10;
    } while (value > 0);
    for (size_t i = 0; i < n; ++i) out[i] = reversed[n - 1 - i];
    out[n] = '\0';
}

void i64_to_text(int64_t value, char out[22]) {
    if (value < 0) {
        out[0] = '-';
        u64_to_text(uint64_t(-(value + 1)) + 1, out + 1);
    } else {
        u64_to_text(uint64_t(value), out);
    }
}

void CommandVerifier::sign(uint64_t seq, int64_t ts, const char* cmd, char out_hex[65]) const {
    char seq_text[21];
    char ts_text[22];
    u64_to_text(seq, seq_text);
    i64_to_text(ts, ts_text);
    HmacSha256 mac(key_, sizeof key_);
    mac.update(seq_text);
    mac.update("|");
    mac.update(ts_text);
    mac.update("|");
    mac.update(cmd);
    uint8_t digest[Sha256::DIGEST_SIZE];
    mac.finish(digest);
    static const char HEX[] = "0123456789abcdef";
    for (size_t i = 0; i < sizeof digest; ++i) {
        out_hex[2 * i] = HEX[digest[i] >> 4];
        out_hex[2 * i + 1] = HEX[digest[i] & 0x0f];
    }
    out_hex[64] = '\0';
}

Verdict CommandVerifier::verify(uint64_t seq, int64_t ts, const char* cmd, const char* mac_hex, int64_t now_s) {
    if (!has_key_ || cmd == nullptr || mac_hex == nullptr || strlen(mac_hex) != 64) return Verdict::MALFORMED;

    char expected[65];
    sign(seq, ts, cmd, expected);
    uint8_t difference = 0;  // constant time: no early exit on the first wrong character
    for (size_t i = 0; i < 64; ++i) difference |= uint8_t(expected[i] ^ mac_hex[i]);
    if (difference != 0) return Verdict::BAD_SIGNATURE;

    if (seq <= last_seq_) return Verdict::REPLAYED;
    if (now_s < CLOCK_VALID_AFTER) return Verdict::NO_CLOCK;
    const int64_t skew = now_s > ts ? now_s - ts : ts - now_s;
    if (skew > MAX_SKEW_S) return Verdict::STALE;

    last_seq_ = seq;
    return Verdict::OK;
}

}  // namespace hestia
