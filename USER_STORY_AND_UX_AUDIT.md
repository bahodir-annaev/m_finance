# MIZAN Finance — Financial Officer User Story & UI / Business-Logic Audit

> Walkthrough of every implemented feature from the point of view of the person who
> actually runs the numbers, followed by the conflicts and illogical behaviors found
> along the way, each with a suggested fix.
>
> Companion document: [`CALCULATIONS_AUDIT.md`](CALCULATIONS_AUDIT.md) covers the
> *formula-level* correctness of each metric. This document covers the *workflow and
> consistency* level: places where two features contradict each other, where the UI
> and the code disagree, or where a normal financial-officer task cannot be completed
> as designed. Overlapping items are cross-referenced, not repeated.
>
> Reviewed on 2026-07-14 against the current working tree (v6 milestone branch).

---

## Part 1 — User story: a month with Dilnoza, Financial Officer

**Persona.** Dilnoza is the financial officer (CFO role) of a ~15-person architecture
firm in Tashkent. She has a `manager` login; the firm's director holds the `admin`
login. She works in Uzbek, occasionally switching the UI to Russian for the accountant.
Everything below maps to implemented features.

### Scene 1 — Morning check-in (Dashboard, `/`)

Dilnoza logs in (`/login`) and lands on the **Dashboard**. She scans:

- the headline tiles — projects, hours, staff counts, utilization, average cost/billing
  rate, total cost, profit/loss;
- the **CFO panel** — burn rate, runway, capacity %, AR outstanding/overdue,
  projects-on-course, FX gain/loss, breakeven revenue, revenue per employee,
  profit per hour, and the open loan balance;
- the top-10 projects table.

She trusts the red/green coloring to tell her where to click next. If AR shows red,
she goes to External; if a project is "off course", she opens its detail page.

### Scene 2 — Recording money in and out (Accounting, `/accounting`)

A client paid 150 mln UZS against project "Navoiy Tower". Dilnoza opens
**Accounting** — the universal entry form — picks *external / tushum*, the project,
the amount received, payment type, and saves. The same page is her single entry point
for: internal expenses (salary, rent, utilities…), new projects, staff and salary
changes, equipment, licenses, overhead items, exchange rates, and loans + loan
payments.

She also issues an invoice for 300 mln UZS that the client will pay in 45 days —
this is where she first hits friction (see **B1**: the form cannot record an unpaid
invoice at all).

### Scene 3 — Chasing receivables (External, `/external`)

Mid-month she reviews **External transactions**: filters by project, sorts by date,
and uses the summary tiles (income / outsourcing+material / remainder). Rows flagged
by the import reconciler show a warning in the edit modal. She clicks a row to fix a
typo in the paid amount — the modal writes straight through the generic
`/api/update/transactions/<id>` endpoint.

### Scene 4 — Monthly close (Import → Periods)

At month end the director (admin) imports the NIZAM timesheet Excel (`/import`).
Unmatched project or counterparty names land in **Reconcile**
(`/reference/reconcile`), where names get linked to existing records or created,
with aliases remembered for the next import.

Dilnoza then opens **Periods** (`/periods`), creates `2026-07`, and *soft-closes* it:
the app snapshots every production employee's cost rate and allocation shares
(`period_allocations`) and stamps `applied_cost_amount` onto each `project_hours`
row, so history stops moving when salaries change later. After the accountant's
review, the director *hard-closes* it.

### Scene 5 — Quoting a new project (Pricing, `/pricing`)

A new hotel project comes in. Dilnoza enters planned hours per production employee,
sets the four risk factors (deadline, client type, complexity, currency), plus
outsourcing and material budgets. The engine returns minimum / target / premium
contract prices. She likes the numbers, ticks *save to project* and *generate
milestones*, and fills the schedule editor (per-milestone work type, months, % of
income and hours). The plan is now frozen onto the project and a milestone ladder
exists.

### Scene 6 — Tracking delivery (Projects → Project detail → Plan → Budget)

