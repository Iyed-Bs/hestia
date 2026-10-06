// =============================================================================
// Native tests of lib/hestia_core (pio test -e native).
//
// 1. spec/controller_vectors.json: every scenario the gateway's Python
//    controller passes, this C++ controller must pass too.
// 2. spec/command_vectors.json: envelopes signed by the gateway's own signer;
//    the C++ verifier must reach the same verdict on each.
// 3. SHA-256 / HMAC-SHA256 against published test vectors (FIPS 180-4, RFC 4231).
// 4. The production-permit lease.
// =============================================================================
#include <ArduinoJson.h>
#include <unity.h>

#include <math.h>

#include <fstream>
#include <sstream>
#include <string>

#include "command_auth.h"
#include "hestia_commands.h"
#include "hestia_controller.h"
#include "sha256.h"

using namespace hestia;

// Unity calls these around every test; nothing to prepare here.
void setUp() {}
void tearDown() {}

static std::string read_file(const char* path) {
    std::ifstream in(path, std::ios::binary);
    std::stringstream text;
    text << in.rdbuf();
    return text.str();
}

static std::string hex(const uint8_t* bytes, size_t n) {
    static const char* digits = "0123456789abcdef";
    std::string out;
    for (size_t i = 0; i < n; ++i) {
        out += digits[bytes[i] >> 4];
        out += digits[bytes[i] & 15];
    }
    return out;
}

// ── 1. Controller vectors ─────────────────────────────────────────────────────

static Reading reading_from(JsonObjectConst defaults, JsonObjectConst overrides, uint32_t t_ms) {
    JsonDocument merged;
    for (JsonPairConst kv : defaults) merged[kv.key()] = kv.value();
    for (JsonPairConst kv : overrides) merged[kv.key()] = kv.value();
    Reading r;
    r.electrolyte_c = merged["electrolyte_c"].as<float>();
    r.koh_wt_pct = merged["koh_wt_pct"].as<float>();
    r.level_low = merged["level_low"].as<bool>();
    r.h2_ppm = merged["h2_ppm"].as<float>();
    r.t_ms = t_ms;
    r.tank_bar = merged["tank_bar"].as<float>();
    r.temp_valid = merged["temp_valid"].as<bool>();
    r.koh_valid = merged["koh_valid"].as<bool>();
    r.h2_valid = merged["h2_valid"].as<bool>();
    r.tank_valid = merged["tank_valid"].as<bool>();
    return r;
}

