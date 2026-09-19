# CLAUDE.md — MIZAN Finance v5

Guidance for Claude Code when working in `mizan5/`.

> Built from [`../NEW_APP_PLAN.md`](../NEW_APP_PLAN.md). Account codes come from
> [`../CHART_OF_ACCOUNTS.md`](../CHART_OF_ACCOUNTS.md) (НСБУ №21). The v4 app in the
> parent directory still runs independently — different database, different port.
>
> **End-user documentation:** [`USER_MANUAL.md`](USER_MANUAL.md) (English) and
> [`USER_MANUAL.ru.md`](USER_MANUAL.ru.md) (Russian) — same 39-section structure, covering
> every screen, the month-end procedure, administration and the v4 migration. Keep them in
> step when a page, field, setting or error key changes.
>
> **What v4 has that v5 does not:** [`V4_FEATURE_GAP.md`](V4_FEATURE_GAP.md) — every unported
> v4 feature, what was replaced on purpose, and a suggested port order.

## What this is

MIZAN Finance v5 — a local offline financial app for an architecture firm, rebuilt on
**double-entry bookkeeping**. Stack unchanged from v4: Python 3.10+, Flask, SQLite,
openpyxl. No JS framework, no build step, server-rendered.

**The primary goal is unchanged: calculate each production staff member's man-hour cost
to the firm accurately.** The ledger exists to make the *inputs* to that calculation
(actual salaries, actual overhead, actual depreciation) auditable instead of
hand-maintained.

## Running

```bash
python app.py                      # auto-detects port 5000, opens a browser
PORT=5177 MIZAN_NO_BROWSER=1 python app.py
```

First run prints a generated admin password once, or set `MIZAN_ADMIN_PASSWORD`.
Database: `mizan5.db` (override with `MIZAN5_DB`).

## Tests

```bash
python run_tests.py          # everything — 826 assertions
python test_ledger.py        # balance enforcement, periods, reversal
python test_documents.py     # posting rules, allocation, void, advances
python test_rates.py         # man-hour cost engine + GOLDEN v4 PARITY
python test_pricing.py       # price ladder linearity, milestones, budget
python test_reports.py       # P&L, balance sheet, cash flow, VAT, close
python test_direction.py     # internal/external/financing ladder + conservation
python test_depreciation.py  # schedule, posting, and the no-double-count rule
python test_overhead.py      # the same rule for salary/licenses + pool arithmetic
python test_asset_classes.py # asset lives, account pairs, the name classifier
python test_bank_accounts.py # bank-account subconto: rule, balances, storno, backfill
python test_v4_features.py   # the v4 port: plan lens, NIZAM, KPI, quote, loans, paging, periods
python test_i18n.py          # translation completeness
python test_app.py           # every page renders + full HTTP flows
python test_migration.py     # replays the real v4 DB, reconciles cash and AR
```

Standalone scripts, not pytest. Each builds its own temp database; none needs a server.

## The one rule

**Nothing writes `journal_entries` / `journal_lines` except `ledger.post_entry()`,
and only `posting.py`, the manual-entry form and the period close call it.**

Two deliberate exceptions touch an *analytic* on existing lines and never an amount,
account, date or entry: `bank_accounts.assign_unassigned_lines()` (bank account) and
`milestones.assign_document_to_milestone()` (phase). Both are documented where they live.

`post_entry()` enforces two invariants and refuses otherwise:
1. Σdebit == Σcredit within `BALANCE_EPSILON` (1 UZS — currency conversion rounds).
2. No posting into a `soft_closed` / `hard_closed` fiscal period.

The ledger tables are deliberately absent from `_ALLOWED_TABLES`, so generic CRUD
cannot reach them.

## Architecture

