# MIZAN Finance v4.0 — Calculations Audit

> Review of the calculations behind every display page, checked against
> conventional financial-metric definitions. Generated 2026-07-03.

This document lists (1) what each page computes, (2) how it compares to standard
finance practice, and (3) concrete fixes for the problematic areas. Fixes are
grouped at the bottom with ready-to-apply code.

---

## Scope — pages reviewed

| Page | Calculation source |
|---|---|
| Staff / Hourly Rate | [`models/staff.py`](models/staff.py) `calculate_hourly_rate` |
| KPI | [`models/staff.py`](models/staff.py) `get_staff_kpi` |
| Projects / Profitability | [`models/projects.py`](models/projects.py) `calculate_project_cost` |
| FX Gain/Loss | [`models/projects.py`](models/projects.py) `calculate_fx_gain_loss` |
| Budget (Plan vs Actual) | [`controllers/projects_bp.py`](controllers/projects_bp.py) `budget_page` |
| Pricing Engine | [`models/pricing.py`](models/pricing.py) `pricing_estimate` |
| Dashboard (CFO metrics) | [`models/dashboard.py`](models/dashboard.py) |
| Cash Flow | [`models/transactions.py`](models/transactions.py) |
| Loans | [`models/loans.py`](models/loans.py) |
| Equipment depreciation | [`models/equipment.py`](models/equipment.py) |

---

## Findings summary

| # | Area | Severity | Verdict |
|---|---|---|---|
| 1 | General equipment `quantity` ignored | ❌ Bug | **✅ FIXED** — now `quantity × price ÷ lifespan` |
| 2 | Loan `interest_rate` never accrued | ⚠️ Gap | Remaining balance understates true liability for interest-bearing loans |
| 3 | Project "gross margin" mislabeled + cash vs POC bases mixed | ⚠️ Naming/basis | **✅ FIXED (label)** — key renamed `gross_margin` → `net_margin`; POC split still optional |
| 4 | Overhead allocated by *realized* billable hours | ⚠️ Modeling | Mild circularity — idle staff absorb less overhead |
| 5 | Burn rate includes non-cash depreciation | ⚠️ Concept | **✅ FIXED** — burn rate is now cash-only; `total_operating_cost` keeps the full figure |
| 6 | KPI utilization vs `kpi_months` span | ⚠️ Data | Distorted if `kpi_months` ≠ actual imported months |
| 7 | Loan FX uses issue-date rate for later payments | ⚠️ Minor | Cross-currency remaining balance drifts |
| 8 | AR aging by entry date, not due date | ℹ️ Note | Acceptable; conventional aging often uses due date |

Everything else — cost-plus rate build-up, CVP breakeven, pricing gross-up
`/(1 − margin)`, budget variance, direct-method cash flow, FX revaluation
direction, straight-line depreciation method — is **conventionally sound**.

---

## Detail + fixes

### 1. ❌ General equipment `quantity` ignored — BUG · ✅ FIXED

**Where:** [`models/equipment.py`](models/equipment.py) `get_general_equipment_monthly`

**Before:**
```python
return sum(r['price'] / max(r['lifespan_months'], 1) for r in rows)
```

