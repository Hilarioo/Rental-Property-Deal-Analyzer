#!/usr/bin/env node
// Sprint 7B-5 verdict parity canary.
//
// Runs 5 fixture scenarios through BOTH the JS verdict (ported verbatim from
// index.html ~line 2336 `computeJoseVerdict`) and the Python verdict
// (batch.verdict.compute_jose_verdict) and compares verdict + reason count.
// Exit 0 on parity, 1 on divergence.
//
// This is NOT a unit test. It's a one-shot dev script run before a commit to
// confirm we didn't drift between the two implementations.
//
// Usage: node scripts/verdict_parity_check.mjs
// Requires: venv/bin/python resolvable from repo root.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..');

// -------------------------------------------------------------------------
// Load spec thresholds (single source of truth per ADR-002).
// -------------------------------------------------------------------------
const SPEC = JSON.parse(
  readFileSync(resolve(REPO_ROOT, 'spec/constants.json'), 'utf-8'),
);
const JOSE_THRESHOLDS = SPEC.jose;

// -------------------------------------------------------------------------
// JS verdict — PORTED VERBATIM from index.html ~line 2336.
// Keep in lockstep with that file; any change to one must mirror to the other.
// -------------------------------------------------------------------------
function computeJoseVerdict(ctx) {
  var c = ctx || {};
  var T = JOSE_THRESHOLDS;
  var reasons = [];
  var redReasons = [];
  var yellowReasons = [];
  var fmt$ = function (n) { return '$' + Math.round(n).toLocaleString(); };

  if (c.isExcludedByZipTier) {
    redReasons.push('ZIP ' + (c.zip || '') + ' on excluded list');
  }
  if (c.hasFlatRoof) {
    redReasons.push('Flat roof / commercial conversion — FHA disqualifier');
  }
  if (c.hasUnpermittedAdu) {
    redReasons.push('Unpermitted ADU / garage conversion — FHA disqualifier');
  }
  if (c.isPre1978WithGalvanized) {
    redReasons.push('Pre-1978 w/ galvanized + knob-and-tube — FHA disqualifier');
  }
  if (c.propertyType === 'sfh' && (c.units || 1) <= 1) {
    redReasons.push('SFR without legal ADU — no 75% rental offset possible');
  }
  var unitsUnknownFail = !!c.hardFailUnitsUnknown;
  if (c.qualifyingIncome > 0 && c.piti > 0) {
    var dtiPct = (c.piti / c.qualifyingIncome) * 100;
    if (dtiPct > T.maxDtiPct) {
      redReasons.push(
        'PITI ' + fmt$(c.piti) + ' is ' + Math.round(dtiPct) + '% of qualifying income — exceeds ' + T.maxDtiPct + '% DTI gate',
      );
    }
  }

  var units = c.units || 1;
  var priceCeiling = (units >= 3) ? T.priceCeilingTriplex : T.priceCeilingDuplex;
  if (c.price > priceCeiling) {
    var over = c.price - priceCeiling;
    var overPct = over / priceCeiling;
    var msg = 'Price ' + fmt$(c.price) + ' exceeds ' + (units >= 3 ? 'triplex+' : 'duplex') + ' ceiling ' + fmt$(priceCeiling) + ' by ' + fmt$(over);
    if (overPct > 0.10) redReasons.push(msg); else yellowReasons.push(msg);
  }

  if (c.netPiti > T.netPitiGreen) {
    var netOver = c.netPiti - T.netPitiGreen;
    var netOverPct = netOver / T.netPitiGreen;
    var netMsg = 'Net PITI ' + fmt$(c.netPiti) + ' exceeds ' + fmt$(T.netPitiGreen) + ' by ' + fmt$(netOver);
    if (c.netPiti > T.netPitiRed || netOverPct > 0.10) redReasons.push(netMsg);
    else yellowReasons.push(netMsg);
  }

  if (c.cashToClose > T.cashCloseGreen) {
    var cashOver = c.cashToClose - T.cashCloseGreen;
    var cashOverPct = cashOver / T.cashCloseGreen;
    var cashMsg = 'Cash to close ' + fmt$(c.cashToClose) + ' exceeds ' + fmt$(T.cashCloseGreen) + ' by ' + fmt$(cashOver);
    if (c.cashToClose > T.cashCloseRed || cashOverPct > 0.10) redReasons.push(cashMsg);
    else yellowReasons.push(cashMsg);
  }

  if (c.effectiveRehab > T.rehabGreen) {
    var rehabOver = c.effectiveRehab - T.rehabGreen;
    var rehabOverPct = rehabOver / T.rehabGreen;
    var rehabMsg = 'Rehab ' + fmt$(c.effectiveRehab) + ' exceeds ' + fmt$(T.rehabGreen) + ' by ' + fmt$(rehabOver);
    if (c.effectiveRehab > T.rehabRed || rehabOverPct > 0.10) redReasons.push(rehabMsg);
    else yellowReasons.push(rehabMsg);
  }

  if (c.zipTier === 'outside') {
    redReasons.push('ZIP outside all target market tiers');
  } else if (c.zipTier === 'tier3') {
    yellowReasons.push('Tier 3 ZIP — Richmond motivated sellers, underwrite conservatively');
  }

  if (typeof c.roofAgeYears === 'number' && c.roofAgeYears > T.roofAgeYellow) {
    yellowReasons.push('Roof ' + c.roofAgeYears + ' yrs old — FHA appraisal risk');
  }

  if (unitsUnknownFail) {
    redReasons.push('Unit count not detected — re-scrape or enter manually');
  }

  var verdict;
  if (redReasons.length > 0) {
    verdict = 'red';
    reasons = redReasons.concat(yellowReasons);
  } else if (yellowReasons.length > 0) {
    verdict = 'yellow';
    reasons = yellowReasons;
  } else {
    verdict = 'green';
    reasons = [
      'Net PITI ' + fmt$(c.netPiti || 0) + ' under ' + fmt$(T.netPitiGreen) + ' cap',
      'Cash to close ' + fmt$(c.cashToClose || 0) + ' under ' + fmt$(T.cashCloseGreen) + ' cap',
    ];
    if (c.zipTier === 'tier1' || c.zipTier === 'tier2') {
      reasons.push((c.zipTier === 'tier1' ? 'Tier 1' : 'Tier 2') + ' priority ZIP' + (c.zip ? ' ' + c.zip : ''));
    }
  }
  return { verdict: verdict, reasons: reasons.slice(0, 3) };
}

