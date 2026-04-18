// BASELINE JS unit tests — pre-Jose-fix behavior locked so Sprint 1+
// changes can't silently regress. Run with: node --test tests/calc.test.mjs
//
// When a sprint intentionally changes a baseline formula (e.g. Sprint 1
// adds FHA MIP to PITI), update the expected value here with a
// `// Sprint N:` comment and a link to the acceptance criterion.

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  computeMonthlyPI,
  computePITI,
  computeCashFlow,
  computeNOI,
  computeCapRate,
  computeGRM,
  computeDSCR,
  computeCoC,
  computeAmortization,
  computeOpex,
  onePercentRule,
  seventyPercentRule,
  fhaAnnualMipRate,
  computeFhaLoanAmount,
  computeFhaPITI,
  computeQualifyingIncome,
  maxPitiAtDti,
  FHA_MIP_UPFRONT_RATE,
  FHA_MIP_ANNUAL_STANDARD,
  FHA_MIP_ANNUAL_HIGH,
  FHA_BASELINE_LOAN_LIMIT,
} from '../calc.js';

// --- computeMonthlyPI ---

test('computeMonthlyPI: $482,500 at 6.5% / 30yr ≈ $3,050', () => {
  // FHA base loan for $500K purchase with 3.5% down (pre-MIP).
  const pi = computeMonthlyPI(482500, 6.5, 30);
  assert.ok(Math.abs(pi - 3050.12) < 1, `expected ~$3,050, got ${pi.toFixed(2)}`);
});

test('computeMonthlyPI: zero interest amortizes linearly', () => {
  const pi = computeMonthlyPI(120000, 0, 30);
  assert.equal(pi, 120000 / 360);
});

test('computeMonthlyPI: zero loan returns zero', () => {
  assert.equal(computeMonthlyPI(0, 6.5, 30), 0);
});

test('computeMonthlyPI: zero term returns zero (guard)', () => {
  assert.equal(computeMonthlyPI(100000, 6.5, 0), 0);
});

// --- computePITI (BASELINE, no MIP) ---

test('computePITI: $500K/3.5% down/6.5% BASELINE (no MIP) ≈ $3,779', () => {
  // Sprint 1: this will become ~$4,004 once FHA MIP is added.
  const loanAmount = 500000 * (1 - 0.035);
  const result = computePITI({
    loanAmount,
    annualRatePct: 6.5,
    termYears: 30,
    annualTaxes: 6250, // 1.25% of $500K
    annualInsurance: 1800,
  });
  assert.ok(Math.abs(result.pi - 3050.12) < 1, `pi ≈ $3,050, got ${result.pi.toFixed(2)}`);
  assert.ok(Math.abs(result.taxes - 520.83) < 0.5);
  assert.ok(Math.abs(result.insurance - 150) < 0.5);
  // BASELINE total — pre-MIP.
  assert.ok(Math.abs(result.piti - 3721) < 5, `baseline PITI (no MIP) ≈ $3,721, got ${result.piti.toFixed(2)}`);
});

test('computePITI: shape — has pi, taxes, insurance, piti keys', () => {
  const r = computePITI({ loanAmount: 100000, annualRatePct: 5, termYears: 30, annualTaxes: 1200, annualInsurance: 600 });
  assert.ok('pi' in r && 'taxes' in r && 'insurance' in r && 'piti' in r);
  // Sum invariant: piti = pi + taxes + insurance
  assert.ok(Math.abs(r.piti - (r.pi + r.taxes + r.insurance)) < 1e-6);
});

// --- computeCashFlow ---

test('computeCashFlow: monthly and annual are consistent', () => {
  const result = computeCashFlow({ totalMonthlyIncome: 4200, totalOpex: 1800, monthlyPI: 1500 });
  assert.equal(result.monthlyCF, 900);
  assert.equal(result.annualCF, 10800);
});

test('computeCashFlow: negative cash flow produces negative values', () => {
  const result = computeCashFlow({ totalMonthlyIncome: 2000, totalOpex: 1500, monthlyPI: 1000 });
  assert.equal(result.monthlyCF, -500);
  assert.equal(result.annualCF, -6000);
});

// --- computeNOI ---

