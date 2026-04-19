// Pure financial calculation reference implementation.
//
// Sprint 0 scope: mirror the current formulas from index.html so tests can
// lock baseline behavior. Sprint 1 adds FHA MIP, qualifying income, DTI.
//
// IMPORTANT: index.html contains an inline copy of this math (around
// lines 3093-3353 for baseline PITI/cashflow, and FHA MIP+DTI additions
// near the same block in Sprint 1). Both locations MUST produce identical
// numbers. When you change a formula here, mirror it there (and vice versa).
//
// Invariants:
//   - Every function is pure (no DOM, no module state)
//   - Inputs are plain numbers; outputs are plain numbers or plain objects
//   - All percentages are passed as whole numbers (e.g. 6.5 for 6.5%)
//   - All money amounts are in dollars
//   - Term is in years; internal math uses months

/**
 * Monthly principal & interest for a fully-amortizing loan.
 * Matches index.html:3131-3138 behavior.
 *
 * @param {number} loanAmount - Principal in dollars
 * @param {number} annualRatePct - Annual interest rate as whole number (6.5 = 6.5%)
 * @param {number} termYears - Loan term in years
 * @returns {number} Monthly P&I payment in dollars
 */
export function computeMonthlyPI(loanAmount, annualRatePct, termYears) {
  if (loanAmount <= 0 || termYears <= 0) return 0;
  const n = termYears * 12;
  const monthlyRate = annualRatePct / 100 / 12;
  if (monthlyRate === 0) return loanAmount / n;
  return loanAmount * (monthlyRate * Math.pow(1 + monthlyRate, n)) / (Math.pow(1 + monthlyRate, n) - 1);
}

/**
 * PITI (Principal, Interest, Taxes, Insurance) monthly.
 * BASELINE — does NOT include FHA MIP. Sprint 1 will add MIP support.
 * Matches index.html:3140-3142 behavior.
 *
 * @param {object} args
 * @param {number} args.loanAmount
 * @param {number} args.annualRatePct
 * @param {number} args.termYears
 * @param {number} args.annualTaxes - Property taxes per year
 * @param {number} args.annualInsurance - Hazard insurance per year
 * @returns {{pi: number, taxes: number, insurance: number, piti: number}}
 */
export function computePITI({ loanAmount, annualRatePct, termYears, annualTaxes, annualInsurance }) {
  const pi = computeMonthlyPI(loanAmount, annualRatePct, termYears);
  const taxes = annualTaxes / 12;
  const insurance = annualInsurance / 12;
  return { pi, taxes, insurance, piti: pi + taxes + insurance };
}

/**
 * Monthly and annual cash flow.
 * Matches index.html:3152-3153 behavior.
 */
export function computeCashFlow({ totalMonthlyIncome, totalOpex, monthlyPI }) {
  const monthlyCF = totalMonthlyIncome - totalOpex - monthlyPI;
  return { monthlyCF, annualCF: monthlyCF * 12 };
}

/**
 * Annual net operating income.
 * Matches index.html:3156 behavior.
 * NOI does NOT subtract debt service.
 */
export function computeNOI({ totalMonthlyIncome, totalOpex }) {
  return (totalMonthlyIncome * 12) - (totalOpex * 12);
}

/**
 * Cap rate as percentage.
 * Matches index.html:3166.
 */
export function computeCapRate(noi, price) {
  if (price <= 0) return 0;
  return (noi / price) * 100;
}

/**
 * Gross rent multiplier.
 * Matches index.html:3169-3170.
 */
export function computeGRM(price, annualRent) {
  if (annualRent <= 0) return 0;
  return price / annualRent;
}

/**
 * Debt service coverage ratio.
 * BASELINE — uses P&I only as denominator (matches index.html:3177). Commercial
 * lenders typically use PITI; the DSCR convention may shift in a later sprint.
 *
 * @returns {number | null} null if no debt service
 */
export function computeDSCR(noi, annualPI) {
  if (annualPI <= 0) return null;
  return noi / annualPI;
}

/**
 * Cash on cash return as percentage.
 * Matches index.html:3163.
 */
export function computeCoC(annualCF, totalCashInvested) {
  if (totalCashInvested <= 0) return 0;
  return (annualCF / totalCashInvested) * 100;
}

/**
 * Amortization schedule.
 * Matches index.html:3358-3392 (with the final-month rounding adjustment).
 *
 * @returns {Array<{month: number, interest: number, principal: number, balance: number}>}
 */
export function computeAmortization({ loanAmount, annualRatePct, termYears }) {
  if (loanAmount <= 0 || termYears <= 0) return [];
  const n = termYears * 12;
  const monthlyRate = annualRatePct / 100 / 12;
  const pi = computeMonthlyPI(loanAmount, annualRatePct, termYears);
  const schedule = [];
  let balance = loanAmount;
  for (let month = 1; month <= n; month++) {
    const interest = balance * monthlyRate;
    let principal = pi - interest;
    if (month === n) {
      principal = Math.min(principal, balance);
    }
    balance = Math.max(0, balance - principal);
    schedule.push({ month, interest, principal, balance });
  }
  return schedule;
}