```
app.py            factory + startup; blueprints listed in BLUEPRINT_MODULES
auth.py           Flask-Login, require_role(), require_level('manager'|'admin')
utils.py          render_page(), t(), fmt(), parse_float(), form_rows()
translations.py   uz (reference) / en / ru — test_i18n.py fails on a gap
models/
  base.py         get_db(), init_db() (whole schema), CRUD, audit, periods, seeds
  ledger.py       post_entry(), reverse_entry(), balances, trial balance
  posting.py      document -> journal lines. The ONLY builder of postings
  documents.py    document CRUD, numbering, allocations, post/void lifecycle
  bank_accounts.py the firm's own bank accounts — the 1C «Банковские счета» subconto
  payroll.py      payroll runs and remittances
  loans.py        loan register; balance per loan from its own documents; auto-close
  import_nizam.py NIZAM CRM timesheet import (matches staff, never creates them)
  staff.py        THE MAN-HOUR COST ENGINE
  pricing.py      risk scoring + the price ladder
  milestones.py   project_phases, actuals, schedule generation
  projects.py     profitability, per-project FX, budget under both lenses, top projects
  reports.py      P&L, balance sheet, cash flow (+ by payment method), aging, VAT, FX,
                  period close, dashboard
  depreciation.py monthly depreciation of the equipment register
  import_v4.py    one-shot migration from the v4 database
controllers/      17 blueprints, route handlers only — no SQL
templates/        base.html + _macros.html + one per page
```

Controllers import from `models`, never `models.<submodule>`.

## Documents → postings

Users enter *documents*; the ledger is derived.

```
modal form -> documents + document_lines (draft)
           -> posting.py rule builds balanced lines
           -> ledger.post_entry() validates and writes
           -> everything else is a SELECT over journal_lines
```

Lifecycle: `draft` (editable, no ledger effect) → `posted` (immutable, carries
`entry_id`) → `void` (a reversing entry exists; the pair nets to zero everywhere).
A posted document is never edited — void it and enter a replacement.

| Document | Debit | Credit |
|---|---|---|
| sales_invoice | 4010 total | 9030 net (per line, per project) + 6410.1 VAT |
| purchase_invoice | line account (2010 if project-tagged, else 9420) + 4410 VAT | 6010 total |
| cash_in | 5110/5010/5210 | 4010 (allocated) / 6310 (advance) / 9390 (no counterparty) / line accounts |
| cash_out | 6010 (allocated) / 4310 / 9430 (no counterparty) / line accounts | 5110/5010/5210 |
| payroll | 2010 (production) or **9420.2** (admin): gross+social; 6710: PIT | 6710 gross, 6420.1 PIT, 6520 social |
| dividend | 8710 | 6610 |
| loan | cash or 5820 | 6820/7820 or cash |
| opening | each balance vs **0000** — a correct set leaves 0000 at zero | |
| manual | user lines, balance validated | |

Two routines post without a document, following the same pattern: FX revaluation
(`reports.post_fx_revaluation`) and monthly depreciation
(`depreciation.post_period_depreciation`, **Dr 9420.1 / Cr the asset class's own
contra account**). Both leave
`journal_entries.document_id` NULL — the `documents.doc_type` CHECK has no value
for them and adding one would need a full SQLite table rebuild.

Posting rules reference accounts by **purpose** via `account_map`, never by hardcoded
code, so re-pointing a purpose is a settings change.

**Settlement is not a `paid` column.** An invoice's outstanding balance is its total
minus `payment_allocations`. One payment can settle several invoices; an unallocated
remainder becomes an advance on 6310/4310.

## Bank accounts (`models/bank_accounts.py`)

One ledger code (5110, 5210) is several real 20-digit accounts at the bank. This is
modelled the 1C way — as an **analytic**, not as sub-account codes: `accounts.subconto =
'bank_account'` marks a ledger account as subdivided, `bank_accounts` is the register, and
`journal_lines.bank_account_id` / `documents.bank_account_id` carry the dimension. The
ledger code stays single, so `account_map`, `cash_account_ids()`, `direction.py` and the
trial balance are untouched; per-bank balances are `balances_by_analytic(...,
'bank_account_id')`, and Σ banks + unassigned == the ledger balance by construction.

The rule, in `ledger._check_bank_accounts()` inside `post_entry()`: an account with
`subconto='bank_account'` **and at least one active bank account** refuses a line without
`bank_account_id` (`bank_account_required`) or with one of another ledger account
(`bank_account_mismatch`). With none registered it posts as before — the rule switches on
per account when the register is filled, so 5010 can be subdivided later from `/accounts`
with no code change. `posting.cash_account_for(conn, doc)` resolves a cash document's
money account **from its bank account** (a USD bank account is registered against 5210)
and checks currency and `is_active`; without one it falls back to `cash_account_purpose()`.

`assign_unassigned_lines()` is the deliberate exception to "nothing writes journal_lines
but post_entry": a one-time backfill that sets the analytic on lines posted before the
register existed. It never touches an amount, account, date or entry.

The new columns are added to existing databases by `base.MIGRATED_COLUMNS` — the only
`ALTER TABLE` path in the app; use it for any further additive column.

