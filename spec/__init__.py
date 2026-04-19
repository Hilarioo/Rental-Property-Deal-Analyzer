"""Read-only loader for spec/constants.json + spec/profile.local.json.

Usage:
    from spec import constants
    jose = constants.jose
    print(jose["netPitiGreen"])

Per ADR-002 §8: hard-fail on missing or malformed PUBLIC JSON (constants.json).
Silent fallback to hardcoded defaults is the exact drift mode this loader was
created to kill.

Sprint 10A §10-2: profile.local.json is PRIVATE and gitignored. If it's
missing (fresh clone, CI, container without a mounted secret) we log a
warning and fall back to redacted empty structures so the tool still boots.
Private fields ("defaults", "jose") are the only ones that can be absent.

Python stdlib only. Do not add dependencies.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

_log = logging.getLogger(__name__)

_PATH_PUBLIC = Path(__file__).parent / "constants.json"
_PATH_PRIVATE = Path(__file__).parent / "profile.local.json"

# --- Public: hard-fail per ADR-002 §8 ---
if not _PATH_PUBLIC.exists():
    raise FileNotFoundError(
        f"spec/constants.json not found at {_PATH_PUBLIC}. "
        "ADR-002 mandates hard-fail — no silent fallback."
    )

try:
    _RAW: dict = json.loads(_PATH_PUBLIC.read_text(encoding="utf-8"))
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

# --- Private: optional, log-and-fallback per Sprint 10A §10-2 ---
_PROFILE_RAW: dict = {}
if _PATH_PRIVATE.exists():
    try:
        _PROFILE_RAW = json.loads(_PATH_PRIVATE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        # Don't hard-fail — downstream code sees empty defaults/jose and the
        # UI banner surfaces the issue. This keeps boot resilient if someone
        # corrupts their local file.
        _log.warning(
            "spec/profile.local.json is malformed (%s); using redacted defaults.",
            exc,
        )
        _PROFILE_RAW = {}
else:
    _log.warning(
        "spec/profile.local.json not found — using redacted profile. "
        "Copy spec/profile.example.json to spec/profile.local.json and fill it in."
    )


# Module-level bindings (ADR-002 §8 reader contract).
# `defaults` and `jose` come from the private file; the rest are public.
constants = SimpleNamespace(
    raw=_RAW,
    meta=_META,
    fha=_RAW["fha"],
    jose=_PROFILE_RAW.get("jose") or {},
    topsis_weights=_RAW["topsisWeights"],
    insurance=_RAW["insuranceHeuristic"],
    defaults=_PROFILE_RAW.get("defaults") or {},
    presets=_RAW["presets"],
    zip_tiers=_RAW["zipTiers"],
    rehab_categories=_RAW["rehabCategories"],
    profile_raw=_PROFILE_RAW,
    profile_available=bool(_PROFILE_RAW),
)

__all__ = ["constants"]