/**
 * Total monthly operating expenses.
 * Matches index.html:3145-3149.
 *
 * Percentages (maintenance, vacancy, capex, management) are applied to
 * total monthly income, not to gross rent alone.
 */
export function computeOpex({
  totalMonthlyIncome,
  annualTaxes,
  annualInsurance,
  maintenancePct,
  vacancyPct,
  capexPct,
  managementPct,
  hoaMonthly = 0,
  utilitiesMonthly = 0,
  otherMonthly = 0,
}) {
  const monthlyTaxes = annualTaxes / 12;
  const monthlyInsurance = annualInsurance / 12;
  const maint = totalMonthlyIncome * maintenancePct / 100;
  const vacancy = totalMonthlyIncome * vacancyPct / 100;
  const capex = totalMonthlyIncome * capexPct / 100;
  const management = totalMonthlyIncome * managementPct / 100;
  const total = monthlyTaxes + monthlyInsurance + maint + vacancy + capex + management + hoaMonthly + utilitiesMonthly + otherMonthly;
  return { monthlyTaxes, monthlyInsurance, maint, vacancy, capex, management, total };
}

/**
 * 1% rule test — is monthly rent at least 1% of purchase price.
 * Matches index.html:3198.
 */
export function onePercentRule(totalMonthlyIncome, price) {
  return { pass: totalMonthlyIncome >= price * 0.01, pct: price > 0 ? (totalMonthlyIncome / price) * 100 : 0 };
}

/**
 * 70% rule for BRRRR — is (price + rehab) ≤ 70% of ARV.
 * Matches index.html:3206-3207.
 */
export function seventyPercentRule(price, rehab, arv) {
  if (arv <= 0) return { pass: false, pct: 0 };
  const pct = ((price + rehab) / arv) * 100;
  return { pass: (price + rehab) <= arv * 0.70, pct };
}

// =====================================================================
// Sprint 1 — FHA MIP, qualifying income, DTI
// =====================================================================
//
// FHA MIP mechanics (2025 HUD rules, 30-yr fixed, >95% LTV):
//   - Upfront MIP: 1.75% of the BASE loan amount. Normally financed
//     into the loan (rolled in, not paid at closing). So:
//       baseLoan        = price * (1 - downPct/100)
//       upfrontMip      = baseLoan * 0.0175
//       financedLoan    = baseLoan + upfrontMip   ← P&I is computed on this
//   - Annual MIP: a monthly premium equal to
//       (baseLoan * annualRate) / 12
//     NOTE: HUD calculates annual MIP on the BASE loan amount, not the
//     financed amount, and it is a fixed schedule — it does not amortize
//     down with the loan balance. (We use the constant base for simplicity;
//     real servicers recalc annually but the drift is pennies.)
//   - Rate table (baseline loans ≤ $726,200 FHA limit):
//       standard: 0.55% (replaces the old 0.85% post-2023 HUD cut)
//     For loans above $726,200 the rate is 0.75%. Jose's $525K ceiling is
//     well under the limit, so 0.55% is the operative rate for him.
//
// Spec conflict resolved: USER_PROFILE.md §3 and HANDOFF.md disagree on
// the annual rate (0.55% vs 0.85%). USER_PROFILE.md + current HUD rules
// win. See commit message for sprint1 FHA work.

export const FHA_MIP_UPFRONT_RATE = 0.0175;
export const FHA_BASELINE_LOAN_LIMIT = 726200; // 2025 FHA conforming baseline
export const FHA_MIP_ANNUAL_STANDARD = 0.0055; // ≤ baseline limit
export const FHA_MIP_ANNUAL_HIGH = 0.0075;     // > baseline limit
export const FHA_RENTAL_OFFSET = 0.75;         // FHA rule — 75% of projected rent

/**
 * Pick annual MIP rate based on base loan amount.
 * @param {number} baseLoanAmount - Principal BEFORE upfront MIP financing.
 * @returns {number} 0.0055 or 0.0075 as a decimal.
 */
export function fhaAnnualMipRate(baseLoanAmount) {
  return baseLoanAmount > FHA_BASELINE_LOAN_LIMIT
    ? FHA_MIP_ANNUAL_HIGH
    : FHA_MIP_ANNUAL_STANDARD;
}

/**
 * Compute FHA loan amounts.
 * @param {{price: number, downPct: number}} args
 * @returns {{baseLoan: number, upfrontMip: number, financedLoan: number}}
 */
export function computeFhaLoanAmount({ price, downPct }) {
  if (price <= 0) return { baseLoan: 0, upfrontMip: 0, financedLoan: 0 };
  const baseLoan = price * (1 - downPct / 100);
  const upfrontMip = baseLoan * FHA_MIP_UPFRONT_RATE;
  const financedLoan = baseLoan + upfrontMip;
  return { baseLoan, upfrontMip, financedLoan };
}

