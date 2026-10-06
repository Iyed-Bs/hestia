// =============================================================================
// Hestia controller firmware (ESP32)
//
// Every CONTROL_PERIOD_MS, whatever the network is doing:
//   1. read the sensors                       (sensors.cpp)
//   2. decide every actuator                  (lib/hestia_core: the same rules
//                                              as the gateway, same test file)
//   3. drive the relays                       (actuators.cpp)
//   4. publish telemetry if the broker is up
//
// The safety rules run here, locally. The gateway can only ask: every command
// is signed (lib/hestia_core/src/command_auth.h) and checked against the same
// rules before it changes anything, and the production permit is a lease that
// lapses by itself if the gateway goes quiet. Losing Wi-Fi therefore never
// leaves the electrolyser running unsupervised: it stops at the end of the
// lease, while cooling, electrolyte top-up, gas detection and extraction keep
// working.
//
// Setup: copy include/secrets_template.h to include/secrets.h and fill it in
// (see firmware/README.md).
// =============================================================================
#include <Arduino.h>
#include <ArduinoJson.h>
#include <PubSubClient.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <esp_arduino_version.h>
#include <esp_task_wdt.h>
#include <esp_timer.h>
#include <time.h>

#include "actuators.h"
#include "command_auth.h"
#include "config.h"
#include "hestia_commands.h"
#include "hestia_controller.h"
#include "sensors.h"

#if __has_include("secrets.h")
#include "secrets.h"
#else
#error "Copy include/secrets_template.h to include/secrets.h and fill it in (see firmware/README.md)"
#endif

static_assert(sizeof(COMMAND_HMAC_KEY) == 65, "COMMAND_HMAC_KEY must be 64 hexadecimal characters (see secrets.h)");

using namespace hestia;

// ── State ─────────────────────────────────────────────────────────────────────
static State g_state;
static PermitLease g_permit;
static CommandVerifier g_verifier;
static Reading g_reading;
static Outputs g_outputs;
static uint32_t g_telemetry_seq = 0;
static uint32_t g_last_cycle_ms = 0;
static bool g_cycle_now = true;  // run a cycle at once (boot, or right after a command)

// ── Network ───────────────────────────────────────────────────────────────────
static WiFiClientSecure g_tls;
static PubSubClient g_mqtt(g_tls);
static uint32_t g_last_wifi_try_ms = 0;
static uint32_t g_last_mqtt_try_ms = 0;

static char T_TELEMETRY[96], T_CMD[96], T_ACK[96], T_STATUS[96];

// ── Helpers ───────────────────────────────────────────────────────────────────

static float clamp(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }
static float round2(float v) { return roundf(v * 100.0f) / 100.0f; }

static void iso_now(char out[21]) {
    const time_t now = time(nullptr);
    struct tm utc;
    gmtime_r(&now, &utc);
    strftime(out, 21, "%Y-%m-%dT%H:%M:%SZ", &utc);
}

static void publish_ack(uint64_t seq, bool ok, const char* result) {
    JsonDocument ack;
    ack["seq"] = seq;
    ack["ok"] = ok;
    ack["result"] = result;
    char buffer[256];
    const size_t n = serializeJson(ack, buffer, sizeof buffer);
    g_mqtt.publish(T_ACK, reinterpret_cast<const uint8_t*>(buffer), n, false);
}

// ── Commands from the gateway ─────────────────────────────────────────────────