// -------------------------------------------------------------------------
// Fixtures — 5 canonical scenarios.
// -------------------------------------------------------------------------
const FIXTURES = [
  {
    name: 'GREEN happy-path',
    ctx: {
      price: 500000,
      effectiveRehab: 30000,
      cashToClose: 40000,
      netPiti: 2400,
      piti: 2400,
      qualifyingIncome: 5714,  // 2400 / 0.42 => ~5714; DTI = 42%
      zip: '94590',
      zipTier: 'tier1',
      isExcludedByZipTier: false,
      units: 2,
      propertyType: 'multi',
      roofAgeYears: 8,
      hasFlatRoof: false,
      hasUnpermittedAdu: false,
      isPre1978WithGalvanized: false,
      hardFailUnitsUnknown: false,
    },
  },
  {
    name: 'YELLOW ≤10% net PITI miss',
    ctx: {
      price: 500000,
      effectiveRehab: 30000,
      cashToClose: 40000,
      netPiti: 2600,  // +4% over 2500 green cap
      piti: 2600,
      qualifyingIncome: 8000,
      zip: '94590',
      zipTier: 'tier1',
      isExcludedByZipTier: false,
      units: 2,
      propertyType: 'multi',
      roofAgeYears: 8,
      hasFlatRoof: false,
      hasUnpermittedAdu: false,
      isPre1978WithGalvanized: false,
      hardFailUnitsUnknown: false,
    },
  },
  {
    name: 'RED by DTI (60%)',
    ctx: {
      price: 500000,
      effectiveRehab: 30000,
      cashToClose: 40000,
      netPiti: 2400,
      piti: 3000,
      qualifyingIncome: 5000,  // DTI 60% > 55% ceiling
      zip: '94590',
      zipTier: 'tier1',
      isExcludedByZipTier: false,
      units: 2,
      propertyType: 'multi',
      roofAgeYears: 8,
      hasFlatRoof: false,
      hasUnpermittedAdu: false,
      isPre1978WithGalvanized: false,
      hardFailUnitsUnknown: false,
    },
  },
  {
    name: 'RED by excluded zip',
    ctx: {
      price: 500000,
      effectiveRehab: 30000,
      cashToClose: 40000,
      netPiti: 2400,
      piti: 2400,
      qualifyingIncome: 5714,
      zip: '94803',
      zipTier: 'outside',  // Python maps 'excluded' zip tier to 'outside' before calling verdict
      isExcludedByZipTier: true,
      units: 2,
      propertyType: 'multi',
      roofAgeYears: 8,
      hasFlatRoof: false,
      hasUnpermittedAdu: false,
      isPre1978WithGalvanized: false,
      hardFailUnitsUnknown: false,
    },
  },
  {
    name: 'RED by units-unknown (7B-1, multi)',
    ctx: {
      price: 500000,
      effectiveRehab: 30000,
      cashToClose: 40000,
      netPiti: 2400,
      piti: 2400,
      qualifyingIncome: 5714,
      zip: '94590',
      zipTier: 'tier1',
      isExcludedByZipTier: false,
      units: 2,
      propertyType: 'multi',
      roofAgeYears: 8,
      hasFlatRoof: false,
      hasUnpermittedAdu: false,
      isPre1978WithGalvanized: false,
      hardFailUnitsUnknown: true,
    },
  },
  {
    // Sprint 7B review follow-up: Python's units_unknown trigger is
    // propertyType-agnostic. A mis-classified SFR that fails unit detection
    // must also hard-fail — no silent duplex math. This fixture pins the
    // propertyType='sfh' + hardFailUnitsUnknown=true path so JS + Python
    // stay aligned on the broader signal.
    name: 'RED by units-unknown + SFR (broad trigger)',
    ctx: {
      price: 450000,
      effectiveRehab: 25000,
      cashToClose: 38000,
      netPiti: 2300,
      piti: 2300,
      qualifyingIncome: 5400,
      zip: '94590',
      zipTier: 'tier1',
      isExcludedByZipTier: false,
      units: 1,
      propertyType: 'sfh',
      roofAgeYears: 6,
      hasFlatRoof: false,
      hasUnpermittedAdu: false,
      isPre1978WithGalvanized: false,
      hardFailUnitsUnknown: true,
    },
  },
];