Weekly, she opens **Projects** (`/projects`) and drills into a project
(`/projects/<id>`): milestone cards with earned value, CPI/SPI, late/behind flags,
a monthly plan-vs-fact rollup with the on-course verdict, and a list of unassigned
actuals she attaches to milestones. **Plan** (`/plan`) gives her the same verdict
across all projects; **Budget** (`/budget`) compares frozen plan totals to live
actual cost.

### Scene 7 — Cash, debt, and the owners (Cashflow, Loans, Dividends)

- **Cashflow** (`/cashflow`): monthly income vs external/internal expense and
  dividends, with a running balance — the number that feeds runway.
- **Oldi-Berdi / Loans** (`/loans`): open borrowings and lendings, remaining
  balances, overdue flags. (Entries are added from Accounting, not here.)
- **Dividends** (`/dividends`): founder distributions, recorded like transactions
  and included in cash outflow.

### Scene 8 — People economics (Staff, Hourly, KPI, Equipment)

Before salary reviews she checks **Hourly** (`/hourly`) — the full cost build-up per
production employee (salary → tax/social → admin share → equipment/licenses →
overhead share → cost rate → billing rate) — and **KPI** (`/kpi`) for utilization
and value created per person.

### Scene 9 — Asking the robot (AI, `/ai`)

Instead of assembling a board memo by hand, she clicks *CFO Summary* on the **AI**
page: a local Ollama-served model gets a snapshot of dashboard, AR, and loan data and
writes a briefing in her language. *Anomalies* flags risks; *categorize* suggests a
tx_type during manual entry.

### Scene 10 — Reporting out (Export)

For the monthly board pack she hits **Export** (`/export`) and downloads the Excel
workbook (salaries, hourly rates, budget, KPI, dashboard sheets).

**Acceptance criteria for the whole story** (this is what the app promises her):

1. Every soum recorded once, in one place, visible consistently on every page.
2. Cash reports reflect actual cash; plan reports reflect the frozen plan; the two
   are never silently mixed.
3. A number shown red is actionable, and a number shown green is safe to ignore.
4. Closing a period makes history immutable.
5. She can do all of this without editing the database by hand.

The findings below are the places where the current implementation breaks one of
these five promises.

---

## Part 2 — Findings: conflicts, illogical logic, and suggested solutions

Severity: 🔴 wrong numbers reach the officer · 🟠 workflow contradiction / task
impossible · 🟡 polish, i18n, hardening.

### Summary table

> **Fix pass applied 2026-07-15** — see the *Status* column. ✅ = fixed in code,
> 🔶 = partially fixed, ⬜ = open (needs a product decision or a bigger change).