## Loans (`models/loans.py`)

The register holds the terms; the balance is `loan_balance()` — the ledger movement on
the loan's principal account restricted to entries whose document carries this
`loan_id`, so two loans with one counterparty do not share a figure. `sync_loan_status()`
closes a loan when that balance reaches zero and at least one disbursement is posted
(an unbooked loan has a zero balance for the wrong reason), and reopens it if a repayment
is voided — `documents_bp.document_void` calls it for any document with a `loan_id`.
Interest goes to 9610/9530 and never counts toward closing.

## Internal / external (`models/direction.py`)

v4 stored `direction` on every transaction: **external** was client- and project-facing
money, **internal** was the firm's own running costs. v5 has no such column and gains none
— direction is **derived from the posting**, per document, and is a reporting lens only.
Putting it into a posting rule, or letting it decide a `cost_pool`, would break the
double-count rule.

The ladder, in order — the order *is* the design:

| # | Test | Result |
|---|---|---|
| 1 | `payroll` document | internal |
| 2 | `loan`/`dividend` document, or a line on a `FINANCING` account | financing |
| 3 | the document settles invoices (`payment_allocations`) | whatever those are |
| 4 | a sales invoice, or a line on `ar`/`revenue`/`advances_received` | external |
| 5 | a line carrying a `project_id` | external |
| 6 | an invoice with a line on `production_cost`/`cogs` | external |
| 7 | otherwise | internal |

Rung 6 is why the order matters. **2010 is debited by two unrelated things** — outsourcing
and materials on a client project, *and* production payroll, which is also where
`import_v4.TX_ACCOUNT_MAP` puts v4's `maosh`/`premiya`. 2010 alone cannot decide. Rung 1
removes payroll first; rung 6 then admits 2010 only on an *invoice*, which is exactly the
shape the migration gives a v4 external cost, while a v4 internal cost arrives as a bare
`cash_out` and falls through to rung 7. That is what makes the derived value reproduce v4's
stored one — `test_migration.py` pins it against the real database.

Unattributed money stays internal: it lands on 9390/9430, and calling a receipt with no
counterparty "client money" would be a claim the ledger does not support.

`financing` is not an invention — v4 also kept loans and dividends out of the split, in
their own tables and their own cash-flow columns. Folding them into internal would misstate
the month.

Surfaces: a filter and badge on the document lists, and the split columns on
`/reports/cashflow` and the dashboard. `inflow`/`outflow`/`net`/`running_balance` stay the
undivided authoritative figures — the split only ever adds detail beside them.

## The man-hour cost engine (`models/staff.py`)

Formula chain, carried over from v4 unchanged:

```
available_hours = (365 - 104 weekends - holidays - leave) / 12 * 8   ~151 h/mo
total_monthly   = gross + tax + social + admin_share
                + personal_equipment + personal_licenses
                + general_equipment_share + overhead_share
cost_rate       = total_monthly / available_hours
billing_rate    = cost_rate * markup (2.0 default)
```

Three v5 changes, each checked against A/E practice (AIA/PSMJ, FAR Part 31 / AASHTO):

1. **Overhead comes from the ledger** — a trailing-window average of posted costs on
   accounts flagged `cost_pool='indirect'` (9410/9420/9430), divided by months actually
   elapsed. `overhead_budget` is the fallback for an empty ledger.
   Switch: `overhead_from_ledger`.
2. **Allocation base defaults to direct labor cost**, not hours — the standard base,
   since a senior hour absorbs more overhead than a junior one. Setting
   `allocation_base_labor_cost=0` restores v4's hours base **exactly**;
   `test_rates.py` pins that parity by running the real v4 engine in a subprocess.
3. **Three benchmarks reported** (never fed back into the arithmetic): overhead rate as
   % of direct labor (industry 150–180%), utilization (75–85%), and the equivalent net
   multiplier on raw labor (2.75–3.25).

`cost_pool` on an account is what keeps salary and tax out of the overhead pool —
the v5 equivalent of v4's `INDIRECT_POOL_EXCLUDED_TX_TYPES`.

### THE DOUBLE-COUNT RULE

> **An account may not carry `cost_pool='indirect'` if the same cost is already
> modeled by a register that `calculate_hourly_rate()` reads.**