// -------------------------------------------------------------------------
// Python verdict invocation — one subprocess call with all 5 fixtures.
// -------------------------------------------------------------------------
function runPythonVerdict(fixtures) {
  const pyPath = resolve(REPO_ROOT, 'venv/bin/python');
  const script = `
import json, sys
sys.path.insert(0, ${JSON.stringify(REPO_ROOT)})
from batch.verdict import compute_jose_verdict
payload = json.loads(sys.stdin.read())
out = []
for fx in payload:
    ctx = fx["ctx"]
    # Python expects snake_case for a few keys? No — compute_jose_verdict reads
    # camelCase keys directly (see batch/verdict.py). Pass as-is.
    res = compute_jose_verdict(ctx)
    out.append({"name": fx["name"], "verdict": res["verdict"], "reasons": res["reasons"]})
print(json.dumps(out))
`;
  const proc = spawnSync(pyPath, ['-c', script], {
    input: JSON.stringify(fixtures),
    encoding: 'utf-8',
    cwd: REPO_ROOT,
  });
  if (proc.status !== 0) {
    console.error('Python verdict invocation failed:');
    console.error(proc.stderr);
    process.exit(2);
  }
  return JSON.parse(proc.stdout);
}

// -------------------------------------------------------------------------
// Main.
// -------------------------------------------------------------------------
const jsResults = FIXTURES.map((fx) => ({
  name: fx.name,
  ...computeJoseVerdict(fx.ctx),
}));
const pyResults = runPythonVerdict(FIXTURES);

let pass = 0;
const divergences = [];

for (let i = 0; i < FIXTURES.length; i++) {
  const js = jsResults[i];
  const py = pyResults[i];
  const verdictMatch = js.verdict === py.verdict;
  const jsReasons = (js.reasons || []).map(String);
  const pyReasons = (py.reasons || []).map(String);
  // Upgraded per Code Review: compare reason TEXT, not just count.
  // Order matters — both sides must surface reasons in the same priority.
  const reasonsMatch =
    jsReasons.length === pyReasons.length &&
    jsReasons.every((r, idx) => r === pyReasons[idx]);
  if (verdictMatch && reasonsMatch) {
    pass++;
    console.log(`  ok  [${FIXTURES[i].name}] → ${js.verdict} (${jsReasons.length} reasons)`);
  } else {
    divergences.push({ fixture: FIXTURES[i].name, js, py });
    console.log(`  FAIL [${FIXTURES[i].name}]`);
    console.log(`       JS verdict: ${js.verdict}   PY verdict: ${py.verdict}`);
    console.log(`       JS reasons: ${JSON.stringify(jsReasons)}`);
    console.log(`       PY reasons: ${JSON.stringify(pyReasons)}`);
  }
}

console.log(`\nparity: ${pass}/${FIXTURES.length}`);
if (divergences.length > 0) {
  process.exit(1);
}
process.exit(0);
