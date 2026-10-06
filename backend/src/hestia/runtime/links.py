"""
Links between the gateway engine and a controller.

TwinLink  drives the virtual controller of the digital twin (HESTIA_MODE=twin)
MqttLink  talks to a real ESP32 through the building's private broker
          (HESTIA_MODE=device): TLS, per-device credentials, signed commands

Both deliver the same Telemetry objects to the engine and accept the same
commands, so nothing above this file knows (or cares) which one is running.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import ssl
import threading
import time
from collections.abc import Callable, Coroutine
from typing import Any, Protocol

from pydantic import ValidationError

from hestia.domain.commands import Command, CommandRejected
from hestia.domain.energy import HvacCommand
from hestia.domain.telemetry import SiteContext, Telemetry
from hestia.settings import Settings
from hestia.twin.device import VirtualDevice
from hestia.twin.faults import FaultName
from hestia.twin.plant import BenchModel

from .envelope import EnvelopeSigner

log = logging.getLogger(__name__)

# The engine's entry point: (telemetry or None, site context or None, monotonic seconds).
Sink = Callable[[Telemetry | None, SiteContext | None, float], Coroutine[Any, Any, None]]


class LinkError(RuntimeError):
    """The command could not be delivered (device offline, no acknowledgement)."""


class DeviceLink(Protocol):
    kind: str
    expected_period_s: float

    async def start(self, sink: Sink) -> None: ...
    async def stop(self) -> None: ...
    async def send(self, command: Command) -> str: ...
    async def set_permit(self, permit: bool) -> None: ...
    def set_hvac(self, hvac: HvacCommand) -> None: ...
    def now_s(self) -> float: ...


# ── Digital twin ─────────────────────────────────────────────────────────────


class TwinLink:
    """Drives a virtual device: the bench replica (live view) or a simulator site."""

    kind = "twin"
    TICK_S = 0.25  # real seconds between twin updates
    MAX_STEP_S = 30.0  # physics step ceiling at high speed (every time constant is longer)

    def __init__(self, settings: Settings, device: VirtualDevice | None = None) -> None:
        self.device = device or VirtualDevice(BenchModel(seed=settings.twin_seed))
        self.speed = settings.twin_speed
        self.step_s = settings.twin_step_s
        self.expected_period_s = settings.twin_step_s
        self._task: asyncio.Task[None] | None = None
        self.paused = False

    async def start(self, sink: Sink) -> None:
        self._task = asyncio.create_task(self._run(sink), name="twin-loop")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self, sink: Sink) -> None:
        while True:
            started = time.monotonic()
            if not self.paused:
                await self.advance(self.speed * self.TICK_S, sink)
            await asyncio.sleep(max(0.0, self.TICK_S - (time.monotonic() - started)))

    async def advance(self, seconds: float, sink: Sink) -> None:
        """Run the physics forward by `seconds` of simulated time."""
        step = (
            max(self.step_s, seconds / math.ceil(seconds / self.MAX_STEP_S)) if seconds > 0 else self.step_s
        )
        steps = max(1, math.ceil(seconds / step))
        dt = seconds / steps
        for _ in range(steps):
            telemetry, site = self.device.cycle(dt)
            await sink(telemetry, site, self.device.uptime_s)

    async def send(self, command: Command) -> str:
        return self.device.apply(command)  # raises CommandRejected like the firmware would

    async def set_permit(self, permit: bool) -> None:
        self.device.set_permit(permit)

    def set_hvac(self, hvac: HvacCommand) -> None:
        self.device.set_hvac(hvac)

    def now_s(self) -> float:
        # The twin's forward-only clock (see VirtualDevice), not its weather position.
        return self.device.uptime_s

    # Twin-only controls (operator, from the dashboard's simulation panel).
    def set_fault(self, name: FaultName, on: bool) -> None:
        self.device.set_fault(name, on)

    def set_speed(self, speed: float) -> None:
        self.speed = max(1.0, min(3600.0, speed))

    def jump_to(self, month: int, day: int, hour: float) -> None:
        self.device.jump_to(month, day, hour)

    def bump_test(self, gas_ppm: float) -> float:
        """Expose the simulated sensor to test gas; returns its peak reading."""
        return self.device.bump_test(gas_ppm)

    def service_sensor(self, kind: str) -> None:
        """What a technician's intervention does to the simulated sensor."""
        faults = self.device.faults
        if kind == "calibration":
            faults.set("h2_drift", False, self.device.uptime_s)
        elif kind == "sensor_replaced":
            sensor_faults: tuple[FaultName, ...] = ("h2_drift", "h2_poisoned", "h2_dead", "h2_stuck")
            for name in sensor_faults:
                faults.set(name, False, self.device.uptime_s)


# ── Real controller over MQTT ────────────────────────────────────────────────


def _resolve(future: asyncio.Future[dict[str, Any]], payload: dict[str, Any]) -> None:
    if not future.done():
        future.set_result(payload)