/**
 * FHA PITI including monthly MIP. Default assumes upfront MIP is financed
 * (rolled into loan amount) per FHA norm.
 *
 * @param {object} args
 * @param {number} args.price
 * @param {number} args.downPct
 * @param {number} args.annualRatePct
 * @param {number} args.termYears
 * @param {number} args.annualTaxes
 * @param {number} args.annualInsurance
 * @param {boolean} [args.financeUpfrontMip=true] - If false, upfrontMip is
 *   paid at closing and NOT added to financed loan (rare; kept for completeness).
 * @returns {{
 *   pi: number, taxes: number, insurance: number, mipMonthly: number,
 *   piti: number, upfrontMip: number, baseLoan: number, financedLoan: number
 * }}
 */
export function computeFhaPITI({
  price,
  downPct,
  annualRatePct,
  termYears,
  annualTaxes,
  annualInsurance,
  financeUpfrontMip = true,
}) {
  const { baseLoan, upfrontMip, financedLoan } = computeFhaLoanAmount({ price, downPct });
  const loanForPI = financeUpfrontMip ? financedLoan : baseLoan;
  const pi = computeMonthlyPI(loanForPI, annualRatePct, termYears);
  const taxes = annualTaxes / 12;
  const insurance = annualInsurance / 12;
  const mipMonthly = baseLoan * fhaAnnualMipRate(baseLoan) / 12;
  return {
    pi,
    taxes,
    insurance,
    mipMonthly,
    piti: pi + taxes + insurance + mipMonthly,
    upfrontMip,
    baseLoan,
    financedLoan,
  };
}

/**
 * Qualifying income for FHA DTI underwriting.
 *
 * Rule: For owner-occupied 2-4 unit, the non-owner units' projected rent
 * counts at 75%. For pure investment (non-owner-occupied), ALL units count
 * at 75%. Only W-2 income is used for Jose's case — self-employment is
 * written off to near-zero per USER_PROFILE §2.
 *
 * Unit index 0 is treated as owner-occupied when ownerOccupied=true.
 *
 * @param {object} args
 * @param {number} args.w2Monthly - Monthly W-2 gross
 * @param {number} args.units - Unit count (1-4)
 * @param {number[]} args.perUnitRents - Monthly projected rent per unit, index-aligned
 * @param {boolean} args.ownerOccupied
 * @returns {number} Monthly qualifying income
 */
export function computeQualifyingIncome({ w2Monthly, units, perUnitRents, ownerOccupied }) {
  const rents = Array.isArray(perUnitRents) ? perUnitRents.slice(0, units) : [];
  if (ownerOccupied) {
    if (units <= 1) {
      // SFR with owner-occupancy: no rental offset. Per USER_PROFILE §11,
      // "SFR without legal ADU (no rental offset possible)". If the property
      // has a legal ADU, model it as units=2 with ADU rent at index 1.
      return w2Monthly;
    }
    // Owner occupies unit 0; units 1..N-1 are rented at 75% offset.
    const rentable = rents.slice(1).reduce((s, r) => s + (r || 0), 0);
    return w2Monthly + FHA_RENTAL_OFFSET * rentable;
  }
  // Pure investment (non-owner-occupied): count all units at 75%.
  const rentable = rents.reduce((s, r) => s + (r || 0), 0);
  return w2Monthly + FHA_RENTAL_OFFSET * rentable;
}

/**
 * Max monthly PITI allowed at a given DTI ratio, after subtracting
 * monthly debts.
 *
 * @param {object} args
 * @param {number} args.qualifyingIncome
 * @param {number} args.dtiPct - Whole-number percent (45 for 45%)
 * @param {number} [args.monthlyDebts=0] - Other recurring debt payments
 * @returns {number} Max allowable PITI in dollars
 */
export function maxPitiAtDti({ qualifyingIncome, dtiPct, monthlyDebts = 0 }) {
  // Clamp at 0: a heavily-indebted borrower can't have "negative" max PITI;
  // the UI would render a nonsense "-$500" otherwise.
  return Math.max(0, qualifyingIncome * dtiPct / 100 - monthlyDebts);
}

// =====================================================================
// ADR-002: shared constants loader
// =====================================================================
//
// The FHA constants above (FHA_MIP_UPFRONT_RATE, FHA_MIP_ANNUAL_STANDARD,
// FHA_MIP_ANNUAL_HIGH, FHA_BASELINE_LOAN_LIMIT, FHA_RENTAL_OFFSET) are
// kept as module defaults so the 61-test baseline keeps passing without
// a running server. Real runtime callers (index.html, future ESM imports)
// should fetch the shared JSON via loadSpecConstants() and use those
// values. The module-level defaults and the JSON file are populated from
// the same source of truth (handoff/USER_PROFILE.md) — drift only matters
// if someone edits one without the other, which is exactly what the
// single JSON file exists to kill.

/** Fetch the shared spec/constants.json (served at /spec/constants.json).
 *  Returns the parsed object. Throws on network or parse failure — by
 *  design; silent fallback is the drift mode this file was created to
 *  kill. */
export async function loadSpecConstants(url = '/spec/constants.json') {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`spec load failed: HTTP ${resp.status}`);
  const spec = await resp.json();
  if (!spec?._meta?.version) throw new Error('spec missing _meta.version');
  return spec;
}
