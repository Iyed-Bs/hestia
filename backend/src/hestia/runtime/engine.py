"""
The gateway engine: one pipeline for every message from the controller.

    telemetry ──▶ integrity checks ──▶ maintenance standing ──▶ ML advice
              ──▶ energy manager (permit, heating) ──▶ link (to the device)
              ──▶ safety journal + alerts on every meaningful transition
              ──▶ snapshot pushed to every open dashboard (Server-Sent Events)

It runs the same way on the digital twin and on a real ESP32 (see links.py).
All work happens on one asyncio loop and one lock, so the order of events in
the journal is the order in which they happened.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from hestia import __version__
from hestia.domain.commands import Command, CommandRejected, ResetH2Alarm, SetActuator, SetMode, SetThreshold
from hestia.domain.energy import EnergyDecision, EnergyInputs, EnergyManager
from hestia.domain.telemetry import SiteContext, Telemetry
from hestia.ml.runtime import Advice, MLAdvisor
from hestia.settings import Settings
from hestia.trust.integrity import IntegrityConfig, IntegrityMonitor, IntegrityReport, Level
from hestia.trust.journal import EventKind, SafetyJournal
from hestia.trust.maintenance import CheckKind, MaintenanceLog, Sensor
from hestia.twin.faults import ALL_FAULTS, BENCH_FAULTS, FaultName
from hestia.twin.params import SiteProfile
from hestia.twin.plant import SiteModel

from .links import DeviceLink, LinkError, TwinLink
from .notifier import Notifier
from .site import SiteEstimator

log = logging.getLogger(__name__)

# How sensors are named in journal lines and alert e-mails.
SENSOR_LABEL = {
    "h2": "H₂ detector",
    "temperature": "Temperature probe",
    "electrolyte": "Electrolyte probe",
    "link": "Controller link",
}
SENSOR_LABEL_FR = {
    "h2": "Détecteur H₂",
    "temperature": "Sonde de température",
    "electrolyte": "Sonde d'électrolyte",
    "link": "Liaison contrôleur",
}

BROADCAST_EVERY_S = 0.25  # real seconds between dashboard updates
MAINTENANCE_REFRESH_S = 30.0


class Engine:
    def __init__(
        self,
        settings: Settings,
        journal: SafetyJournal,
        maintenance: MaintenanceLog,
        link: DeviceLink,
        advisor: MLAdvisor | None,
        notifier: Notifier,
        *,
        label: str = "Gateway",
    ) -> None:
        self.s = settings
        self.label = label  # "Gateway", or "Simulator" for a private simulation
        self.snapshot_extra: Callable[[], dict[str, Any]] | None = None
        self.journal = journal
        self.maintenance = maintenance
        self.link = link
        self.advisor = advisor
        self.notifier = notifier
        self.integrity = IntegrityMonitor(
            IntegrityConfig(expected_period_s=link.expected_period_s),
            h2_baseline_ppm=settings.h2_baseline_ppm,
        )
        self.energy = EnergyManager()
        self.site_estimator = SiteEstimator(settings) if link.kind == "device" else None

        self.telemetry: Telemetry | None = None
        self.site: SiteContext | None = None
        self.trust: IntegrityReport | None = None
        self.decision: EnergyDecision | None = None
        self.advice: Advice = Advice(available=False)

        self._lock = asyncio.Lock()
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._last_broadcast = 0.0
        self._last_maintenance = -1e12
        self._advice_hour: int | None = None
        self._levels: dict[str, Level] = {}
        self._h2_trusted = True
        self._online = False
        self._overheated = False
        self._warning = False
        self._vented_kg = 0.0
        self._alarm = False
        self._expected_alarm_clear = False
        self._tasks: list[asyncio.Task[None]] = []
        self.started_at = datetime.now(UTC)

    # ── What is on this installation ─────────────────────────────────────────

    def site_profile(self) -> SiteProfile | None:
        """The simulated site's profile (None on the bench replica and real devices)."""
        if isinstance(self.link, TwinLink) and isinstance(self.link.device.model, SiteModel):
            return self.link.device.model.p
        return None

    def fixed_supply(self) -> bool:
        """The stack runs from a lab supply (the bench), not from solar surplus."""
        if isinstance(self.link, TwinLink):
            return not isinstance(self.link.device.model, SiteModel)
        return self.s.pv_source == "bench"

    def stack_min_w(self) -> float:
        if isinstance(self.link, TwinLink):
            return self.link.device.plant.p.stack.min_power_w
        return self.s.stack_rated_w * self.s.stack_min_load

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._restore_calibration()
        self.journal.append(
            EventKind.SYSTEM_START,
            f"{self.label} started (Hestia {__version__}, {self.link.kind} mode)",
            details={"mode": self.link.kind, "site": self.s.site_name},
        )
        if self.advisor and self.advisor.errors:
            self.journal.append(
                EventKind.MODEL_INTEGRITY,
                "Some ML models were refused at start-up",
                details=self.advisor.errors,
            )
        if hasattr(self.link, "on_rejected"):
            self.link.on_rejected = self._telemetry_rejected
        await self.link.start(self.ingest)
        self._tasks.append(asyncio.create_task(self._watchdog(), name="engine-watchdog"))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await self.link.stop()

    def _restore_calibration(self) -> None:
        """A gateway restart must not forget the H₂ sensor's calibrated baseline."""
        for record in self.maintenance.history(limit=200):
            if record["kind"] == "calibration" and record["sensor"] == "h2" and record["result"] == "pass":
                baseline = record["data"].get("clean_air_ppm")
                if baseline is not None:
                    self.integrity.calibrated(float(baseline))
                break

    async def _watchdog(self) -> None:
        """Re-assess once a second even when nothing arrives (that is how silence is noticed)."""
        while True:
            await asyncio.sleep(1.0)
            if self.site_estimator:
                self.site = await self.site_estimator.current()
            if self.link.kind == "device" or getattr(self.link, "paused", False):
                await self.ingest(None, None, self.link.now_s())

    # ── The pipeline ─────────────────────────────────────────────────────────

    async def ingest(self, telemetry: Telemetry | None, site: SiteContext | None, now_s: float) -> None:
        async with self._lock:
            if site is not None:
                self.site = site
            if telemetry is not None:
                for event in self.integrity.observe(telemetry, now_s):
                    if event == "rebooted":
                        self.journal.append(
                            EventKind.DEVICE_REBOOTED,
                            "The controller restarted",
                            actor=f"device:{telemetry.device_id}",
                        )
                self.telemetry = telemetry

            if time.monotonic() - self._last_maintenance > MAINTENANCE_REFRESH_S:
                self._last_maintenance = time.monotonic()
                self.integrity.set_maintenance(*self.maintenance.h2_sensor_standing())

            self.trust = self.integrity.assess(now_s)
            self._update_advice()

            if telemetry is not None:
                site_now = self.site
                profile = self.site_profile()
                self.decision = self.energy.decide(
                    EnergyInputs(
                        now_s=now_s,
                        available_w=site_now.available_w if site_now else 0.0,
                        stack_min_w=self.stack_min_w(),
                        electrolyser_running=telemetry.electrolyser,
                        h2_sensor_trusted=self.trust.h2_trusted,
                        h2_sensor_why=self.trust.h2_why,
                        h2_tank_pct=site_now.h2_tank_pct if site_now else None,
                        indoor_c=site_now.indoor_c if site_now else None,
                        outdoor_c=site_now.outdoor_c if site_now else None,
                        heat_setpoint_c=profile.building.heat_setpoint_c if profile else 20.0,
                        cool_setpoint_c=profile.building.cool_setpoint_c if profile else 26.0,
                        cooling_available=profile is not None,
                        h2_use=profile.h2_use if profile else "never",
                        ml_production_level=self.advice.production_level if self.advice.available else None,
                        fixed_supply=self.fixed_supply(),
                        otherwise_curtailed=profile is not None and profile.surplus_order()[-1] == "hydrogen",
                        gas_detected=telemetry.h2_warning or telemetry.h2_alarm_latched,
                    )
                )
                try:
                    await self.link.set_permit(self.decision.permit)
                except LinkError as exc:
                    log.warning("Permit not delivered: %s", exc)
                self.link.set_hvac(self.decision.hvac)

            critical = await self._transitions(telemetry)
            now = time.monotonic()
            if critical or now - self._last_broadcast >= BROADCAST_EVERY_S:
                self._last_broadcast = now
                self._broadcast()

    def _update_advice(self) -> None:
        if not self.advisor or not self.site:
            return
        ts = self.site.timestamp
        hour_key = ts.toordinal() * 24 + ts.hour
        if hour_key == self._advice_hour:
            return
        self._advice_hour = hour_key
        day = ts.timetuple().tm_yday
        self.advisor.record_hour(
            ts.hour, day, self.site.outdoor_c, self.site.humidity_pct, self.site.irradiance_wpm2
        )
        self.advice = self.advisor.advise(
            hour=ts.hour + ts.minute / 60,
            day=day,
            temp_c=self.site.outdoor_c,
            humidity=self.site.humidity_pct,
            irradiance=self.site.irradiance_wpm2,
            h2_ppm=self.telemetry.h2_ppm if self.telemetry else 55.0,
        )

    async def _transitions(self, t: Telemetry | None) -> bool:
        """Write every safety-relevant change to the journal; alert on critical ones."""
        critical = False
        trust = self.trust
        if trust is None:
            return False
        device = f"device:{t.device_id}" if t else f"device:{self.s.device_id}"

        online = trust.sensors["link"].level is not Level.UNTRUSTED
        if online != self._online:
            self._online = online
            if online:
                self.journal.append(EventKind.DEVICE_ONLINE, "Controller reporting", actor=device)
            elif self.telemetry is not None:  # never seen at all: not an "offline" event
                self.journal.append(
                    EventKind.DEVICE_OFFLINE, trust.sensors["link"].findings[0].message, actor=device
                )
                await self.notifier.alert(
                    "offline",
                    "Controller unreachable",
                    "Contrôleur injoignable",
                    trust.sensors["link"].findings[0].message,
                    "Plus aucune télémesure : la production est suspendue par sécurité.",
                )
                critical = True

        for name, health in trust.sensors.items():
            if name == "link":
                continue
            before = self._levels.get(name, Level.OK)
            if health.level != before:
                self._levels[name] = health.level
                messages = [f.message for f in health.findings] or ["All checks passing again"]
                self.journal.append(
                    EventKind.SENSOR_TRUST_CHANGED,
                    f"{SENSOR_LABEL.get(name, name)}: {before.label} → {health.level.label}",
                    details={
                        "sensor": name,
                        "from": before.label,
                        "to": health.level.label,
                        "findings": messages,
                    },
                    severity="critical"
                    if health.level is Level.UNTRUSTED
                    else "warning"
                    if health.level
                    else "info",
                )
                if health.level is Level.UNTRUSTED:
                    critical = True
                    await self.notifier.alert(
                        f"trust-{name}",
                        f"{SENSOR_LABEL.get(name, name)} no longer trusted",
                        f"{SENSOR_LABEL_FR.get(name, name)} : plus fiable",
                        "\n".join(messages),
                        "Le capteur ne peut plus garantir ses mesures : la production est suspendue.",
                    )

        if trust.h2_trusted != self._h2_trusted:
            self._h2_trusted = trust.h2_trusted
            if not trust.h2_trusted:
                self.journal.append(
                    EventKind.PERMIT_WITHDRAWN_FOR_SAFETY, f"Hydrogen production suspended: {trust.h2_why}"
                )

        if t is not None:
            if t.h2_warning != self._warning:
                self._warning = t.h2_warning
                if t.h2_warning:
                    critical = True
                    self.journal.append(
                        EventKind.H2_WARNING,
                        f"Hydrogen detected at {t.h2_ppm:.0f} ppm (stage 1, {t.h2_warning_ppm:.0f} ppm): "
                        "production stopped, extraction running",
                        actor=device,
                        details={"h2_ppm": t.h2_ppm, "threshold": t.h2_warning_ppm},
                    )
                    await self.notifier.alert(
                        "h2-warning",
                        "Hydrogen detected",
                        "Hydrogène détecté",
                        f"H₂ at {t.h2_ppm:.0f} ppm near the stack. "
                        "Production stopped, extraction fan running.\n"
                        "Check fittings and lines; the alarm stage cuts power if it keeps rising.",
                        f"H₂ à {t.h2_ppm:.0f} ppm près de l'empilement. "
                        "Production arrêtée, extraction en marche.\n"
                        "Vérifier raccords et conduites ; le stade d'alarme coupe l'alimentation "
                        "si cela monte.",
                    )
                else:
                    self.journal.append(
                        EventKind.H2_WARNING,
                        "Air clean again after the purge: hydrogen warning cleared",
                        actor=device,
                        severity="info",
                    )
            if t.h2_alarm_latched and not self._alarm:
                self._alarm = True
                critical = True
                self.journal.append(
                    EventKind.ALARM_LATCHED,
                    f"H₂ alarm latched at {t.h2_ppm:.0f} ppm (stage 2, {t.h2_alarm_ppm:.0f} ppm)",
                    actor=device,
                    details={"h2_ppm": t.h2_ppm, "threshold": t.h2_alarm_ppm},
                )
                await self.notifier.alert(
                    "h2-alarm",
                    "H2 LEAK ALARM",
                    "ALARME FUITE H2",
                    f"H₂ at {t.h2_ppm:.0f} ppm. Power cut, emergency relay open, extraction running.\n"
                    "Keep away, ventilate, inspect, then reset with a written reason in Hestia.",
                    f"H₂ à {t.h2_ppm:.0f} ppm. Alimentation coupée, relais d'urgence ouvert, "
                    "extraction en marche.\n"
                    "S'éloigner, ventiler, inspecter, puis réarmer avec un motif écrit dans Hestia.",
                )
            elif not t.h2_alarm_latched and self._alarm:
                self._alarm = False
                if not self._expected_alarm_clear:
                    self.journal.append(
                        EventKind.ALARM_RESET,
                        "H₂ alarm cleared on the controller itself",
                        actor=device,
                        severity="warning",
                    )
                self._expected_alarm_clear = False

            overheated = t.temp_valid and t.electrolyte_c >= t.temp_alert_c
            if overheated and not self._overheated:
                critical = True
                self.journal.append(
                    EventKind.OVER_TEMPERATURE,
                    f"Electrolyte at {t.electrolyte_c:.1f} °C (limit {t.temp_alert_c:.0f} °C)",
                    actor=device,
                )
                await self.notifier.alert(
                    "overtemp",
                    "Electrolyte over-temperature",
                    "Surchauffe de l'électrolyte",
                    f"{t.electrolyte_c:.1f} °C: production stopped, cooling forced on.",
                    f"{t.electrolyte_c:.1f} °C : production arrêtée, refroidissement forcé.",
                )
            self._overheated = overheated

        vented = self.site.h2_vented_kg if self.site and self.site.h2_vented_kg is not None else 0.0
        if vented > self._vented_kg + 1e-6:
            critical = True
            self.journal.append(
                EventKind.PRESSURE_RELIEF,
                f"Storage relief valve vented {1000 * (vented - self._vented_kg):.1f} g of hydrogen "
                f"(tank at its maximum pressure)",
                actor=device,
                details={"vented_g": round(1000 * (vented - self._vented_kg), 2)},
            )
            self._vented_kg = vented
        return critical

    def _telemetry_rejected(self, why: str) -> None:
        self.journal.append(EventKind.TELEMETRY_REJECTED, f"Message from the controller rejected: {why}")

    # ── Operator actions ─────────────────────────────────────────────────────

    async def command(self, command: Command, actor: str) -> str:
        if isinstance(command, ResetH2Alarm):
            self._expected_alarm_clear = True
        try:
            result = await self.link.send(command)
        except CommandRejected as exc:
            self._expected_alarm_clear = False
            self.journal.append(
                EventKind.COMMAND_REJECTED,
                f"Command refused: {exc}",
                actor=actor,
                details=command.model_dump(mode="json"),
            )
            raise
        match command:
            case ResetH2Alarm(reason=reason):
                self.journal.append(
                    EventKind.ALARM_RESET,
                    "H₂ alarm reset by an operator",
                    actor=actor,
                    details={"reason": reason},
                )
            case SetMode(mode=mode):
                self.journal.append(EventKind.MODE_CHANGED, f"Mode set to {mode.value}", actor=actor)
            case SetActuator(actuator=a, on=on):
                self.journal.append(
                    EventKind.MANUAL_ACTUATOR, f"{a} switched {'ON' if on else 'OFF'} by hand", actor=actor
                )
            case SetThreshold(name=name, value=value):
                self.journal.append(
                    EventKind.THRESHOLD_CHANGED,
                    f"{name} set to {value:g}",
                    actor=actor,
                    details={"name": name, "value": value},
                )
        async with self._lock:
            self._broadcast()
        return result

    async def record_maintenance(
        self, *, kind: CheckKind, sensor: Sensor, technician: str, data: dict[str, float], notes: str
    ) -> dict[str, Any]:
        result = self.maintenance.record(
            kind=kind,
            sensor=sensor,
            technician=technician,
            data=data,
            notes=notes,
            baseline_ppm=self.integrity.h2_baseline_ppm,
            warning_ppm=self.telemetry.h2_warning_ppm if self.telemetry else 500.0,
        )
        if sensor == "h2":
            if kind == "calibration" and result["result"] == "pass" and "clean_air_ppm" in data:
                self.integrity.calibrated(float(data["clean_air_ppm"]))
            if kind == "sensor_replaced":
                self.integrity.sensor_replaced()
            if isinstance(self.link, TwinLink) and result["result"] == "pass":
                self.link.service_sensor(kind)
        self._last_maintenance = -1e12  # re-read the standing on the next message
        return result

    # ── Digital-twin controls ────────────────────────────────────────────────

    def twin(self) -> TwinLink:
        if not isinstance(self.link, TwinLink):
            raise CommandRejected("Only available in digital-twin mode")
        return self.link

    def set_fault(self, name: FaultName, on: bool, actor: str) -> None:
        self.twin().set_fault(name, on)
        self.journal.append(
            EventKind.FAULT_INJECTED,
            f"[Simulation] fault '{name}' {'on' if on else 'off'}",
            actor=actor,
            details={"fault": name, "on": on, "simulation": True},
        )

    # ── Snapshots for the dashboard ──────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        twin = None
        if isinstance(self.link, TwinLink):
            d = self.link.device
            plant = d.plant
            totals = dict(plant.totals)
            if isinstance(d.model, SiteModel):
                totals.update(d.model.totals)
            twin = {
                "model": d.model.kind,
                "speed": self.link.speed,
                "paused": self.link.paused,
                "faults": sorted(d.faults.active),
                "available_faults": list(ALL_FAULTS if isinstance(d.model, SiteModel) else BENCH_FAULTS),
                "sim_time": self.site.timestamp.isoformat() if self.site else None,
                "local_time": self.site.local_time if self.site else "",
                "totals": {k: round(v, 4) for k, v in totals.items()},
                "electrolyte_l": round(plant.solution.litres(plant.electrolyte_c), 2),
                "electrolyte_wt_pct": round(plant.solution.wt_pct, 2),
                "conductivity_s_per_cm": round(plant.solution.conductivity(plant.electrolyte_c), 3),
                "stack": {
                    "rated_w": plant.p.stack.rated_w,
                    "min_w": plant.p.stack.min_power_w,
                    "cells": plant.p.stack.cells,
                    "cell_voltage": round(plant.point.cell_voltage, 3),
                    "current_a": round(plant.point.current_a, 2),
                    "faraday_efficiency": round(plant.point.faraday_efficiency, 4),
                    "heat_w": round(plant.point.heat_w, 1),
                    "kwh_per_kg": round(plant.point.kwh_per_kg, 1) if plant.point.h2_kg_per_s > 0 else None,
                    "h2_g_per_h": round(plant.point.h2_kg_per_s * 3.6e6, 2),
                },
            }
        return {
            "version": __version__,
            "mode": self.link.kind,
            "site_name": self.s.site_name,
            "updated_at": datetime.now(UTC).isoformat(),
            "online": self._online,
            "telemetry": self.telemetry.model_dump(mode="json") if self.telemetry else None,
            "site": self.site.model_dump(mode="json") if self.site else None,
            "trust": self.trust.as_dict() if self.trust else None,
            "energy": asdict(self.decision) if self.decision else None,
            "advice": {**asdict(self.advice), "production_label": self.advice.production_label},
            "twin": twin,
            "journal_head": self.journal.head_hash()[:16],
            "sim": self.snapshot_extra() if self.snapshot_extra else None,
        }

    def _broadcast(self) -> None:
        payload = json.dumps(self.snapshot(), default=str)
        for queue in list(self._subscribers):
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()  # keep only the newest snapshot for slow clients
            queue.put_nowait(payload)

    def subscribe(self) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        queue.put_nowait(json.dumps(self.snapshot(), default=str))
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def uptime(self) -> timedelta:
        return datetime.now(UTC) - self.started_at
