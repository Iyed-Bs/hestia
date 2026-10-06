"""
Machine-learning advisor: forecasts and advice that help the energy manager,
never decisions that bypass the safety controller.

Four models, all trained on a year of real Tunis weather (Open-Meteo,
2025-05-24 → 2026-05-24) and exported by ml/export_onnx.py:

- pv_power        dense network:  conditions now        → PV output (W)
- thermal_demand  LSTM:           last 24 hourly samples → next-hour heat demand (W)
- leak_anomaly    isolation forest on conditions + H₂ reading → "unusual
                  conditions" score. Despite its historical file name it
                  CANNOT detect leaks: its score saturates above the H₂ range
                  seen in training (~55 ppm), see tests/test_ml_runtime.py and
                  ADR-004. Leaks are handled by the deterministic threshold
                  and latch in domain/control.py, never by this model.
- rl_policy       Q-table:        PV, demand, hour       → production level 0–4 (advice)

Supply-chain safety: every model file's SHA-256 is checked against
manifest.json before it is loaded, and only ONNX + JSON are ever read (no
pickles, no TensorFlow). A file that fails the check is not loaded; the
advisor reports itself unavailable and the energy manager simply falls back
to its plain rules. The ML layer can make the system better, never unsafe.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

MODELS_DIR = Path(__file__).resolve().parent / "models"
RL_LEVELS = ("OFF", "LOW", "MEDIUM", "HIGH", "FULL")


# Loaded sessions, shared by every advisor in the process and keyed by the
# file's fingerprint: the isolation forest takes ~10 s to build in ONNX
# Runtime, so it is built once, in the background, never on the request path.
_SESSIONS: dict[str, Any] = {}
_SESSIONS_LOCK = threading.Lock()


class ModelIntegrityError(RuntimeError):
    """A model file does not match the fingerprint in the manifest."""


@dataclass(frozen=True)
class Advice:
    """What the ML layer thinks, for one moment. Purely advisory."""

    available: bool
    pv_forecast_w: float | None = None
    heat_demand_forecast_w: float | None = None  # None until 24 h of history exist
    production_level: int | None = None  # 0 (OFF) … 4 (FULL)
    anomaly: bool = False
    anomaly_severity: float = 0.0  # 0 = normal … 1 = strongly anomalous

    @property
    def production_label(self) -> str | None:
        return None if self.production_level is None else RL_LEVELS[self.production_level]


class MLAdvisor:
    """Loads the models once and answers advice requests."""

    def __init__(self, models_dir: Path = MODELS_DIR, *, background: bool = False) -> None:
        """Load and verify the models.

        background=True returns immediately and loads on a worker thread (the
        gateway does this so start-up is never blocked); each model becomes
        available as soon as it is ready. Tests load in the foreground.
        """
        self.models_dir = models_dir
        self.manifest: dict[str, Any] = json.loads((models_dir / "manifest.json").read_text(encoding="utf-8"))
        self._sessions: dict[str, Any] = {}
        self._history: deque[list[float]] = deque(maxlen=24)
        self.errors: dict[str, str] = {}
        q = np.asarray(self.manifest["rl_policy"]["q_table"], dtype=np.float64)
        self._q_table = q if q.ndim == 2 else None
        if background:
            threading.Thread(target=self._load_all, name="ml-loader", daemon=True).start()
        else:
            self._load_all()

    def _load_all(self) -> None:
        for name, meta in self.manifest["models"].items():
            try:
                self._sessions[name] = self._load(meta)
            except (ModelIntegrityError, OSError, ValueError) as exc:
                self.errors[name] = str(exc)
                log.error("Model %s not loaded: %s", name, exc)

    def _load(self, meta: dict[str, Any]) -> Any:
        import onnxruntime as ort

        path = self.models_dir / meta["file"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != meta["sha256"]:
            raise ModelIntegrityError(f"{path.name}: SHA-256 mismatch (file altered or corrupted)")
        with _SESSIONS_LOCK:
            if digest not in _SESSIONS:
                options = ort.SessionOptions()
                options.intra_op_num_threads = 1  # a gateway shares its CPU with the control loop
                _SESSIONS[digest] = ort.InferenceSession(
                    str(path), sess_options=options, providers=["CPUExecutionProvider"]
                )
            return _SESSIONS[digest]

    @property
    def available(self) -> bool:
        return bool(self._sessions)

    def status(self) -> dict[str, Any]:
        """Model health for the dashboard and the safety report."""
        return {
            "loaded": sorted(self._sessions),
            "loading": sorted(set(self.manifest["models"]) - set(self._sessions) - set(self.errors)),
            "errors": self.errors,
            "exported_at": self.manifest.get("exported_at"),
            "trained_on": self.manifest.get("trained", {}).get("samples"),
        }

    # ── Individual models ────────────────────────────────────────────────────

    def _run(self, name: str, x: np.ndarray) -> float | None:
        session = self._sessions.get(name)
        if session is None:
            return None
        meta = self.manifest["models"][name]
        mean = np.asarray(meta["norm_mean"], dtype=np.float32)
        std = np.asarray(meta["norm_std"], dtype=np.float32)
        z = ((x - mean) / std).astype(np.float32)
        value = float(np.asarray(session.run(None, {meta["input"]: z})[0]).reshape(-1)[0])
        if "target_mean" in meta:  # the LSTM was trained on normalised targets
            value = value * meta["target_std"] + meta["target_mean"]
        return value if math.isfinite(value) else None

    def pv_forecast(
        self, hour: float, day: float, temp_c: float, humidity: float, irradiance: float
    ) -> float | None:
        value = self._run("pv_power", np.array([[hour, day, temp_c, humidity, irradiance]], dtype=np.float32))
        return None if value is None else max(0.0, value)

    def heat_demand_forecast(self) -> float | None:
        if len(self._history) < 24:
            return None
        value = self._run("thermal_demand", np.asarray([list(self._history)], dtype=np.float32))
        return None if value is None else max(0.0, value)

    def production_advice(self, pv_w: float, demand_w: float, hour: float) -> int | None:
        if self._q_table is None:
            return None
        n_states = self._q_table.shape[0]
        # Same state compression the agent was trained with (ml/scripts/train.py).
        value = (
            min(pv_w / 1800.0, 1.0) * 0.5 + (1.0 - min(demand_w / 8000.0, 1.0)) * 0.3 + (hour / 23.0) * 0.2
        )
        state = int(np.clip(value * (n_states - 1), 0, n_states - 1))
        return int(np.argmax(self._q_table[state]))

    def anomaly(self, features: list[float]) -> tuple[bool, float]:
        session = self._sessions.get("leak_anomaly")
        if session is None:
            return False, 0.0
        meta = self.manifest["models"]["leak_anomaly"]
        x = np.asarray([features[: meta["n_features"]]], dtype=np.float32)
        outputs = session.run([meta["score_output"]], {meta["input"]: x})
        score = float(np.asarray(outputs[0]).reshape(-1)[0])
        threshold = float(meta["decision_threshold"])
        severity = float(np.clip((threshold - score) / (abs(threshold) + 1e-9), 0.0, 1.0))
        return score < threshold, severity

    # ── Combined advice ──────────────────────────────────────────────────────

    def advise(
        self, *, hour: float, day: float, temp_c: float, humidity: float, irradiance: float, h2_ppm: float
    ) -> Advice:
        """Everything the energy manager can use for one control moment.

        Call it about once per simulated hour for the LSTM history to mean
        "the last 24 hours"; the engine does this (runtime/engine.py).
        """
        if not self.available:
            return Advice(available=False)
        pv = self.pv_forecast(hour, day, temp_c, humidity, irradiance)
        demand = self.heat_demand_forecast()
        level = self.production_advice(pv or 0.0, demand if demand is not None else 3000.0, hour)
        anomalous, severity = self.anomaly(
            [temp_c, humidity, irradiance, pv or 0.0, demand if demand is not None else 0.0, h2_ppm]
        )
        return Advice(True, pv, demand, level, anomalous, round(severity, 3))

    def record_hour(self, hour: float, day: float, temp_c: float, humidity: float, irradiance: float) -> None:
        """Append one hourly sample to the LSTM's 24-hour history."""
        self._history.append([hour, day, temp_c, humidity, irradiance])
