// Pure financial calculation reference implementation.
//
// Sprint 0 scope: mirror the current formulas from index.html so tests can
// lock baseline behavior. Sprint 1 will add FHA MIP support here and wire
// index.html to import from this module.
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
