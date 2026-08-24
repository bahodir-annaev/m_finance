# CLAUDE.md — MIZAN Finance v5

Guidance for Claude Code when working in `mizan5/`.

> Built from [`../NEW_APP_PLAN.md`](../NEW_APP_PLAN.md). Account codes come from
> [`../CHART_OF_ACCOUNTS.md`](../CHART_OF_ACCOUNTS.md) (НСБУ №21). The v4 app in the
> parent directory still runs independently — different database, different port.

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
python run_tests.py          # everything — 398 assertions
python test_ledger.py        # balance enforcement, periods, reversal
python test_documents.py     # posting rules, allocation, void, advances
python test_rates.py         # man-hour cost engine + GOLDEN v4 PARITY
python test_pricing.py       # price ladder linearity, milestones, budget
python test_reports.py       # P&L, balance sheet, cash flow, VAT, close
python test_i18n.py          # translation completeness
python test_app.py           # every page renders + full HTTP flows
python test_migration.py     # replays the real v4 DB, reconciles cash and AR
```

Standalone scripts, not pytest. Each builds its own temp database; none needs a server.

## The one rule

**Nothing writes `journal_entries` / `journal_lines` except `ledger.post_entry()`,
and only `posting.py`, the manual-entry form and the period close call it.**

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
  payroll.py      payroll runs and remittances
  staff.py        THE MAN-HOUR COST ENGINE
  pricing.py      risk scoring + the price ladder
  milestones.py   project_phases, actuals, schedule generation
  projects.py     profitability, budget plan-vs-fact
  reports.py      P&L, balance sheet, cash flow, aging, VAT, FX, period close
  import_v4.py    one-shot migration from the v4 database
controllers/      15 blueprints, route handlers only — no SQL
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
| cash_in | 5110/5010/5210 | 4010 (allocated) / 6310 (unallocated = advance) / line accounts |
| cash_out | 6010 (allocated) / 4310 / line accounts | 5110/5010/5210 |
| payroll | 2010 (production) or 9420 (admin): gross+social; 6710: PIT | 6710 gross, 6420.1 PIT, 6520 social |
| dividend | 8710 | 6610 |
| loan | cash or 5820 | 6820/7820 or cash |
| opening | each balance vs **0000** — a correct set leaves 0000 at zero | |
| manual | user lines, balance validated | |

Posting rules reference accounts by **purpose** via `account_map`, never by hardcoded
code, so re-pointing a purpose is a settings change.

**Settlement is not a `paid` column.** An invoice's outstanding balance is its total
minus `payment_allocations`. One payment can settle several invoices; an unallocated
remainder becomes an advance on 6310/4310.

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

Rates are frozen per period into `period_allocations`; closed periods cost from the
snapshot so history never moves when today's salaries change.

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
on 2010 (`direct_labor`) and `soliq` on 9820 (`excluded`), so neither pollutes the
overhead pool — exactly as v4 excluded them.

## Pinned invariants

1. Every journal entry balances (ε = 1 UZS).
2. Trial balance totals zero at any date.
3. AR aging equals open invoice balances by due date.
4. Cash on the dashboard == Σ 5xxx balances == cash-flow closing balance.
5. Rate parity: v5 in hours mode reproduces v4's rates to the cent.
6. Price ladder linearity: Σ per-milestone target == target(Σ costs).
7. Void is a perfect mirror: entry + reversal net to zero on every account.
8. Post-migration: v5 cash and receivable equal v4's computed equivalents.

Breaking any of these should fail a test. If one fails, fix the cause — do not relax
the assertion.

## Open decisions (defaults chosen)

- **VAT**: firm assumed VAT-registered at 12% (`vat_rate`); per-line 0 supported.
  If the firm is on упрощённый режим, set `vat_rate` to 0 — schema unchanged.
- **Editing posted documents**: void + re-enter, never in-place.
- **Loan interest**: cash-basis, recognised on payment.
- **Earned revenue / POC**: a report calculation, not posted to the ledger.