static void test_controller_vectors() {
    const std::string text = read_file(SPEC_DIR "/controller_vectors.json");
    TEST_ASSERT_FALSE_MESSAGE(text.empty(), "spec/controller_vectors.json not found");
    JsonDocument spec;
    TEST_ASSERT_TRUE(deserializeJson(spec, text) == DeserializationError::Ok);

    int checked = 0;
    for (JsonObjectConst scenario : spec["scenarios"].as<JsonArrayConst>()) {
        const std::string name = scenario["name"].as<const char*>();
        JsonDocument initial;
        for (JsonPairConst kv : spec["defaults"]["initial"].as<JsonObjectConst>()) initial[kv.key()] = kv.value();
        for (JsonPairConst kv : scenario["initial"].as<JsonObjectConst>()) initial[kv.key()] = kv.value();

        State state;
        TEST_ASSERT_TRUE(parse_mode(initial["mode"].as<const char*>(), state.mode));
        TEST_ASSERT_TRUE(parse_phase(initial["phase"].as<const char*>(), state.phase));
        state.production_permit = initial["production_permit"].as<bool>();
        state.storage_mawp_bar = initial["storage_mawp_bar"].as<float>();
        PermitLease lease;
        uint32_t clock_ms = 0;  // the device's millis(), advanced by each step's "dt"

        int index = 0;
        for (JsonObjectConst step : scenario["steps"].as<JsonArrayConst>()) {
            ++index;
            const std::string where = name + ", step " + std::to_string(index);
            if (step["command"].is<JsonObjectConst>()) {
                std::string cmd;
                serializeJson(step["command"], cmd);
                const CommandResult result = apply_command_json(state, lease, cmd.c_str(), 0);
                TEST_ASSERT_EQUAL_MESSAGE(step["accepted"].as<bool>(), result.ok, (where + ": " + result.message).c_str());
                continue;
            }
            const float dt = step["dt"].is<float>() ? step["dt"].as<float>() : spec["defaults"]["dt"].as<float>();
            clock_ms += uint32_t(lroundf(dt * 1000.0f));
            const Reading r =
                reading_from(spec["defaults"]["reading"].as<JsonObjectConst>(), step["reading"].as<JsonObjectConst>(), clock_ms);
            const Outputs out = compute_outputs(r, state);
            for (JsonPairConst kv : step["expect"].as<JsonObjectConst>()) {
                const std::string key = kv.key().c_str();
                const std::string what = where + ": " + key + " (reason: " + out.reason + ")";
                if (key == "phase") {
                    TEST_ASSERT_EQUAL_STRING_MESSAGE(kv.value().as<const char*>(), to_string(out.phase), what.c_str());
                } else if (key == "mode") {
                    TEST_ASSERT_EQUAL_STRING_MESSAGE(kv.value().as<const char*>(), to_string(state.mode), what.c_str());
                } else {
                    bool observed = false;
                    if (key == "electrolyser") observed = out.electrolyser;
                    else if (key == "cooling_pump") observed = out.cooling_pump;
                    else if (key == "koh_dosing") observed = out.koh_dosing;
                    else if (key == "water_makeup") observed = out.water_makeup;
                    else if (key == "ventilation") observed = out.ventilation;
                    else if (key == "h2_relay_closed") observed = out.h2_relay_closed;  // gitleaks:allow (a JSON key name, not a secret)
                    else if (key == "h2_warning") observed = state.h2_warning;
                    else if (key == "h2_alarm_latched") observed = state.h2_alarm_latched;
                    else TEST_FAIL_MESSAGE(("unknown expectation " + key).c_str());
                    TEST_ASSERT_EQUAL_MESSAGE(kv.value().as<bool>(), observed, what.c_str());
                }
                ++checked;
            }
        }
    }
    TEST_ASSERT_GREATER_THAN(100, checked);  // the file really was exercised
}

// The density → KOH conversion matches the gateway's physics (values from
// physics/electrolyte.py: 30 wt% is 1.2890 kg/L at 25 °C and 1.2716 at 52 °C).
static void test_koh_from_density() {
    TEST_ASSERT_FLOAT_WITHIN(0.01f, 30.0f, koh_wt_pct_from_density(1.2890f, 25.0f));
    TEST_ASSERT_FLOAT_WITHIN(0.01f, 30.0f, koh_wt_pct_from_density(1.27160f, 52.0f));
    TEST_ASSERT_FLOAT_WITHIN(0.01f, 25.0f, koh_wt_pct_from_density(1.24035f, 25.0f));
}

// ── 2. Command vectors ────────────────────────────────────────────────────────

static const char* verdict_name(Verdict v) {
    switch (v) {
        case Verdict::OK: return "ok";
        case Verdict::BAD_SIGNATURE: return "bad_signature";
        case Verdict::REPLAYED: return "replayed";
        case Verdict::STALE: return "stale";
        case Verdict::NO_CLOCK: return "no_clock";
        default: return "malformed";
    }
}

static void test_command_vectors() {
    const std::string text = read_file(SPEC_DIR "/command_vectors.json");
    TEST_ASSERT_FALSE_MESSAGE(text.empty(), "spec/command_vectors.json not found");
    JsonDocument spec;
    TEST_ASSERT_TRUE(deserializeJson(spec, text) == DeserializationError::Ok);

    CommandVerifier verifier;
    TEST_ASSERT_TRUE(verifier.set_key_hex(spec["key_hex"].as<const char*>()));
    const int64_t now = spec["now"].as<int64_t>();
    for (JsonObjectConst c : spec["cases"].as<JsonArrayConst>()) {
        JsonObjectConst e = c["envelope"].as<JsonObjectConst>();
        const Verdict v = verifier.verify(e["seq"].as<uint64_t>(), e["ts"].as<int64_t>(), e["cmd"].as<const char*>(),
                                          e["mac"].as<const char*>(), now);
        TEST_ASSERT_EQUAL_STRING_MESSAGE(c["expect"].as<const char*>(), verdict_name(v), c["name"].as<const char*>());
    }
}

