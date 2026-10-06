"""
The energy manager: the gateway's supervisory layer.

It decides two things each cycle:

1. the production permit sent to the safety controller: whether producing
   hydrogen is possible and worth it right now (enough power for the stack's
   minimum load, room in the tank, a trusted H₂ sensor, no short-cycling,
   advice from the ML layer);
2. comfort: heat, cool or nothing, and whether stored hydrogen may be used
   for it (through the fuel cell or the hydrogen boiler).

It can only ever *withhold* production: the safety controller still applies
every interlock, and drops the permit by itself if the gateway goes silent.

The physical dispatch of power (which source covers which load) happens in
the site model (twin/plant.py) with fixed, documented priorities; the energy
manager sets the intent.

Decisions, with reasons (docs/DESIGN-DECISIONS.md):
- the stack starts at 1.2 × its minimum load and stops below its minimum
  (physics/electrolyser.py explains why alkaline stacks have one);
- the battery may only bridge a *running* stack through a cloud; it never
  starts production at night (ADR-003);
- an untrusted H₂ sensor withdraws the permit at once;
- comfort band 20-26 °C (EN 16798-1, category II) with ±0.5 K hysteresis; no
  heating when it is warmer outside than the heating setpoint;
- stored hydrogen keeps a 5 % reserve, released (down to 1 %) only when the
  building falls below 15 °C;
- "auto" use of hydrogen: when its heat is useful (heating), or when the
  tank is above 80 % (using some makes room for surplus that would otherwise
  be curtailed). In summer it is otherwise kept: a kWh of hydrogen gives
  electricity *and* heat in winter, electricity only in summer;
- ML advice may delay a start, but never while the surplus the stack would
  use has nowhere else to go (it would be curtailed).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

HvacMode = Literal["off", "heat", "cool"]
H2Use = Literal["auto", "grid_backup", "heating_only", "never"]
AUTO_SPILL_PCT = 80.0  # auto: above this the tank is used even without heating, to make room


@dataclass(frozen=True)
class EnergyPolicy:
    start_margin: float = 1.2  # start when the available power reaches 1.2 × the stack minimum
    min_on_s: float = 300.0  # anti short-cycling (stack wear)
    min_off_s: float = 200.0
    tank_stop_pct: float = 98.0
    tank_resume_pct: float = 90.0
    comfort_hysteresis_c: float = 0.5
    emergency_below_c: float = 15.0  # people at risk: the H₂ reserve may be used
    h2_reserve_pct: float = 5.0
    emergency_reserve_pct: float = 1.0
    use_ml_advice: bool = True


@dataclass(frozen=True)
class EnergyInputs:
    now_s: float  # monotonic seconds (simulated or real)
    available_w: float  # power the stack could use now (surplus, or the bench supply)
    stack_min_w: float  # the stack's minimum load
    electrolyser_running: bool  # as reported by the controller
    h2_sensor_trusted: bool
    h2_sensor_why: str = ""
    h2_tank_pct: float | None = None  # None when the gas is not stored
    indoor_c: float | None = None  # None when there is no building
    outdoor_c: float | None = None
    heat_setpoint_c: float = 20.0
    cool_setpoint_c: float = 26.0
    cooling_available: bool = False
    h2_use: H2Use = "never"
    ml_production_level: int | None = None  # 0 (OFF) … 4 (FULL), advisory
    # Hydrogen is the last destination for surplus here: what the stack does
    # not take is curtailed, so waiting can only waste it.
    otherwise_curtailed: bool = False
    # A bench on a lab supply: power is always there, so solar advice does not apply.
    fixed_supply: bool = False
    # The controller reports hydrogen in the room (either stage): the gateway
    # withholds the permit too, so both layers say the same thing.
    gas_detected: bool = False


@dataclass(frozen=True)
class HvacCommand:
    mode: HvacMode = "off"
    setpoint_c: float = 0.0
    allow_h2: bool = False  # stored hydrogen may cover this demand
    emergency: bool = False
    reason: str = ""


@dataclass(frozen=True)
class EnergyDecision:
    permit: bool
    permit_reason: str
    hvac: HvacCommand

    @property
    def heating(self) -> bool:
        return self.hvac.mode == "heat"


class EnergyManager:
    def __init__(self, policy: EnergyPolicy | None = None) -> None:
        self.policy = policy or EnergyPolicy()
        self._tank_full = False
        self._mode: HvacMode = "off"
        self._permit = False
        self._changed_at = -1e12  # long ago: free to change immediately at start-up

    def decide(self, x: EnergyInputs) -> EnergyDecision:
        p = self.policy
        hvac = self._hvac_decision(x)
        permit, reason, hard = self._permit_decision(x)

        # Anti short-cycling applies to "soft" reasons only; safety and
        # physical impossibilities (hard) act immediately.
        if permit != self._permit and not hard:
            held_for = x.now_s - self._changed_at
            minimum = p.min_on_s if self._permit else p.min_off_s
            if held_for < minimum:
                # Keep the current state; say which state is held and why.
                left = minimum - held_for
                permit = self._permit
                reason = (
                    f"Each run lasts at least {p.min_on_s:.0f} s to protect the stack ({left:.0f} s left)"
                    if self._permit
                    else f"Stack rests at least {p.min_off_s:.0f} s between runs ({left:.0f} s left)"
                )
        if permit != self._permit:
            self._permit = permit
            self._changed_at = x.now_s
        return EnergyDecision(permit, reason, hvac)

    def _permit_decision(self, x: EnergyInputs) -> tuple[bool, str, bool]:
        """Returns (permit, reason, hard) where hard = cannot be delayed."""
        p = self.policy
        if x.gas_detected:
            return False, "Hydrogen detected in the room: production suspended", True
        if not x.h2_sensor_trusted:
            return False, f"H₂ sensor not trusted: {x.h2_sensor_why or 'see the safety page'}", True

        if x.h2_tank_pct is not None:
            if x.h2_tank_pct >= p.tank_stop_pct:
                self._tank_full = True
            elif x.h2_tank_pct < p.tank_resume_pct:
                self._tank_full = False
            if self._tank_full:
                return (
                    False,
                    f"H₂ tank full ({x.h2_tank_pct:.0f} %), resumes below {p.tank_resume_pct:.0f} %",
                    True,
                )

        start_w = p.start_margin * x.stack_min_w
        if x.electrolyser_running:
            if x.available_w < x.stack_min_w:
                return (
                    False,
                    f"Not enough power to keep the stack at its minimum load "
                    f"({x.available_w:.0f} W < {x.stack_min_w:.0f} W)",
                    True,
                )
        elif x.available_w < start_w:
            return (
                False,
                f"Waiting for power ({x.available_w:.0f} W, the stack needs {start_w:.0f} W to start)",
                True,
            )

        if x.fixed_supply:
            return True, "Producing on the bench supply", False
        if p.use_ml_advice and x.ml_production_level == 0 and not x.otherwise_curtailed:
            return False, "ML advisor suggests waiting (low expected value now)", False
        return True, "Producing on surplus power", False

    def _hvac_decision(self, x: EnergyInputs) -> HvacCommand:
        p = self.policy
        if x.indoor_c is None:
            self._mode = "off"
            return HvacCommand(reason="No building on this installation")
        indoor, h = x.indoor_c, p.comfort_hysteresis_c
        warm_outside = x.outdoor_c is not None and x.outdoor_c >= x.heat_setpoint_c + 2.0

        mode = self._mode
        heat_done = mode == "heat" and (indoor >= x.heat_setpoint_c + h or warm_outside)
        cool_done = mode == "cool" and (indoor <= x.cool_setpoint_c - h or not x.cooling_available)
        if heat_done or cool_done:
            mode = "off"
        if mode == "off":
            if indoor < x.heat_setpoint_c - h and not warm_outside:
                mode = "heat"
            elif indoor > x.cool_setpoint_c + h and x.cooling_available:
                mode = "cool"
        self._mode = mode

        emergency = mode == "heat" and indoor < p.emergency_below_c
        reserve = p.emergency_reserve_pct if emergency else p.h2_reserve_pct
        has_h2 = x.h2_tank_pct is not None and x.h2_tank_pct > reserve
        tank = x.h2_tank_pct or 0.0
        allow = has_h2 and (
            x.h2_use == "grid_backup"
            or (x.h2_use == "heating_only" and mode == "heat")
            or (x.h2_use == "auto" and (mode == "heat" or tank >= AUTO_SPILL_PCT))
        )

        if mode == "heat":
            setpoint = x.heat_setpoint_c
            reason = (
                f"Emergency heating: building below {p.emergency_below_c:.0f} °C"
                if emergency
                else f"Heating to {setpoint:.0f} °C ({indoor:.1f} °C inside)"
            )
        elif mode == "cool":
            setpoint = x.cool_setpoint_c
            reason = f"Cooling to {setpoint:.0f} °C ({indoor:.1f} °C inside)"
        else:
            setpoint = 0.0
            reason = (
                f"Warm outside ({x.outdoor_c:.0f} °C): no heating needed"
                if warm_outside and indoor < x.heat_setpoint_c
                else f"Comfortable ({indoor:.1f} °C)"
            )
        if mode != "off":
            if allow:
                reason += "; stored hydrogen available"
            elif x.h2_tank_pct is not None and not has_h2:
                reason += f"; hydrogen at reserve ({x.h2_tank_pct:.0f} %)"
        return HvacCommand(mode=mode, setpoint_c=setpoint, allow_h2=allow, emergency=emergency, reason=reason)