static void on_message(char* topic, uint8_t* payload, unsigned int length) {
    if (strcmp(topic, T_CMD) != 0 || length > 1024) return;

    // Parse from a const pointer so ArduinoJson copies the strings: the
    // client's buffer is reused as soon as we publish the acknowledgement.
    JsonDocument envelope;
    if (deserializeJson(envelope, static_cast<const uint8_t*>(payload), length) != DeserializationError::Ok) return;
    if (!envelope["seq"].is<uint64_t>()) return;  // nothing to acknowledge
    const uint64_t seq = envelope["seq"].as<uint64_t>();
    const int64_t ts = envelope["ts"] | int64_t(0);
    const char* cmd = envelope["cmd"].as<const char*>();
    const char* mac = envelope["mac"].as<const char*>();

    const Verdict verdict = g_verifier.verify(seq, ts, cmd, mac, int64_t(time(nullptr)));
    if (verdict != Verdict::OK) {
        Serial.printf("[cmd] %s\n", to_string(verdict));
        publish_ack(seq, false, to_string(verdict));
        return;
    }
    const CommandResult result = apply_command_json(g_state, g_permit, cmd, millis());
    Serial.printf("[cmd] %s: %s\n", result.ok ? "ok" : "refused", result.message);
    publish_ack(seq, result.ok, result.message);
    g_cycle_now = true;  // apply it now, not in up to two seconds
}

// ── Connectivity (never blocks the control loop for long) ─────────────────────

