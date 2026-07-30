# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Full knowledge-transfer reference:** see [`KT.md`](KT.md) — covers every table, model, controller, and business rule in detail.

## What this is

MIZAN Finance v4.0 — a local offline financial management app for an architecture firm. Stack: Python 3.10+, Flask, SQLite, openpyxl. No JS framework; no build step. The entire UI is rendered server-side.

## Running the app

```bash
python app.py
```

Auto-detects port 5000 (falls back to a free port if 5000 is busy), opens browser automatically. Set `PORT` env var to force a specific port.

First-run installs dependencies:
```bash
pip install flask openpyxl
```

Or run `SETUP.bat` (Windows) / `MIZAN_Finance.bat` to launch via batch file.

## Running tests

Tests are standalone scripts, not pytest. Each is self-contained:

```bash
python test_loan_summary.py   # unit tests — no server needed
python test_loans_i18n.py     # translation key completeness — no server needed
python test_excel_per_page.py # integration — requires server on port 5099
```

To run `test_excel_per_page.py`, start the app on port 5099 first:
```bash
PORT=5099 python app.py
python test_excel_per_page.py
```

## Architecture

The app uses a Flask Blueprint architecture with separated concerns:

**`app.py`** — Flask app factory + startup entrypoint (~90 lines). Contains:
- `create_app()` factory that registers all 16 Blueprints
- Local-network-only `before_request` guard (`127.x`, `192.168.x`, `10.x`, `172.x`)
- Startup logic: `init_db()`, `ensure_staff_exists()`, `seed_equipment_and_licenses()`, port detection, browser launch

**`models/`** — all DB logic, split into focused modules:
- `models/base.py` — `get_db()`, `init_db()` (schema + migrations), generic CRUD helpers (`get_record`, `update_record`, `delete_record`, `get_all_records`), audit log, fiscal period helpers
- `models/staff.py` — hourly rate calculation, KPI, proportional cost distribution, period snapshots
- `models/projects.py` — project cost/profitability, FX gain/loss, earned revenue
- `models/equipment.py` — equipment and license monthly depreciation
- `models/pricing.py` — risk scoring, pricing estimate
- `models/transactions.py` — cash flow by month, payment summary
- `models/loans.py` — loan listing, detail, summary
- `models/dashboard.py` — dashboard aggregations, burn rate, capacity, AR aging
- `models/import_export.py` — Excel column matching and import engine
- `models/__init__.py` — re-exports every public symbol for backward compatibility

**`controllers/`** — 16 Flask Blueprints (one per feature area). Route handlers only — no direct SQL. Each imports from `models/` and calls `utils.render_page()`.

**`templates/`** — Jinja2 HTML files. `base.html` provides the sidebar layout. Each page has its own template file.

**`utils.py`** — `fmt(n, decimals)`, `t(key, *args)`, `render_page(page, template, **ctx)`. Every controller uses `render_page()` which injects `t`, `fmt`, `lang` into every template automatically.

**`auth.py`** — Flask-Login setup, `User` model, `require_role(*roles)` decorator.

**`translations.py`** — i18n dictionary. `TRANSLATIONS['uz'|'en'|'ru'][key]` stores all UI strings. Languages: Uzbek (default), English, Russian. Active language stored in cookie `mizan_lang`.

**`import_nizam.py`** — NIZAM CRM integration. Maps NIZAM employee names to internal staff records (`NIZAM_MAP`), seeds default salaries, equipment and licenses on first run.

**`database.py`** — Legacy compatibility shim only. Re-exports from `models.base`. Do not add new code here.

## Key financial model (v4.0)

Understanding the cost model is essential for any calculation-related work:

- **Available hours** = `(365 - 104 weekends - holidays - leave_days) / 12 × 8` (annual basis, ~151 h/month). NOT the old 176 h/month.
- **Cost Rate** = `Total Monthly Cost / Available Hours`
- **Billing Rate** = `Cost Rate × Markup` (markup ≈ 2.0×; absorbs utilization loss and target margin — not added separately)
- **Admin / overhead / general equipment** are distributed proportionally to each production staff member based on their share of current-period billable hours (not head count, not cumulative hours)
- **Project cost** = `Σ(hours × Cost Rate)` — risk coefficient is NOT applied to cost, only to pricing
- **Pricing** = `Cost × Risk` — risk score is calculated from deadline, client type, complexity, currency (4-factor matrix)
- **Cash flow** = actual cash movements only: transactions (external + internal) + dividends + loan flows (issue/repayment); theoretical salary/overhead are NOT added — that would double-count
- **Income classification** = `INCOME_TX_TYPES` in `models/base.py` (`tushum`, `mizan_monthly`, `yakuniy_hisob`). Never hardcode `tx_type='tushum'` as the only income — every aggregation (cash flow, payment summary, project income, AR aging, page tiles) must use this set

## Database

SQLite file: `mizan_finance.db` in the project root. Schema is created/migrated on every startup via `init_db()`. Foreign keys are enabled (`PRAGMA foreign_keys = ON`). `get_db()` returns a connection with `row_factory = sqlite3.Row`.

Core tables: `staff`, `salary_history`, `projects`, `project_hours`, `transactions`, `transaction_lines`, `loans`, `loan_payments`, `personal_equipment`, `general_equipment`, `personal_licenses`, `overhead`, `settings`, `exchange_rates`.

> **v5 migration**: the original `external_transactions` and `internal_transactions` tables were merged into a single `transactions` table with a `direction` column (`'external'` or `'internal'`). The old tables were renamed to `_legacy_external_transactions` and `_legacy_internal_transactions`.

`transactions.tx_type` values: `tushum`, `mizan_monthly`, `yakuniy_hisob` (external income — see `INCOME_TX_TYPES`); `outsourcing`, `material` (external expense); `maosh`, `premiya`, `ijara`, `kommunal`, `soliq`, `ovqat`, `litsenziya`, `malaka`, `overhead` (internal).

`transactions.status` is canonical `pending` / `partial` / `paid`, always derived from `paid` vs `amount` (see `update_transaction()` in `models/transactions.py`); legacy `To'langan`/`Kutilmoqda` values are migrated on startup.

## Adding a new page

1. Add translation keys to all three languages in `translations.py`
2. Create a new Blueprint in `controllers/<name>_bp.py`:
   ```python
   from flask import Blueprint
   from flask_login import login_required
   from utils import render_page
   from models import get_db   # or specific model functions
   bp = Blueprint('<name>', __name__)
   @bp.route('/<path>')
   @login_required
   def page(): ...
       return render_page('<name>', '<name>.html', ...)
   ```
3. Register the Blueprint in `app.py → create_app()` (import + `app.register_blueprint(bp)`)
4. Add the nav link in `templates/base.html` sidebar block
5. Create `templates/<name>.html` extending `base.html`
6. Any new DB queries or calculations go in `models/` (pick the right submodule or create a new one), not in the controller

---

## Detailed business logic

### The two staff types — the foundation of everything

The entire cost model depends on `staff.staff_type`:

- **`production`** — architects, BIM engineers, visualizers. Bill hours to projects. Their per-person cost is the basis for the hourly rate.
- **`admin`** — HR, PM, IT director. Do not bill to projects. Their total cost is redistributed across all production staff proportionally.

### Hourly rate formula chain (`database.py → calculate_hourly_rate`)

For each production staff member, six cost components are summed to get total monthly cost, then divided by available hours:

```
total_monthly = gross_salary
              + tax (12% of gross)
              + social contribution (12% of gross)
              + admin_share           ← share of all admin salaries
              + personal_equipment    ← their computer/monitor monthly depreciation
              + personal_licenses     ← their Revit/3ds Max license (annual ÷ 12)
              + general_equipment     ← share of printer/server depreciation
              + overhead_share        ← share of rent/utilities/food

cost_rate    = total_monthly / available_hours
billing_rate = cost_rate × markup (default 2.0×)
```

