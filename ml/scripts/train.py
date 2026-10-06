#!/usr/bin/env python3
"""
Train all models for the hydrogen system.

Models trained (primary — TensorFlow):
    1. PV power prediction       — Feed-forward NN (TensorFlow)
    2. Thermal demand forecast   — LSTM (TensorFlow)
    3. Electrolyser optimisation — Q-learning RL agent
    4. Unusual conditions        — Isolation Forest (scikit-learn); historically named
                                   "leak detector", but it cannot detect leaks (see below)

Fallbacks:
    If TensorFlow is not available the script falls back to scikit-learn
    regressors (saved under `pv_model/` and `thermal_model/`).

Usage:
    python scripts/train.py
    python scripts/train.py --dataset data/processed/dataset.csv --epochs 100
"""

import csv
import json
import pickle
import argparse
import sys
from pathlib import Path

import numpy as np

import random

# Try TensorFlow first (preferred for NN/LSTM). If unavailable, fall back to scikit-learn implementations.
TF_OK = False
SK_OK = False
try:
    import tensorflow as tf
    from tensorflow.keras import Sequential, layers
    from tensorflow.keras.optimizers import Adam
    from tensorflow.keras.callbacks import EarlyStopping, ModelCheckpoint, ReduceLROnPlateau
    TF_OK = True
except Exception:
    TF_OK = False

try:
    from sklearn.ensemble import IsolationForest, HistGradientBoostingRegressor
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import mean_absolute_error
    import joblib
    SK_OK = True
except Exception:
    SK_OK = False

if not TF_OK and not SK_OK:
    print("[ERROR] Neither TensorFlow nor scikit-learn are available. Install one of them.")


# ── Dataset loading ────────────────────────────────────────────────────────

def load_dataset(path: str):
    """
    Returns arrays ready for training.

    Features (X): [hour, day_of_year, temperature_c, humidity_pct, solar_irradiance_wpm2]
    Targets:       pv_power_w, thermal_demand_w
    """
    X, y_pv, y_th = [], [], []
    with open(path) as f:
        for row in csv.DictReader(f):
            try:
                X.append([
                    float(row["hour"]),
                    float(row["day_of_year"]),
                    float(row["temperature_c"]),
                    float(row["humidity_pct"]),
                    float(row["solar_irradiance_wpm2"]),
                ])
                y_pv.append(float(row["pv_power_w"]))
                y_th.append(float(row["thermal_demand_w"]))
            except (KeyError, ValueError):
                pass

    X    = np.array(X,    dtype=np.float32)
    y_pv = np.array(y_pv, dtype=np.float32)
    y_th = np.array(y_th, dtype=np.float32)
    print(f"Dataset: {len(X)} samples  features={X.shape[1]}")
    return X, y_pv, y_th


def time_split(X, y, ratio=0.7):
    """Chronological 70/30 split (no shuffle — time series)."""
    n = int(len(X) * ratio)
    return X[:n], X[n:], y[:n], y[n:]


# ── Normalisation ──────────────────────────────────────────────────────────

def normalise(X_train, X_test):
    mean = X_train.mean(axis=0)
    std  = X_train.std(axis=0) + 1e-8
    return (X_train - mean) / std, (X_test - mean) / std, mean, std


def set_random_seeds(seed: int = 42):
    random.seed(seed)
    np.random.seed(seed)
    try:
        if TF_OK:
            tf.random.set_seed(seed)
    except Exception:
        pass


# ── 1. PV Neural Network ───────────────────────────────────────────────────

def train_pv_model(X, y_pv, epochs, out_dir: Path):
    if not SK_OK:
        return

    print("\n-- PV Regression (scikit-learn) ----------------------")
    X_tr, X_te, y_tr, y_te = time_split(X, y_pv)

    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X_tr)
    X_te_s = scaler.transform(X_te)

    # HistGradientBoostingRegressor is a strong, simple tabular regressor with early stopping
    model = HistGradientBoostingRegressor(max_iter=500, early_stopping=True,
                                         validation_fraction=0.15, random_state=42)
    model.fit(X_tr_s, y_tr)

    mae = mean_absolute_error(y_te, model.predict(X_te_s))
    print(f"  Test MAE: {mae:.1f} W")

    save_dir = out_dir / "pv_model"
    save_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, save_dir / "pv_model.joblib")
    joblib.dump(scaler, save_dir / "pv_scaler.joblib")
    print(f"  Saved -> {save_dir}/")


