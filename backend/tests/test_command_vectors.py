"""Run the shared signed-command vectors (spec/command_vectors.json).

The firmware's verifier (firmware/test/test_core) runs the same file: the
gateway and the controller must agree on every signature, replay and clock
decision, byte for byte.
"""

from __future__ import annotations

import json
from pathlib import Path

from hestia.runtime.envelope import EnvelopeSigner, EnvelopeVerifier

SPEC = json.loads(
    (Path(__file__).resolve().parents[2] / "spec" / "command_vectors.json").read_text(encoding="utf-8")
)
VERDICT = {
    "bad signature": "bad_signature",
    "replayed sequence number": "replayed",
    "timestamp outside the allowed window": "stale",
}


def test_every_case_gets_the_expected_verdict() -> None:
    verifier = EnvelopeVerifier(bytes.fromhex(SPEC["key_hex"]))
    for case in SPEC["cases"]:
        try:
            verifier.verify(case["envelope"], now=SPEC["now"])
            verdict = "ok"
        except ValueError as exc:
            verdict = VERDICT[str(exc)]
        assert verdict == case["expect"], case["name"]


def test_the_signer_still_produces_the_published_envelope() -> None:
    """If the envelope format changes, the vectors (and the firmware) must change with it."""
    first = SPEC["cases"][0]["envelope"]
    signer = EnvelopeSigner(bytes.fromhex(SPEC["key_hex"]))
    again = signer.sign(json.loads(first["cmd"]), now=SPEC["now"])
    assert again == first