`available_hours` ≈ 151 h/month from `(365 − 104 weekends − holidays − leave_days) / 12 × 8`.

The 2.0× markup absorbs utilization loss and the target margin in a single multiplier — no separate margin is added anywhere else.

### Overhead/admin/general-equipment distribution

Shared costs (admin salaries, overhead table, general equipment depreciation) are split across production staff proportionally to each person's share of **billable hours in the most recent imported period**, not by head count:

```
admin_share for person X = admin_total × (X's hours this period / total billable hours this period)
```

Fallback chain if period data is missing: cumulative all-time hours → equal head-count split.

### Database tables

#### `settings` — global parameters

| Column | Type | Description |
|---|---|---|
| `key` | TEXT PK | Parameter name (e.g. `usd_rate`, `tax_rate`, `social_rate`, `billing_multiplier`, `holidays_per_year`, `avg_leave_days`, `target_margin`, `kpi_months`) |
| `value` | REAL NOT NULL | Numeric value |
| `label` | TEXT | Human-readable label shown in UI |
| `unit` | TEXT | Display unit (e.g. `%`, `UZS`) |

#### `exchange_rates` — historical UZS/USD rates

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `date` | TEXT UNIQUE NOT NULL | Date string `YYYY-MM-DD` |
| `rate` | REAL NOT NULL | UZS per 1 USD on that date |

Lookup pattern: `WHERE date <= ? ORDER BY date DESC LIMIT 1` — always finds the most recent rate on or before a given date.

#### `staff` — employee register

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `name` | TEXT UNIQUE NOT NULL | Short display name |
| `full_name` | TEXT | Full legal name |
| `nizam_name` | TEXT | Name as it appears in NIZAM CRM import |
| `role` | TEXT NOT NULL | Job title |
| `department` | TEXT NOT NULL | Department name |
| `staff_type` | TEXT NOT NULL | `production` (bills hours) or `admin` (cost redistributed) |
| `is_active` | INTEGER | `1` = active, `0` = archived |
| `staff_code` | TEXT | Short code — `MZ-` prefix for production, `MA-` for admin (added via migration) |
| `created_at` | TEXT | Row creation timestamp |

#### `salary_history` — salary timeline per staff member

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `staff_id` | INTEGER NOT NULL → `staff.id` | Owner |
| `base_salary` | REAL NOT NULL DEFAULT 0 | Monthly gross base salary (UZS) |
| `premium` | REAL NOT NULL DEFAULT 0 | Monthly premium/bonus on top of base |
| `start_date` | TEXT NOT NULL | Effective from date (`YYYY-MM-DD`) — UNIQUE with staff_id |
| `end_date` | TEXT | Effective until date; `NULL` = current active salary |

#### `personal_equipment` — per-staff computers, monitors, etc.

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `name` | TEXT NOT NULL | Equipment name |
| `staff_id` | INTEGER → `staff.id` | Assigned staff member |
| `price` | REAL NOT NULL | Purchase price (UZS) |
| `lifespan_months` | INTEGER NOT NULL DEFAULT 36 | Depreciation period; monthly cost = price ÷ lifespan_months |
| `purchase_date` | TEXT | Purchase date |
| `is_active` | INTEGER DEFAULT 1 | `0` = retired/sold |

#### `personal_licenses` — per-staff software licenses (Revit, 3ds Max, etc.)

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `name` | TEXT NOT NULL | License/software name |
| `staff_id` | INTEGER → `staff.id` | Assigned staff member |
| `annual_cost` | REAL NOT NULL DEFAULT 0 | Annual license cost (UZS); monthly = annual_cost ÷ 12 |
| `license_type` | TEXT DEFAULT `named` | `named` (tied to one person) or `floating` |
| `is_active` | INTEGER DEFAULT 1 | `0` = expired/cancelled |