The `general_equipment` table has a `quantity` column, it is written on insert
([`controllers/accounting_bp.py:223`](controllers/accounting_bp.py#L223)), and
`CLAUDE.md`/`KT.md` document the intended formula as
`quantity × price ÷ lifespan_months`. As written, 5 printers depreciate like 1.

**Blast radius:** flows into `general_eq_share` in every `calculate_hourly_rate`
→ every `cost_rate`/`billing_rate` → project cost, KPI, pricing, dashboard, and
the `burn_rate` equipment component. All are understated when any row has
quantity > 1.

**Fix (applied):**
```python
def get_general_equipment_monthly():
    conn = get_db()
    rows = conn.execute(
        "SELECT quantity, price, lifespan_months FROM general_equipment WHERE is_active=1"
    ).fetchall()
    conn.close()
    return sum(
        (r['quantity'] or 1) * r['price'] / max(r['lifespan_months'], 1)
        for r in rows
    )
```

---

### 2. ⚠️ Loan interest never accrued

**Where:** [`models/loans.py`](models/loans.py) — `get_all_loans`, `get_loan_detail`, `get_loan_summary`

`remaining = total_amount − Σ payments`. The `loans.interest_rate` column is
stored but never used, so the displayed liability is pure principal. For any
interest-bearing loan the true payoff is higher.

**Fix (simple-interest accrual to today):** add an accrued-interest helper and
include it in `remaining`.
```python
from datetime import datetime, date

def _accrued_interest(total_amount, annual_rate_pct, issue_date, as_of=None):
    """Simple interest accrued from issue_date to as_of (today)."""
    if not annual_rate_pct or annual_rate_pct <= 0 or not issue_date:
        return 0.0
    try:
        start = datetime.strptime(issue_date, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return 0.0
    as_of = as_of or date.today()
    days = max((as_of - start).days, 0)
    return total_amount * (annual_rate_pct / 100.0) * (days / 365.0)
```
Then in `get_all_loans` / `get_loan_detail`:
```python
interest = _accrued_interest(loan['total_amount'], loan['interest_rate'], loan['issue_date'])
remaining = loan['total_amount'] + interest - total_paid
```
Expose `accrued_interest` in the returned dict so the UI can show principal vs
interest separately. (If loans are genuinely 0%, this is a no-op and safe.)

**Decision needed:** simple vs compound, and day-count (365 vs 360). Simple /
Actual-365 is the least surprising default; confirm with the finance owner.

---

### 3. ⚠️ Project margin label + revenue basis · ✅ FIXED (label)

**Where:** [`models/projects.py`](models/projects.py) `calculate_project_cost`

```python
profit       = income − (mizan_cost + outsourcing + material)   # income = SUM(paid)
gross_margin = profit / income × 100
```

Two issues:
- **Label:** this is a **net project margin** (after all project costs), not a
  GAAP "gross margin." Rename the key or the UI label to `net_margin` /
  "Sof marja" to avoid confusion.
- **Basis mismatch:** `profit` uses **cash received** (`SUM(paid)`), while the
  same result dict also returns `earned_revenue = contract × min(actual/est
  hours, 1)` (**percentage-of-completion**) and `deferred_revenue`. Two
  different revenue bases sit side by side; a reader can conflate them.

**Fix (choose one basis, present the other as supplementary):**
- Keep cash `profit` for the cash view, **rename `gross_margin` → `net_margin`**.
- Add an explicit POC profit alongside so they aren't confused:
```python
poc_profit = earned_revenue - total_expense if earned_revenue > 0 else None
poc_margin = (poc_profit / earned_revenue * 100) if earned_revenue else 0
```
- In the template, group the cash figures and the POC figures under separate
  headings.

No arithmetic here is *wrong* — the risk is presentational. **Applied:** the
`gross_margin` key was renamed to `net_margin` (with the `margin` alias kept for
backward compatibility) and a clarifying comment added. The POC split remains
optional polish, not yet applied.

---

### 4. ⚠️ Overhead allocated by realized billable hours

**Where:** [`models/staff.py`](models/staff.py) `_proportional_share` / `calculate_hourly_rate`

Shared costs are split by each person's **share of current-period billable
hours**. Economically this means an employee who logs *few* billable hours
absorbs *less* fixed overhead, lowering their cost rate — the opposite of the
true burden (idle staff cost more to carry, not less). Conventional ABC
allocates fixed overhead on **capacity/head-count**, reserving activity-based
splits for genuinely variable pools.

**This is a modeling choice, not a bug.** Options if you want to change it:
- **Head-count split** for fixed overhead (rent, admin) — most defensible.
- **Capacity-hours split** (`available_hours` per person) instead of realized
  billable hours — keeps proportionality without the circularity.
- Leave as-is if leadership explicitly wants cost to follow utilization.

Recommend documenting the intent in `CLAUDE.md` either way. No code change
until the finance owner picks a basis.

---

### 5. ⚠️ Burn rate mixes in non-cash depreciation · ✅ FIXED

**Where:** [`models/dashboard.py`](models/dashboard.py) `get_burn_rate_and_runway`

**Before:**
```python
burn_rate = monthly_salary_full + monthly_overhead + monthly_equipment
```

`monthly_equipment` is general-equipment **depreciation** — a non-cash charge.
Burn rate is by definition cash leaving the business, so including depreciation
overstates cash burn (and understates runway).

**Fix (applied):** `burn_rate` is now cash-only, and the full figure is kept as
`total_operating_cost`; runway is computed from the cash burn.
```python
burn_rate = monthly_salary_full + monthly_overhead            # cash only
total_operating_cost = burn_rate + monthly_equipment
runway = running_balance / burn_rate if burn_rate > 0 else 999
# both burn_rate and total_operating_cost returned in the dict
```
Note: the dashboard's displayed burn-rate figure drops by the equipment
depreciation amount — intended. `total_operating_cost` is available if the old
value should be surfaced under a clearer label.

---

### 6. ⚠️ KPI utilization vs `kpi_months`

**Where:** [`models/staff.py`](models/staff.py) `get_staff_kpi`

```python
utilization = total_hours / (kpi_months × available_hours)
```

`total_hours` is **all-time** hours; the denominator uses the `kpi_months`
setting (default 43). If `kpi_months` doesn't equal the actual span of imported
`project_hours`, utilization is systematically wrong.

**Fix:** derive the span from the data instead of trusting a static setting:
```python
span_row = conn.execute(
    "SELECT COUNT(DISTINCT period) AS months FROM project_hours WHERE hours > 0"
).fetchone()
kpi_months = span_row['months'] or (get_setting('kpi_months') or 43)
```
Or, at minimum, validate/auto-update `kpi_months` on import. Same concern
applies to `capacity` on the dashboard, which uses the same `kpi_months`.

---

### 7. ⚠️ Loan FX uses issue-date rate for payments

**Where:** [`models/loans.py`](models/loans.py) `get_loan_summary`

```python
rate = 1.0 if currency == 'UZS' else get_rate_for_date(issue_date)
totals[...]      += total_amount * rate
paid_totals[...] += paid * rate      # payments valued at ISSUE-date rate
```

Payments made later at different FX rates are revalued at the **issue-date**
rate, so the UZS remaining balance drifts from reality.

**Fix:** value payments at their own payment-date rate (requires summing
payments individually rather than a single `SUM(amount)`):
```python
pays = conn.execute(
    "SELECT amount, date FROM loan_payments WHERE loan_id=?", (r['id'],)
).fetchall()
paid_uzs = sum(
    p['amount'] * (1.0 if r['currency'] == 'UZS'
                   else (get_rate_for_date(p['date']) or 1.0))
    for p in pays
)
```
Minor for small/short loans; matters for large USD loans over long horizons.

---

### 8. ℹ️ AR aging by entry date

**Where:** [`models/dashboard.py`](models/dashboard.py) `get_ar_aging`

Buckets outstanding `amount − paid` by days since the transaction `date`.
Conventional aging often ages from the **invoice due date** (`deadline` column
exists on `transactions`). Current behavior is acceptable; switch to `deadline`
(falling back to `date`) if you want true past-due aging.

---

## Confirmed-correct (no change needed)

- **Cost-plus rate build-up** — fully-loaded cost ÷ available hours × markup. ✅
- **Pricing gross-up** `target = minimum / (1 − margin)` — correct; avoids the
  classic `× (1 + margin)` error. ✅
- **Breakeven** `fixed / contribution-margin-ratio` — correct CVP. ✅
- **Budget variance** `(fact − plan) / plan` with thresholds. ✅
- **Cash flow** direct method, actual `paid` only, no theoretical accruals
  (avoids double-counting). ✅
- **FX revaluation** `usd × (current_rate − tx_rate)` — correct direction. ✅
- **Depreciation method** straight-line, `price ÷ lifespan_months` — standard
  (the only issue is the missing `quantity` multiplier, item #1). ✅

---

## Suggested fix order

1. ~~**#1 general-equipment quantity**~~ — ✅ **DONE**.
2. ~~**#5 burn rate**~~ — ✅ **DONE**.
3. ~~**#3 margin rename**~~ — ✅ **DONE** (label only; POC split still optional).
4. **#6 kpi_months** — data-integrity, affects utilization + capacity. *Pending.*
5. **#2 loan interest** — needs a business decision (simple/compound, day-count). *Pending.*
6. **#7 loan FX**, **#4 overhead basis**, **#8 AR due-date** — refinements, do
   when their pages are next touched. *Pending.*
