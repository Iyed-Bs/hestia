"""
Export the trained models to the gateway's runtime format.

Why: the gateway must not need TensorFlow (over 1 GB) or Python pickles
(loading a pickle can execute arbitrary code, so a tampered model file would
be a remote-code-execution hole). Every model is exported to ONNX, every
constant to JSON, and the gateway checks each file's SHA-256 against the
manifest written here before loading it.

Each export is verified: the ONNX model must reproduce the original model's
outputs on 500 random inputs, or the export fails.

Run (in an environment with TensorFlow, tf2onnx and skl2onnx, see
ml/requirements-export.txt):

    python ml/export_onnx.py

Writes backend/src/hestia/ml/models/.
"""

from __future__ import annotations

import hashlib
import json
import pickle  # nosec B403 - reads OUR training artefact, offline, never on the gateway
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "models"
OUT = ROOT.parent / "backend" / "src" / "hestia" / "ml" / "models"
RNG = np.random.default_rng(7)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export_keras_savedmodel(name: str, subdir: str, input_shape: tuple[int, ...]) -> dict:
    import tensorflow as tf
    import tf2onnx
    import onnxruntime as ort

    model = tf.saved_model.load(str(SRC / subdir))
    fn = model.serve if hasattr(model, "serve") else model.signatures["serving_default"]
    spec = (tf.TensorSpec((None, *input_shape), tf.float32, name="x"),)

    # The exported serving function names its input "keras_tensor"; wrap it
    # so the ONNX graph gets a stable input name ("x") and a single output.
    @tf.function(input_signature=spec)
    def wrapped(x):  # type: ignore[no-untyped-def]
        out = fn(x)
        return list(out.values())[0] if isinstance(out, dict) else out

    out_path = OUT / f"{name}.onnx"
    tf2onnx.convert.from_function(wrapped, input_signature=spec, opset=17, output_path=str(out_path))

    # Verify against TensorFlow on normalised random inputs.
    x = RNG.normal(size=(500, *input_shape)).astype(np.float32)
    tf_out = fn(tf.constant(x))
    tf_out = (list(tf_out.values())[0] if hasattr(tf_out, "values") else tf_out).numpy().reshape(-1)
    sess = ort.InferenceSession(str(out_path), providers=["CPUExecutionProvider"])
    ort_out = sess.run(None, {sess.get_inputs()[0].name: x})[0].reshape(-1)
    max_err = float(np.max(np.abs(tf_out - ort_out)))
    assert max_err < 1e-3, f"{name}: ONNX output differs from TensorFlow by {max_err}"
    print(f"  {name}: ONNX matches TensorFlow (max abs error {max_err:.2e})")

    meta = {
        "file": out_path.name,
        "input": sess.get_inputs()[0].name,
        "input_shape": list(input_shape),
        "norm_mean": np.load(SRC / subdir / "norm_mean.npy").astype(float).tolist(),
        "norm_std": np.load(SRC / subdir / "norm_std.npy").astype(float).tolist(),
        "verified_max_abs_error": max_err,
    }
    target_mean = SRC / subdir / "target_mean.npy"
    if target_mean.exists():
        meta["target_mean"] = float(np.load(target_mean)[0])
        meta["target_std"] = float(np.load(SRC / subdir / "target_std.npy")[0])
    return meta


def export_leak_detector() -> dict:
    import onnxruntime as ort
    from skl2onnx import to_onnx
    from sklearn.pipeline import make_pipeline

    with open(SRC / "leak_detector.pkl", "rb") as f:
        det = pickle.load(f)  # nosec B301 - our own artefact, see module docstring
    scaler, iso = det["scaler"], det["model"]
    n = int(det.get("n_features", 5))
    pipe = make_pipeline(scaler, iso)
    x = (RNG.normal(size=(500, n)) * scaler.scale_ + scaler.mean_).astype(np.float32)
    onx = to_onnx(pipe, x[:1], target_opset={"": 17, "ai.onnx.ml": 3})
    out_path = OUT / "leak_anomaly.onnx"
    out_path.write_bytes(onx.SerializeToString())

    # The ONNX graph returns decision_function = score_samples - offset_.
    sess = ort.InferenceSession(str(out_path), providers=["CPUExecutionProvider"])
    outputs = {o.name: o for o in sess.get_outputs()}
    score_name = "scores" if "scores" in outputs else list(outputs)[-1]
    ort_scores = sess.run([score_name], {sess.get_inputs()[0].name: x})[0].reshape(-1)
    sk_scores = iso.decision_function(scaler.transform(x))
    max_err = float(np.max(np.abs(ort_scores - sk_scores)))
    assert max_err < 1e-4, f"leak detector: ONNX differs from scikit-learn by {max_err}"
    print(f"  leak_anomaly: ONNX matches scikit-learn (max abs error {max_err:.2e})")
    return {
        "file": out_path.name,
        "input": sess.get_inputs()[0].name,
        "score_output": score_name,
        "n_features": n,
        "features": ["temp", "humidity", "irradiance", "pv_w", "thermal_w", "h2_ppm"][:n],
        # Original rule: anomaly when score_samples < threshold.
        # decision_function = score_samples - offset_, hence:
        "decision_threshold": float(det["threshold"] - iso.offset_),
        "score_threshold": float(det["threshold"]),
        "verified_max_abs_error": max_err,
    }


def export_rl_policy() -> dict:
    d = np.load(SRC / "rl_agent.npz", allow_pickle=False)
    return {"n_states": int(d["n_states"]), "n_actions": int(d["n_actions"]), "q_table": d["Q"].astype(float).round(6).tolist()}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print("Exporting models to", OUT)
    manifest: dict = {
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "trained": json.loads((SRC / "summary.json").read_text(encoding="utf-8")),
        "models": {
            "pv_power": export_keras_savedmodel("pv_power", "pv_nn", (5,)),
            "thermal_demand": export_keras_savedmodel("thermal_demand", "thermal_lstm", (24, 5)),
            "leak_anomaly": export_leak_detector(),
        },
        "rl_policy": export_rl_policy(),
    }
    for meta in manifest["models"].values():
        meta["sha256"] = sha256(OUT / meta["file"])
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Wrote manifest.json")


if __name__ == "__main__":
    main()