class MqttLink:
    kind = "device"

    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.expected_period_s = settings.device_period_s
        base = f"{settings.topic_prefix}/{settings.site_id}/{settings.device_id}"
        self.t_telemetry, self.t_cmd, self.t_ack, self.t_status = (
            f"{base}/telemetry",
            f"{base}/cmd",
            f"{base}/ack",
            f"{base}/status",
        )
        self.signer = EnvelopeSigner(settings.command_secret())
        self._client: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sink: Sink | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self.rejected_messages = 0
        self.on_rejected: Callable[[str], None] | None = None
        self._permit_task: asyncio.Task[None] | None = None
        self._permit = False
        self._hvac_sent: dict[str, object] | None = None
        self.connected = False

    async def start(self, sink: Sink) -> None:
        import paho.mqtt.client as mqtt

        self._loop = asyncio.get_running_loop()
        self._sink = sink
        from paho.mqtt.enums import CallbackAPIVersion

        client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=f"hestia-gateway-{self.s.site_id}")
        if self.s.mqtt_password:
            client.username_pw_set(self.s.mqtt_username, self.s.mqtt_password.get_secret_value())
        if self.s.mqtt_tls:
            context = ssl.create_default_context(
                cafile=str(self.s.mqtt_ca_cert) if self.s.mqtt_ca_cert else None
            )
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            client.tls_set_context(context)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.connect_async(self.s.mqtt_host, self.s.mqtt_port, keepalive=30)
        client.loop_start()
        self._client = client
        self._permit_task = asyncio.create_task(self._refresh_permit(), name="permit-refresh")

    async def stop(self) -> None:
        if self._permit_task:
            self._permit_task.cancel()
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()

    # paho callbacks run on paho's network thread.
    def _on_connect(self, client: Any, userdata: Any, flags: Any, reason: Any, properties: Any) -> None:
        self.connected = not reason.is_failure
        if self.connected:
            client.subscribe([(self.t_telemetry, 1), (self.t_ack, 1), (self.t_status, 1)])
            log.info("Connected to MQTT broker %s:%s", self.s.mqtt_host, self.s.mqtt_port)
        else:
            log.error("MQTT connection refused: %s", reason)

    def _on_disconnect(self, client: Any, userdata: Any, flags: Any, reason: Any, properties: Any) -> None:
        self.connected = False
        log.warning("MQTT disconnected: %s", reason)

    def _on_message(self, client: Any, userdata: Any, msg: Any) -> None:
        if self._loop is None or self._sink is None:
            return  # not started yet
        if len(msg.payload) > 4096:  # nothing legitimate is that large
            self._reject(f"oversized message on {msg.topic}")
            return
        try:
            payload = json.loads(msg.payload)
        except (ValueError, UnicodeDecodeError):
            self._reject(f"non-JSON message on {msg.topic}")
            return
        if msg.topic == self.t_telemetry:
            try:
                telemetry = Telemetry.model_validate(payload)
            except ValidationError as exc:
                self._reject(f"invalid telemetry ({exc.error_count()} errors)")
                return
            if telemetry.device_id != self.s.device_id:
                self._reject("telemetry from an unexpected device id")
                return
            asyncio.run_coroutine_threadsafe(self._sink(telemetry, None, time.monotonic()), self._loop)
        elif msg.topic == self.t_ack and isinstance(payload, dict):
            seq = payload.get("seq")
            with self._lock:
                future = self._pending.pop(seq, None) if isinstance(seq, int) else None
            if future:
                self._loop.call_soon_threadsafe(_resolve, future, payload)

    def _reject(self, why: str) -> None:
        self.rejected_messages += 1
        log.warning("Rejected MQTT message: %s", why)
        if self.on_rejected and self._loop:
            self._loop.call_soon_threadsafe(self.on_rejected, why)

    async def _publish(self, command: dict[str, Any], timeout_s: float = 5.0) -> dict[str, Any]:
        if not self._client or not self.connected:
            raise LinkError("The controller's broker is not connected")
        if self._loop is None:
            raise LinkError("The link is not started")
        envelope = self.signer.sign(command)
        future: asyncio.Future[dict[str, Any]] = self._loop.create_future()
        with self._lock:
            self._pending[envelope["seq"]] = future
        self._client.publish(self.t_cmd, json.dumps(envelope), qos=1)
        try:
            return await asyncio.wait_for(future, timeout_s)
        except TimeoutError as exc:
            with self._lock:
                self._pending.pop(envelope["seq"], None)
            raise LinkError("The controller did not acknowledge the command") from exc

    async def send(self, command: Command) -> str:
        ack = await self._publish(command.model_dump(mode="json"))
        if not ack.get("ok"):
            raise CommandRejected(str(ack.get("result", "Refused by the controller")))
        return str(ack.get("result", "Done"))

    async def set_permit(self, permit: bool) -> None:
        if permit != self._permit:
            self._permit = permit
            await self._send_permit()

    async def _send_permit(self) -> None:
        try:
            await self._publish(
                {"kind": "set_permit", "permit": self._permit, "valid_for_s": self.s.permit_valid_s}
            )
        except LinkError as exc:
            log.warning("Permit not delivered: %s", exc)

    async def _refresh_permit(self) -> None:
        """Re-send a granted permit before it expires on the device (fail-safe lease)."""
        while True:
            await asyncio.sleep(self.s.permit_valid_s / 3)
            if self._permit:
                await self._send_permit()

    def set_hvac(self, hvac: HvacCommand) -> None:
        # Heating and cooling on a real site go through the building's own
        # heat pump / fuel cell controller; publishing the intent (retained,
        # only when it changes) is the site integration point.
        intent: dict[str, object] = {
            "mode": hvac.mode,
            "setpoint_c": hvac.setpoint_c,
            "allow_h2": hvac.allow_h2,
        }
        if self._client and self.connected and intent != self._hvac_sent:
            self._hvac_sent = intent
            self._client.publish(
                f"{self.s.topic_prefix}/{self.s.site_id}/hvac", json.dumps(intent), qos=1, retain=True
            )

    def now_s(self) -> float:
        return time.monotonic()