test('computeNOI: $4,000/mo income, $1,500/mo opex → $30,000/yr', () => {
  assert.equal(computeNOI({ totalMonthlyIncome: 4000, totalOpex: 1500 }), 30000);
});

// --- computeCapRate ---

test('computeCapRate: $30,000 NOI on $500K = 6%', () => {
  assert.equal(computeCapRate(30000, 500000), 6);
});

test('computeCapRate: zero price returns zero (no crash)', () => {
  assert.equal(computeCapRate(30000, 0), 0);
});

// --- computeGRM ---

test('computeGRM: $500K price / $48K annual rent = 10.42', () => {
  assert.ok(Math.abs(computeGRM(500000, 48000) - 10.4167) < 0.001);
});

test('computeGRM: zero rent returns zero', () => {
  assert.equal(computeGRM(500000, 0), 0);
});

// --- computeDSCR (BASELINE: P&I denominator, not PITI) ---

test('computeDSCR: BASELINE uses P&I only, not PITI', () => {
  // $30K NOI, $20K annual P&I → DSCR = 1.5.
  // Sprint later may change this to use annualized PITI instead.
  assert.equal(computeDSCR(30000, 20000), 1.5);
});

test('computeDSCR: zero debt service returns null', () => {
  assert.equal(computeDSCR(30000, 0), null);
});

// --- computeCoC ---

test('computeCoC: $12K annual CF on $100K invested = 12%', () => {
  assert.equal(computeCoC(12000, 100000), 12);
});

test('computeCoC: zero cash invested returns zero', () => {
  assert.equal(computeCoC(12000, 0), 0);
});

// --- computeAmortization ---

test('computeAmortization: $100K at 6% / 30yr has 360 months and ends at zero', () => {
  const schedule = computeAmortization({ loanAmount: 100000, annualRatePct: 6, termYears: 30 });
  assert.equal(schedule.length, 360);
  assert.ok(schedule[359].balance < 0.01, `final balance should be ~0, got ${schedule[359].balance}`);
});

test('computeAmortization: total principal equals original loan amount', () => {
  const schedule = computeAmortization({ loanAmount: 100000, annualRatePct: 6, termYears: 30 });
  const totalPrincipal = schedule.reduce((sum, row) => sum + row.principal, 0);
  assert.ok(Math.abs(totalPrincipal - 100000) < 0.01, `total principal ≈ $100,000, got ${totalPrincipal.toFixed(2)}`);
});

// --- computeOpex ---

test('computeOpex: percentages apply to totalMonthlyIncome, not gross rent', () => {
  const r = computeOpex({
    totalMonthlyIncome: 4000,
    annualTaxes: 6000,
    annualInsurance: 1200,
    maintenancePct: 5,
    vacancyPct: 5,
    capexPct: 5,
    managementPct: 10, // 10% of $4000 = $400
  });
  // Sum of pct-based lines: (5+5+5+10)% of $4000 = $1000
  assert.equal(r.maint + r.vacancy + r.capex + r.management, 1000);
  // Monthly taxes + insurance
  assert.equal(r.monthlyTaxes, 500);
  assert.equal(r.monthlyInsurance, 100);
  // Total
  assert.equal(r.total, 1600);
});

// --- onePercentRule ---

test('onePercentRule: $5K/mo rent on $500K passes', () => {
  const r = onePercentRule(5000, 500000);
  assert.equal(r.pass, true);
  assert.equal(r.pct, 1);
});

test('onePercentRule: $4K/mo rent on $500K fails', () => {
  const r = onePercentRule(4000, 500000);
  assert.equal(r.pass, false);
  assert.equal(r.pct, 0.8);
});

// --- seventyPercentRule ---

test('seventyPercentRule: $350K purchase + $50K rehab on $600K ARV passes', () => {
  const r = seventyPercentRule(350000, 50000, 600000);
  assert.equal(r.pass, true);
});

test('seventyPercentRule: $500K purchase + $100K rehab on $800K ARV fails', () => {
  const r = seventyPercentRule(500000, 100000, 800000);
  assert.equal(r.pass, false);
});

test('seventyPercentRule: zero ARV returns fail and 0 pct', () => {
  const r = seventyPercentRule(500000, 50000, 0);
  assert.equal(r.pass, false);
  assert.equal(r.pct, 0);
});

