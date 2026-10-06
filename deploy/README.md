# Deploying Hestia

Hestia runs on one small machine in the building (a mini-PC or a Raspberry
Pi 5 with Docker). Nothing needs a cloud account.

```
 browsers ──HTTPS 443──▶ caddy ──▶ gateway ◀──MQTT/TLS 8883── ESP32 controller(s)
                                     │            (mosquitto, "device" profile)
                                     └── /data volume: database, safety journal, caches
```

| Port | Service | Who connects |
|---|---|---|
| 443 (80 redirects) | Caddy → gateway | operators' browsers |
| 8883 | Mosquitto (TLS, logins, ACL) | controllers only (device profile) |

The gateway itself is never exposed: only Caddy and the broker are.

## A. Simulation or public demo (digital twin)

```bash
cp .env.example .env
# two secrets, and the demo logins:
sed -i "s/^HESTIA_SECRET_KEY=$/HESTIA_SECRET_KEY=$(openssl rand -hex 32)/; \
        s/^HESTIA_JOURNAL_KEY=$/HESTIA_JOURNAL_KEY=$(openssl rand -hex 32)/; \
        s/^HESTIA_DEMO_USERS=false/HESTIA_DEMO_USERS=true/" .env
docker compose up -d
```

Open https://localhost (accept Caddy's local certificate) and sign in as
`visitor` / `hestia-visitor` or `operator` / `hestia-operator`. For a public
URL, set `HESTIA_DOMAIN` to the domain and `HESTIA_TLS` to your e-mail address.

Demo logins only exist in twin mode: the gateway refuses to start with them
next to a real controller.

## B. A real installation

**1. Network.** Put the controllers on the building's IoT network or VLAN.
They only need to reach this machine on port 8883.

**2. Secrets, certificates, broker logins** (once):

```bash
./deploy/setup.sh hestia.local 192.168.1.20   # names/IPs the controllers will use
```

The script fills `.env` with fresh random secrets (production, device mode),
creates a private certificate authority and the broker's certificate in
`deploy/mosquitto/certs/`, and the gateway's own broker login. Run on Linux
(GNU `sed`); it needs only Docker.

**3. Each controller:**

```bash
./deploy/add-mqtt-user.sh esp32_1             # username = the controller's DEVICE_ID
```

Then follow `firmware/README.md`: copy `secrets_template.h` to `secrets.h`,
paste the MQTT password it printed, the content of
`deploy/mosquitto/certs/ca.crt`, and `HESTIA_DEVICE_COMMAND_KEY` from `.env`.

**4. Start, and create the first administrator:**

```bash
docker compose --profile device up -d
docker compose exec gateway python -m hestia.cli create-user alice --role admin --name "Alice Martin"
```

**5. E-mail alerts (optional, off by default).** To have the H₂ alarm, an
untrusted sensor, an over-temperature or a silent controller e-mailed to the
people responsible, follow the steps in the "E-mail alerts" section of `.env`:
a Gmail account for the building, an app password from
https://myaccount.google.com/apppasswords, the recipients, then restart and
send a test from *Administration → E-mail alerts*. Any other mail server works
too (`HESTIA_EMAIL_PROVIDER=smtp`).

**6. Calibrate and record.** Until a bump test of the H₂ sensor has been
recorded and passed (Safety → Record a check), the gateway does not trust the
sensor and grants no production permit. This is deliberate.

## Operations

| Task | Command |
|---|---|
| Logs | `docker compose logs -f gateway` |
| Verify the safety journal | `docker compose exec gateway python -m hestia.cli verify-journal` |
| Export the journal | `docker compose exec gateway python -m hestia.cli export-journal /data/journal.jsonl` |
| Send a test alert e-mail | `docker compose exec gateway python -m hestia.cli test-email` |
| Check the ML model fingerprints | `docker compose exec gateway python -m hestia.cli check-models` |
| Reset a password (signs the user out) | `docker compose exec gateway python -m hestia.cli reset-password alice` |
| Revoke a controller | `./deploy/add-mqtt-user.sh esp32_1` (new password) or delete its line in `deploy/mosquitto/passwd` |
| Update | `git pull && docker compose build && docker compose --profile device up -d` |

**Backups.** Back up two things together: the `hestia_hestia-data` volume
(database and journal) and `.env`. Without `HESTIA_JOURNAL_KEY` the journal
can still be read, but its signatures can no longer be verified.

**Rotating secrets.** A new `HESTIA_SECRET_KEY` signs everyone out. A new
`HESTIA_DEVICE_COMMAND_KEY` must be flashed into every controller at the same
time, or they refuse all commands (and therefore produce nothing). Do not
rotate `HESTIA_JOURNAL_KEY` on a running site: start a new journal instead.

**Hardening already in place.** Read-only gateway container, no Linux
capabilities, unprivileged user, no-new-privileges; HTTPS with HSTS and a
strict Content Security Policy; TLS-only MQTT with per-device logins and topic
ACLs; signed, replay-proof commands; secrets never printed in error messages.