#### `general_equipment` — shared office equipment (printers, servers, etc.)

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `name` | TEXT NOT NULL | Equipment name |
| `quantity` | INTEGER NOT NULL DEFAULT 1 | Number of units |
| `price` | REAL NOT NULL | Unit price (UZS) |
| `lifespan_months` | INTEGER NOT NULL DEFAULT 36 | Depreciation period per unit |
| `purchase_date` | TEXT | Purchase date |
| `is_active` | INTEGER DEFAULT 1 | `0` = retired |

Total monthly depreciation = `quantity × price ÷ lifespan_months`, distributed proportionally across production staff.

#### `overhead` — fixed monthly firm costs (rent, utilities, food, etc.)

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `name` | TEXT UNIQUE NOT NULL | Cost item name |
| `monthly_amount` | REAL NOT NULL DEFAULT 0 | Fixed monthly cost (UZS) |
| `is_active` | INTEGER DEFAULT 1 | `0` = inactive |

Distributed proportionally across production staff by billable hours share.

#### `projects` — client project register

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `name` | TEXT UNIQUE NOT NULL | Project name |
| `client` | TEXT | Client name |
| `responsible` | TEXT | Responsible architect/PM |
| `risk_coefficient` | REAL NOT NULL DEFAULT 1.15 | Pricing multiplier from risk score (never applied to cost) |
| `risk_score` | REAL DEFAULT 0 | Weighted risk score (added via migration) |
| `contract_amount` | REAL DEFAULT 0 | Signed contract value |
| `currency` | TEXT DEFAULT `UZS` | Contract currency (`UZS` or `USD`) |
| `start_date` | TEXT | Project start date |
| `end_date` | TEXT | Project deadline |
| `status` | TEXT DEFAULT `active` | `active`, `completed`, `paused` |
| `is_billable` | INTEGER DEFAULT 1 | `0` = internal/non-billable |
| `estimated_total_hours` | REAL DEFAULT 0 | Total planned hours (used for earned-revenue calc) |
| `planned_hours` | REAL DEFAULT 0 | Frozen plan hours (set when plan is frozen) |
| `planned_cost` | REAL DEFAULT 0 | Frozen plan cost (UZS) |
| `planned_revenue` | REAL DEFAULT 0 | Frozen plan revenue (UZS) |
| `planned_outsourcing` | REAL DEFAULT 0 | Frozen plan outsourcing budget |
| `planned_material` | REAL DEFAULT 0 | Frozen plan materials budget |
| `plan_frozen_date` | TEXT | Date when plan was frozen; `NULL` = no frozen plan |
| `created_at` | TEXT | Row creation timestamp |

#### `project_hours` — time entries (imported from NIZAM or entered manually)

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `project_id` | INTEGER NOT NULL → `projects.id` | Project |
| `staff_id` | INTEGER NOT NULL → `staff.id` | Staff member |
| `hours` | REAL NOT NULL DEFAULT 0 | Hours worked |
| `period` | TEXT NOT NULL | Month string `YYYY-MM` |
| `source` | TEXT DEFAULT `nizam` | Import source (`nizam` or `manual`) |
| `imported_at` | TEXT | Import timestamp |

UNIQUE constraint on `(project_id, staff_id, period)` — one row per person per project per month.

#### `transactions` — unified money flows (external + internal)