// --- Sprint 1: FHA MIP ---
// New-function tests. The BASELINE computePITI tests above remain correct
// for the non-FHA path and are NOT modified in Sprint 1.

test('Sprint 1: FHA constants match HUD 2025 (0.55% / 0.75% / 1.75%)', () => {
  assert.equal(FHA_MIP_UPFRONT_RATE, 0.0175);
  assert.equal(FHA_MIP_ANNUAL_STANDARD, 0.0055);
  assert.equal(FHA_MIP_ANNUAL_HIGH, 0.0075);
  assert.equal(FHA_BASELINE_LOAN_LIMIT, 726200);
});

test('Sprint 1: fhaAnnualMipRate returns 0.55% for $482,500 base loan', () => {
  assert.equal(fhaAnnualMipRate(482500), 0.0055);
});

test('Sprint 1: fhaAnnualMipRate returns 0.75% for $800,000 base loan', () => {
  assert.equal(fhaAnnualMipRate(800000), 0.0075);
});

test('Sprint 1: fhaAnnualMipRate boundary at $726,200 is standard', () => {
  // At exactly the limit we take the cheaper rate; strictly > tips to high.
  assert.equal(fhaAnnualMipRate(726200), 0.0055);
  assert.equal(fhaAnnualMipRate(726200.01), 0.0075);
});

test('Sprint 1: computeFhaLoanAmount for $500K @ 3.5% down', () => {
  // baseLoan   = 500000 * (1 - 0.035) = 482500
  // upfrontMip = 482500 * 0.0175      = 8443.75
  // financed   = 482500 + 8443.75     = 490943.75
  const r = computeFhaLoanAmount({ price: 500000, downPct: 3.5 });
  assert.ok(Math.abs(r.baseLoan - 482500) < 0.01, `baseLoan ${r.baseLoan}`);
  assert.ok(Math.abs(r.upfrontMip - 8443.75) < 0.01, `upfrontMip ${r.upfrontMip}`);
  assert.ok(Math.abs(r.financedLoan - 490943.75) < 0.01, `financedLoan ${r.financedLoan}`);
});

test('Sprint 1: computeFhaPITI — canonical $500K / 3.5% / 6.5% / 30yr / 1.25% tax / $1,800 ins', () => {
  // Breakdown (derived, verified with node calculator):
  //   baseLoan       = $482,500.00
  //   upfrontMip     =   $8,443.75 (financed)
  //   financedLoan   = $490,943.75
  //   P&I (on financed, 6.5%/30yr) = $3,103.10
  //   monthly taxes  =    $520.83  ($6,250/yr)
  //   monthly ins    =    $150.00  ($1,800/yr)
  //   monthly MIP    =    $221.15  ($482,500 × 0.0055 / 12)
  //   PITI           =  $3,995.08
  // This supersedes the HANDOFF.md $4,004 figure (which assumed 0.85% MIP —
  // the pre-2023 rate. USER_PROFILE.md specifies 0.55% for Jose's scenario.)
  const r = computeFhaPITI({
    price: 500000,
    downPct: 3.5,
    annualRatePct: 6.5,
    termYears: 30,
    annualTaxes: 6250,
    annualInsurance: 1800,
  });
  assert.ok(Math.abs(r.pi - 3103.10) < 1, `pi got ${r.pi.toFixed(2)}`);
  assert.ok(Math.abs(r.taxes - 520.83) < 0.5, `taxes got ${r.taxes.toFixed(2)}`);
  assert.ok(Math.abs(r.insurance - 150) < 0.5, `ins got ${r.insurance.toFixed(2)}`);
  assert.ok(Math.abs(r.mipMonthly - 221.15) < 0.5, `mip got ${r.mipMonthly.toFixed(2)}`);
  assert.ok(Math.abs(r.piti - 3995.08) < 2, `PITI got ${r.piti.toFixed(2)}`);
  assert.ok(Math.abs(r.upfrontMip - 8443.75) < 0.01);
  assert.ok(Math.abs(r.baseLoan - 482500) < 0.01);
  assert.ok(Math.abs(r.financedLoan - 490943.75) < 0.01);
});

