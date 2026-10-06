# Machine learning

Training and export of the models the gateway's ML advisor uses. **None of
this is needed to run Hestia**: the exported models are already in
`backend/src/hestia/ml/models/`, with their SHA-256 fingerprints.

## The models, and what they are for

| Model | Kind | Input → output | Used for |
|---|---|---|---|
| `pv_power` | dense network | weather now → PV output (W) | the energy manager's view of the next minutes |
| `thermal_demand` | LSTM | last 24 hourly samples → next-hour heat demand (W) | when to keep hydrogen for heating |
| `rl_policy` | Q-table (Q-learning) | PV, demand, hour → production level 0–4 | advice: may delay a start, never more |
| `leak_anomaly` | isolation forest | conditions + H₂ reading → "unusual" score | flags unusual conditions. **Not a leak detector** (ADR-004) |

All four only *advise*. If they fail to load, or fail their fingerprint
check, the gateway runs on its plain rules (docs/ARCHITECTURE.md).

**What the training data is.** A year of real hourly weather for Tunis
(Open-Meteo archive). The PV and heat-demand *targets* are computed from that
weather with physical formulas (`scripts/build_dataset.py`), so these two
models are fast learned surrogates of known physics, not models of a measured
plant. On a real site, retrain them on the site's metered production and
consumption: the pipeline below is built for that.

## Pipeline

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
python scripts/download_data.py --days 365            # Open-Meteo, no API key
python scripts/build_dataset.py                        # → data/processed/dataset.csv
python scripts/train.py                                # → models/
```

## Export for the gateway

The gateway never loads TensorFlow or Python pickles: TensorFlow is over a
gigabyte, and unpickling a file can run arbitrary code. `export_onnx.py`
converts every model to ONNX (constants to JSON), checks that each ONNX model
reproduces the original's outputs on 500 random inputs, and writes the
manifest with each file's SHA-256:

```bash
python -m venv .venv-export && .venv-export/bin/pip install -r requirements-export.txt
python export_onnx.py                                  # → backend/src/hestia/ml/models/
```

Then check them from the gateway: `hestia check-models`.