static void test_no_clock_means_no_command() {
    CommandVerifier verifier;
    TEST_ASSERT_TRUE(verifier.set_key_hex("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"));
    char mac[65];
    verifier.sign(5, 1000, "{}", mac);
    // A device that booted without NTP thinks it is 1970: refuse, do not guess.
    TEST_ASSERT_EQUAL(int(Verdict::NO_CLOCK), int(verifier.verify(5, 1000, "{}", mac, 1000)));
}

static void test_bad_keys_are_refused() {
    CommandVerifier verifier;
    TEST_ASSERT_FALSE(verifier.set_key_hex("replace-with-64-hex-characters-generated-as-above"));
    TEST_ASSERT_FALSE(verifier.set_key_hex("zz0102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"));
    TEST_ASSERT_FALSE(verifier.has_key());
}

// ── 3. Hash primitives ────────────────────────────────────────────────────────

static void test_sha256_known_answers() {
    uint8_t digest[32];
    Sha256 h;
    h.update("abc");
    h.finish(digest);
    TEST_ASSERT_EQUAL_STRING("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", hex(digest, 32).c_str());
    h.update("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq");  // two blocks
    h.finish(digest);
    TEST_ASSERT_EQUAL_STRING("248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1", hex(digest, 32).c_str());
}

static void test_hmac_rfc4231() {
    uint8_t mac[32];
    uint8_t key1[20];
    for (uint8_t& b : key1) b = 0x0b;
    HmacSha256 m1(key1, sizeof key1);
    m1.update("Hi There");
    m1.finish(mac);
    TEST_ASSERT_EQUAL_STRING("b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7", hex(mac, 32).c_str());

    HmacSha256 m2(reinterpret_cast<const uint8_t*>("Jefe"), 4);
    m2.update("what do ya want for nothing?");
    m2.finish(mac);
    TEST_ASSERT_EQUAL_STRING("5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843", hex(mac, 32).c_str());

    uint8_t key6[131];  // key longer than a block: hashed first
    for (uint8_t& b : key6) b = 0xaa;
    HmacSha256 m6(key6, sizeof key6);
    m6.update("Test Using Larger Than Block-Size Key - Hash Key First");
    m6.finish(mac);
    TEST_ASSERT_EQUAL_STRING("60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54", hex(mac, 32).c_str());
}

// ── 4. Permit lease ───────────────────────────────────────────────────────────

static void test_permit_lapses_without_refresh() {
    PermitLease lease;
    TEST_ASSERT_FALSE(lease.active(0));  // nothing granted at boot
    lease.grant(true, 120, 1000);
    TEST_ASSERT_TRUE(lease.active(1000 + 119000));
    TEST_ASSERT_FALSE(lease.active(1000 + 120000));  // the gateway went quiet
    lease.grant(true, 120, 4294960000UL);            // just before millis() wraps
    TEST_ASSERT_TRUE(lease.active(1000));            // still valid after the wrap
    lease.grant(false, 120, 0);
    TEST_ASSERT_FALSE(lease.active(1));
}

static void test_permit_command() {
    State state;
    PermitLease lease;
    CommandResult r = apply_command_json(state, lease, R"({"kind":"set_permit","permit":true,"valid_for_s":60})", 0);
    TEST_ASSERT_TRUE(r.ok);
    TEST_ASSERT_TRUE(lease.active(59000));
    TEST_ASSERT_FALSE(lease.active(60000));
    r = apply_command_json(state, lease, R"({"kind":"launch_rocket"})", 0);
    TEST_ASSERT_FALSE(r.ok);
    r = apply_command_json(state, lease, "not json", 0);
    TEST_ASSERT_FALSE(r.ok);
}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_controller_vectors);
    RUN_TEST(test_koh_from_density);
    RUN_TEST(test_command_vectors);
    RUN_TEST(test_no_clock_means_no_command);
    RUN_TEST(test_bad_keys_are_refused);
    RUN_TEST(test_sha256_known_answers);
    RUN_TEST(test_hmac_rfc4231);
    RUN_TEST(test_permit_lapses_without_refresh);
    RUN_TEST(test_permit_command);
    return UNITY_END();
}
