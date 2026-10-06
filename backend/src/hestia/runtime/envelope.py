"""
Signed command envelopes between the gateway and a controller.

A command published on MQTT looks like:

    {"seq": 1790861234567, "ts": 1790861234, "cmd": "{\"kind\":\"set_mode\",\"mode\":\"AUTO\"}",
     "mac": "9f2c…"}

- cmd  is the command as a JSON *string*, signed exactly as transmitted, so
       the firmware never has to re-serialise JSON to check a signature;
- mac  = HMAC-SHA256(key, f"{seq}|{ts}|{cmd}") with the key shared by the
       gateway (HESTIA_DEVICE_COMMAND_KEY) and the firmware (COMMAND_HMAC_KEY);
- seq  strictly increases (milliseconds since the epoch, so it keeps
       increasing across gateway restarts); the device rejects any seq it
       has already passed: replayed messages are refused;
- ts   must be within MAX_SKEW_S of the device's clock (NTP): an old captured
       message cannot be replayed after a device reboot either.

The broker's TLS and per-device passwords keep outsiders off the network;
the signature makes sure that even someone on the broker cannot drive the
electrolyser. The firmware implements verify() in
firmware/lib/hestia_core/src/command_auth.cpp; spec/command_vectors.json keeps
the two in agreement.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

MAX_SKEW_S = 30


def _mac(key: bytes, seq: int, ts: int, cmd: str) -> str:
    return hmac.new(key, f"{seq}|{ts}|{cmd}".encode(), hashlib.sha256).hexdigest()


class EnvelopeSigner:
    def __init__(self, key: bytes) -> None:
        if len(key) < 32:
            raise ValueError("The device command key must be at least 32 bytes")
        self._key = key
        self._last_seq = 0

    def sign(self, command: dict[str, Any], now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        seq = max(int(now * 1000), self._last_seq + 1)
        self._last_seq = seq
        ts = int(now)
        cmd = json.dumps(command, separators=(",", ":"), sort_keys=True)
        return {"seq": seq, "ts": ts, "cmd": cmd, "mac": _mac(self._key, seq, ts, cmd)}


class EnvelopeVerifier:
    """Device-side check (used by the tests and documented for the firmware)."""

    def __init__(self, key: bytes) -> None:
        self._key = key
        self.last_seq = 0

    def verify(self, envelope: dict[str, Any], now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        seq, ts, cmd, mac = (
            int(envelope["seq"]),
            int(envelope["ts"]),
            str(envelope["cmd"]),
            str(envelope["mac"]),
        )
        if not hmac.compare_digest(_mac(self._key, seq, ts, cmd), mac):
            raise ValueError("bad signature")
        if seq <= self.last_seq:
            raise ValueError("replayed sequence number")
        if abs(now - ts) > MAX_SKEW_S:
            raise ValueError("timestamp outside the allowed window")
        self.last_seq = seq
        parsed: dict[str, Any] = json.loads(cmd)
        return parsed
