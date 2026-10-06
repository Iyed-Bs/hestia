"""
From the weather file's horizontal irradiance to the AC output of tilted panels.

Weather files (Open-Meteo) give the global horizontal irradiance (GHI) as the
mean of the preceding hour. Panels are tilted, so the chain is:

1. Sun position. Declination and equation of time from Spencer (1971), as in
   Duffie & Beckman, *Solar Engineering of Thermal Processes*, 4th ed., ch. 1.
2. Beam / diffuse split of GHI with the Erbs, Klein & Duffie (1982) diffuse
   fraction correlation.
3. Irradiance on the tilted plane with the HDKR model (Hay, Davies, Klucher,
   Reindl; Duffie & Beckman eq. 2.16.7), which keeps circumsolar and
   horizon brightening that the plain isotropic model loses.
4. Cell temperature with the NOCT model, and module power with the module's
   temperature coefficient.
5. System losses and inverter efficiency, with the defaults of NREL PVWatts v5
   (Dobos 2014): 14.08 % losses, 96 % inverter, -0.37 %/K for standard
   crystalline modules.

Checked in tests/test_physics.py: equinox declination, Erbs limits, and the
bundled Tunis year giving an annual yield in the range PVGIS reports for Tunis.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

SOLAR_CONSTANT = 1361.0  # W/m², Kopp & Lean (2011)


@dataclass(frozen=True)
class Sun:
    cos_zenith: float  # ≤ 0 when the sun is below the horizon
    declination: float  # rad
    hour_angle: float  # rad, negative in the morning
    normal_extraterrestrial: float  # W/m² on a surface facing the sun, above the atmosphere


def sun_at(when_utc: datetime, latitude_deg: float, longitude_deg: float) -> Sun:
    """Sun position at a UTC instant (longitude positive east)."""
    day = when_utc.timetuple().tm_yday
    hours = when_utc.hour + when_utc.minute / 60.0 + when_utc.second / 3600.0
    b = 2.0 * math.pi * (day - 1) / 365.0
    # Spencer (1971): equation of time (minutes) and declination (radians).
    eot_min = 229.18 * (
        0.000075
        + 0.001868 * math.cos(b)
        - 0.032077 * math.sin(b)
        - 0.014615 * math.cos(2 * b)
        - 0.040849 * math.sin(2 * b)
    )
    declination = (
        0.006918
        - 0.399912 * math.cos(b)
        + 0.070257 * math.sin(b)
        - 0.006758 * math.cos(2 * b)
        + 0.000907 * math.sin(2 * b)
        - 0.002697 * math.cos(3 * b)
        + 0.00148 * math.sin(3 * b)
    )
    solar_time = hours + longitude_deg / 15.0 + eot_min / 60.0
    hour_angle = math.radians(15.0 * (solar_time - 12.0))
    phi = math.radians(latitude_deg)
    cos_zenith = math.sin(phi) * math.sin(declination) + math.cos(phi) * math.cos(declination) * math.cos(
        hour_angle
    )
    # Earth-sun distance correction (Spencer 1971).
    distance = (
        1.000110
        + 0.034221 * math.cos(b)
        + 0.001280 * math.sin(b)
        + 0.000719 * math.cos(2 * b)
        + 0.000077 * math.sin(2 * b)
    )
    return Sun(cos_zenith, declination, hour_angle, SOLAR_CONSTANT * distance)


def erbs_diffuse_fraction(clearness: float) -> float:
    """Share of GHI that is diffuse, from the clearness index kt (Erbs et al. 1982)."""
    kt = max(0.0, clearness)
    if kt <= 0.22:
        return 1.0 - 0.09 * kt
    if kt <= 0.80:
        return 0.9511 - 0.1604 * kt + 4.388 * kt**2 - 16.638 * kt**3 + 12.336 * kt**4
    return 0.165


def cos_incidence(sun: Sun, latitude_deg: float, tilt_deg: float, azimuth_deg: float) -> float:
    """Cosine of the angle between the sun and the panel normal (Duffie & Beckman eq. 1.6.2).

    azimuth_deg: 0 = facing the equator (south in the northern hemisphere),
    negative east, positive west.
    """
    d, w = sun.declination, sun.hour_angle
    phi, beta, gamma = (math.radians(x) for x in (latitude_deg, tilt_deg, azimuth_deg))
    if latitude_deg < 0:
        gamma += math.pi  # in the southern hemisphere "facing the equator" means north
    return (
        math.sin(d) * math.sin(phi) * math.cos(beta)
        - math.sin(d) * math.cos(phi) * math.sin(beta) * math.cos(gamma)
        + math.cos(d) * math.cos(phi) * math.cos(beta) * math.cos(w)
        + math.cos(d) * math.sin(phi) * math.sin(beta) * math.cos(gamma) * math.cos(w)
        + math.cos(d) * math.sin(beta) * math.sin(gamma) * math.sin(w)
    )


def plane_of_array(
    ghi: float,
    sun: Sun,
    latitude_deg: float,
    tilt_deg: float,
    azimuth_deg: float,
    albedo: float = 0.20,
) -> float:
    """Irradiance on the tilted panels (W/m²), HDKR model."""
    if ghi <= 0.0:
        return 0.0
    beta = math.radians(tilt_deg)
    ground = ghi * albedo * (1.0 - math.cos(beta)) / 2.0
    if sun.cos_zenith < 0.065:
        # Sun within ~4° of the horizon: the beam/diffuse split is meaningless
        # there (division by a tiny cos θz). Treat everything as diffuse.
        return ghi * (1.0 + math.cos(beta)) / 2.0 + ground
    horizontal_extraterrestrial = sun.normal_extraterrestrial * sun.cos_zenith
    clearness = min(1.0, ghi / horizontal_extraterrestrial)
    diffuse = erbs_diffuse_fraction(clearness) * ghi
    beam = ghi - diffuse
    rb = max(0.0, cos_incidence(sun, latitude_deg, tilt_deg, azimuth_deg)) / sun.cos_zenith
    anisotropy = (beam / sun.cos_zenith) / sun.normal_extraterrestrial  # beam transmittance
    horizon = 1.0 + math.sqrt(beam / ghi) * math.sin(beta / 2.0) ** 3
    return (
        (beam + diffuse * anisotropy) * rb
        + diffuse * (1.0 - anisotropy) * (1.0 + math.cos(beta)) / 2.0 * horizon
        + ground
    )


@dataclass(frozen=True)
class PvArray:
    kwp: float  # nameplate DC power at standard test conditions
    tilt_deg: float = 30.0
    azimuth_deg: float = 0.0  # 0 = facing the equator
    temp_coeff_per_k: float = -0.0037  # PVWatts "standard" crystalline module
    noct_c: float = 45.0  # nominal operating cell temperature
    system_losses: float = 0.1408  # soiling, shading, mismatch, wiring, LID, availability (PVWatts v5)
    inverter_efficiency: float = 0.96
    albedo: float = 0.20

    def cell_temperature(self, poa: float, ambient_c: float) -> float:
        return ambient_c + (self.noct_c - 20.0) / 800.0 * poa

    def ac_power(self, poa: float, ambient_c: float) -> float:
        """AC output (W) for a plane-of-array irradiance and ambient temperature."""
        if poa <= 0.0 or self.kwp <= 0.0:
            return 0.0
        cell = self.cell_temperature(poa, ambient_c)
        dc = self.kwp * 1000.0 * (poa / 1000.0) * (1.0 + self.temp_coeff_per_k * (cell - 25.0))
        ac = dc * (1.0 - self.system_losses) * self.inverter_efficiency
        return max(0.0, min(ac, self.kwp * 1000.0 * self.inverter_efficiency))

    def output(
        self, ghi: float, ambient_c: float, when_utc: datetime, latitude: float, longitude: float
    ) -> float:
        """AC output (W) from horizontal irradiance at an instant."""
        sun = sun_at(when_utc, latitude, longitude)
        poa = plane_of_array(ghi, sun, latitude, self.tilt_deg, self.azimuth_deg, self.albedo)
        return self.ac_power(poa, ambient_c)
