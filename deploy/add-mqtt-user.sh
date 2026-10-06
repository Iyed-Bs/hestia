#!/usr/bin/env bash
# =============================================================================
# Create (or reset) a broker login.
#
#   ./deploy/add-mqtt-user.sh esp32_1            new controller: prints its password
#   ./deploy/add-mqtt-user.sh esp32_1 <password> set a given password
#
# A controller's username must be its DEVICE_ID (firmware/include/config.h):
# the ACL (deploy/mosquitto/acl) only lets it use topics carrying that id.
# To revoke a controller, run this again (new password) or delete its line
# from deploy/mosquitto/passwd, then reload the broker.
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

user="${1:-}"
if ! [[ "$user" =~ ^[A-Za-z0-9_-]{1,32}$ ]]; then
    echo "usage: $0 <username: letters, digits, - and _> [password]" >&2
    exit 64
fi
password="${2:-$(docker run --rm alpine:3 sh -c 'head -c 24 /dev/urandom | base64 | tr -d "/+=\n"')}"

# Hash the password into the broker's file (inside a container: nothing to
# install), readable by the broker's user only.
docker run --rm -e U="$user" -e P="$password" -v "$PWD/deploy/mosquitto:/work" eclipse-mosquitto:2 sh -eu -c '
    touch /work/passwd
    mosquitto_passwd -b /work/passwd "$U" "$P"
    chown 1883:1883 /work/passwd && chmod 600 /work/passwd
'

# Apply it now if the broker is running.
docker compose kill -s HUP mosquitto > /dev/null 2>&1 || true

echo "MQTT login  username: $user"
echo "            password: $password"
echo "(shown once: put it in firmware/include/secrets.h as MQTT_PASSWORD)"