static void keep_wifi(uint32_t now) {
    if (WiFi.status() == WL_CONNECTED || now - g_last_wifi_try_ms < WIFI_RETRY_MS) return;
    g_last_wifi_try_ms = now;
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

static void keep_mqtt(uint32_t now) {
    if (g_mqtt.connected() || WiFi.status() != WL_CONNECTED || now - g_last_mqtt_try_ms < MQTT_RETRY_MS) return;
    g_last_mqtt_try_ms = now;
    // Last will: the broker publishes "offline" for us if we vanish.
    if (g_mqtt.connect(DEVICE_ID, MQTT_USERNAME, MQTT_PASSWORD, T_STATUS, 1, true, "offline")) {
        g_mqtt.publish(T_STATUS, "online", true);
        g_mqtt.subscribe(T_CMD, 1);
        Serial.println("[mqtt] connected");
    } else {
        Serial.printf("[mqtt] connection failed (state %d)\n", g_mqtt.state());
    }
}

// ── Telemetry (exactly the gateway's Telemetry schema, domain/telemetry.py) ───

static void publish_telemetry() {
    if (!g_mqtt.connected()) return;
    char ts[21];
    iso_now(ts);
    JsonDocument t;
    t["device_id"] = DEVICE_ID;
    t["seq"] = ++g_telemetry_seq;
    t["ts"] = ts;
    t["uptime_s"] = round2(esp_timer_get_time() / 1e6f);
    t["firmware"] = FIRMWARE_VERSION;
    // Clamped to the schema's bounds: an absurd raw value is already flagged
    // invalid, and must not make the gateway drop the whole message.
    t["electrolyte_c"] = round2(clamp(g_reading.electrolyte_c, -200.0f, 200.0f));
    t["koh_wt_pct"] = round2(clamp(g_reading.koh_wt_pct, 0.0f, 60.0f));
    t["level_low"] = g_reading.level_low;
    t["h2_ppm"] = round2(clamp(g_reading.h2_ppm, 0.0f, 100000.0f));
    t["tank_bar"] = round2(clamp(g_reading.tank_bar, 0.0f, 1000.0f));
    t["temp_valid"] = g_reading.temp_valid;
    t["koh_valid"] = g_reading.koh_valid;
    t["h2_valid"] = g_reading.h2_valid;
    t["tank_valid"] = g_reading.tank_valid;
    t["mode"] = to_string(g_state.mode);
    t["phase"] = to_string(g_outputs.phase);
    t["reason"] = g_outputs.reason;
    t["electrolyser"] = g_outputs.electrolyser;
    t["cooling_pump"] = g_outputs.cooling_pump;
    t["koh_dosing"] = g_outputs.koh_dosing;
    t["water_makeup"] = g_outputs.water_makeup;
    t["ventilation"] = g_outputs.ventilation;
    t["h2_relay_closed"] = g_outputs.h2_relay_closed;
    t["h2_warning"] = g_state.h2_warning;
    t["h2_alarm_latched"] = g_state.h2_alarm_latched;
    t["production_permit"] = g_state.production_permit;
    t["koh_low_pct"] = g_state.thresholds.koh_low_pct;
    t["koh_high_pct"] = g_state.thresholds.koh_high_pct;
    t["temp_alert_c"] = g_state.thresholds.temp_alert_c;
    t["h2_warning_ppm"] = g_state.thresholds.h2_warning_ppm;
    t["h2_alarm_ppm"] = g_state.thresholds.h2_alarm_ppm;
    t["storage_mawp_bar"] = g_state.storage_mawp_bar;
    char buffer[1024];
    const size_t n = serializeJson(t, buffer, sizeof buffer);
    g_mqtt.publish(T_TELEMETRY, reinterpret_cast<const uint8_t*>(buffer), n, false);
}

// ── One control cycle ─────────────────────────────────────────────────────────

static void control_cycle(uint32_t now) {
    g_reading = sensors_read(now);
    g_state.production_permit = g_permit.active(now);  // the lease lapses by itself
    g_outputs = compute_outputs(g_reading, g_state);
    actuators_apply(g_outputs);
    publish_telemetry();
}

// ── Arduino entry points ──────────────────────────────────────────────────────

void setup() {
    actuators_begin();  // first: every load off, emergency relay open
    Serial.begin(115200);
    Serial.printf("\n%s  device %s  site %s\n", FIRMWARE_VERSION, DEVICE_ID, SITE_ID);

#if ESP_ARDUINO_VERSION_MAJOR >= 3
    const esp_task_wdt_config_t wdt = {WATCHDOG_S * 1000U, 0, true};
    esp_task_wdt_reconfigure(&wdt);
#else
    esp_task_wdt_init(WATCHDOG_S, true);
#endif
    esp_task_wdt_add(nullptr);

    sensors_begin();
    g_state.storage_mawp_bar = STORAGE_MAWP_BAR;  // 0 = no pressurised storage on this installation
    if (!g_verifier.set_key_hex(COMMAND_HMAC_KEY)) {
        Serial.println("[cmd] COMMAND_HMAC_KEY is not 64 hex characters: commands disabled, no production");
    }

    snprintf(T_TELEMETRY, sizeof T_TELEMETRY, "%s/%s/%s/telemetry", TOPIC_PREFIX, SITE_ID, DEVICE_ID);
    snprintf(T_CMD, sizeof T_CMD, "%s/%s/%s/cmd", TOPIC_PREFIX, SITE_ID, DEVICE_ID);
    snprintf(T_ACK, sizeof T_ACK, "%s/%s/%s/ack", TOPIC_PREFIX, SITE_ID, DEVICE_ID);
    snprintf(T_STATUS, sizeof T_STATUS, "%s/%s/%s/status", TOPIC_PREFIX, SITE_ID, DEVICE_ID);

    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    configTime(0, 0, NTP_SERVER_1, NTP_SERVER_2);  // UTC; commands wait for a valid clock

    g_tls.setCACert(MQTT_CA_CERT);  // the gateway's own certificate authority, TLS 1.2+
    g_mqtt.setServer(MQTT_HOST, MQTT_PORT);
    g_mqtt.setBufferSize(1024);
    g_mqtt.setKeepAlive(30);
    g_mqtt.setSocketTimeout(5);
    g_mqtt.setCallback(on_message);
}

void loop() {
    esp_task_wdt_reset();
    const uint32_t now = millis();

    if (g_cycle_now || now - g_last_cycle_ms >= CONTROL_PERIOD_MS) {
        g_cycle_now = false;
        g_last_cycle_ms = now;
        control_cycle(now);
    }

    keep_wifi(now);
    keep_mqtt(now);
    g_mqtt.loop();  // receives commands (on_message)
    delay(10);
}