test('Sprint 1: computeFhaPITI respects financeUpfrontMip=false (P&I on base loan)', () => {
  const financed = computeFhaPITI({
    price: 500000, downPct: 3.5, annualRatePct: 6.5, termYears: 30,
    annualTaxes: 6250, annualInsurance: 1800,
  });
  const paidAtClosing = computeFhaPITI({
    price: 500000, downPct: 3.5, annualRatePct: 6.5, termYears: 30,
    annualTaxes: 6250, annualInsurance: 1800, financeUpfrontMip: false,
  });
  assert.ok(paidAtClosing.pi < financed.pi, 'P&I should be lower when upfront MIP is not financed');
  // MIP monthly is unchanged (always computed on base).
  assert.ok(Math.abs(paidAtClosing.mipMonthly - financed.mipMonthly) < 0.01);
});

// --- Sprint 1: qualifying income (75% rental offset) ---

test('Sprint 1: qualifying income — owner-occupied duplex, W-2 $4,506 + other unit $2,000/mo = $6,006', () => {
  const q = computeQualifyingIncome({
    w2Monthly: 4506,
    units: 2,
    perUnitRents: [0, 2000], // index 0 = owner-occupied
    ownerOccupied: true,
  });
  assert.equal(q, 6006);
});

test('Sprint 1: qualifying income — owner-occupied fourplex, 3 rented units = $8,631', () => {
  const q = computeQualifyingIncome({
    w2Monthly: 4506,
    units: 4,
    perUnitRents: [0, 1800, 1800, 1900],
    ownerOccupied: true,
  });
  // 4506 + 0.75 * (1800 + 1800 + 1900) = 4506 + 0.75 * 5500 = 4506 + 4125 = 8631
  assert.equal(q, 8631);
});

test('Sprint 1: qualifying income — pure investment counts ALL units at 75%', () => {
  const q = computeQualifyingIncome({
    w2Monthly: 4506,
    units: 2,
    perUnitRents: [2000, 2000],
    ownerOccupied: false,
  });
  // 4506 + 0.75 * 4000 = 4506 + 3000 = 7506
  assert.equal(q, 7506);
});

test('Sprint 1: qualifying income — SFH owner-occupied gets no rent offset', () => {
  const q = computeQualifyingIncome({
    w2Monthly: 4506,
    units: 1,
    perUnitRents: [0],
    ownerOccupied: true,
  });
  assert.equal(q, 4506);
});

// --- Sprint 1: DTI max PITI ---

test('Sprint 1: maxPitiAtDti — $6,006 at 45/50/55%', () => {
  assert.ok(Math.abs(maxPitiAtDti({ qualifyingIncome: 6006, dtiPct: 45 }) - 2702.70) < 0.01);
  assert.ok(Math.abs(maxPitiAtDti({ qualifyingIncome: 6006, dtiPct: 50 }) - 3003.00) < 0.01);
  assert.ok(Math.abs(maxPitiAtDti({ qualifyingIncome: 6006, dtiPct: 55 }) - 3303.30) < 0.01);
});

test('Sprint 1: maxPitiAtDti subtracts monthly debts', () => {
  // $6,006 * 45% = $2,702.70; minus $500 debts = $2,202.70
  const max = maxPitiAtDti({ qualifyingIncome: 6006, dtiPct: 45, monthlyDebts: 500 });
  assert.ok(Math.abs(max - 2202.70) < 0.01);
});

test('Sprint 1: maxPitiAtDti clamps at 0 when debts exceed DTI ceiling', () => {
  // $4,506 * 45% = $2,027.70; minus $5,000 debts would be -$2,972.30.
  // Clamp prevents negative dollar display in the UI.
  const max = maxPitiAtDti({ qualifyingIncome: 4506, dtiPct: 45, monthlyDebts: 5000 });
  assert.equal(max, 0);
});

test('Sprint 1: computeQualifyingIncome SFR with owner-occ + no ADU rent = no offset', () => {
  // Defensive: an owner-occ SFR with units=1 should never grant rental offset
  // even if caller passes a per-unit rent array with a positive value.
  const qi = computeQualifyingIncome({
    w2Monthly: 4506,
    units: 1,
    perUnitRents: [1500],
    ownerOccupied: true,
  });
  assert.equal(qi, 4506);
});
