# Hestia controller firmware (ESP32)

The controller sits next to the electrolyser. It reads the sensors, decides
every relay locally every two seconds, and talks to the Hestia gateway over
MQTT with TLS. **Safety never depends on the network**: the gateway can only
*ask* for things, and only with a signed command.

```
lib/hestia_core/        portable C++, no Arduino calls (tested on a PC)
  hestia_controller.*   the safety controller: same rules as backend/src/hestia/domain/control.py
  hestia_commands.*     command rules + the production-permit lease
  command_auth.*        HMAC-SHA256 signature, replay and clock checks
  sha256.*              SHA-256 / HMAC (FIPS 180-4, RFC 2104)
src/                    the hardware: sensors, relays, Wi-Fi, TLS MQTT, NTP, watchdog
include/config.h        pins, timing, calibration, identity   (not secret)
include/secrets.h       Wi-Fi, MQTT login, CA certificate, command key  (never committed)
test/test_core/         native tests: the shared specs in ../spec, hash test vectors
```

## Safety design

| Situation | What the controller does |
|---|---|
| H₂ above 500 ppm, 3 readings in a row (stage 1) | Production stops, **extraction fan on** until the air has been clean for 5 minutes |
| H₂ above 2 000 ppm, 3 readings in a row (stage 2) | Everything off, emergency relay opened, extraction keeps running, alarm **latched** until an operator resets it with a written reason |
| Temperature or electrolyte unreadable | Everything off (extraction stays on if gas is present) |
| H₂ sensor unavailable (warming up, wire cut, saturated) | No production; cooling and electrolyte top-up continue |
| Electrolyte at 55 °C while producing | Cooling loop on (off again at 50 °C): the stack keeps producing |
| Electrolyte at 60 °C anyway (loop failing, heat wave) | Production stops, cooling until 50 °C, electrolyte checked, then restart |
| Electrolyte ≥ the alarm temperature (70 °C) | Electrolyser and dosing off, **cooling forced on**, in every mode |
| KOH below its band (25 wt%) | Production pauses while concentrate is dosed; never dosed into a running stack |
| Storage at 95 % of its maximum working pressure, or its pressure unreadable | No production |
| Manual mode | The operator drives the relays; the interlocks above still apply |
| Gateway silent, crashed or unreachable | The production permit is a 2-minute lease: production stops when it lapses |
| ESP32 resets, hangs or loses power | Relays drop; the emergency relay opens the electrolyser bus (wire it fail-safe, below) |
| A command without a valid signature, replayed, or more than 30 s old | Refused and acknowledged as refused |
| No NTP time yet | Every command refused, so no permit and no production |

The rules in `lib/hestia_core` and in the gateway's Python controller run the
same scenario file, `spec/controller_vectors.json`, and the same signed
envelopes, `spec/command_vectors.json`. If one side changes, CI fails until
the other does too.

## Wiring (ESP32 DevKit v1)

| GPIO | Connected to | Notes |
|---|---|---|
| 13 | DS18B20 electrolyte temperature | 4.7 kΩ pull-up to 3.3 V |
| 34 | Electrolyte density transmitter, 4-20 mA | across a 150 Ω shunt; ADC1, input only |
| 35 | MQ-8 analog out | through a 5 V → 3.3 V divider; ADC1, input only |
| 36 | Storage pressure transmitter, 4-20 mA (optional) | across a 150 Ω shunt; set `STORAGE_MAWP_BAR` to use it |
| 32 | Float switch, electrolyte tank | to GND, internal pull-up |
| 25 | Relay: electrolyser power | |
| 33 | Relay: cooling pump | NO contact |
| 26 | Relay: KOH dosing pump (45 wt% concentrate) | |
| 27 | Relay: deionised water make-up pump | |
| 17 | Relay: extraction fan | powered from **outside** the electrolyser bus, so it runs after a trip |
| 16 | **Emergency relay** on the electrolyser power bus | wire it so the bus is powered only while the coil is energised |

Why density for the electrolyte: at 30 wt% KOH the pH is above 14, where
glass probes saturate, and conductivity peaks inside the 25-32 wt% band (26
and 28 wt% read the same), so neither can tell the controller which way the
concentration is moving. Density rises steadily with concentration; with the
temperature it gives the KOH mass fraction in closed form
(`koh_wt_pct_from_density`, the same formula as the gateway's physics).

Pins, calibration constants and timings are all in `include/config.h`.

## Setting up a controller

1. **Provision it on the gateway** (see `deploy/README.md`):
   `deploy/mosquitto/add-device.sh esp32_1` creates its MQTT login and prints it.
2. **Fill in the secrets**:
   ```
   cp include/secrets_template.h include/secrets.h
   ```
   Every field is explained in the file: Wi-Fi, broker host, the device's MQTT
   login, the gateway's CA certificate (`deploy/mosquitto/certs/ca.crt`), and
   `COMMAND_HMAC_KEY`, identical to `HESTIA_DEVICE_COMMAND_KEY` on the gateway.
   The build refuses a key that is not 64 hexadecimal characters.
3. **Check the identity** in `include/config.h`: `SITE_ID` and `DEVICE_ID` must
   match `HESTIA_SITE_ID` and `HESTIA_DEVICE_ID`.
4. **Build and flash**:
   ```
   pio run -e esp32dev -t upload
   pio device monitor
   ```
5. **Calibrate**, then record each step in the dashboard (Safety → Record a check):
   the density transmitter in 20 and 30 wt% KOH standards, the H₂ sensor in
   clean air, then a bump test with test gas above the 500 ppm warning level. Until a bump test passes, the gateway does not
   trust the H₂ sensor and grants no production permit.

## Tests

```
pio test -e native
```

These run on any PC with a C++ compiler (CI uses GCC on Linux). They cover:

- every scenario in `spec/controller_vectors.json` (thermal cycle and cooling
  loop, electrolyte band, both gas stages and the purge, storage interlock,
  manual mode, thresholds);
- the density → KOH conversion against the gateway's physics;
- every signed envelope in `spec/command_vectors.json` (made by the gateway's own signer);
- SHA-256 and HMAC-SHA256 against FIPS 180-4 and RFC 4231 vectors;
- the permit lease, including the `millis()` wrap after 49 days.