This is the sharpest edge in v5. The rate engine charges several costs from
hand-maintained registers, and the ledger books those same costs as expenses.
If such an expense account also sits in the overhead pool, the cost is charged
once from its register and again through `overhead_share`, with no credit
anywhere to offset it. Nothing looks wrong — every rate is simply too high.

Three accounts are children of 9420 for exactly this reason, all flagged
`excluded`, listed together as `base.REGISTER_MODELED_CODES`:

| Account | Register that already charges it | Measured if pooled |
|---|---|---|
| 9420.1 depreciation | `equipment` → `personal_eq` / `general_eq` | +26% on one rate |
| 9420.2 admin salary | `staff` + `salary_history` → `admin_total_cost()` | +13% on every rate |
| 9420.3 licenses | `licenses` → `personal_licenses_monthly()` | grows with the estate |

`posting.assert_not_pooled(purpose, conn)` is the single guard: it resolves a
purpose and raises `PostingError('account_in_pool')` if the account is pooled.
Depreciation, the payroll admin leg and the license path all call it, so
flipping a flag on `/accounts` fails loudly instead of quietly inflating rates.
`init_db()` also re-corrects these three codes from `indirect` on every startup.
`test_depreciation.py` and `test_overhead.py` pin both the effect and the guard,
each with a negative control that flips the flag and asserts the refusal.

The same principle keeps income out of the pool: `ledger_overhead_monthly()`
sums debits **net of credits**, so an unattributed cash receipt falls to 9390
(`other_income`, unpooled) rather than 9430 — booking it to 9430 would let a
client payment *subtract* itself from overhead and lower every rate.

Still open: **capitalization**. Nothing posts to 0150, so equipment bought
through a purchase invoice is expensed to 9420 — into the pool — while the
register depreciates the same asset into the same rates. That is this rule
being broken through the other door. `depreciation_reconciliation()` reports
the gap as `uncapitalized` (register cost − 0150) and `/rates` warns when it is
non-zero, but the fix is to point such a line at 0150 by hand.

### Depreciation

`models/depreciation.py` posts the equipment register monthly so the P&L shows
depreciation and the balance sheet shows accumulated depreciation. It runs
automatically inside `close_period()` — **before** `get_pnl()` is read, or the charge
would never reach 9910 — and on demand from the card on `/periods`.

The entry groups its two sides on **different dimensions, deliberately**: the debit
side by who bears the cost (per employee for `personal` assets, one line for
`general`), because that is what the rate engine allocates on; the credit side by
**asset class**, because each class has its own contra account. Credits are rounded
per class and the largest class absorbs the remainder, so the entry balances to the
tiyin rather than merely inside `post_entry`'s 1 UZS tolerance.

### Asset classes

`equipment` carries two orthogonal columns that must not be merged:

- **`kind`** — `personal` / `general`. WHO BEARS THE COST: one employee's rate, or
  spread across production staff.
- **`asset_class`** — WHAT THE THING IS. Decides its useful life and which pair of
  ledger accounts it uses. A desk and a laptop can both be `personal`.

| Class | Asset | Accum. |   | Class | Asset | Accum. |
|---|---|---|---|---|---|---|
| computer | 0150 | 0250 |   | vehicle | 0160 | 0260 |
| furniture | 0140 | 0240 |   | building | 0120.1 | 0220.1 |
| machinery | 0130 | 0230 |   | other | 0190 | 0290 |

0200 remains the fallback for a class with no `accum_account` configured — wrong in
presentation but not in arithmetic, which beats blocking a period close.

**A class default is a default.** `asset_classes.default_lifespan_months` pre-fills
newly entered assets; `equipment.lifespan_months` on an existing row always wins.
Editing a default must never re-life an asset already on the books — that would move
depreciation already posted and every rate quoted from it. `depreciation_schedule()`
reports rows that differ from their class default as `off_default`, and
`/staff/equipment` badges the count, so the divergence is visible rather than silent.

Every class ships at **36 months**, the single value the whole v4 register was imported
with, so introducing classes moved no number. Setting the real lives is a firm decision
made on `/staff/equipment`. For reference: the Tax Code (Art. 306 §30) ceilings are
20%/yr for computers and peripherals (60 months) and 15%/yr for furniture and office
equipment (80 months) — 36 months is 33.3%/yr, above both. Those are *tax* ceilings;
under НСБУ №5 the book life is the firm's own estimate, which is why it is a setting.

