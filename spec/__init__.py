"""Read-only loader for spec/constants.json.

Usage:
    from spec import constants
    jose = constants.jose
    print(jose["netPitiGreen"])

Per ADR-002 §8: hard-fail on missing or malformed JSON. Silent fallback to
hardcoded defaults is the exact drift mode this loader was created to kill.

Python stdlib only. Do not add dependencies.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

_PATH = Path(__file__).parent / "constants.json"

if not _PATH.exists():
    raise FileNotFoundError(
        f"spec/constants.json not found at {_PATH}. "
        "ADR-002 mandates hard-fail — no silent fallback."
    )

try:
    _RAW: dict = json.loads(_PATH.read_text(encoding="utf-8"))
except json.JSONDecodeError as exc:
    raise ValueError(
        f"spec/constants.json is malformed: {exc}. "
        "ADR-002 mandates hard-fail on parse errors."
    ) from exc

_META = _RAW.get("_meta") or {}
if not _META.get("version"):
    raise ValueError(
        "spec/constants.json missing _meta.version — schema guard tripped."
    )


# Module-level bindings (ADR-002 §8 reader contract).
# Each is a dict (or list) read straight from the JSON.
constants = SimpleNamespace(
    raw=_RAW,
    meta=_META,
    fha=_RAW["fha"],
    jose=_RAW["jose"],
    topsis_weights=_RAW["topsisWeights"],
    insurance=_RAW["insuranceHeuristic"],
    defaults=_RAW["defaults"],
    presets=_RAW["presets"],
    zip_tiers=_RAW["zipTiers"],
    rehab_categories=_RAW["rehabCategories"],
)

__all__ = ["constants"]
