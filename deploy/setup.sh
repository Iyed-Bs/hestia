#!/usr/bin/env bash
# =============================================================================
# One-time setup of a real installation (HESTIA_MODE=device).
#
#   ./deploy/setup.sh hestia.local 192.168.1.20
#
# Arguments: the names and/or IP addresses the controllers will use to reach
# this machine (they go into the broker's TLS certificate). Then:
#   1. creates .env from .env.example if needed, switches it to production
#      device mode, and fills every empty secret with a fresh random value;
#   2. creates a private certificate authority and the broker's certificate
#      (deploy/mosquitto/certs; ca.crt is what the controllers trust);
#   3. creates the gateway's own broker login.
# Safe to run again: existing secrets, CA and logins are kept.
# Needs: docker. Nothing is installed on the host.
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "$#" -eq 0 ]; then
    echo "usage: $0 <name-or-ip> [<name-or-ip>...]   e.g. $0 hestia.local 192.168.1.20" >&2
    exit 64
fi

random_hex() { docker run --rm alpine:3 sh -c 'head -c 32 /dev/urandom | od -An -tx1 | tr -d " \n"'; }

# ── 1. .env ──────────────────────────────────────────────────────────────────
[ -f .env ] || cp .env.example .env
set_var() {  # set_var NAME VALUE: replace the line, or append it
    if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi
}
fill_secret() {  # only if empty: never rotate a secret behind the operator's back
    if ! grep -q "^$1=." .env; then set_var "$1" "$(random_hex)"; echo "  generated $1"; fi
}
set_var HESTIA_ENV production
set_var HESTIA_MODE device
set_var HESTIA_DEMO_USERS false
fill_secret HESTIA_SECRET_KEY
fill_secret HESTIA_JOURNAL_KEY
fill_secret HESTIA_DEVICE_COMMAND_KEY
fill_secret HESTIA_MQTT_PASSWORD
chmod 600 .env

# ── 2. Certificates ──────────────────────────────────────────────────────────
mkdir -p deploy/mosquitto/certs
docker run --rm -e NAMES="$*" -v "$PWD/deploy/mosquitto/certs:/certs" alpine:3 sh -eu -c '
    apk add --no-cache openssl >/dev/null
    cd /certs
    if [ ! -f ca.key ]; then
        openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -sha256 -days 3650 -nodes \
            -keyout ca.key -out ca.crt -subj "/CN=Hestia building CA" 2>/dev/null
        echo "  created the certificate authority"
    fi
    # "mosquitto" is how the gateway reaches the broker inside Docker.
    san="DNS:mosquitto"
    for n in $NAMES; do
        case "$n" in *[!0-9.]*) san="$san,DNS:$n" ;; *) san="$san,IP:$n" ;; esac
    done
    openssl req -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
        -keyout server.key -out server.csr -subj "/CN=hestia-broker" 2>/dev/null
    printf "subjectAltName=%s\nextendedKeyUsage=serverAuth\n" "$san" > server.ext
    openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 825 -sha256 \
        -out server.crt -extfile server.ext 2>/dev/null
    rm -f server.csr server.ext
    chown 1883:1883 server.key && chmod 600 server.key   # readable by the broker only
    chmod 600 ca.key && chmod 644 ca.crt server.crt
    echo "  broker certificate for: $san"
'

# ── 3. The gateway's broker login ────────────────────────────────────────────
gateway_password=$(grep "^HESTIA_MQTT_PASSWORD=" .env | cut -d= -f2-)
./deploy/add-mqtt-user.sh hestia-gateway "$gateway_password" > /dev/null
echo "  broker login for the gateway: hestia-gateway"

cat <<EOF

Done. Next:
  1. For each controller:   ./deploy/add-mqtt-user.sh esp32_1
     (prints its MQTT password for firmware/include/secrets.h)
  2. In secrets.h also set:
       MQTT_CA_CERT      = the content of deploy/mosquitto/certs/ca.crt
       COMMAND_HMAC_KEY  = HESTIA_DEVICE_COMMAND_KEY from .env
  3. Start:                  docker compose --profile device up -d
  4. Create the first administrator:
       docker compose exec gateway python -m hestia.cli create-user <name> --role admin
EOF
