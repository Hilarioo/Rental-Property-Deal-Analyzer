#!/usr/bin/env node
// Sprint 9-1 verdict parity harness (expanded from Sprint 7B-5 canary).
//
// Loads fixtures from tests/fixtures/verdict_parity.json and runs every
// one through both the JS verdict (ported verbatim from index.html
// ~line 2336 `computeJoseVerdict`) and the Python verdict
// (batch.verdict.compute_jose_verdict). Compares verdict + reason TEXT
// in order. Exit 0 on parity, 1 on any divergence.
//
// Wired into `make test` so verdict drift fails CI, not prod.
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
// Sprint 10A §10-2: jose/defaults moved to spec/profile.local.json (private,
// gitignored). Tests run locally where that file is always present, so we
// hard-fail if it's missing — parity coverage requires the real thresholds.
// -------------------------------------------------------------------------
const SPEC = JSON.parse(
  readFileSync(resolve(REPO_ROOT, 'spec/constants.json'), 'utf-8'),
);
const PROFILE = JSON.parse(
  readFileSync(resolve(REPO_ROOT, 'spec/profile.local.json'), 'utf-8'),
);
const JOSE_THRESHOLDS = PROFILE.jose;

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
    // Sprint 12 hotfix mirror — keep JS / Python / parity-harness copy in lockstep.
    var ptRaw = (c.propertyTypeRaw || '').toLowerCase();
    var usrc = c.unitsSource || '';
    if (ptRaw.indexOf('condo') !== -1) {
      redReasons.push('Single condo unit — no other units to rent, 75% FHA offset unavailable');
    } else if (ptRaw.indexOf('townhouse') !== -1 || ptRaw.indexOf('townhome') !== -1) {
      redReasons.push('Single townhouse unit — no other units to rent, 75% FHA offset unavailable');
    } else if (usrc === 'address_suffix' || usrc === 'address_hash_suffix' || usrc === 'url_slug') {
      redReasons.push('Address suffix (APT/UNIT/#) indicates one unit of a larger building — no 75% FHA rental offset possible');
    } else {
      redReasons.push('SFR without legal ADU — no 75% rental offset possible');
    }
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

  // Sprint 12-1: layered Yellow. Mirrors `_classifyOverage` in index.html +
  // batch/verdict.py. Keep these three call sites in lockstep.
  function classifyOverage(value, green, yellow, red) {
    if (value <= green) return 'green';
    if (value > red) return 'red';
    var tenPctOk = (value - green) / green <= 0.10;
    var explicitYellowOk = (yellow !== null && yellow !== undefined) && value <= yellow;
    return (explicitYellowOk || tenPctOk) ? 'yellow' : 'red';
  }

  // Sprint 12-2: geospatial parity — ported from index.html _geospatialFail.
  // Reads PROFILE.location + SPEC.zipTiers.conditionalCities directly (no DOM).
  function haversineMiles(lat1, lng1, lat2, lng2) {
    var R = 3958.8;
    var toRad = function(d) { return d * Math.PI / 180; };
    var phi1 = toRad(lat1), phi2 = toRad(lat2);
    var dPhi = toRad(lat2 - lat1), dLam = toRad(lng2 - lng1);
    var a = Math.sin(dPhi / 2) * Math.sin(dPhi / 2)
          + Math.cos(phi1) * Math.cos(phi2) * Math.sin(dLam / 2) * Math.sin(dLam / 2);
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(a)));
  }
  function geospatialFail(c) {
    if (typeof c.lat !== 'number' || typeof c.lng !== 'number') return null;
    var loc = (PROFILE && PROFILE.location) || {};
    var home = loc.homeBase || {};
    if (typeof home.lat !== 'number' || typeof home.lng !== 'number') return null;
    if (home.lat === 0 && home.lng === 0) return null;
    var miles = haversineMiles(c.lat, c.lng, home.lat, home.lng);
    if (typeof loc.maxMilesHard === 'number' && miles > loc.maxMilesHard) {
      return 'Outside commute radius — ' + Math.round(miles) + ' mi from home base exceeds ' + loc.maxMilesHard + ' mi hard cap';
    }
    var cond = (SPEC.zipTiers && SPEC.zipTiers.conditionalCities) || {};
    var addrLower = (c.address || '').toLowerCase();
    for (var city in cond) {
      if (!Object.prototype.hasOwnProperty.call(cond, city)) continue;
      var rule = cond[city];
      if (!rule || rule.rule !== 'maxMilesFromHomeBase') continue;
      if (addrLower.indexOf(city.toLowerCase()) === -1) continue;
      if (typeof rule.threshold !== 'number') continue;
      if (miles > rule.threshold) {
        return city + ' conditional-market rule: ' + Math.round(miles) + ' mi from home base exceeds ' + rule.threshold + ' mi threshold';
      }
    }
    return null;
  }
  var geoMsg = geospatialFail(c);
  if (geoMsg) redReasons.push(geoMsg);

  if (c.netPiti > T.netPitiGreen) {
    var netMsg = 'Net PITI ' + fmt$(c.netPiti) + ' exceeds ' + fmt$(T.netPitiGreen) + ' by ' + fmt$(c.netPiti - T.netPitiGreen);
    if (classifyOverage(c.netPiti, T.netPitiGreen, T.netPitiYellow, T.netPitiRed) === 'red') redReasons.push(netMsg);
    else yellowReasons.push(netMsg);
  }

  if (c.cashToClose > T.cashCloseGreen) {
    var cashMsg = 'Cash to close ' + fmt$(c.cashToClose) + ' exceeds ' + fmt$(T.cashCloseGreen) + ' by ' + fmt$(c.cashToClose - T.cashCloseGreen);
    if (classifyOverage(c.cashToClose, T.cashCloseGreen, T.cashCloseYellow, T.cashCloseRed) === 'red') redReasons.push(cashMsg);
    else yellowReasons.push(cashMsg);
  }

  if (c.effectiveRehab > T.rehabGreen) {
    var rehabMsg = 'Rehab ' + fmt$(c.effectiveRehab) + ' exceeds ' + fmt$(T.rehabGreen) + ' by ' + fmt$(c.effectiveRehab - T.rehabGreen);
    var rehabCls = classifyOverage(c.effectiveRehab, T.rehabGreen, T.rehabYellow, T.rehabRed);
    if (rehabCls === 'red') redReasons.push(rehabMsg);
    else yellowReasons.push(rehabMsg);
    // Sprint 12-6 parity mirror: 203(k) stretch hint when cash-funded red-fails.
    if (rehabCls === 'red' && c.stretchScenario && c.stretchScenario.viable) {
      var sc = c.stretchScenario;
      var sharePct = Math.round((sc.self_perform_share || 0) * 100);
      yellowReasons.push(
        '203(k) stretch viable: PITI ' + fmt$(sc.piti || 0) +
        ', cash-to-close ' + fmt$(sc.cash_to_close || 0) +
        ' (self-perform ' + sharePct + '%)'
      );
    }
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
    redReasons.push('Unit count ambiguous — cannot confirm 2-4 unit eligibility; set units manually in the single-property wizard');
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
// Load fixtures (shared with tests/test_verdict_parity.py)
// -------------------------------------------------------------------------
const FIXTURE_PATH = resolve(REPO_ROOT, 'tests/fixtures/verdict_parity.json');
const FIXTURES = JSON.parse(readFileSync(FIXTURE_PATH, 'utf-8'));

// -------------------------------------------------------------------------
// Python verdict invocation — one subprocess call with all fixtures.
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
  const fx = FIXTURES[i];
  const js = jsResults[i];
  const py = pyResults[i];
  const verdictMatch = js.verdict === py.verdict;
  const jsReasons = (js.reasons || []).map(String);
  const pyReasons = (py.reasons || []).map(String);
  const reasonsMatch =
    jsReasons.length === pyReasons.length &&
    jsReasons.every((r, idx) => r === pyReasons[idx]);
  // When `expected_verdict` is present in the fixture it also has to match
  // both sides — catches cases where JS+Python silently agree on the wrong
  // answer.
  const expectedMatch = fx.expected_verdict
    ? js.verdict === fx.expected_verdict
    : true;
  if (verdictMatch && reasonsMatch && expectedMatch) {
    pass++;
    console.log(`  ok  [${fx.name}] → ${js.verdict} (${jsReasons.length} reasons)`);
  } else {
    divergences.push({ fixture: fx.name, js, py, expected: fx.expected_verdict });
    console.log(`  FAIL [${fx.name}]`);
    if (!expectedMatch) {
      console.log(`       EXPECTED verdict: ${fx.expected_verdict}  got JS=${js.verdict} PY=${py.verdict}`);
    }
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
