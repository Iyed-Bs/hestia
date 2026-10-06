#include "actuators.h"

#include <Arduino.h>

#include "config.h"

static const int OUTPUT_PINS[] = {PIN_ELECTROLYSER, PIN_COOLING_PUMP, PIN_KOH_DOSING,
                                  PIN_WATER_MAKEUP, PIN_VENTILATION,  PIN_H2_RELAY};

void actuators_begin() {
    for (int pin : OUTPUT_PINS) {
        digitalWrite(pin, LOW);  // set the level before enabling the driver: no glitch at boot
        pinMode(pin, OUTPUT);
    }
}

void actuators_apply(const hestia::Outputs& out) {
    // The emergency relay opens first and closes last, so the bus is never
    // powered while the controller is switching loads.
    if (!out.h2_relay_closed) digitalWrite(PIN_H2_RELAY, LOW);
    digitalWrite(PIN_ELECTROLYSER, out.electrolyser ? HIGH : LOW);
    digitalWrite(PIN_COOLING_PUMP, out.cooling_pump ? HIGH : LOW);
    digitalWrite(PIN_KOH_DOSING, out.koh_dosing ? HIGH : LOW);
    digitalWrite(PIN_WATER_MAKEUP, out.water_makeup ? HIGH : LOW);
    digitalWrite(PIN_VENTILATION, out.ventilation ? HIGH : LOW);
    if (out.h2_relay_closed) digitalWrite(PIN_H2_RELAY, HIGH);
}
