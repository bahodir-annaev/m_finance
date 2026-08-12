# MIZAN Finance v4/v5 — Complete Explainer

**MIZAN Finance** is a local, offline financial management system built specifically for an architecture firm. It runs on Python/Flask with a SQLite database — no external services, no cloud dependency, no build step. Everything is rendered server-side with Jinja2 templates.

---

## Table of Contents

1. [Application Architecture](#1-application-architecture)
2. [Database Schema](#2-database-schema)
3. [The Cost Model — How Rates Are Calculated](#3-the-cost-model)
4. [Data Entry Points](#4-data-entry-points)
5. [Report Views](#5-report-views)
6. [Import & Export](#6-import--export)
7. [Supporting Systems](#7-supporting-systems)

---

## 1. Application Architecture

The app was refactored from a single-file monolith (`app.py` + `database.py`) into a proper MVC structure in v5:

```
app.py                ← Flask factory: registers blueprints, starts server
controllers/          ← One blueprint per feature; HTTP routing only
    accounting_bp.py  ← Universal data entry (POST handler)
    api_bp.py         ← JSON CRUD API + language switcher
    cashflow_bp.py    ← Cash flow view
    dashboard_bp.py   ← Dashboard
    equipment_bp.py   ← Equipment & overhead view
    external_bp.py    ← External transactions view
    import_export_bp.py ← NIZAM & Excel import, xlsx export
    internal_bp.py    ← Internal transactions view
    loans_bp.py       ← Oldi-Berdi loans view
    pricing_bp.py     ← Pricing engine (GET + POST)
    projects_bp.py    ← Projects list + Budget plan vs actual
    settings_bp.py    ← Global settings
    staff_bp.py       ← Staff, Hourly rates, KPI
models/               ← All DB queries, calculations, business logic
    base.py           ← Connection, schema init, CRUD helpers, audit log
    staff.py          ← Salary, hourly rate, KPI, proportional allocation
    projects.py       ← Project cost, FX gain/loss, earned revenue
    transactions.py   ← Cash flow by month, payment summary
    dashboard.py      ← Burn rate, runway, capacity, AR aging
    equipment.py      ← Depreciation queries
    loans.py          ← Loan and payment queries
    pricing.py        ← Risk scoring and pricing estimate
    import_export.py  ← Excel analyze and import
templates/            ← Jinja2 HTML templates (one per page)
translations.py       ← i18n strings for uz / en / ru
utils.py              ← render_page(), t(), fmt()
import_nizam.py       ← NIZAM CRM staff mapping + seed data
database.py           ← Legacy file (still present for backward compat)
mizan_finance.db      ← SQLite database (project root)
```

### Key design principles

- **Controllers are thin.** They read form data, call a model function, and pass results to a template. No calculations happen in controllers.
- **Models are pure.** All financial logic, DB queries, and calculations live in `models/`. Controllers never write raw SQL.
- **Single database connection per request.** `get_db()` opens a connection; callers close it in a `finally` block.
- **Schema is self-migrating.** `init_db()` runs on every startup and adds new columns via `ALTER TABLE` wrapped in `try/except`, so upgrades never break existing databases.
- **Soft deletes.** `delete_record()` sets `is_active=0` if the table has that column; otherwise it hard-deletes. Tables with `is_active` include `personal_equipment`, `general_equipment`, `personal_licenses`, `overhead`, `transactions`.

---

## 2. Database Schema

The database has three generations of tables:

- **Core tables** — existed since v4, carry the main data
- **v5.1 dimension tables** — audit log, categories, fiscal periods, counterparties, tags
- **v5.2 snapshot/allocation tables** — period cost freezing and earned revenue tracking
- **v5.3** — project phases

### 2.1 Core Tables

#### `settings` — global parameters

The single source of truth for all rates and coefficients. Every calculation reads from here.

| Key | Default | Meaning |
|-----|---------|---------|
| `usd_rate` | 12 850 | Current UZS per 1 USD |
| `tax_rate` | 0.12 | Income tax rate (12%) |
| `social_rate` | 0.12 | Social insurance rate (12%) |
| `billing_multiplier` | 2.0 | Markup applied to cost rate to get billing rate |
| `effective_hours` | 152 | Available billable hours per month (annual calc) |
| `target_margin` | 0.50 | Target profit margin used in pricing engine |
| `holidays_per_year` | 14 | Public holidays, used in hours calculation |
| `avg_leave_days` | 20 | Average leave + sick days per year |
| `utilization_rate` | 0.75 | Target billable utilization |
| `kpi_months` | 43 | KPI window (months of project history) |

#### `exchange_rates` — historical UZS/USD rates

Stores a date-indexed timeline of UZS/USD rates. Lookups use `WHERE date <= ? ORDER BY date DESC LIMIT 1` — always finds the most recent rate on or before a given date. Used to calculate FX gain/loss on USD transactions.

#### `staff` — employee register

The most critical classification is `staff_type`:

- **`production`** — architects, BIM engineers, visualizers. Their hours are billed to projects. Their per-person cost is the basis of the hourly rate.
- **`admin`** — HR, PM, IT director. Do not bill to projects. Their full cost is redistributed proportionally to all production staff.

Each staff member gets a `staff_code`: `MZ-001` for production, `MA-001` for admin.

Related: `salary_history` — a timeline of salary changes. Each row has `start_date` and `end_date` (NULL = currently active). When a salary is updated, the old row gets `end_date = today` and a new row is inserted. The current salary is always the row where `end_date IS NULL`.

#### `personal_equipment` — per-staff hardware

Computers, monitors, peripherals assigned to individual staff members. Monthly depreciation = `price / lifespan_months`. Feeds directly into that person's hourly cost rate.

#### `general_equipment` — shared office hardware

Printers, servers, plotters, etc. Monthly depreciation = `quantity × price / lifespan_months`. Total is distributed proportionally across all production staff by billable hours share.

#### `personal_licenses` — per-staff software

Revit, 3ds Max, Adobe, etc. Monthly cost = `annual_cost / 12`. Feeds into that person's hourly cost rate.

#### `overhead` — fixed monthly firm costs

Rent, utilities, internet, food, transport, MS365, etc. Each row is a named cost item with a fixed `monthly_amount`. Total is distributed proportionally across production staff by billable hours share.

#### `projects` — project register

Each project has:
- Basic info: `name`, `client`, `responsible`, `status` (active/completed/paused), `currency`
- Contract: `contract_amount`, `estimated_total_hours`
- Risk: `risk_coefficient` (1.0–1.45), `risk_score`
- **Frozen plan columns**: `planned_hours`, `planned_cost`, `planned_revenue`, `planned_outsourcing`, `planned_material`, `plan_frozen_date`

The frozen plan columns are written by the Pricing Engine when a plan is "frozen" to a project. They become the PLAN side of the Budget report and never change again, while actual figures continue to update.

#### `project_hours` — time entries

One row per staff member per project per month (`UNIQUE(project_id, staff_id, period)`). Typically imported from NIZAM CRM. Can also be entered manually. The `period` is a `YYYY-MM` string.

In v5.1, snapshot columns were added: `applied_cost_rate`, `applied_billing_rate`, `applied_cost_amount`, `applied_billing_amount`, `rate_snapshot_at`. These are written when a fiscal period is closed, freezing the rates that were used so recalculations don't change historical costs.

#### `loans` — bilateral debt ledger

Tracks both directions of lending:
- `olgan` (= "took") — the firm borrowed money from someone
- `bergan` (= "gave") — the firm lent money to someone

Status: `ochiq` (open) or `yopilgan` (closed). A loan auto-closes when payments sum to `total_amount`.

Related: `loan_payments` — individual repayment entries (principal or interest).

### 2.2 The Unified `transactions` Table (v5.2 migration)

In v5, the old separate `external_transactions` and `internal_transactions` tables were merged into a single `transactions` table with a `direction` column:

| `direction` | `tx_type` | Meaning |
|-------------|-----------|---------|
| `external` | `tushum` | Client payment received (income) |
| `external` | `outsourcing` | Paid to subcontractors (project expense) |
| `external` | `material` | Purchased materials (project expense) |
| `external` | `mizan_monthly` | Monthly management fee from client |
| `external` | `yakuniy_hisob` | Final settlement / reconciliation |
| `internal` | `overhead_payment` | Rent, utilities, etc. paid |
| `internal` | `salary_disbursement` | Actual salary cash disbursed |
| `internal` | `overhead` | Generic internal expense category |

**Important**: only `paid` (actual cash received/disbursed) counts in cash flow calculations. `amount` is the invoiced/committed amount. The difference is accounts receivable or payable.

On startup, `init_db()` runs a one-time migration that copies all rows from `external_transactions` → `transactions` (direction='external') and `internal_transactions` → `transactions` (direction='internal'), then renames the old tables to `_legacy_external_transactions` and `_legacy_internal_transactions`.

Related: `transaction_lines` — allows a single transaction to be split across multiple projects (multi-line invoices). Each line has `project_id`, `amount`, optionally `project_phase_id` and `category_id`.

### 2.3 v5.1 Dimension Tables

#### `transaction_categories` — hierarchical category tree

Pre-seeded with:
- `revenue` (parent)
  - `tushum` — client payment
  - `mizan_monthly` — monthly fee
  - `yakuniy_hisob` — final settlement
- `direct_cost` (parent, affects_project_cost=1)
  - `outsourcing`
  - `material`
- `indirect_cost` (parent)
  - `salary_disbursement`
  - `overhead_payment`

#### `audit_log` — change history

Every `update_record()` and `delete_record()` call writes to this table, recording the old and new value of each changed field. Also used to log import events (`snapshot`, `import` actions).

#### `fiscal_periods` — period lifecycle

Each accounting period (month) can be in state: `open` → `soft_closed` → `hard_closed`. Closing a period triggers:
1. `snapshot_period_allocations()` — freezes cost rate breakdown per staff for that period
2. `snapshot_hours_rates()` — writes `applied_cost_amount` / `applied_billing_amount` onto `project_hours` rows
3. Updates `fiscal_periods.status`

This means costs for closed periods are frozen and won't change even if salaries or overhead are later updated.

#### `counterparties` — client/vendor registry

Named registry of external parties with `counterparty_type` (client, vendor, subcontractor, etc.). `counterparty_aliases` stores alternate spellings for fuzzy-matching on import.

#### `tags` + `entity_tags` — flexible tagging

Tags can be applied to any entity (project, transaction, staff member, etc.) via the `entity_tags` junction table.

### 2.4 v5.2 Snapshot Tables

#### `period_allocations` — frozen rate snapshot per staff per period

When a period is closed, one row is written per active production staff member, capturing every component of their cost rate at that moment: `gross_salary`, `tax_amount`, `admin_share_amount`, `overhead_share_amount`, `general_equipment_share_amount`, `personal_equipment_amount`, `personal_licenses_amount`, `cost_rate`, `billing_rate`, `available_hours`.

#### `project_earned_revenue_snapshots` — EV per project per period

Tracks earned revenue (contract × completion ratio) at each period close, enabling period-over-period revenue recognition analysis.

### 2.5 v5.3 Project Phases

#### `project_phases` — sub-project breakdown

Each project can have named phases with their own `planned_hours`, `planned_cost`, `planned_revenue`, `start_date`, `end_date`, `completion_percent`. Phase codes must be unique per project. `project_hours` rows can reference a `phase_id`.

---

## 3. The Cost Model

Understanding this section is essential for any calculation-related work.

### 3.1 Available hours

```
yearly_work_days  = 365 − 104 (weekends) − holidays_per_year
net_work_days     = yearly_work_days − avg_leave_days
monthly_work_days = net_work_days / 12
available_hours   = monthly_work_days × 8
```

Default result: ~151–152 hours/month. This replaces the old 176 h/month (which assumed no holidays or leave).

### 3.2 Hourly rate formula (per production staff member)

```
gross            = base_salary + premium
tax              = gross × tax_rate          (12%)
social           = gross × social_rate       (12%)
admin_share      = admin_total × (my_hours / total_period_hours)
personal_eq      = Σ(price / lifespan_months) for my equipment
personal_lic     = Σ(annual_cost / 12) for my licenses
general_eq_share = gen_eq_total × (my_hours / total_period_hours)
overhead_share   = overhead_total × (my_hours / total_period_hours)

total_monthly = gross + tax + social + admin_share
              + personal_eq + personal_lic + general_eq_share + overhead_share

cost_rate    = total_monthly / available_hours
billing_rate = cost_rate × billing_multiplier   (default 2.0×)
```

The 2.0× markup absorbs both utilization loss (not all hours are billable) and the target profit margin in a single multiplier — no separate margin is added anywhere else.

### 3.3 Proportional allocation of shared costs

Admin salaries, overhead, and general equipment are shared costs. They are allocated to each production staff member **proportional to their share of billable hours in the most recent imported period**:

```
share_for_X = total_cost × (X's hours in period / all production hours in period)
```

Fallback chain if period data is missing: cumulative all-time hours → equal head-count split.

### 3.4 Project cost calculation

```python
for each staff_member in project_hours:
    if period is closed and snapshot_exists:
        cost    = applied_cost_amount   # frozen historical value
        billing = applied_billing_amount
    else:
        cost    = hours × cost_rate     # live calculation
        billing = hours × billing_rate

mizan_cost    = Σ cost                  # true cost to the firm
mizan_billing = Σ billing               # what should be charged
mizan_price   = mizan_cost × risk_coefficient  # quoted price (not used in P&L)

income  = Σ paid on tushum transactions
profit  = income − (mizan_cost + outsourcing + material)
margin% = profit / income × 100
```

The `risk_coefficient` is **never** applied to `mizan_cost`. It only raises the quoted price in the Pricing Engine. P&L always uses `mizan_cost`.

### 3.5 Pricing Engine formula

Pre-sales tool for quoting new projects:

**Risk score** is calculated from four factors:

| Factor | Low (1) | Medium (2) | High (3) | Weight |
|--------|---------|-----------|---------|--------|
| Deadline | >8 months | 4–8 months | <4 months | 30% |
| Client type | Regular | New | Government | 25% |
| Complexity | Simple | Medium | High | 25% |
| Currency | UZS | USD | Other | 20% |

```
weighted_score = d×0.30 + client×0.25 + complexity×0.25 + currency×0.20
risk_coeff     = 1 + (weighted_score − 1) × 0.15

mizan_cost         = Σ(planned_hours × cost_rate) × risk_coeff
minimum_contract   = mizan_cost + outsourcing + materials
target_contract    = minimum_contract / (1 − target_margin)   → guarantees margin
premium_contract   = target_contract × 1.2
```

### 3.6 Earned revenue

```
completion_ratio = min(actual_hours / estimated_total_hours, 1.0)
earned_revenue   = contract_amount × completion_ratio
deferred_revenue = contract_amount − earned_revenue
```

### 3.7 FX gain/loss

For USD-denominated transactions:
```
fx_gain_loss = Σ(paid × (current_usd_rate − rate_at_transaction_date))
```

Positive = UZS weakened since transaction date (firm benefits on USD holdings). Negative = UZS strengthened.

### 3.8 Burn rate and runway

```
burn_rate = all_staff_gross × (1 + tax_rate + social_rate)
          + overhead_total
          + general_equipment_monthly

running_balance = cumulative net of all paid amounts in transactions table

runway_months = running_balance / burn_rate
```

Runway turns red on the dashboard when < 6 months.

---

## 4. Data Entry Points

All data entry is intentionally centralized. There is one primary entry page plus several supporting mechanisms.

### 4.1 Accounting page (`/accounting`) — the universal entry form

The most important page in the app. A single URL handles eight distinct entry sections, selected via a hidden `section` field in each form:

#### `section = transaction` — add a financial transaction

- **direction = `tashqi`** (external): creates an `external`-direction transaction in `transactions`. Links to a project. Calculates USD equivalent from current rate. Sets `status` to `paid` / `partial` / `pending` via `derive_status()`. (It does **not** write a `transaction_lines` row — that table is dead, see `models/base.py:705`.)
- **direction = `kirish`** (internal): creates an `internal`-direction transaction. Used for salary disbursements, rent payments, etc.

Once saved, a partially-paid transaction can receive **follow-up payments** from the `+ To'lov` button on `/external` and `/internal`. Each one is a new row in `transactions` linked back via `parent_tx_id`, carrying its own date and payment type, so cash flow reports it in the month the money actually moved.

Fields: date, direction, tx_type, description, amount (UZS), paid (UZS), contract amount, responsible, paid_to, doc_id, payment_type (bank/cash), deadline, notes, currency, project.

#### `section = project` — create a new project

Fields: name, client, responsible, contract amount, currency, start date, end date, estimated hours, risk coefficient, status.

#### `section = staff` — add staff or update salary

- `staff_action = add_staff`: inserts into `staff` then creates a `salary_history` entry
- `staff_action = update_salary`: closes the current salary entry (`end_date = today`) and opens a new one

Fields: name, role, department, staff_type, base_salary, premium.

#### `section = equipment` — add equipment

- `eq_type = personal`: inserts into `personal_equipment` linked to a staff member
- `eq_type = general`: inserts into `general_equipment`

Fields: name, price, lifespan_months, staff_id (personal only), quantity (general only).

#### `section = license` — add a software license

Inserts into `personal_licenses`. Fields: name, annual_cost, staff_id, license_type (named/floating).

#### `section = overhead` — add or update an overhead item

- `oh_action = add`: inserts into `overhead` (upserts by name)
- `oh_action = update`: updates `monthly_amount` for an existing overhead item

#### `section = rate` — record USD exchange rate

Inserts into `exchange_rates` with a specific date. Allows maintaining a historical rate timeline.

#### `section = loan` — add a loan or a repayment

- `loan_action = add_loan`: inserts into `loans` with type (olgan/bergan), counterparty, amount, currency, interest rate, dates
- `loan_action = add_payment`: inserts into `loan_payments`. Auto-closes the parent loan if cumulative payments reach `total_amount`.

### 4.2 Pricing Engine (`/pricing`, POST) — pre-sales quote

Lets you plan a project before signing. Input: hours per production staff member + four risk factors + outsourcing/materials budget. Output: minimum, target, and premium contract amounts in UZS and USD.

The result can be **frozen to an existing project** via the "Save to project" dropdown. This writes the estimated hours, contract amount, risk coefficient, and all planned budget figures into the `projects` row (`plan_frozen_date` is set). These become the PLAN baseline for the Budget report.

### 4.3 Settings page (`/settings`, POST)

Bulk-updates the `settings` table. All form fields named `setting_<key>` are written as `FLOAT` values. Also displays the full `exchange_rates` history (read-only in this view; rates are entered via Accounting).

### 4.4 Generic CRUD API (`/api/update/<table>/<id>`, `/api/delete/<table>/<id>`)

Used by inline-edit UI elements across all views. The update endpoint accepts form data, coerces numeric fields, and calls `update_record()` which validates the table against an allowlist, writes the changes, and logs every field change to `audit_log`. The delete endpoint calls `delete_record()` which soft-deletes (sets `is_active=0`) or hard-deletes depending on the table.

Allowed for read: `transactions`, `staff`, `salary_history`, `personal_equipment`, `general_equipment`, `personal_licenses`, `overhead`, `exchange_rates`, `loans`, `loan_payments`, `counterparties`, `tags`.

Allowed for delete: `transactions`, `personal_equipment`, `general_equipment`, `personal_licenses`, `overhead`, `exchange_rates`.

### 4.5 NIZAM CRM import (`/import`, file upload)

Accepts an `.xlsx` file exported from the NIZAM CRM system. The file contains a sheet named "table" with columns: employee name, project name, and hours per month. The importer:

1. Maps NIZAM employee names to internal staff via `NIZAM_MAP` in `import_nizam.py`
2. Skips non-billable project names (internal tasks, portfolio work, etc.) listed in `NON_BILLABLE`
3. Upserts rows into `project_hours` — one row per (project, staff, period). Duplicate imports for the same period are safely ignored (`INSERT OR IGNORE` on the unique constraint).
4. Returns a report: projects processed, hour entries written, skipped, employees mapped, and unmatched names.

The `period` parameter on the upload form allows filtering to a specific month or importing all periods.

---

## 5. Report Views

All views are read-only (GET requests only, except Settings and Pricing). They read live data from the database and display computed metrics.

### 5.1 Dashboard (`/`)

The main overview page. Cards show:

| Metric | Source |
|--------|--------|
| Total billable projects | `COUNT(*) FROM projects WHERE is_billable=1` |
| Total billable hours | `SUM(project_hours.hours)` for billable projects |
| Production / admin staff count | `COUNT(*) FROM staff WHERE staff_type=?` |
| Avg cost rate / billing rate | Average across all production staff |
| Total MIZAN cost (UZS + USD) | `Σ calculate_project_cost()` for all projects |
| Total income | `Σ paid` on `tushum` transactions |
| Gross profit & margin % | `income − mizan_cost` |
| Total FX gain/loss | `Σ fx_gain_loss` across all projects |
| Total earned revenue | `Σ earned_revenue` across all projects |
| Monthly overhead | `SUM(overhead.monthly_amount)` |
| Utilization % | `total_hours / (prod_count × kpi_months × available_hours) × 100` |
| Burn rate | Monthly fixed costs: all staff + overhead + equipment depreciation |
| Runway (months) | `running_cash_balance / burn_rate` (red < 6 months) |
| Capacity % | `booked_hours / (prod_count × available_hours × kpi_months) × 100` (red > 85%) |
| AR total / overdue | Outstanding `amount − paid` on unpaid `tushum` invoices |
| Breakeven revenue | `monthly_fixed / avg_margin_pct` |
| Revenue per employee | `total_income / prod_count` |
| Profit per hour | `gross_profit / total_hours` |
| Top 10 projects | By total hours with cost/income/profit |
| Loan summary | Open loans: taken vs given, overdue count |

### 5.2 Staff page (`/staff`)

Table of all staff (production and admin) with salary breakdown:

- Base salary, premium, gross
- Tax amount (12% of gross)
- Social contribution (12% of gross)
- Total employer cost

Filterable by department and staff_type. Does not show hourly rates (that's the Hourly page).

### 5.3 Hourly Rates page (`/hourly`)

Shows the full cost rate breakdown for each **production** staff member. For each person, displays every component that feeds into their cost rate:

| Column | Content |
|--------|---------|
| Salary + Premium | Current gross |
| Tax + JSSM | 24% total statutory cost |
| Admin share | Proportional share of all admin salaries |
| Tech + Licenses | Personal equipment depreciation + license monthly cost |
| General Equipment | Proportional share of shared equipment depreciation |
| Overhead share | Proportional share of rent, utilities, etc. |
| Total monthly | Sum of all above |
| Cost Rate | Total monthly / available_hours (UZS/hour) |
| Billing Rate | Cost Rate × markup (UZS/hour) |
| Billing USD | Billing Rate / usd_rate |

Also shows the current `effective_hours` and `billing_multiplier` settings.

### 5.4 KPI page (`/kpi`)

Per-person productivity report for all production staff, computed over the full `kpi_months` window:

| Column | Content |
|--------|---------|
| Staff code | MZ-001 format |
| Projects | Count of distinct projects worked on |
| Hours | Total billable hours (all time) |
| Avg hours/project | Hours / projects |
| Cost Rate | Current cost rate |
| Billing Rate | Current billing rate |
| Cost value | hours × cost_rate (total investment in this person's time) |
| Revenue value | hours × billing_rate (what those hours should bill) |
| Revenue (USD) | Revenue value / usd_rate |
| Utilization % | hours / (kpi_months × available_hours) × 100 |
| Rating | 1–5 stars based on utilization (≥85%=5, ≥70%=4, ≥55%=3, ≥35%=2, else 1) |

### 5.5 Projects page (`/projects`)

List of all billable projects with:
- Total hours (all time), number of staff who worked on it
- Status (active/completed/paused)
- Contract amount and currency

Filterable by status.

### 5.6 Budget page (`/budget`)

Plan vs actual comparison for every billable project that has logged hours.

For each project:

| Column group | Source |
|---|---|
| PLAN: hours, cost, outsourcing, total | Frozen `planned_*` columns on `projects` (or estimated hours if no plan frozen) |
| FACT: hours, cost, outsourcing, total | Live from `calculate_project_cost()` |
| Delta hours, cost, total | FACT − PLAN |
| Delta % | (FACT − PLAN) / PLAN × 100 |
| Budget status | Oshgan (over, >5%), Byudjet ichida (under, <−10%), Chegarada (borderline) |

Footer row shows portfolio totals and aggregate variance %.

### 5.7 Pricing Engine page (`/pricing`)

Pre-sales calculator. Not a report per se — it's an interactive form that shows results after POST. Input:
- Hours per production staff member
- Risk factors: deadline, client type, complexity, currency

Output table shows:
- Per-staff: planned hours, cost rate, billing rate, cost amount, billing amount
- Portfolio: total hours, base MIZAN cost, risk-adjusted cost, outsourcing, materials
- Three price points: Minimum (break-even), Target (ensures margin), Premium (×1.2)
- All amounts in UZS and USD
- Current risk coefficient

Can save the result as a frozen plan on an existing project.

### 5.8 Cash Flow page (`/cashflow`)

Monthly cash flow waterfall, aggregated from the `transactions` table:

| Column | Calculation |
|--------|------------|
| Month | `strftime('%Y-%m', date)` |
| Income | `SUM(paid)` where `tx_type='tushum'` |
| External expenses | `SUM(paid)` for outsourcing + material transactions |
| Internal expenses | `SUM(paid)` for all internal transactions |
| Net cash flow | Income − total expenses |
| Running balance | Cumulative sum of net cash flow |

Also shows a payment type summary (bank vs cash) with income/expense/net per type.

### 5.9 External Transactions page (`/external`)

Full ledger of all `direction='external'` transactions. Columns: date, type, description, project, client, responsible, amount, paid, currency, status, doc_id.

Summary cards at the top: total income received, total outsourcing+material committed, total pending (invoiced but not paid).

Filterable by project, tx_type, and status.

### 5.10 Internal Transactions page (`/internal`)

Full ledger of all `direction='internal'` transactions — salary disbursements, rent payments, overhead expenses. Columns: date, type, description, amount, paid.

Summary cards: total amount committed, total paid, total pending.

Filterable by category (tx_type) and responsible.

### 5.11 Loans page (`/loans`)

Oldi-Berdi (lending/borrowing) ledger split into two sections:

**Olgan (firm borrowed):**
- List of open loans with counterparty, amount, currency, issue date, due date, remaining balance, paid %
- Overdue flag if `due_date < today` and status still `ochiq`

**Bergan (firm lent):**
- Same structure for loans the firm has given out

**Summary cards:**
- Total borrowed (open), remaining to repay
- Total lent (open), remaining to collect
- Count of overdue loans

### 5.12 Equipment page (`/equipment`)

Four sections on one page:

1. **Personal equipment** — per-staff hardware with monthly depreciation. Total depreciation shown.
2. **General equipment** — shared hardware with monthly depreciation. Total shown.
3. **Software licenses** — per-staff licenses with monthly amortized cost. Total shown.
4. **Overhead items** — monthly fixed costs. Total shown.

This page is informational; editing is done via the Accounting page or the CRUD API.

### 5.13 Settings page (`/settings`)

Editable table of all `settings` rows with current values, labels, and units. Submitting the form updates all settings at once.

Also shows the complete `exchange_rates` history as a read-only table.

---

## 6. Import & Export

### 6.1 NIZAM CRM import (`/import`)

Imports hours from the NIZAM CRM time-tracking system. The NIZAM file is an Excel workbook where each row is a work entry with employee name, project name, and hours.

**Mapping flow:**
1. `NIZAM_MAP` translates NIZAM names (all-caps) to internal staff names and fills in role/department/staff_type
2. `NON_BILLABLE` list filters out internal/administrative projects
3. `import_nizam_file()` upserts into `project_hours` with `source='nizam'`
4. Unknown names are collected into `unmatched_names` and shown as a warning

### 6.2 Excel transaction import (`/api/import-excel/analyze`, POST)

Analyzes any Excel file for importable transaction data:
1. Scans each sheet, reads the first 5 rows to find the header
2. Matches column names against multilingual keyword patterns (`_COL_PATTERNS` supports Uzbek, English, and Russian column headers)
3. Auto-detects sheet type (external, internal, budget, hourly, kpi) by keyword presence
4. Imports matched rows into the unified `transactions` table

### 6.3 Excel export (`/api/export/xlsx`)

Generates a multi-sheet Excel workbook with brown-header styling:

| Sheet | Content |
|-------|---------|
| `09-Dashboard` | Dashboard KPI summary |
| `03-Maosh` | Full staff salary list with tax/social breakdown |
| `07-Soat narxi` | Hourly rate breakdown per production staff |
| `08-Byudjet` | Project budget: hours, MIZAN cost, billing, outsourcing, income, profit, margin |
| `10-KPI` | Staff KPI: hours, projects, rates, utilization |

The `?sheet=` query parameter activates a specific sheet as default in the downloaded file.

---

## 7. Supporting Systems

### 7.1 Internationalization

Three languages: **Uzbek** (default), **English**, **Russian**. Active language is stored in the `mizan_lang` cookie (1-year expiry). Language switch is via `/set-lang/<lang>`, which redirects back to the referring page.

`t(key, *args)` in templates fetches the current language string from `translations.py`. Format placeholders use `{}`.

### 7.2 Number formatting

`fmt(n, decimals=0)` formats numbers with comma separators. Used in every template and in export. Example: `fmt(12500000)` → `"12,500,000"`.

### 7.3 Audit log

Every call to `update_record()` records the old and new value of each changed field. Every call to `delete_record()` records the action. Import events are logged with action=`import` or action=`snapshot`. The `audit_log` table is currently write-only (no UI to browse it), but it provides a full change history for debugging.

### 7.4 Fiscal period management

`fiscal_periods` tracks accounting months. The functions `snapshot_period_allocations()` and `snapshot_hours_rates()` can be called (via `close_fiscal_period()`) to freeze all cost rates for a month. Once frozen:

- `is_period_closed(period_code)` returns `True`
- `calculate_project_cost()` uses the frozen `applied_cost_amount` from `project_hours` instead of recalculating live rates
- This means past profitability figures do not change when salaries are updated

This is v5.1 functionality — the UI to trigger period closing is not yet implemented in the controllers (it would be a POST to a `/periods/close` endpoint).

### 7.5 `import_nizam.py` — seed data

On first run (via `ensure_staff_exists()` and `seed_equipment_and_licenses()`), this module:
- Creates all staff records from `NIZAM_MAP`
- Sets default salaries from `DEFAULT_SALARIES`
- Seeds personal equipment (computers, monitors) from `PERSONAL_EQUIPMENT`
- Seeds personal licenses (Revit, 3ds Max, etc.) from `PERSONAL_LICENSES`
- Seeds general equipment (servers, plotters, etc.) from `GENERAL_EQUIPMENT`

This gives a clean, fully-configured database on first launch without manual data entry.

---

## Quick Reference: URL Map

| URL | Method | Purpose |
|-----|--------|---------|
| `/` | GET | Dashboard |
| `/accounting` | GET/POST | Universal data entry |
| `/staff` | GET | Staff salary list |
| `/hourly` | GET | Hourly rate breakdown |
| `/kpi` | GET | Staff KPI |
| `/projects` | GET | Project list |
| `/budget` | GET | Plan vs actual by project |
| `/pricing` | GET/POST | Pricing engine |
| `/cashflow` | GET | Monthly cash flow |
| `/external` | GET | External transactions ledger |
| `/internal` | GET | Internal transactions ledger |
| `/loans` | GET | Loan ledger |
| `/equipment` | GET | Equipment, licenses, overhead |
| `/settings` | GET/POST | Global settings |
| `/import` | GET/POST | NIZAM + Excel import |
| `/export` | GET | Export landing page |
| `/api/export/xlsx` | GET | Download Excel report |
| `/api/import-excel/analyze` | POST | Analyze + import Excel transactions |
| `/api/get/<table>/<id>` | GET | Read a record (JSON) |
| `/api/update/<table>/<id>` | POST | Update a record (JSON) |
| `/api/delete/<table>/<id>` | POST | Delete a record (JSON) |
| `/set-lang/<lang>` | GET | Switch UI language (uz/en/ru) |