Replaces the legacy `external_transactions` and `internal_transactions` tables. The `direction` column distinguishes them.

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `direction` | TEXT NOT NULL | `'external'` (client-facing) or `'internal'` (firm expenses) |
| `tx_type` | TEXT NOT NULL | Type code — see values above |
| `date` | TEXT NOT NULL | Transaction date (`YYYY-MM-DD`) |
| `ref_id` | TEXT | Auto-generated reference (`PRJ-HHMMSS` or `MZ-HHMMSS`) |
| `doc_id` | TEXT | Document/contract ID (e.g. `SH-001`) |
| `project_id` | INTEGER → `projects.id` | Linked project (nullable) |
| `counterparty_id` | INTEGER → `counterparties.id` | Structured counterparty FK (optional) |
| `responsible_id` | INTEGER → `staff.id` | Responsible staff FK (optional) |
| `responsible` | TEXT | Responsible person name (free-text, for manual entry) |
| `client` | TEXT | Client / payer name (external transactions) |
| `paid_to` | TEXT | Recipient name (outsourcing, material, salary) |
| `description` | TEXT | Transaction description |
| `notes` | TEXT | Free-text notes |
| `amount` | REAL NOT NULL DEFAULT 0 | Invoiced / committed amount (UZS) |
| `paid` | REAL NOT NULL DEFAULT 0 | Amount actually received/paid |
| `currency` | TEXT NOT NULL DEFAULT `UZS` | Transaction currency |
| `exchange_rate` | REAL | UZS/USD rate at entry time (for FX gain/loss calc) |
| `amount_usd` | REAL | Transaction amount in USD |
| `contract_amount` | REAL DEFAULT 0 | Contract face value (UZS) |
| `contract_currency` | TEXT | Contract currency if different |
| `contract_amount_usd` | REAL | Contract value in USD |
| `payment_type` | TEXT DEFAULT `bank` | `bank`, `naqd`, `karta`, `online`, `ichki` |
| `deadline` | TEXT | Payment deadline (`YYYY-MM-DD`) |
| `status` | TEXT DEFAULT `pending` | `pending`, `partial`, `paid`, `To'langan`, `Kutilmoqda` |
| `supersedes_id` | INTEGER → `transactions.id` | Links to original if this is a correction |
| `superseded_by_id` | INTEGER → `transactions.id` | Points to correction if this was superseded |
| `created_at` | TEXT | Row creation timestamp |
| `updated_at` | TEXT | Last update timestamp |

Only actual cash flows are recorded — no accruals or theoretical costs.

#### `transaction_lines` — line-item detail per transaction

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `transaction_id` | INTEGER NOT NULL → `transactions.id` | Parent transaction |
| `line_no` | INTEGER NOT NULL | Line sequence (UNIQUE with transaction_id) |
| `project_id` | INTEGER → `projects.id` | Project allocation for this line |
| `project_phase_id` | INTEGER → `project_phases.id` | Phase allocation (optional) |
| `category_id` | INTEGER → `transaction_categories.id` | Structured category |
| `amount` | REAL NOT NULL | Line amount |
| `description` | TEXT | Line description |

#### `loans` — bilateral debt ledger

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `loan_type` | TEXT NOT NULL | `olgan` (firm borrowed money) or `bergan` (firm lent money) |
| `counterparty` | TEXT NOT NULL | Lender or borrower name |
| `description` | TEXT | Purpose / notes |
| `total_amount` | REAL NOT NULL DEFAULT 0 | Principal amount |
| `currency` | TEXT DEFAULT `UZS` | `UZS` or `USD` |
| `interest_rate` | REAL DEFAULT 0 | Annual interest rate (%) |
| `issue_date` | TEXT NOT NULL | Date loan was issued |
| `due_date` | TEXT | Repayment deadline |
| `status` | TEXT DEFAULT `ochiq` | `ochiq` (open) or `yopilgan` (closed) |
| `notes` | TEXT | Additional notes |
| `created_at` | TEXT | Row creation timestamp |

#### `loan_payments` — loan repayment entries

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `loan_id` | INTEGER NOT NULL → `loans.id` | Parent loan |
| `date` | TEXT NOT NULL | Payment date (`YYYY-MM-DD`) |
| `amount` | REAL NOT NULL DEFAULT 0 | Amount paid |
| `payment_type` | TEXT DEFAULT `asosiy` | `asosiy` (principal) or `foiz` (interest) |
| `notes` | TEXT | Notes |
| `created_at` | TEXT | Row creation timestamp |

Remaining balance = `loans.total_amount − SUM(principal payments)` — only `payment_type='asosiy'` reduces the balance and counts toward auto-closing; `foiz` (interest) is tracked separately. Loan issues and payments are also real cash flows and feed `get_cash_flow_by_month()`.