def train_pv_nn(X, y_pv, epochs, out_dir: Path):
    """
    TensorFlow feed-forward NN for PV prediction.
    Falls back to scikit-learn implementation if TF not available.
    """
    if not TF_OK:
        print("[WARN] TensorFlow not available — falling back to scikit-learn PV model")
        return train_pv_model(X, y_pv, epochs, out_dir)

    print("\n-- PV Neural Network (TensorFlow) -------------------")
    set_random_seeds(42)

    X_tr, X_te, y_tr, y_te = time_split(X, y_pv)
    X_tr_n, X_te_n, mean, std = normalise(X_tr, X_te)

    model = Sequential([
        layers.Input(shape=(X.shape[1],)),
        layers.Dense(64, activation="relu"),
        layers.Dropout(0.15),
        layers.Dense(32, activation="relu"),
        layers.Dense(16, activation="relu"),
        layers.Dense(1, activation="linear"),
    ])
    model.compile(optimizer=Adam(1e-3), loss="mse", metrics=["mae"])

    save_dir = out_dir / "pv_nn"
    save_dir.mkdir(parents=True, exist_ok=True)

    chk_path = str(save_dir / "best_weights.weights.h5")
    callbacks = [
        EarlyStopping(patience=10, restore_best_weights=True),
        ModelCheckpoint(chk_path, save_best_only=True, save_weights_only=True),
        ReduceLROnPlateau(factor=0.5, patience=5),
    ]

    history = model.fit(
        X_tr_n, y_tr,
        validation_split=0.15,
        epochs=epochs,
        batch_size=32,
        callbacks=callbacks,
        verbose=1,
    )

    # Evaluate
    X_te_n = (X_te - mean) / std
    mae = model.evaluate(X_te_n, y_te, verbose=0)[1]
    print(f"  Test MAE: {mae:.1f} W")

    # Save model and normalisation (use Keras export for SavedModel compatibility)
    try:
        model.export(str(save_dir))
    except Exception:
        model.save(str(save_dir))
    np.save(save_dir / "norm_mean.npy", mean)
    np.save(save_dir / "norm_std.npy", std)

    # Save training history
    with open(save_dir / "history.json", "w") as f:
        json.dump(history.history, f)

    print(f"  Saved -> {save_dir}/")


# ── 2. Thermal LSTM ────────────────────────────────────────────────────────

SEQ_LEN = 24   # Use the past 24 hours to predict the next hour (flattened sequence)

def make_sequence_features(X, y, seq_len):
    Xs, ys = [], []
    for i in range(len(X) - seq_len):
        seq = X[i : i + seq_len].flatten()
        Xs.append(seq)
        ys.append(y[i + seq_len])
    return np.array(Xs, dtype=np.float32), np.array(ys, dtype=np.float32)


def train_thermal_model(X, y_th, epochs, out_dir: Path):
    if not SK_OK:
        return

    print("\n-- Thermal Regression (scikit-learn, flattened sequences) --")
    Xs, ys = make_sequence_features(X, y_th, SEQ_LEN)

    n = int(len(Xs) * 0.7)
    X_tr, X_te = Xs[:n], Xs[n:]
    y_tr, y_te = ys[:n], ys[n:]

    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X_tr)
    X_te_s = scaler.transform(X_te)

    model = HistGradientBoostingRegressor(max_iter=500, early_stopping=True,
                                         validation_fraction=0.15, random_state=42)
    model.fit(X_tr_s, y_tr)

    mae = mean_absolute_error(y_te, model.predict(X_te_s))
    print(f"  Test MAE: {mae:.1f} W")

    save_dir = out_dir / "thermal_model"
    save_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, save_dir / "thermal_model.joblib")
    joblib.dump(scaler, save_dir / "thermal_scaler.joblib")
    print(f"  Saved -> {save_dir}/")