`classify_asset()` guesses a class from an asset's free-text name using ordered
keyword rules — machines before their accessories, since a laptop's name routinely
lists its monitor. It is a suggestion engine: `classify_register()` reports every
assignment, only touches rows still on `other` so a hand correction is never
overwritten, and writes nothing unless `apply=True`. On the real 156-row register it
places every row with no ambiguous matches (98 computer, 58 furniture).

Two predicates deliberately differ and must not be unified: `staff.NOT_EXPIRED`
asks "was this asset alive **as of a date**" — defaulting to today, which is what
the v4 parity test pins — while `depreciation._month_index` asks "was it alive
**in that period**" (right for a posting).

Idempotency is the memo prefix `Amortizatsiya <period>`, the same mechanism
`reopen_period()` uses to find a closing entry. Reopening a period reverses the
depreciation entry too, so a re-close recomputes from a corrected register.

Not covered: **asset disposal** (`is_active=0` mid-life stops the charge and
leaves accumulated < cost), and **capitalization** (see the double-count rule) —
`depreciation_reconciliation()` reports `uncapitalized` per class, so the gap between
each class's register cost and its own asset account is visible.

### The overhead pool and its provenance

`ledger_overhead_monthly()` derives its window and its divisor from **one span**
— `min(window months, history available)` — so they can never disagree. Deriving
them separately puts the cutoff before the first posting whenever the rounding
goes up, dividing real data by more months than produced it.

`overhead_monthly()` returns a `reason`, not just a `source`. Falling back to the
static `overhead_budget` table is silent otherwise, and that table ships seeded
with demo figures, so a stale or empty ledger would produce confident fabricated
rates. `/rates` and `/rates/overhead` warn on any reason but `ledger`/`disabled`.

Allocation shares always sum to exactly 1 on both bases. `billable_hours_by_staff()`
filters `is_active` to match `_production_staff()`; without it a departed
employee's hours sit in the denominator while they receive no share, and that
slice of overhead is charged to nobody.

Rates are frozen per period into `period_allocations`. `snapshot_period_allocations()`
costs the period **as of its last day** — salaries in force then, the overhead
window ending then, assets alive then — via `rate_context(as_of=…)`. Without that,
closing March in August would stamp August's figures onto March, and "history never
moves" would only hold for periods closed on time. `as_of` defaults to None (today)
everywhere else, which is what `/rates` shows and what the parity test pins.

## Pricing (`models/pricing.py`)

Four weighted risk factors (deadline 30%, client 25%, complexity 25%, currency 20%)
→ `risk_coeff = 1 + (score − 1) × 0.15`. Then the single ladder:

```
minimum = labor_cost × risk_coeff + outsourcing + materials
target  = minimum / (1 − target_margin)
premium = target × 1.2
```

It is **linear in its cost inputs**, so `Σ(per-milestone target) == target(Σ costs)`
exactly — pinned by `test_pricing.py`. That identity is why per-milestone figures and
project totals can never disagree.

Risk is applied to **price only, never to cost**. `planned_cost` on both `projects`
and `project_phases` is risk-exclusive labor, so plan and actual compare like with like.

Two tiers of plan, deliberately: `project_phases.planned_*` is the live working plan;
`projects.planned_*` is the frozen baseline, written only by `freeze_project_plan()`.
Drift between them shows as `is_stale`.

Two portfolio lenses, also deliberately, on `/budget` and the project page
(`projects.get_budget_overview`): **whole** — the frozen baseline against lifetime
actuals, the ±5/−10 badge; **to date** — `milestones.get_project_monthly_rollup()`,
the day-weighted milestone plan against actuals for months strictly before the current
one, giving the on/edge/off traffic light (profit drift, schedule, collections). They
measure different things and are never reconciled into one number. The dashboard's
on-course tile is `get_projects_on_course_summary()` over the same overview.

Hours and posted invoices that carry a project but no phase are listed on the project
page as unassigned; `assign_hours_to_milestone()` tags a timesheet month and
`assign_document_to_milestone()` tags a document together with its lines and journal
lines (the phase analytic only — see "The one rule").

The scratch quote on `/pricing` runs `quote_schedule()` with no writes;
`save_quote_to_project()` turns it into milestones with their employee rows, so
`planned_cost` stays derived from `milestone_staff`.

## UI conventions

Every list view: filter bar → table → **[+ New]** button top-right. The button opens a
native `<dialog>`; the row pencil opens the same modal prefilled via a `data-row` JSON
payload. Forms POST normally and the server redirects back — no client-side state.

