// Sensor drivers. Each reading comes with the device's own plausibility flag;
// the controller refuses to run what it cannot measure.
#include "sensors.h"

#include <Arduino.h>
#include <DallasTemperature.h>
#include <OneWire.h>

#include "config.h"

static OneWire one_wire(PIN_ONEWIRE);
static DallasTemperature ds18(&one_wire);

void sensors_begin() {
    ds18.begin();
    ds18.setWaitForConversion(false);  // never block the loop for 750 ms
    ds18.requestTemperatures();
    pinMode(PIN_LEVEL, INPUT_PULLUP);
    analogReadResolution(12);
    analogSetPinAttenuation(PIN_DENSITY, ADC_11db);  // 0–3.1 V range
    analogSetPinAttenuation(PIN_H2, ADC_11db);
    analogSetPinAttenuation(PIN_TANK, ADC_11db);
}

// Median of a few ADC readings, in volts.
static float median_volts(int pin) {
    float v[ANALOG_SAMPLES];
    for (int i = 0; i < ANALOG_SAMPLES; ++i) {
        v[i] = analogReadMilliVolts(pin) / 1000.0f;  // factory-calibrated conversion
        delay(4);
    }
    for (int i = 1; i < ANALOG_SAMPLES; ++i) {  // insertion sort, 5 elements
        const float key = v[i];
        int j = i - 1;
        while (j >= 0 && v[j] > key) {
            v[j + 1] = v[j];
            --j;
        }
        v[j + 1] = key;
    }
    return v[ANALOG_SAMPLES / 2];
}

// A 4-20 mA loop, in milliamps (see config.h).
static float loop_milliamps(int pin) { return median_volts(pin) / SHUNT_OHMS * 1000.0f; }
static bool loop_ok(float ma) { return ma >= LOOP_MIN_VALID_MA && ma <= LOOP_MAX_VALID_MA; }

hestia::Reading sensors_read(uint32_t now_ms) {
    hestia::Reading r;
    r.t_ms = now_ms;

    // Electrolyte temperature. -127 °C = unplugged; exactly 85 °C is the
    // DS18B20's power-on value (a conversion that never happened), and the
    // electrolyte can never legitimately reach it (stack maximum 70 °C).
    r.electrolyte_c = ds18.getTempCByIndex(0);
    r.temp_valid = r.electrolyte_c > -50.0f && r.electrolyte_c < 125.0f && r.electrolyte_c != 85.0f;
    ds18.requestTemperatures();  // ready long before the next cycle

    // KOH strength from density. The conversion needs the temperature, so an
    // unreadable temperature also makes the KOH reading invalid.
    const float density_ma = loop_milliamps(PIN_DENSITY);
    const float density =
        DENSITY_AT_4MA + (density_ma - 4.0f) / 16.0f * (DENSITY_AT_20MA - DENSITY_AT_4MA) + DENSITY_OFFSET_KG_L;
    r.koh_valid = loop_ok(density_ma) && r.temp_valid;
    r.koh_wt_pct = r.koh_valid ? hestia::koh_wt_pct_from_density(density, r.electrolyte_c) : 0.0f;

    // Storage pressure, when the installation has a pressurised tank.
    if (STORAGE_MAWP_BAR > 0.0f) {
        const float tank_ma = loop_milliamps(PIN_TANK);
        r.tank_valid = loop_ok(tank_ma);
        const float bar = (tank_ma - 4.0f) / 16.0f * TANK_BAR_AT_20MA;
        r.tank_bar = bar > 0.0f ? bar : 0.0f;
    }

    // Electrolyte level: the float pulls the input low while the tank is full.
    r.level_low = digitalRead(PIN_LEVEL) == HIGH;

    // Hydrogen in air.
    const float h2_volts = median_volts(PIN_H2);
    const float sensor_volts = h2_volts * H2_DIVIDER_RATIO;
    const float ppm = (sensor_volts - H2_CLEAN_AIR_VOLTS) / H2_VOLTS_PER_100PPM * 100.0f;
    r.h2_ppm = ppm > 0.0f ? ppm : 0.0f;
    r.h2_valid = now_ms >= H2_WARMUP_MS && h2_volts > H2_MIN_VALID_VOLTS && h2_volts < H2_MAX_VALID_VOLTS;

    return r;
}