| # | Sev | Area | One-liner | Status |
|---|-----|------|-----------|--------|
| A1 | 🔴 | Revenue types | `mizan_monthly` / `yakuniy_hisob` income vanishes from cash flow and is counted as **expense** in the payment summary | ✅ `INCOME_TX_TYPES` in `models/base.py`, used by all aggregations |
| A2 | 🔴 | Cashflow page | Top tiles and monthly table on the same page use two different income definitions and won't reconcile | ✅ tiles now computed from the monthly series itself |
| A3 | 🔴 | Budget | Projects without a frozen plan fall back to plan = fact → always "within budget" | ✅ "Reja yo'q" status, excluded from plan totals |
| A4 | 🔴 | Plan vs fact basis | Frozen `planned_cost` is risk-inclusive, fact labor cost is risk-exclusive → systematic "under budget" illusion | ✅ freeze now stores risk-exclusive `total_cost`; risk lives in `planned_revenue` |
| A5 | 🔴 | Pricing freeze | Freezing a plan **overwrites `contract_amount`** with the quote | ✅ signed contract & existing hours estimate preserved |
| A6 | 🔴 | Loans | Loan flows never touch cash flow → runway wrong; interest payments close principal | ✅ loan flows in `get_cash_flow_by_month`; principal-only balance & auto-close; asosiy/foiz selector |
| A7 | 🔴 | FX | "Current rate" is the manual `usd_rate` setting; fallback reads a UZS figure as USD | ✅ `get_current_usd_rate()` from `exchange_rates`; fallback now converts UZS→USD |
| A8 | 🔴 | Tx edit modal | Editing amount/paid bypasses derived-field logic (`amount_usd`, `exchange_rate`, `status`) | ✅ `update_transaction()` re-derives; modal status read-only |
| A9 | 🔴 | Capacity metric | "Booked capacity" is historical worked hours vs a 43-month theoretical pool | ⬜ needs a product decision on the horizon (see detail) |
| B1 | 🟠 | Accounting form | Cannot record an unpaid invoice (`paid > 0` required) | ✅ amount + paid + deadline fields; `paid=0` allowed |
| B2 | 🟠 | Statuses | Three status vocabularies; free-text status input; filter commented out | ✅ migration to paid/partial/pending; filter re-enabled; labels translated |
| B3 | 🟠 | External tiles | Income (cash), expense (accrual), "remainder" (AR+AP mixed) on one row | ✅ split into AR ("Undirilmagan tushum") and AP ("To'lanmagan xarajat") tiles |
| B4 | 🟠 | Loans UX | Loans page read-only; no way to record interest as interest | 🔶 interest selector added (Accounting); Loans page actions still open |
| B5 | 🟠 | Settings | Dead settings knobs; numeric parse can 500 | 🔶 parse guarded, debug print removed; dead keys still rendered |
| B6 | 🟠 | Equipment | Depreciation never ends | ✅ stops after `purchase_date + lifespan_months` (undated rows unchanged) |
| B7 | 🟠 | Dividends | Blank "paid" silently means "paid in full"; decorator misuse | ✅ blank = 0 (pending); role check moved into the route |
| C1 | 🟡 | UI text | Dashboard formula box contradicts the code; title says v4.0 | 🔶 burn-rate text + AI docstring fixed; version branding untouched |
| C2 | 🟡 | i18n | Sidebar items and tiles hardcoded in Uzbek | 🔶 cashflow tiles/labels translated; sidebar items still open |
| C3 | 🟡 | ref_id | `PRJ-HHMMSS` collides across days | ✅ date-scoped `PRJ-YYYYMMDD-HHMMSS` (same for MZ/DIV) |
| C4 | 🟡 | Security | Default admin password and hardcoded fallback `SECRET_KEY` | ⬜ open |

---

### A1 — Two revenue tx_types are second-class citizens 🔴

