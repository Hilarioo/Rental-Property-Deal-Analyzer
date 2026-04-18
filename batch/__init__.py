"""Batch analysis & TOPSIS ranking package.

Implements Commit 1 of the spec in handoff/BATCH_DESIGN.md (sync mode only).
Submodules:
    db         — SQLite helpers (url hash, retry wrapper, schema access)
    verdict    — Python port of index.html's computeJoseVerdict (Sprint 4)
    enrichment — FEMA NFHL, Cal Fire FHSZ, OSM Overpass, Census geocoder
    llm        — consolidated structured-extraction LLM call with Vision
    insurance  — deterministic heuristic layered with the LLM uplift
    ranking    — 13-criterion matrix, Pareto filter, TOPSIS scoring
    pipeline   — end-to-end per-URL orchestration + batch writeback
"""
