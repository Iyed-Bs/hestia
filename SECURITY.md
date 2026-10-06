# Security

Hestia supervises equipment that makes and stores hydrogen in a building.
Security here is a safety property: the question is not only "can someone
read the data?" but "can someone, or something failing, make the
installation unsafe, or hide that it was?"

## Reporting a vulnerability

Please use GitHub's private reporting (**Security → Report a vulnerability**
on this repository). Do not open a public issue for a security problem.

## What Hestia is, and is not

Hestia is a supervision, maintenance and planning layer. Its controller
enforces safety interlocks, but **it is not a certified safety instrumented
system**. A real installation must also have a certified hydrogen detector,
wired independently of Hestia to its own alarm and ventilation, as local
regulations require. Hestia makes that detector's upkeep visible and provable
(the trust layer); it does not replace it.

## Design principles

1. **Safety is decided locally.** The controller applies every interlock
   itself, every two seconds, with or without a network. The gateway can only
   *ask*, and asking cannot override an interlock.
2. **Fail safe.** No permit, no clock, no trusted H₂ sensor, no heartbeat:
   each one means *no production*, never "carry on".
3. **Prove, don't claim.** Every safety-relevant event goes into a
   hash-chained, signed journal; pass or fail of a maintenance check is
   computed from the measured numbers, not declared.
4. **The same rules everywhere.** The Python controller (digital twin) and
   the C++ controller (ESP32) run the same scenario file and the same signed
   envelopes in CI.

## Threat model

Assets, in order: people's safety (leak, overheating), the equipment, the
integrity of the safety record, operator accounts, site data.

| # | Threat | Controls |
|---|---|---|
| 1 | Someone on the building network drives the electrolyser | MQTT is TLS-only with per-device logins and topic ACLs; every command is HMAC-SHA256-signed, with a strictly increasing sequence number and a ±30 s clock window (`runtime/envelope.py`, `firmware/lib/hestia_core/src/command_auth.cpp`); interlocks apply whatever the command |
| 2 | Captured commands replayed later, or after a reboot | Sequence numbers refuse replays; timestamps refuse old messages after a reboot; without NTP time the controller refuses every command |
| 3 | Gateway crashes, hangs or is unplugged | The production permit is a 2-minute lease: the controller stops production by itself; cooling, electrolyte top-up, gas detection and extraction keep running |
| 4 | Controller crashes or loses power | Relays drop; the emergency relay is wired fail-safe (bus powered only while energised); task watchdog |
| 5 | Forged telemetry to hide a leak or trigger false alarms | Only the device's own login may publish its topics (ACL); strict schema with physical bounds, oversized and invalid messages rejected and journalled; the H₂ alarm latches on the controller itself, whatever the gateway believes |
| 6 | A sensor that lies (drift, frozen, dead, poisoned) | Trust layer: frozen/dead/drift/implausible-value checks, bump tests and calibrations with computed pass/fail, overdue checks; an untrusted H₂ sensor withdraws the permit |
| 7 | Rewriting history after an incident | Journal entries chained with SHA-256 and signed with a gateway-only key; `verify-journal`; every safety report prints the chain head, so a copy kept elsewhere proves what existed that day |
| 8 | Web attacks: XSS, CSRF, session theft, clickjacking | Strict CSP (`script-src 'self'`, no inline scripts), React escaping with `dangerouslySetInnerHTML` banned by lint; SameSite=Strict + per-session CSRF token on every change; HttpOnly, Secure cookies with server-side revocation; `frame-ancestors 'none'`, HSTS |
| 9 | Password guessing | argon2id; throttling per account and per address, journalled; 12-character minimum |
| 10 | Privilege misuse | Three roles (viewer / operator / admin); every command and change journalled with its author; an alarm reset requires a written reason |
| 11 | Leaked secrets | Never in git (ignore rules, gitleaks in CI and pre-commit); production refuses missing or weak secrets; configuration and validation errors never echo submitted values; `.env` created with mode 600; the e-mail app password lives only in `.env`, never in the browser or the database, and can be revoked from the Google account without touching anything else |
| 12 | Supply chain | Locked dependencies with hashes (uv, npm); pip-audit, npm audit, private vulnerability alerts; GitHub Actions pinned to commit SHAs; CodeQL; Trivy scan of the image; ML models loaded only as ONNX + JSON after a SHA-256 check (no pickle) |
| 13 | A visitor exhausting the gateway, or reaching the real installation, through the simulator | Each sign-in gets one simulation, at most `HESTIA_SIM_MAX_SESSIONS` at once, closed after `HESTIA_SIM_IDLE_MINUTES` unwatched; it runs in memory with its own journal key and e-mail off; its API only reaches its own simulated device, never the live link or journal |
| 14 | Container escape or lateral movement | Gateway runs read-only, as an unprivileged user, with no Linux capabilities and no-new-privileges; only the reverse proxy and the broker are exposed |

## Known limits

- **One command key per site.** A controller stolen with its key could sign
  commands for that site's topics. Revoke its broker login at once
  (`deploy/add-mqtt-user.sh`) and rotate `HESTIA_DEVICE_COMMAND_KEY` on the
  gateway and the remaining controllers.
- **A fully compromised gateway holds the journal key** and could rebuild a
  consistent fake history. What it cannot do is match a head hash already
  printed on an earlier report: keep reports, or export the journal, off the
  machine.
- **Physical access** to the gateway or the controller is out of scope.
- The MQ-8 sensor used in the prototype is an inexpensive indicator, not a
  certified detector (see "What Hestia is, and is not").