def make_sequences(X, y, seq_len):
    Xs, ys = [], []
    for i in range(len(X) - seq_len):
        Xs.append(X[i : i + seq_len])
        ys.append(y[i + seq_len])
    return np.array(Xs, dtype=np.float32), np.array(ys, dtype=np.float32)


def train_thermal_lstm(X, y_th, epochs, out_dir: Path):
    """
    TensorFlow LSTM for thermal demand forecasting.
    Falls back to scikit-learn flattened-sequence regressor if TF not available.
    """
    if not TF_OK:
        print("[WARN] TensorFlow not available — falling back to scikit-learn thermal model")
        return train_thermal_model(X, y_th, epochs, out_dir)

    print("\n-- Thermal LSTM (TensorFlow) -----------------------")
    set_random_seeds(42)

    Xs, ys = make_sequences(X, y_th, SEQ_LEN)
    n = int(len(Xs) * 0.7)
    X_tr, X_te = Xs[:n], Xs[n:]
    y_tr, y_te = ys[:n], ys[n:]

    # Normalise inputs over training set (per-feature)
    mean = X_tr.mean(axis=(0, 1))
    std = X_tr.std(axis=(0, 1)) + 1e-8
    X_tr_n = (X_tr - mean) / std
    X_te_n = (X_te - mean) / std

    # Normalise target (y) — raw Watts 0-6000 causes MSE~16M and unstable gradients
    y_mean = float(y_tr.mean())
    y_std  = float(y_tr.std() + 1e-8)
    y_tr_n = (y_tr - y_mean) / y_std
    y_te_n = (y_te - y_mean) / y_std

    model = Sequential([
        layers.LSTM(64, return_sequences=True, input_shape=(SEQ_LEN, X.shape[1])),
        layers.LSTM(32),
        layers.Dropout(0.15),
        layers.Dense(16, activation="relu"),
        layers.Dense(1, activation="linear"),
    ])
    model.compile(optimizer=Adam(1e-3), loss="mse", metrics=["mae"])

    save_dir = out_dir / "thermal_lstm"
    save_dir.mkdir(parents=True, exist_ok=True)
    chk_path = str(save_dir / "best_weights.weights.h5")
    callbacks = [
        EarlyStopping(patience=25, restore_best_weights=True),
        ModelCheckpoint(chk_path, save_best_only=True, save_weights_only=True),
        ReduceLROnPlateau(factor=0.5, patience=10, min_lr=1e-6),
    ]

    history = model.fit(
        X_tr_n, y_tr_n,
        validation_split=0.15,
        epochs=epochs,
        batch_size=32,
        callbacks=callbacks,
        verbose=1,
    )

    mae_norm = model.evaluate(X_te_n, y_te_n, verbose=0)[1]
    mae_watts = mae_norm * y_std   # denormalize for human-readable MAE
    print(f"  Test MAE: {mae_watts:.1f} W  (normalised MAE: {mae_norm:.4f})")

    try:
        model.export(str(save_dir))
    except Exception:
        model.save(str(save_dir))
    np.save(save_dir / "norm_mean.npy", mean)
    np.save(save_dir / "norm_std.npy", std)
    np.save(save_dir / "target_mean.npy", np.array([y_mean], dtype=np.float32))
    np.save(save_dir / "target_std.npy",  np.array([y_std],  dtype=np.float32))
    with open(save_dir / "history.json", "w") as f:
        json.dump(history.history, f)

    print(f"  Saved -> {save_dir}/")


# ── 3. Electrolyser RL Agent ───────────────────────────────────────────────
#
# State (discretised to 100 bins):
#   pv_power (0–1800 W) + thermal_demand (0–8000 W) + hour (0–23)
#
# Action:
#   0 = OFF
#   1 = LOW  (25 % capacity)
#   2 = MED  (50 % capacity)
#   3 = HIGH (75 % capacity)
#   4 = FULL (100 % capacity)
#
# Reward:
#   + produce when PV surplus is large and thermal demand is low
#   - produce when no PV and grid price is high

N_STATES  = 100
N_ACTIONS = 5


