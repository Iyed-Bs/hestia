"""The ML advisor loads only verified models and gives sane, bounded advice."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from hestia.ml.runtime import MODELS_DIR, MLAdvisor


@pytest.fixture(scope="module")
def advisor() -> MLAdvisor:
    return MLAdvisor()


def test_all_models_load_and_verify(advisor: MLAdvisor) -> None:
    assert advisor.errors == {}
    assert set(advisor.status()["loaded"]) == {"pv_power", "thermal_demand", "leak_anomaly"}


def test_pv_forecast_follows_the_sun(advisor: MLAdvisor) -> None:
    night = advisor.pv_forecast(hour=2, day=180, temp_c=22, humidity=60, irradiance=0)
    noon = advisor.pv_forecast(hour=12, day=180, temp_c=32, humidity=40, irradiance=900)
    assert night is not None and noon is not None
    assert noon > night + 500


def test_heat_demand_needs_24_hours_of_history(advisor: MLAdvisor) -> None:
    fresh = MLAdvisor()
    assert fresh.heat_demand_forecast() is None
    for hour in range(24):
        fresh.record_hour(hour, 20, 8.0, 70, 0 if hour < 7 or hour > 18 else 400)
    winter = fresh.heat_demand_forecast()
    assert winter is not None and winter > 0


def test_production_advice_is_a_valid_level(advisor: MLAdvisor) -> None:
    level = advisor.production_advice(pv_w=1500, demand_w=1000, hour=13)
    assert level in range(5)


def test_unusual_conditions_are_flagged(advisor: MLAdvisor) -> None:
    normal, _ = advisor.anomaly([30, 40, 850, 1400, 800, 55])
    odd, severity = advisor.anomaly([2, 10, 1100, 1700, 9000, 55])  # freezing under a blazing sun
    assert not normal
    assert odd and severity > 0


def test_isolation_forest_cannot_see_a_leak(advisor: MLAdvisor) -> None:
    """Documented limitation, kept as a test so nobody wires safety to this model.

    An isolation forest cannot isolate points beyond the range it was trained
    on, and the training data only ever saw ~55 ppm of H₂: its score saturates,
    so 900 or 5000 ppm look exactly as "normal" as 300 ppm. H₂ leaks are
    therefore handled deterministically (threshold + latch in
    domain/control.py) and the H₂ sensor itself is watched by
    trust/integrity.py. If this test ever fails because a retrained model can
    see leaks, revisit docs/DESIGN-DECISIONS.md (ADR-004) before relying on it.
    """
    scores = {ppm: advisor.anomaly([25, 55, 700, 1200, 1500, ppm]) for ppm in (300, 900, 5000)}
    assert scores[300] == scores[900] == scores[5000]
    assert not scores[5000][0]


def test_tampered_model_is_refused(tmp_path: Path) -> None:
    copy = tmp_path / "models"
    shutil.copytree(MODELS_DIR, copy)
    data = bytearray((copy / "pv_power.onnx").read_bytes())
    data[-1] ^= 0xFF  # flip one byte
    (copy / "pv_power.onnx").write_bytes(bytes(data))
    tampered = MLAdvisor(copy)
    assert "pv_power" in tampered.errors
    assert "SHA-256" in tampered.errors["pv_power"]
    # The other models still work: one bad file never takes the advisor down.
    assert "thermal_demand" in tampered.status()["loaded"]
    manifest = json.loads((copy / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["models"]["pv_power"]["sha256"]