**Where:**
[`models/transactions.py:9`](models/transactions.py#L9) (`get_cash_flow_by_month`),
[`models/transactions.py:50-51`](models/transactions.py#L50-L51) (`get_payment_summary`),
[`models/projects.py:71-75`](models/projects.py#L71-L75) (project income),
[`models/dashboard.py:78`](models/dashboard.py#L78) (AR aging),
[`controllers/external_bp.py:86`](controllers/external_bp.py#L86) (page tiles).

**Problem.** The schema and the seeded `tx_types` define three external *income*
types: `tushum`, `mizan_monthly`, `yakuniy_hisob`. But every aggregation hardcodes
`tx_type = 'tushum'` as the only income:

- `get_cash_flow_by_month` counts only `tushum` as income — money recorded as
  `mizan_monthly` or `yakuniy_hisob` simply disappears from the cash-flow income
  column (and from the running balance → runway).
- `get_payment_summary` goes further: `tx_type != 'tushum'` is treated as
  **expense**, so a monthly MIZAN fee received is *subtracted* from net balance.
- Project income, AR aging, and the External page tiles ignore these types too.

**Failure scenario.** Dilnoza records a 50 mln UZS `mizan_monthly` receipt. Cashflow
income doesn't move, the payment-summary "JAMI XARAJAT" grows by 50 mln, net balance
drops by 50 mln, and runway shortens. The same soum is income on no page and expense
on one.

**Solution.** Define income membership in one place and derive it from data, not
literals — e.g. treat all external tx_types whose category direction is `in`
(`tx_types` already has `direction`; `transaction_categories` has `in/out`) as
income. Practical minimum: a module-level constant
`INCOME_TX_TYPES = ('tushum', 'mizan_monthly', 'yakuniy_hisob')` used by
`get_cash_flow_by_month`, `get_payment_summary`, `calculate_project_cost`,
`get_ar_aging`, and the External tiles. Longer term, add an `is_income` flag to
`tx_types` so admin-created types behave correctly automatically.

### A2 — The Cashflow page disagrees with itself 🔴

**Where:** [`templates/cashflow.html:14-26`](templates/cashflow.html#L14-L26) (tiles
from `get_payment_summary`) vs the monthly table (from `get_cash_flow_by_month`).

**Problem.** Beyond A1, the two data sources differ structurally: the tile "JAMI
TUSHUM"/"SOF BALANS" comes from the payment-type summary (all `paid > 0` rows,
income = `tushum` only, everything else expense), while the table's running balance
counts only `tushum` income and only `outsourcing`/`material` as external expense.
Any external row of another type makes "SOF BALANS" (tiles) ≠ final "Balans"
(table) — on the same screen.

**Solution.** Compute both views from one classification function (see A1). Add an
assertion-style reconciliation in `test_*`: final running balance must equal
payment-summary net.

### A3 — No plan? Then you're magically "on budget" 🔴

**Where:** [`controllers/projects_bp.py:64-68`](controllers/projects_bp.py#L64-L68).

**Problem.** When a project has no frozen plan (`p_hrs == 0 and p_cost == 0`), the
budget page substitutes the *actual* figures as the plan (`p_cost = f_cost`,
`p_out = f_out`). Variance becomes ~0% and the project shows "Chegarada"
(borderline) forever. A CFO scanning /budget cannot distinguish "on plan" from
"never planned".

**Solution.** Don't fabricate a plan. Render a distinct status (e.g. "Reja yo'q" /
no-plan badge, grey row, excluded from totals) and link to the Pricing page to
freeze one. The `/plan` page already handles the "none" state correctly — reuse
that pattern.

### A4 — Plan cost and fact cost are on different bases (and `mizan_cost` is two things) 🔴

**Where:** [`models/pricing.py:63`](models/pricing.py#L63)
(`mizan_cost = total_cost * risk_coeff`),
[`controllers/pricing_bp.py:69-76`](controllers/pricing_bp.py#L69-L76) (freeze writes
`planned_cost = result['mizan_cost']`), vs
[`models/projects.py:137`](models/projects.py#L137) (`mizan_cost = total_cost`, risk
never applied) and the budget/milestone fact side.

**Problem.** CLAUDE.md's rule is "risk coefficient is NOT applied to cost". The
pricing engine, however, returns `mizan_cost` *with* risk baked in, and the freeze
stores that as `planned_cost` (and per-milestone `planned_cost`). Fact-side labor
cost is risk-exclusive. Consequences:

1. Budget and milestone pages systematically show fact labor below plan by exactly
   the risk margin (10–30%), so "Byudjet ichida" is partly an artifact.
2. The same key `mizan_cost` means risk-inclusive in `pricing_estimate` and
   risk-exclusive in `calculate_project_cost` — a trap for every future feature
   (the milestones module had to document this in its docstring).

**Solution.** Pick one meaning. Recommended: store the risk-*exclusive* labor cost
in `planned_cost` (plan vs fact then compares like with like), and keep the risk
uplift only in `planned_revenue` / contract figures where it belongs. Rename the
pricing key to `risked_cost` (keeping `total_cost` as the pure cost) so the two
modules can't be confused again. If the risk-inclusive plan is intentional as a
"budget with contingency", then say so in the UI: label the plan column
"Reja (risk bilan)" and add the contingency line separately instead of letting it
masquerade as expected cost.

### A5 — Freezing a plan overwrites the signed contract amount 🔴

**Where:** [`controllers/pricing_bp.py:69-76`](controllers/pricing_bp.py#L69-L76).

**Problem.** The freeze does `UPDATE projects SET … contract_amount=?` with
`target_contract`. If the project already has a real signed `contract_amount`
(entered when the project was created, and used by earned revenue, deferred revenue,
and dashboards), re-running a pricing estimate silently replaces the legal contract
value with a quote. Also `estimated_total_hours` is overwritten, which changes the
earned-revenue completion ratio retroactively.

**Solution.** Only write `contract_amount` when it is currently 0/NULL; otherwise
leave it and show a diff warning ("quote 480 mln vs signed contract 450 mln").
`planned_revenue` already preserves the quote — that's the right home for it.

### A6 — Loans live outside the cash economy 🔴

**Where:** [`models/transactions.py`](models/transactions.py) (no loan flows),
[`controllers/accounting_bp.py:286-305`](controllers/accounting_bp.py#L286-L305)
(payment handling), [`models/loans.py:16`](models/loans.py#L16) (remaining balance).

**Problem.** Dividends were integrated into cash flow as a real outflow, but loans
were not, in either direction:

1. Borrowing 500 mln UZS (`olgan`) does not increase the running balance; repaying
   it does not decrease it. For a firm that finances payroll with debt, the
   running balance — and therefore **runway** on the dashboard — is simply wrong.
2. The Accounting quick form for loan payments never sends `payment_type`, so every
   payment is recorded as `asosiy` (principal). And the auto-close logic
   (`paid_total >= total_amount`) sums *all* payments, so recording interest
   (if entered via the API with type `foiz`) still counts toward closing the
   principal. A loan can be marked `yopilgan` (closed) purely by paying interest.

**Solution.**
- Include loan flows in `get_cash_flow_by_month` (issue: +in for `olgan` / −out for
  `bergan`; payments: the reverse), or auto-create paired `transactions` rows when a
  loan/payment is saved — one source of truth is better than a third parallel table
  query.
- Add a principal/interest selector to the payment form; compute remaining balance
  and auto-close from `payment_type='asosiy'` only. (Interest accrual itself is
  already flagged as gap #2 in CALCULATIONS_AUDIT.md.)

### A7 — "Current" USD rate is whatever the settings row says 🔴

**Where:** [`models/projects.py:18`](models/projects.py#L18) and
[`models/dashboard.py`](models/dashboard.py) use `get_setting('usd_rate')`;
historical lookups use the `exchange_rates` table
([`models/base.py:27`](models/base.py#L27)).

**Problem.** The app has a proper dated rate table (with a CBU fetch button on the
Settings page), yet FX gain/loss and every UZS↔USD display conversion use the manual
`usd_rate` setting as "today's rate". If the admin adds today's rate to the table
but forgets the setting (two separate forms on the same page), all FX P&L is
computed against a stale rate. Additionally,
[`models/projects.py:29`](models/projects.py#L29) falls back to `paid` as the USD
amount when `amount_usd` is empty — but for rows written by the current accounting
form, `paid` is stored in **UZS**, so one malformed row inflates FX gain/loss by a
factor of ~12,000.

**Solution.** Make `get_current_usd_rate()` = latest `exchange_rates` row, falling
back to the setting; use it everywhere `usd_rate` is read for conversion. Drop the
`paid` fallback in `calculate_fx_gain_loss` (or gate it to rows imported from the
legacy tables, e.g. `WHERE amount_usd IS NULL AND created_at < migration_date`);
skip rows without a trustworthy USD amount instead of guessing.

### A8 — The row-edit modal bypasses every derivation rule 🔴

**Where:** [`templates/_tx_modal.html`](templates/_tx_modal.html) →
[`controllers/api_bp.py:31-50`](controllers/api_bp.py#L31-L50) →
[`models/base.py:150`](models/base.py#L150) (`update_record`).

**Problem.** On insert, the Accounting form computes `amount_usd`, `exchange_rate`,
UZS conversion for USD entries, and `status` from paid-vs-amount. The edit modal
writes raw form fields through the generic updater, so:

- changing `paid` never updates `status` (an invoice can stay "Kutilmoqda" after
  full payment → still counted in AR aging → false red on the dashboard);
- changing `amount` or `currency` never recomputes `amount_usd`/`exchange_rate`
  (FX gain/loss silently drifts, see A7);
- for a USD transaction the modal shows the UZS-stored `amount` next to a currency
  dropdown saying "USD" — inviting the user to retype the USD figure into a UZS
  field;
- `status` is a free-text `<input>` — anything can be typed (see B2).

**Solution.** Add a thin `update_transaction(id, data)` service in
`models/transactions.py` that re-derives `status`, `amount_usd`, `exchange_rate`
(and UZS amounts on currency change) before delegating to `update_record`; point
`/api/update/transactions/...` at it. In the modal, make status a `<select>` of the
canonical statuses and show amounts in the transaction currency.

### A9 — "Capacity" compares the past to a theoretical pool 🔴

**Where:** [`models/dashboard.py:49-70`](models/dashboard.py#L49-L70).

**Problem.** `total_capacity = current production staff × available hours ×
kpi_months (43)` while `booked_capacity` is the *historical* sum of all hours ever
logged to active projects. Booked hours are work already done, not commitments;
staff count is today's, hours span years. The tile turns red at 85% regardless of
whether the team has any free time next month. It fails acceptance criterion 3
(red must be actionable). Same 43-month issue as CALCULATIONS_AUDIT.md #6, but this
one drives a colored CFO tile.

**Solution.** Make it forward-looking: booked = remaining planned hours
(`projects.estimated_total_hours − actual hours`, or milestone `planned_hours` of
non-done milestones); capacity = staff × available_hours × a short horizon (e.g.
next 3 months, configurable). If a rolling utilization figure is also wanted, label
it "O'rtacha yuklanish (43 oy)" so nobody reads it as free capacity.

---

### B1 — You cannot record an invoice that isn't paid yet 🟠

**Where:** [`controllers/accounting_bp.py:114`](controllers/accounting_bp.py#L114)
(`if paid_val > 0 and tx_type:`), and line 87 (hidden `amount_val` defaults to
`paid_val`).

**Problem.** The AR-aging dashboard is built on `paid < amount` rows, but the only
data-entry form in the app refuses to save a transaction with `paid = 0`, and the
committed-amount field is hidden (amount is forced equal to paid). The entire AR
feature can only be fed by Excel import or by editing rows afterward through the
modal. This directly contradicts Scene 2 of the user story.

**Solution.** In the external branch, accept `amount_val > 0 or paid_val > 0`;
expose the amount ("Hisob-faktura summasi") and paid ("To'landi") fields side by
side, defaulting paid to 0 with the deadline field alongside. Status derivation
already handles the rest (`Kutilmoqda` when unpaid).

### B2 — Three status vocabularies, a free-text field, and a disabled filter 🟠

**Where:** schema default `pending` ([`models/base.py:493`](models/base.py#L493));
inserts write `To'langan` / `Kutilmoqda`
([`controllers/accounting_bp.py:123`](controllers/accounting_bp.py#L123)) and
`paid`; legacy imports carry `pending/partial/paid`; the modal has status as
free text ([`templates/_tx_modal.html:78`](templates/_tx_modal.html#L78)); and the
External page's status filter is commented out
([`templates/external.html:48-53`](templates/external.html#L48-L53)) — presumably
because the mixed values made it useless.

**Problem.** No query can rely on status. AR aging avoids it (uses `paid < amount`),
but the UI shows the raw values to the user, mixing English codes and Uzbek labels
in one column, and the officer cannot filter by status at all.

**Solution.** Canonicalize to `pending / partial / paid` in the DB with a one-time
migration (`To'langan → paid`, `Kutilmoqda → pending`), translate for display via
`t()` like every other label, make the modal field a `<select>`, and re-enable the
filter. Better yet: since status is fully derivable from `paid` vs `amount`,
consider computing it on read and dropping stored status inconsistencies entirely.

### B3 — One tile row, three accounting bases 🟠

**Where:** [`controllers/external_bp.py:84-94`](controllers/external_bp.py#L84-L94),
[`templates/external.html:15-20`](templates/external.html#L15-L20).

**Problem.** On the External page: income = `paid` (cash), expense = `amount`
(commitment), "remainder" = `amount − paid` summed over *all* types — which adds
uncollected client invoices (an asset) to unpaid outsourcing bills (a liability)
into one meaningless number.

**Solution.** Split the remainder tile: "Undirilmagan tushum" (AR: income types,
`amount − paid`) and "To'lanmagan xarajat" (AP: expense types). Label the expense
tile to match its basis, or switch it to `paid` for consistency with the income
tile.

### B4 — The Loans page is a dead end 🟠

**Where:** [`controllers/loans_bp.py`](controllers/loans_bp.py),
[`templates/loans.html`](templates/loans.html);
entry lives in [`controllers/accounting_bp.py:268-305`](controllers/accounting_bp.py#L268-L305).

**Problem.** The page where the officer *looks at* loans has no way to add a loan or
a payment — those live in a section of the Accounting mega-form — and no way to
record interest vs principal anywhere in the UI (see A6.2). Every other financial
page (dividends, milestones, categories) is self-contained.

**Solution.** Add "payment" and "new loan" actions on the Loans page (reuse the
Accounting handlers or a small modal), including a principal/interest radio.

### B5 — Settings that do nothing (and one that crashes) 🟠

**Where:** [`models/base.py:554-571`](models/base.py#L554-L571) (seed),
[`controllers/settings_bp.py:16-24`](controllers/settings_bp.py#L16-L24),
[`controllers/staff_bp.py:77`](controllers/staff_bp.py#L77).

**Problem.** The Settings page renders every `settings` row as an editable knob, but
`work_hours` (176), `work_days`, `utilization_rate`, `export_risk`, `kgs_rate`, and
`default_risk` are read nowhere in the current calculation chain, and
`effective_hours` is only *displayed* on the Hourly page header
(`eff_hrs=get_setting('effective_hours')`) while the actual rate formula computes
available hours from holidays/leave — so the header can show 152 while the math
uses 150.9. An admin "tuning" these expects the numbers to change; they don't.
Also `float(request.form[key])` with no guard → one stray comma = 500 error, and
`fetch_rate` leaves a debug `print(url)`.

**Solution.** Remove dead keys from the seed (or add an `is_active`/`hidden` flag
and stop rendering them); on the Hourly page display the *computed*
`available_hours` instead of the orphan setting. Wrap the settings POST in
try/except with a warning flash; delete the print.

### B6 — Assets depreciate forever 🟠

**Where:** [`models/equipment.py`](models/equipment.py) (all three functions ignore
`purchase_date`/`lifespan_months` expiry).

**Problem.** Monthly depreciation is `price / lifespan_months` for every
`is_active=1` row, regardless of age. A laptop bought in 2022 with a 36-month life
should be fully depreciated by mid-2025, but it still adds to its owner's monthly
cost — and through cost rates, to every project quoted — until someone remembers to
untick it. The cost rate is therefore permanently biased upward as the firm ages.

**Solution.** In the monthly-cost queries, include a row only while
`date(purchase_date, '+' || lifespan_months || ' months') > date('now')` (fall back
to including rows with NULL purchase_date, as today). Optionally show "fully
depreciated" badges on the Equipment page instead of silently dropping them.

### B7 — Dividends: blank "paid" means "paid in full", and a decorator misfire 🟠

**Where:** [`controllers/dividends_bp.py:47-48`](controllers/dividends_bp.py#L47-L48)
(`paid = amount` when the field is blank), and line 40
(`@require_role` applied to the helper `_handle_post`, not the route).

**Problem.** (1) An officer recording a *declared but unpaid* dividend must
explicitly type `0`; leaving the natural-looking blank field marks it fully paid and
immediately deducts it from the running cash balance. That's the opposite of the
transactions convention, where amounts default to unpaid. (2) `require_role` on the
helper means a `viewer` who crafts a POST gets a hard 403 page mid-render instead of
the form simply not existing — it works, but only because `login_required` happens
to run first on the page route; the decorator pattern belongs on routes.

**Solution.** Default blank `paid` to 0 and derive `status='pending'` (the model
already handles that); show amount/paid as two explicit fields like the tx form.
Move the role check into `dividends_page` (`if request.method == 'POST' and not
_can_edit(): abort(403)`) and drop the decorator from the helper.

---

### C1 — UI text contradicts the code 🟡

- Dashboard formula box says *"Burn Rate = faqat doimiy xarajatlar (maosh +
  overhead + amortizatsiya)"* ([`templates/dashboard.html:139`](templates/dashboard.html#L139)),
  but the code deliberately **excludes** equipment amortization from burn rate
  (fix #5 in CALCULATIONS_AUDIT.md). Update the text to match (`maosh + overhead`,
  amortization in total operating cost).
- `<title>` and sidebar still say "v4.0" ([`templates/base.html:6`](templates/base.html#L6))
  while the schema/docs are at v6.
- The AI module docstring says default model `gemma4:12b` while the code default is
  `gemma4:e4b` ([`models/ai_agent.py:18,30`](models/ai_agent.py#L30)).

### C2 — i18n gaps in the sidebar and dashboard 🟡

**Where:** [`templates/base.html:39,50-65`](templates/base.html#L39),
[`templates/dashboard.html:93`](templates/dashboard.html#L93),
[`templates/cashflow.html:15-31`](templates/cashflow.html#L15-L31).

"Oldi-Berdi", "Davrlar", "Kategoriyalar", "Kontragentlar", "Qidiruv Jadvallari",
"Moslashtirish", "Foydalanuvchilar", "Chiqish", the dashboard's "Qarz qoldig'i"
tile, and the Cashflow tiles/table headers are hardcoded Uzbek — switching to EN/RU
leaves them untranslated, unlike every `t()`-based label around them. Add keys to
`translations.py` (there is already a completeness test pattern in
`test_loans_i18n.py` to extend).

### C3 — `ref_id` collisions 🟡

**Where:** [`controllers/accounting_bp.py:130,143`](controllers/accounting_bp.py#L130),
[`models/dividends.py:55`](models/dividends.py#L55).

`PRJ-HHMMSS` / `MZ-HHMMSS` / `DIV-HHMMSS` repeat every day and collide within the
same second (e.g. Excel import of many rows). If ref_id is meant as a human
reference, include the date and a sequence: `PRJ-20260714-0042` (a counter per day
from the DB), or just use the row id.

### C4 — Local-app security hygiene 🟡

Default seeded credentials `admin / mizan2024`
([`models/base.py:252-257`](models/base.py#L252-L257)) and the hardcoded fallback
`SECRET_KEY` ([`app.py:19`](app.py#L19)) are acceptable for an offline LAN tool but
worth hardening cheaply: force a password change on first login (a `must_change_pw`
flag), and generate a per-install secret into a git-ignored file instead of a
constant. The LAN allowlist (`172.` matches all of `172.0.0.0/8`, not just the
private `172.16–31` range) could also be tightened.

---

## Fix pass — 2026-07-15

Applied in the order originally proposed; all three standalone test scripts pass
(`test_loan_summary.py`, `test_loans_i18n.py`, `test_milestones.py` — 54 checks)
plus a smoke render of the 12 touched pages and a round-trip check of
`update_transaction` and the cash-flow running balance.

Key code anchors:

- `INCOME_TX_TYPES` / `INCOME_TX_SQL` — [`models/base.py`](models/base.py)
- `update_transaction()` — [`models/transactions.py`](models/transactions.py)
- loan flows in cash flow — [`models/transactions.py`](models/transactions.py) `get_cash_flow_by_month`
- `get_current_usd_rate()` — [`models/base.py`](models/base.py)
- status migration (`To'langan → paid`, `Kutilmoqda → pending`) — [`models/base.py`](models/base.py) migrations list
- budget "Reja yo'q" handling — [`controllers/projects_bp.py`](controllers/projects_bp.py)
- pricing freeze guards — [`controllers/pricing_bp.py`](controllers/pricing_bp.py)

### Still open

1. **A9 — capacity metric**: decide the horizon (e.g. next 3 months of remaining
   planned hours) before changing the tile; today's figure should at least be
   relabelled as a rolling historical utilization.
2. **B4 — loans page actions**: add "new loan" / "payment" forms on `/loans`
   (handlers already exist in `accounting_bp`).
3. **B5 — dead settings keys**: remove or hide `work_hours`, `work_days`,
   `utilization_rate`, `export_risk`, `kgs_rate`, `default_risk`,
   `effective_hours` from the Settings page, or wire them into the calculations.
4. **C2 — sidebar i18n**: move Oldi-Berdi / Davrlar / Kategoriyalar /
   Kontragentlar / Qidiruv Jadvallari / Moslashtirish / Foydalanuvchilar /
   Chiqish and the dashboard "Qarz qoldig'i" tile into `translations.py`.
5. **C4 — security hygiene**: force password change on first login; per-install
   secret key; tighten the `172.` LAN prefix to `172.16–31`.