def discretise_state(pv_w: float, thermal_w: float, hour: int) -> int:
    pv_norm      = min(pv_w   / 1800.0, 1.0)
    thermal_norm = min(thermal_w / 8000.0, 1.0)
    hour_norm    = hour / 23.0
    value = pv_norm * 0.5 + (1.0 - thermal_norm) * 0.3 + hour_norm * 0.2
    return int(np.clip(value * (N_STATES - 1), 0, N_STATES - 1))


def reward(action: int, pv_w: float, thermal_w: float, hour: int) -> float:
    # Reward electrolyzer production only when PV is available (solar-powered system).
    # thermal_w: high demand means H₂ in storage is more valuable — slight bias to conserve.
    # hour: penalise high action late in day (hour > 16) when solar is fading.
    pv_available = pv_w > 300.0
    pv_surplus   = pv_w - 600.0
    high_demand  = thermal_w > 3000.0   # building needs H₂ for HVAC
    solar_fading = hour >= 16            # solar ramp-down; conserve remaining power

    if pv_available and pv_surplus > 0 and not solar_fading:
        # Peak solar, surplus power → reward production strongly
        r = 0.5 + action * 0.15
        if high_demand:
            r -= 0.1 * action  # slight penalty: thermal demand may need stored H₂
        return r
    elif pv_available:
        # Some PV but lower surplus or fading → modest reward
        return 0.2 + action * 0.05
    elif action > 1:
        # No PV, high production action → penalise (wastes stored resources)
        return -0.5 - action * 0.1
    elif action == 0:
        return 0.0   # Idle: neutral
    else:
        return 0.05  # Low action without PV: small positive


def train_rl_agent(X, y_pv, y_th, episodes: int, out_dir: Path):
    print("\n-- RL Agent -----------------------------------------")

    Q       = np.zeros((N_STATES, N_ACTIONS), dtype=np.float32)
    alpha   = 0.15    # learning rate
    gamma   = 0.97    # discount factor
    epsilon = 1.0
    epsilon_decay = 0.995
    epsilon_min   = 0.05

    rng  = np.random.default_rng(42)
    idxs = np.arange(len(X))

    for ep in range(episodes):
        i     = rng.integers(0, len(X))
        pv    = float(y_pv[i])
        th    = float(y_th[i])
        hour  = int(X[i, 0])
        state = discretise_state(pv, th, hour)

        # ε-greedy action
        if rng.random() < epsilon:
            action = rng.integers(N_ACTIONS)
        else:
            action = int(np.argmax(Q[state]))

        r = reward(action, pv, th, hour)

        # Simulate next state (small stochastic perturbation)
        next_pv   = np.clip(pv   + rng.normal(0, 200), 0, 1800)
        next_th   = np.clip(th   + rng.normal(0, 300), 0, 8000)
        next_hour = (hour + 1) % 24
        next_s    = discretise_state(next_pv, next_th, next_hour)

        # Q-learning update
        Q[state, action] += alpha * (r + gamma * np.max(Q[next_s]) - Q[state, action])

        epsilon = max(epsilon_min, epsilon * epsilon_decay)

        if (ep + 1) % (episodes // 5) == 0:
            print(f"  Episode {ep+1:5d}/{episodes}  ε={epsilon:.3f}")

    out_dir.mkdir(parents=True, exist_ok=True)
    np.savez(out_dir / "rl_agent.npz",
             Q=Q,
             n_states=np.array(N_STATES),
             n_actions=np.array(N_ACTIONS))
    print(f"  Saved -> {out_dir}/rl_agent.npz")


# ── 4. H₂ Leak Detector ───────────────────────────────────────────────────
#
# FINDING (docs/DESIGN-DECISIONS.md, ADR-004): this model does NOT detect
# leaks. Trained only on ~50–80 ppm, its score saturates: 300, 900 and
# 5 000 ppm all get the same score. Hestia uses it only to flag unusual
# conditions; leaks are caught by the controller's deterministic threshold
# and latch. The code is kept as it was so the shipped model is reproducible.
#
# Features: [temperature_c, humidity_pct, solar_irradiance_wpm2,
#            pv_power_w, thermal_demand_w]
#
# Trained on normal operating data; flags anomalous states as potential leaks.
# In production use, add real H₂ ppm readings as the primary feature.

def train_leak_detector(X, y_pv, y_th, out_dir: Path):
    if not SK_OK:
        return

    print("\n-- Leak Detector (Isolation Forest) -----------------")

    # Normal H₂ ppm model: background H₂ (50–80 ppm) with small irradiance correlation.
    # When the electrolyzer runs (PV > 0), minor H₂ diffusion raises ambient ppm slightly.
    # This gives the model a baseline distribution to flag true anomalies (600+ ppm leaks).
    # Source: simulation normal operating range (simulation.py L271: 50 + random(0,30)).
    rng = np.random.default_rng(42)
    irradiance_norm = X[:, 4] / 900.0   # 0–1 scale
    h2_ppm_normal = (50.0 + irradiance_norm * 20.0
                     + rng.normal(0, 8.0, size=len(X))).clip(20.0, 110.0)

    # Feature matrix: 6 features including H₂ ppm
    features = np.column_stack([
        X[:, 2],      # temperature_c
        X[:, 3],      # humidity_pct
        X[:, 4],      # solar_irradiance_wpm2
        y_pv,         # pv_power_w
        y_th,         # thermal_demand_w
        h2_ppm_normal # h2_ppm (normal operating range)
    ]).astype(np.float32)

    scaler   = StandardScaler()
    features_scaled = scaler.fit_transform(features)

    iso = IsolationForest(n_estimators=200, contamination=0.03, random_state=42)
    iso.fit(features_scaled)

    scores    = iso.score_samples(features_scaled)
    threshold = float(np.percentile(scores, 3))

    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "leak_detector.pkl", "wb") as f:
        pickle.dump({"scaler": scaler, "model": iso, "threshold": threshold,
                     "n_features": 6, "feature_names":
                     ["temperature_c", "humidity_pct", "irradiance",
                      "pv_power_w", "thermal_demand_w", "h2_ppm"]},
                    f)

    print(f"  Trained on {len(features)} samples  threshold={threshold:.4f}")
    print(f"  Saved -> {out_dir}/leak_detector.pkl")