### Project profitability calculation (`database.py → calculate_project_cost`)

1. Fetch all `project_hours` for the project, grouped by staff member
2. For each staff member: call `calculate_hourly_rate()` → cost_rate and billing_rate
3. `mizan_cost = Σ(hours × cost_rate)` — the true cost to the firm
4. `mizan_billing = Σ(hours × billing_rate)` — what should be charged
5. `mizan_price = mizan_cost × risk_coefficient` — shown in pricing only, never used in P&L
6. `income` = SUM of paid on income transactions (`INCOME_TX_TYPES`) for this project
7. `profit = income − (mizan_cost + outsourcing + material)`
8. FX gain/loss = `Σ(amount_usd × (current_rate − rate_at_transaction_date))` for USD transactions; current rate comes from `get_current_usd_rate()` (latest `exchange_rates` row, NOT the `usd_rate` setting)
9. Earned revenue = `contract_amount × min(actual_hours / estimated_hours, 1.0)`

Risk coefficient is **never** applied to cost. It only raises the quoted price in the Pricing Engine.

### Pricing Engine (`database.py → pricing_estimate`)

Pre-sales tool for quoting new projects. Takes planned hours per staff member plus four risk factors:

| Factor | Low (1) | Medium (2) | High (3) | Weight |
|---|---|---|---|---|
| Deadline | >8 months | 4–8 months | <4 months | 30% |
| Client type | Regular | New | Government | 25% |
| Complexity | Simple | Medium | High | 25% |
| Currency | UZS | USD | Other | 20% |

```
risk_coeff = 1 + (weighted_score − 1) × 0.15
minimum_contract = mizan_cost × risk_coeff + outsourcing + materials
target_contract  = minimum_contract / (1 − target_margin)   ← ensures margin is met
premium_contract = target_contract × 1.2
```

When a plan is "frozen" to a project, the `planned_hours`, `planned_cost`, `planned_revenue`, `planned_outsourcing`, `planned_material`, and `plan_frozen_date` columns on `projects` are written. These become the PLAN side of the Budget page and do not change as actual work proceeds.

### Budget page (plan vs actual)

For each project: PLAN = frozen `planned_*` columns, FACT = live recalculated from actual hours and current cost rates. Variance `(fact − plan) / plan × 100%` → status: Oshgan/over budget (>5%), Byudjet ichida/under (< −10%), Chegarada/borderline. Projects without a frozen plan show "Reja yo'q" and are excluded from plan-side totals — the plan is never fabricated from actuals. `planned_cost` is risk-EXCLUSIVE labor cost (same basis as FACT); the risk uplift lives only in `planned_revenue`.

### CFO metrics

**Burn rate** = monthly fixed *cash* costs only: all-staff gross salary + tax + social + overhead total. General-equipment depreciation is non-cash and excluded from burn rate (it is included in `total_operating_cost`). Variable project costs excluded.

**Runway** = `running_cash_balance / burn_rate`. Running balance = cumulative net of all actual cash flows: transactions + dividends + loan flows.

**Capacity** = `production_staff × available_hours × kpi_months` (total) vs hours on active billable projects (booked). Turns red above 85%. (Known limitation: booked = historical hours, not future commitments — see USER_STORY_AND_UX_AUDIT.md A9.)

**AR Aging** = for `transactions WHERE direction='external' AND tx_type IN INCOME_TX_TYPES AND paid < amount`, buckets the outstanding `amount − paid` by days since invoice date: 0–30, 31–60, 61–90, 90+. Anything over 60 days is "overdue".

**FX gain/loss** = for each USD transaction: `amount_usd × (current_usd_rate − rate_stored_at_transaction_time)`, where `current_usd_rate` is the latest `exchange_rates` row (`get_current_usd_rate()`). Positive = UZS weakened (firm gains on USD holdings); negative = UZS strengthened.