Line-item grids (invoice lines, payment allocations, payroll, manual entries) post
parallel arrays named `<prefix>_<field>`; `utils.form_rows()` zips them back into dicts.
Vanilla JS only, in `base.html`: modal open/close, grid add/remove, live totals, and the
debit/credit balance note that disables the post button until an entry balances.

Reuse `templates/_macros.html` (`modal`, `status_badge`, `edit_btn`, `text_field`,
`empty_row`) rather than writing new markup.

## Adding a page

1. Translation keys in all three languages in `translations.py` (`test_i18n.py` enforces).
2. Query or calculation in the right `models/` module — never SQL in a controller.
3. Blueprint in `controllers/<name>_bp.py`, added to `BLUEPRINT_MODULES` in `app.py`.
4. Nav link in `templates/base.html`.
5. Template extending `base.html`, importing `_macros.html`.
6. A page entry in `test_app.py`'s `PAGES` list.

## Migration from v4

```bash
python -m models.import_v4 ../mizan_finance.db
```

Replays every v4 transaction as a document with a real posting, so no opening balance is
needed and the two systems' cash must agree. Verified on the real 1,028-transaction
database: cash within 0.01 UZS, receivable exact, trial balance balanced, 2 skipped rows
(both genuinely empty). Skipped rows are always reported, never dropped silently.

The v4 indirect-pool semantics survive through `TX_ACCOUNT_MAP`: `maosh`/`premiya` land
on 2010 (`direct_labor`), `soliq` on 9820 (`excluded`) and `litsenziya` on 9420.3
(`excluded`, because the `licenses` register already charges it), so none of them
pollutes the overhead pool — exactly as v4 excluded them.

## Periods

`fiscal_periods` rows appear on their own when a document is posted into a new month;
`create_fiscal_period()` opens one ahead of time and `delete_fiscal_period()` removes an
open, entry-less one. `reopen_period()` **refuses a hard-closed period** unless called
with `allow_hard=True` — the controller never passes it, so from the UI a hard close is
final and a correction is a reversal in an open month. The tests and scripted
corrections pass the flag; that is what keeps "reopen reverses depreciation too" testable.

`rate_context()` carries `closed_periods` (every soft- or hard-closed code) so the actuals
loops can answer "is this month closed?" per timesheet row without a query each — that
one lookup was most of the budget page's render time.

## Pinned invariants

1. Every journal entry balances (ε = 1 UZS).
2. Trial balance totals zero at any date.
3. AR aging equals open invoice balances by due date.
4. Cash on the dashboard == Σ 5xxx balances == cash-flow closing balance.
5. Rate parity: v5 in hours mode reproduces v4's rates to the cent.
6. Price ladder linearity: Σ per-milestone target == target(Σ costs).
7. Void is a perfect mirror: entry + reversal net to zero on every account.
8. Post-migration: v5 cash and receivable equal v4's computed equivalents.
9. No double count: posting depreciation, payroll or a license invoice does not
   move a single cost rate — the register was already charging it.
10. Allocation shares sum to exactly 1 on both bases; no overhead goes unrecovered.
11. Editing an asset class's default life never changes an asset already recorded.
12. The direction split conserves cash: per month, Σ(external, internal, financing)
    equals the undivided inflow and outflow, and a storno lands in its original's
    direction.
13. Bank-account subdivision changes no ledger figure: Σ(per-bank balances) + unassigned
    == the ledger account's balance, a storno mirrors the analytic, and the backfill
    leaves every balance where it was.
14. Tagging a document to a milestone moves no money: totals, status and the trial
    balance are unchanged; only the phase analytic moves.
15. A loan's balance is its own documents' ledger movement; principal repaid in full
    closes it, interest never does, and a voided repayment reopens it.
16. The payment-method split of cash equals the cash-flow totals for the same range.
17. The NIZAM importer never creates a staff row; a re-import replaces hours in place
    and keeps the phase tag and rate snapshot on the row.

Breaking any of these should fail a test. If one fails, fix the cause — do not relax
the assertion.

## Open decisions (defaults chosen)

- **VAT**: firm assumed VAT-registered at 12% (`vat_rate`); per-line 0 supported.
  If the firm is on упрощённый режим, set `vat_rate` to 0 — schema unchanged.
- **Editing posted documents**: void + re-enter, never in-place.
- **Loan interest**: cash-basis, recognised on payment.
- **Earned revenue / POC**: a report calculation, not posted to the ledger.