# ── Summary ────────────────────────────────────────────────────────────────

def save_summary(out_dir: Path, dataset_path: str, n_samples: int):
    from datetime import datetime
    summary = {
        "trained_at":     datetime.utcnow().isoformat(),
        "dataset":        dataset_path,
        "samples":        n_samples,
            "models": {
                "pv_nn":           "pv_nn/            (TF SavedModel) or pv_model/ fallback",
                "thermal_lstm":    "thermal_lstm/     (TF SavedModel) or thermal_model/ fallback",
                "rl_agent":        "rl_agent.npz      (Q-table)",
                "leak_detector":   "leak_detector.pkl (IsolationForest)",
            },
    }
    with open(out_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)


# ── Entry point ────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="data/processed/dataset.csv")
    ap.add_argument("--out",     default="models")
    ap.add_argument("--epochs",  type=int, default=300,
                    help="Max training epochs for NN / LSTM (early-stopping applies)")
    ap.add_argument("--episodes", type=int, default=50000,
                    help="Q-learning episodes for RL agent")
    ap.add_argument("--models", default="all",
                    help="Comma-separated subset to train: pv,thermal,rl,leak  (default: all)")
    args = ap.parse_args()

    dataset_path = args.dataset
    if not Path(dataset_path).exists():
        print(f"Dataset not found: {dataset_path}")
        print("Run: python scripts/build_dataset.py")
        sys.exit(1)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    X, y_pv, y_th = load_dataset(dataset_path)

    models_to_train = set(args.models.lower().split(",")) if args.models != "all" else {"pv", "thermal", "rl", "leak"}

    if "pv"      in models_to_train: train_pv_nn(X, y_pv, args.epochs, out_dir)
    if "thermal" in models_to_train: train_thermal_lstm(X, y_th, args.epochs, out_dir)
    if "rl"      in models_to_train: train_rl_agent(X, y_pv, y_th, args.episodes, out_dir)
    if "leak"    in models_to_train: train_leak_detector(X, y_pv, y_th, out_dir)

    save_summary(out_dir, dataset_path, len(X))
    print(f"\nModels saved to {out_dir}/")
    print("Next: python scripts/predict.py --help")


if __name__ == "__main__":
    main()
