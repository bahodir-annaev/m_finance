# MIZAN Finance v4.0 — Knowledge Transfer Document

> **Purpose:** This document is the authoritative knowledge-transfer reference for MIZAN Finance v4.0. It was written by reading every source file in the repository. Do not rely on this document alone — always verify behaviour against the code.

---

## Table of Contents

1. [What Is MIZAN Finance?](#1-what-is-mizan-finance)
2. [Technology Stack](#2-technology-stack)
3. [Project Structure](#3-project-structure)
4. [Application Startup & Entry Point](#4-application-startup--entry-point)
5. [Authentication & Authorization](#5-authentication--authorization)
6. [Business Domain: The Cost Model](#6-business-domain-the-cost-model)
   - 6.1 [Staff Types — The Foundation](#61-staff-types--the-foundation)
   - 6.2 [Available Hours Calculation](#62-available-hours-calculation)
   - 6.3 [Hourly Rate Formula Chain](#63-hourly-rate-formula-chain)
   - 6.4 [Proportional Cost Distribution](#64-proportional-cost-distribution)
   - 6.5 [Project Cost & Profitability](#65-project-cost--profitability)
   - 6.6 [Pricing Engine (Pre-Sales Quoting)](#66-pricing-engine-pre-sales-quoting)
   - 6.7 [Budget Page — Plan vs Actual](#67-budget-page--plan-vs-actual)
   - 6.8 [Cash Flow](#68-cash-flow)
   - 6.9 [Loans — Bilateral Debt Ledger](#69-loans--bilateral-debt-ledger)
   - 6.10 [CFO Dashboard Metrics](#610-cfo-dashboard-metrics)
   - 6.11 [Fiscal Periods & Snapshots](#611-fiscal-periods--snapshots)
7. [Database: All Tables](#7-database-all-tables)
8. [Models Package (`models/`)](#8-models-package-models)
9. [Controllers (Blueprints)](#9-controllers-blueprints)
10. [Supporting Files](#10-supporting-files)
11. [Templates](#11-templates)
12. [Data Import Pipeline](#12-data-import-pipeline)
13. [API Endpoints Reference](#13-api-endpoints-reference)
14. [Role-Based Access Control Matrix](#14-role-based-access-control-matrix)
15. [Key Invariants & Rules Never to Break](#15-key-invariants--rules-never-to-break)

---

## 1. What Is MIZAN Finance?

MIZAN Finance is a **local, offline financial management application** built specifically for an architecture firm. It runs as a Flask web server accessible only on the local network (`127.x`, `192.168.x`, `10.x`, `172.x`). There is no cloud component, no external database, no JavaScript framework, and no build step.

**Core purpose:** Give the CFO and management a complete picture of:

- How much every architect costs per hour (fully-loaded, activity-based costing)
- Whether each project is profitable
- What to charge for a new project before signing the contract
- Cash-in / cash-out over time, burn rate, and runway
- Loans the firm has taken or given
- Staff KPI and utilization

The application was originally a monolithic `app.py` (~2500 lines with all HTML embedded). It has since been refactored into a proper Flask Blueprint architecture with separate `models/`, `controllers/`, and `templates/` directories.

---

## 2. Technology Stack

| Component | Technology |
|---|---|
| Language | Python 3.10+ |
| Web framework | Flask (with Flask-Login) |
| Database | SQLite 3 (`mizan_finance.db`) |
| Excel I/O | openpyxl |
| HTML rendering | Jinja2 (server-side, no JS framework) |
| Password hashing | Werkzeug `generate_password_hash` / `check_password_hash` |
| Session auth | Flask-Login (cookie-based) |
| Styling | Custom CSS (`/static/style.css`) |
| i18n | Custom dict-based (`translations.py`): Uzbek (`uz`), English (`en`), Russian (`ru`) |
| CRM integration | NIZAM CRM — Excel `.xlsx` time-tracking export |

---

## 3. Project Structure

```
MIZAN_Finance_App_v4/
│
├── app.py                   # Flask app factory + startup entrypoint
├── auth.py                  # Flask-Login setup, User model, require_role decorator
├── utils.py                 # fmt(), t(), render_page() helpers
├── translations.py          # i18n strings for uz/en/ru
├── import_nizam.py          # NIZAM CRM integration: staff seeding + xlsx import
├── database.py              # Legacy import shim (kept for import compatibility)
│
├── models/                  # All database logic — NO route handlers here
│   ├── __init__.py          # Re-exports every public symbol
│   ├── base.py              # get_db(), init_db(), CRUD helpers, audit log
│   ├── staff.py             # Hourly rates, KPI, proportional allocation, snapshots
│   ├── projects.py          # Project cost, FX gain/loss, earned revenue
│   ├── equipment.py         # Equipment and license depreciation
│   ├── pricing.py           # Risk scoring, pricing estimate
│   ├── transactions.py      # Cash flow by month, payment summary
│   ├── loans.py             # Loan listing, detail, summary
│   ├── dashboard.py         # Dashboard aggregations, burn rate, capacity, AR aging
│   └── import_export.py     # Excel column matching and import engine
│
├── controllers/             # Flask Blueprints — route handlers only
│   ├── __init__.py
│   ├── auth_bp.py           # /login, /logout, /admin/users
│   ├── api_bp.py            # /set-lang, /api/get, /api/update, /api/delete
│   ├── dashboard_bp.py      # /
│   ├── staff_bp.py          # /staff, /hourly, /kpi
│   ├── projects_bp.py       # /projects, /budget
│   ├── equipment_bp.py      # /equipment
│   ├── accounting_bp.py     # /accounting (universal data entry)
│   ├── pricing_bp.py        # /pricing
│   ├── cashflow_bp.py       # /cashflow
│   ├── loans_bp.py          # /loans
│   ├── external_bp.py       # /external
│   ├── internal_bp.py       # /internal
│   ├── import_export_bp.py  # /import, /export, /api/import-excel/analyze
│   ├── periods_bp.py        # /periods
│   ├── settings_bp.py       # /settings, /settings/rates, /settings/fetch-rate
│   └── reference_bp.py      # /reference/categories, /reference/counterparties
│
├── templates/               # Jinja2 HTML templates
│   ├── base.html            # Sidebar layout, navigation, lang switcher
│   └── *.html               # One template per page
│
├── static/
│   └── style.css
│
├── uploads/                 # Temporary storage for uploaded Excel files
├── mizan_finance.db         # SQLite database (created on first run)
│
├── CLAUDE.md                # Instructions for Claude Code
├── KT.md                    # This document
└── MIZAN_EXPLAINER.md       # High-level explainer (separate)
```

---

## 4. Application Startup & Entry Point

### `app.py` — App Factory

```python
def create_app():
    app = Flask(__name__, static_folder='static', template_folder='templates')
    app.config['SECRET_KEY'] = os.environ.get('MIZAN_SECRET_KEY', 'mizan-finance-local-secret-2024')
    login_manager.init_app(app)

    @app.before_request
    def restrict_to_local_network(): ...  # blocks non-LAN IPs

    # Register all 16 blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(dashboard_bp)
    # ... etc.
    return app
```

### Startup Sequence (when run as `python app.py`)

1. `init_db()` — creates all tables, runs migrations, seeds defaults
2. `ensure_staff_exists(conn)` — inserts all known staff from `NIZAM_MAP` + extras
3. `seed_equipment_and_licenses(conn)` — seeds computers and licenses on first run
4. Finds a free port (prefers 5000, falls back to random)
5. Opens browser automatically after 1.5 s
6. Starts Flask on `127.0.0.1:<port>` with `debug=False`

### Network Access Control

The `before_request` hook checks `request.remote_addr` against allowed prefixes: `127.`, `::1`, `192.168.`, `10.`, `172.`. Any request from outside the local network receives a `403`.

---

## 5. Authentication & Authorization

### `auth.py`

- **`User`** class extends `flask_login.UserMixin`. Carries `id`, `username`, `role`, `_active`.
- **`load_user(user_id)`** — Flask-Login user loader; fetches from `users` table by `id`.
- **`require_role(*roles)`** — decorator that checks `current_user.role` against a list; redirects to `/login` if unauthenticated, aborts 403 if wrong role.

### Roles

| Role | Access |
|---|---|
| `viewer` | Read-only. Can see all pages. Cannot POST to `/accounting`, `/pricing`, or any CRUD endpoint. |
| `manager` | Can enter transactions, manage projects, run exports, manage periods. Cannot access admin-only pages (Settings, Users, Import, Reference). |
| `admin` | Full access. Manages users, settings, reference data, imports. |

### Session

Flask-Login stores user ID in a signed cookie (`SECRET_KEY`). The "Remember me" option sets the cookie with a long `max_age`.

### Default Admin Credentials

Created automatically on first run if the `users` table is empty:
- Username: `admin`
- Password: `mizan2024`

**Change this on production deployments.**

---

## 6. Business Domain: The Cost Model

### 6.1 Staff Types — The Foundation

Every financial calculation in the system pivots on `staff.staff_type`:

| Type | Description | Billing | Cost Treatment |
|---|---|---|---|
| `production` | Architects, BIM engineers, 3D visualizers | Log hours to projects → generate revenue | Base of hourly rate calculation |
| `admin` | HR, PM, IT Director, Finance Director, CEO, cleaners | Do not log billable hours | Total cost redistributed proportionally to production staff |

Changing a staff member's type fundamentally changes every cost calculation in the system.

### 6.2 Available Hours Calculation

**Source:** `models/staff.py → get_available_hours()`

```
yearly_work_days = 365 − 104 (weekends) − holidays_per_year
net_work_days    = yearly_work_days − avg_leave_days
monthly_work_days = net_work_days / 12
available_hours   = monthly_work_days × 8
```

With defaults (`holidays=14`, `leave=20`):
```
365 − 104 − 14 = 247 total work days
247 − 20       = 227 net days
227 / 12       = 18.9 days/month
18.9 × 8       = 151.3 hours/month
```

This replaces the old `176 h/month` that was used in v3. The 151 h figure is more realistic — it accounts for public holidays and average leave.

`utilization_rate` (default 0.75) is stored in settings but is used for KPI displays, not in the core cost rate formula.

### 6.3 Hourly Rate Formula Chain

**Source:** `models/staff.py → calculate_hourly_rate(staff_id)`

This is the most important function in the entire system. Called from project cost calculations, pricing, KPI, hourly rate page, budget page, and export.

```
gross           = base_salary + premium          (from salary_history, end_date IS NULL)
tax             = gross × tax_rate               (default 12%)
social          = gross × social_rate            (default 12%)
admin_share     = admin_total_cost × share_ratio
general_eq      = general_equipment_monthly × share_ratio
overhead_share  = total_overhead_monthly × share_ratio
personal_eq     = SUM(price / lifespan_months)   (personal_equipment WHERE staff_id=X)
personal_lic    = SUM(annual_cost / 12)          (personal_licenses WHERE staff_id=X)

total_monthly   = gross + tax + social
                + admin_share + general_eq + overhead_share
                + personal_eq + personal_lic

available_hours = ((365 − 104 − holidays − leave_days) / 12) × 8

cost_rate       = total_monthly / available_hours
billing_rate    = cost_rate × billing_multiplier  (default 2.0×)
```

Returns a dict with all components plus `hourly_usd` and `billing_usd` (rates divided by `usd_rate` setting).

Returns `0` (integer) if the staff member has no active salary row.

**Performance note:** The function is designed to re-use the `share_ratio` across all three shared cost components (admin, general_eq, overhead) to avoid triple-querying the period hours. However, it still opens multiple DB connections per call. When calculating many staff members at once (dashboard, export), the caller should cache results.

### 6.4 Proportional Cost Distribution

**Source:** `models/staff.py → _proportional_share(total_cost, staff_id)`

Admin costs, general equipment depreciation, and overhead are not split equally by head count. Instead they follow a three-level fallback:

1. **Current period hours** — Uses the most recently imported `period` from `project_hours.imported_at`. `share_ratio = staff_hours_this_period / total_billable_hours_this_period`
2. **All-time hours** — If the period query returns ≤ 1 hour total, fall back to all-time billable hours.
3. **Equal head count** — If still no hours, divide equally among active production staff.

This means staff who work more hours "absorb" more overhead per rate calculation. A production employee who billed 0 hours in the latest period gets a larger per-hour cost because the shared pool is divided by their zero contribution.

### 6.5 Project Cost & Profitability

**Source:** `models/projects.py → calculate_project_cost(project_id)`

Step-by-step:

1. Fetch all `project_hours` rows for the project, grouped by `(staff_id, period)`.
2. For each `(staff, period)` group:
   - If the fiscal period is `soft_closed` or `hard_closed` **and** `applied_cost_amount > 0` → use the **snapshot** amounts (frozen at close time).
   - Otherwise → call `calculate_hourly_rate(staff_id)` live.
3. Accumulate `mizan_cost` (hours × cost_rate), `mizan_billing` (hours × billing_rate), `total_hours`.
4. Query `transactions` for this project:
   - `income` = SUM of `paid` on `tx_type='tushum'`
   - `outsourcing` = SUM of `amount` on `tx_type='outsourcing'`
   - `material` = SUM of `amount` on `tx_type='material'`
5. Compute:
   - `mizan_price = mizan_cost × risk_coefficient` (pricing display only — NOT used in P&L)
   - `total_expense = mizan_cost + outsourcing + material`
   - `profit = income − total_expense`
   - `gross_margin = profit / income × 100`
6. **FX gain/loss** — for each USD transaction: `paid × (current_usd_rate − rate_at_transaction_date)`
7. **Earned revenue** — `contract_amount × min(actual_hours / estimated_total_hours, 1.0)`
8. `deferred_revenue = contract_amount − earned_revenue` (only if earned_revenue > 0)

**Critical rule:** `risk_coefficient` is applied to `mizan_cost` only for display purposes in the pricing context. It is NEVER added to actual `mizan_cost` or included in profit calculations.

### 6.6 Pricing Engine (Pre-Sales Quoting)

**Source:** `models/pricing.py → pricing_estimate()` and `calculate_risk_score()`

Used before a contract is signed to quote a price. Takes:
- A list of `{staff_id, planned_hours}` entries
- Four risk factor inputs

**Risk scoring:**

| Factor | Low (1) | Medium (2) | High (3) | Weight |
|---|---|---|---|---|
| Deadline | > 8 months | 4–8 months | < 4 months | 30% |
| Client type | Regular | New | Government | 25% |
| Complexity | Simple | Medium | High | 25% |
| Currency | UZS | USD | Other | 20% |

```
weighted_score = d×0.30 + cl×0.25 + cx×0.25 + cu×0.20
risk_coeff     = 1 + (weighted_score − 1) × 0.15
```

**Contract pricing:**

```
total_cost       = Σ(planned_hours × cost_rate)     [at current rates]
mizan_cost       = total_cost × risk_coeff           [risk-adjusted]
minimum_contract = mizan_cost + outsourcing + material
target_contract  = minimum_contract / (1 − target_margin)   [margin-inclusive]
premium_contract = target_contract × 1.2
```

`target_margin` defaults to 0.50 (50%). This ensures the firm keeps 50% of the target contract as gross margin.

**Freezing a plan:** When the user presses "Save to project" in the Pricing Engine, these columns are written to the `projects` table:
- `planned_hours`, `planned_cost`, `planned_revenue`
- `planned_outsourcing`, `planned_material`
- `estimated_total_hours`, `contract_amount`, `risk_coefficient`
- `plan_frozen_date`

These become the immutable PLAN side of the Budget comparison.

### 6.7 Budget Page — Plan vs Actual

**Source:** `controllers/projects_bp.py → budget_page()`

For each billable project with hours:

- **PLAN** = frozen `planned_*` columns on `projects` (or `estimated_total_hours` as fallback if no plan is frozen)
- **FACT** = live recalculated from `calculate_project_cost()` using current cost rates

Variance classification:

| Condition | Status | CSS class |
|---|---|---|
| `(fact_total − plan_total) / plan_total > 5%` | Oshgan (over budget) | `loss` |
| `< −10%` | Byudjet ichida (under budget) | `profit` |
| Between −10% and +5% | Chegarada (borderline) | neutral |

### 6.8 Cash Flow

**Source:** `models/transactions.py → get_cash_flow_by_month()`

Aggregates the `transactions` table by month:

```sql
SELECT strftime('%Y-%m', date) as month,
       SUM(CASE WHEN tx_type='tushum' AND paid > 0 THEN paid ELSE 0 END)          as income,
       SUM(CASE WHEN direction='external' AND tx_type IN ('outsourcing','material') AND paid > 0 THEN paid ELSE 0 END) as ext_expense,
       SUM(CASE WHEN direction='internal' AND paid > 0 THEN paid ELSE 0 END)       as int_expense
FROM transactions WHERE paid > 0
GROUP BY month ORDER BY month
```

Returns a month-by-month array. Each entry includes a `running_balance` — the cumulative sum of `(income − total_expense)` from the earliest transaction to that month.

**Important:** Only `paid > 0` rows are counted. Unpaid invoices (`amount > 0, paid = 0`) do not affect cash flow. Theoretical costs (salaries, overhead) are also excluded — they appear only in `calculate_hourly_rate`. Cash flow = real money moved only.

**Payment summary** (`get_payment_summary`) groups income and expense by `payment_type` (bank, naqd/cash, card, online, ichki).

### 6.9 Loans — Bilateral Debt Ledger

**Source:** `models/loans.py`

Tracks two directions:

| `loan_type` | Meaning |
|---|---|
| `olgan` | Firm **borrowed** money from someone (a liability) |
| `bergan` | Firm **lent** money to someone (an asset) |

Each loan tracks:
- Principal (`total_amount`), currency, interest rate, issue date, due date
- Payments in `loan_payments` (either `asosiy`/principal or `foiz`/interest)
- Status: `ochiq` (open) or `yopilgan` (closed — auto-set when `SUM(payments) >= total_amount`)
- Overdue flag: `status='ochiq' AND due_date < today`

`get_loan_summary()` converts USD loans to UZS using the exchange rate at the issue date for fair comparison.

### 6.10 CFO Dashboard Metrics

**Source:** `models/dashboard.py`

The dashboard aggregates the following CFO-level metrics:

#### Burn Rate
```
monthly_salary_full = SUM(all active staff gross) × (1 + tax_rate + social_rate)
monthly_overhead    = SUM(overhead.monthly_amount)
monthly_equipment   = SUM(general_equipment.price / lifespan_months)
burn_rate           = monthly_salary_full + monthly_overhead + monthly_equipment
```
This is the minimum cash the firm must spend each month regardless of project activity. Variable costs (outsourcing, materials) are excluded.

#### Runway
```
running_balance = last entry of get_cash_flow_by_month()['running_balance']
runway_months   = running_balance / burn_rate
```
"How many months can the firm survive without any new income." Turns red in the UI if < 6 months.

#### Capacity
```
total_capacity  = production_staff_count × available_hours × kpi_months
booked_capacity = SUM(project_hours.hours) for active billable projects
capacity_pct    = booked / total
```
Turns red above 85%.

#### AR Aging
Scans `transactions WHERE tx_type='tushum' AND direction='external' AND paid < amount`. Buckets the outstanding balance (`amount − paid`) by days since invoice date:

| Bucket | Days |
|---|---|
| 0–30 | Current |
| 31–60 | Aging |
| 61–90 | Overdue |
| 90+ | Seriously overdue |

`overdue_items` = the top 10 records in the 90+ bucket.

#### Break-even Revenue
```
monthly_fixed    = admin_total_cost + total_overhead
avg_margin_pct   = (total_income − total_mizan_cost) / total_income   [or 0.5 if no income]
breakeven_revenue = monthly_fixed / avg_margin_pct
```

#### Other Dashboard Metrics
- `revenue_per_employee` = `total_income / production_staff_count`
- `profit_per_hour` = `gross_profit / total_hours`
- `gross_margin_pct` = `gross_profit / total_income × 100`
- FX gain/loss summed across all projects

### 6.11 Fiscal Periods & Snapshots

**Source:** `controllers/periods_bp.py`, `models/staff.py → close_fiscal_period()`

Fiscal periods provide an **audit lock** on historical cost calculations. Once a period is closed, the cost rates used in that period are frozen and cannot drift as salaries or overhead change.

#### Period Lifecycle

```
open → soft_closed → hard_closed
  ↑
  └── can reopen from soft_closed (but NOT from hard_closed)
```

| Status | Can edit transactions? | Rate snapshots? | Reopenable? |
|---|---|---|---|
| `open` | Yes | No | N/A |
| `soft_closed` | No (use reversal) | Yes — per-staff allocation + per-hours snapshot | Yes |
| `hard_closed` | No | Yes (final) | No |

#### What gets snapshotted on close

**`snapshot_period_allocations(period)`** writes to `period_allocations`:
- Per-staff: billable hours, hours_share, every cost component amount, cost_rate, billing_rate, available_hours.

**`snapshot_hours_rates(period)`** writes to `project_hours`:
- `applied_cost_rate`, `applied_billing_rate`
- `applied_cost_amount` (= hours × cost_rate), `applied_billing_amount`
- `rate_snapshot_at`, `rate_snapshot_source`

Once these snapshot columns are populated, `calculate_project_cost()` uses them instead of recalculating live — ensuring historical profitability reports remain stable.

---

## 7. Database: All Tables

All tables are created in `models/base.py → init_db()`. The database file is `mizan_finance.db` in the project root. Foreign keys are enforced with `PRAGMA foreign_keys = ON`.

---

### `users`
**Purpose:** Application user accounts for authentication.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | Auto-increment |
| `username` | TEXT UNIQUE | Login name |
| `password_hash` | TEXT | Werkzeug bcrypt hash |
| `role` | TEXT | `admin`, `manager`, or `viewer` — CHECK constraint |
| `is_active` | INTEGER | 0 = disabled (cannot log in) |
| `created_at` | TEXT | Timestamp |

**Used in:** `auth.py` (load_user, login check), `controllers/auth_bp.py` (user management), `templates/admin_users.html`.

Default seed: `admin` / `mizan2024` inserted on first run if table is empty.

---

### `settings`
**Purpose:** Global numeric parameters for the cost model and UI behaviour.

| Column | Type | Notes |
|---|---|---|
| `key` | TEXT PK | Parameter name |
| `value` | REAL | Current value |
| `label` | TEXT | Human-readable label (shown in Settings page) |
| `unit` | TEXT | Display unit (`%`, `UZS`, `soat`, etc.) |

**Key parameters:**

| Key | Default | Role in system |
|---|---|---|
| `usd_rate` | 12850 | Fallback USD/UZS rate when no exchange_rates entry exists |
| `tax_rate` | 0.12 | Income tax on gross salary (12%) |
| `social_rate` | 0.12 | Social contribution on gross salary (12%) |
| `billing_multiplier` | 2.0 | Markup: billing_rate = cost_rate × 2.0 |
| `holidays_per_year` | 14 | State holidays used in available_hours formula |
| `avg_leave_days` | 20 | Average leave + sick days per year |
| `utilization_rate` | 0.75 | Target utilization (KPI display only) |
| `target_margin` | 0.50 | Pricing engine: target gross margin |
| `kpi_months` | 43 | KPI calculation window in months |
| `effective_hours` | 152 | Available hours/month (informational) |

**Used in:** Nearly every model function. `get_setting(key)` is the primary accessor (from `models/base.py`). `controllers/settings_bp.py` provides the admin UI to edit these.

---

### `exchange_rates`
**Purpose:** Historical UZS/USD exchange rate table for FX calculations.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | Auto-increment |
| `date` | TEXT UNIQUE | `YYYY-MM-DD` |
| `rate` | REAL | UZS per 1 USD |

**Lookup pattern:** `WHERE date <= ? ORDER BY date DESC LIMIT 1` — finds the most recent rate on or before any given date.

**Used in:** `models/base.py → get_rate_for_date()`, `models/projects.py → calculate_fx_gain_loss()`, `models/loans.py → get_loan_summary()`, `controllers/accounting_bp.py` (when recording USD transactions). 

**Fetch from CBU:** `controllers/settings_bp.py → fetch_rate()` hits `https://cbu.uz/oz/arkhiv-kursov-valyut/json/USD/{date}/` to auto-fill rates.

Seeded with rates from 2022-07-01 to 2026-01-01 on first run.

---

### `staff`
**Purpose:** Employee register — the master list of all current and archived staff.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | Auto-increment |
| `name` | TEXT UNIQUE | Short display name (e.g., `Farxod`, `Zafar`) |
| `full_name` | TEXT | Full legal name |
| `nizam_name` | TEXT | Name exactly as it appears in NIZAM CRM exports |
| `role` | TEXT | Job title (e.g., `BIM Architect`, `Lead Architect`) |
| `department` | TEXT | Department (e.g., `BIM`, `Arxitektura`, `Vizualizatsiya`) |
| `staff_type` | TEXT | `production` or `admin` — CHECK constraint |
| `is_active` | INTEGER | 1 = active; 0 = archived |
| `staff_code` | TEXT | `MZ-001` for production; `MA-001` for admin. Generated automatically. |
| `created_at`, `updated_at` | TEXT | Timestamps |

**Used in:** Almost everything. `salary_history`, `personal_equipment`, `personal_licenses` all reference `staff.id`. `project_hours` references `staff.id`. `transactions.responsible_id` references `staff.id`. Hourly rate calculation reads salary from `salary_history` using the staff ID.

**Seeded by:** `import_nizam.py → ensure_staff_exists()` on every startup.

---

### `salary_history`
**Purpose:** Salary timeline — stores the history of salary changes per staff member. Each row represents a salary effective from `start_date` until `end_date` (NULL = currently active).

| Column | Type | Notes |
|---|---|---|
| `id` | PK | Auto-increment |
| `staff_id` | INTEGER → `staff.id` | Owner |
| `base_salary` | REAL | Monthly gross base salary (UZS) |
| `premium` | REAL | Monthly bonus/premium on top of base |
| `start_date` | TEXT | Effective from (`YYYY-MM-DD`). UNIQUE with `staff_id`. |
| `end_date` | TEXT | Effective until. `NULL` = current active row. |
| `updated_at` | TEXT | Timestamp |

**Query pattern for current salary:** `WHERE staff_id=? AND end_date IS NULL`

**When a salary is updated:** The existing open row gets `end_date = today`, and a new row is inserted with `start_date = today, end_date = NULL`. This preserves the full salary history.

**Used in:** `models/staff.py → calculate_hourly_rate()` (reads current salary), `controllers/staff_bp.py → staff_page()` (displays salary), `controllers/accounting_bp.py` (updates salary), `models/dashboard.py → get_burn_rate_and_runway()` (sums all active salaries for burn rate), `controllers/import_export_bp.py → export_xlsx()`.

---

### `personal_equipment`
**Purpose:** Computers, monitors, and other equipment assigned to individual staff members. Depreciated monthly into each person's hourly cost.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | Auto-increment |
| `name` | TEXT | Equipment name (e.g., `Dell Precision 7780`) |
| `staff_id` | INTEGER → `staff.id` | Assigned staff member (nullable — unassigned) |
| `price` | REAL | Purchase price (UZS) |
| `lifespan_months` | INTEGER | Depreciation period. Monthly depreciation = `price / lifespan_months`. |
| `purchase_date` | TEXT | Purchase date |
| `is_active` | INTEGER | 0 = retired/sold (soft delete) |

**Used in:** `models/equipment.py → get_personal_equipment_monthly(staff_id)` → called from `calculate_hourly_rate()`. `controllers/equipment_bp.py` (display). `controllers/accounting_bp.py` (add new equipment). Seeded from `import_nizam.py` PERSONAL_EQUIPMENT list.

---

### `general_equipment`
**Purpose:** Shared office equipment (printers, servers, plotters, NAS, UPS, routers, furniture) whose depreciation is distributed across all production staff proportionally.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | Auto-increment |
| `name` | TEXT | Equipment name |
| `quantity` | INTEGER | Number of units |
| `price` | REAL | **Unit** price (UZS) |
| `lifespan_months` | INTEGER | Depreciation period |
| `purchase_date` | TEXT | Purchase date |
| `is_active` | INTEGER | 0 = retired |

Monthly total = `SUM(quantity × price / lifespan_months)` for all active entries.

**Used in:** `models/equipment.py → get_general_equipment_monthly()` → called from `calculate_hourly_rate()` and `get_burn_rate_and_runway()`. Seeded from `import_nizam.py` GENERAL_EQUIPMENT list.

---

### `personal_licenses`
**Purpose:** Per-staff software licenses (Revit, AutoCAD, 3ds Max, V-Ray, etc.). Annual cost divided by 12 to get monthly contribution to the staff member's cost.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | Auto-increment |
| `name` | TEXT | License/software name |
| `staff_id` | INTEGER → `staff.id` | Assigned staff member |
| `annual_cost` | REAL | Annual license cost (UZS) |
| `license_type` | TEXT | `named` (tied to one person) or `floating` |
| `is_active` | INTEGER | 0 = expired/cancelled |

**Used in:** `models/equipment.py → get_personal_license_monthly(staff_id)` → called from `calculate_hourly_rate()`. Seeded from `import_nizam.py` PERSONAL_LICENSES list.

---

### `overhead`
**Purpose:** Fixed monthly firm-level costs: office rent, utilities, internet, food, transport, general licenses.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | Auto-increment |
| `name` | TEXT UNIQUE | Cost item name |
| `monthly_amount` | REAL | Fixed monthly cost (UZS) |
| `is_active` | INTEGER | 0 = no longer applicable |
| `updated_at` | TEXT | Timestamp |

Monthly total is distributed proportionally across production staff by billable hours share.

**Used in:** `models/staff.py → get_total_overhead()` → called from `calculate_hourly_rate()` and `get_burn_rate_and_runway()`. Displayed on Equipment page. Managed via the Accounting page (Section: Overhead).

Seeded with 8 default items (rent, utilities, internet, food, office supplies, transport, general licenses, miscellaneous).

---

### `projects`
**Purpose:** Client project register. Every billable engagement is a row here.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | Auto-increment |
| `name` | TEXT UNIQUE | Project name (also used as NIZAM import key) |
| `client` | TEXT | Client name |
| `responsible` | TEXT | Responsible architect/PM (free text) |
| `risk_coefficient` | REAL | Default 1.15 — pricing multiplier from risk score |
| `risk_score` | REAL | Raw weighted risk score (0–3 range) |
| `contract_amount` | REAL | Signed contract value |
| `currency` | TEXT | `UZS` or `USD` |
| `start_date`, `end_date` | TEXT | Project timeline |
| `status` | TEXT | `active`, `completed`, `paused` |
| `is_billable` | INTEGER | 0 = internal/non-billable projects (filtered from most reports) |
| `estimated_total_hours` | REAL | Total planned hours — denominator in earned revenue calc |
| `planned_hours` | REAL | Frozen from pricing estimate |
| `planned_cost` | REAL | Frozen plan cost |
| `planned_revenue` | REAL | Frozen plan revenue (target contract) |
| `planned_outsourcing` | REAL | Frozen outsourcing budget |
| `planned_material` | REAL | Frozen materials budget |
| `plan_frozen_date` | TEXT | Set when pricing estimate is saved to project |
| `created_at`, `updated_at` | TEXT | Timestamps |

**Used in:** `calculate_project_cost()`, `calculate_fx_gain_loss()`, `get_earned_revenue()`, `controllers/projects_bp.py → budget_page()`, `controllers/accounting_bp.py` (new project form), `controllers/pricing_bp.py` (save estimate), NIZAM import (auto-creates projects by name).

---

### `project_hours`
**Purpose:** Time entries — the record of how many hours each staff member worked on each project in each calendar month.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | Auto-increment |
| `project_id` | INTEGER → `projects.id` | Project |
| `staff_id` | INTEGER → `staff.id` | Staff member |
| `hours` | REAL | Hours worked |
| `period` | TEXT | `YYYY-MM` format |
| `source` | TEXT | `nizam` (from CRM import) or `manual` |
| `imported_at` | TEXT | Import timestamp |
| `applied_cost_rate` | REAL | **Snapshot:** cost_rate used at period close |
| `applied_billing_rate` | REAL | **Snapshot:** billing_rate at period close |
| `applied_cost_amount` | REAL | **Snapshot:** hours × applied_cost_rate |
| `applied_billing_amount` | REAL | **Snapshot:** hours × applied_billing_rate |
| `rate_snapshot_at` | TEXT | When snapshot was taken |
| `rate_snapshot_source` | TEXT | `period_close` or other source |
| `phase_id` | INTEGER | Optional link to `project_phases` |
| `supersedes_id`, `superseded_by_id` | INTEGER | Correction chain |

**UNIQUE constraint:** `(project_id, staff_id, period)` — only one row per person per project per month. NIZAM imports use `INSERT OR REPLACE`.

**Used in:** `calculate_project_cost()` (the core hours data), `models/staff.py` (period hours for share ratio), `get_staff_kpi()`, `get_capacity_data()`, `controllers/projects_bp.py`, NIZAM import.

---

### `transactions`
**Purpose:** Unified table for all money flows. Replaced the legacy `external_transactions` and `internal_transactions` tables.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | Auto-increment |
| `direction` | TEXT | `'external'` (client-facing) or `'internal'` (firm expenses) |
| `tx_type` | TEXT | Type code (see below) |
| `date` | TEXT | Transaction date `YYYY-MM-DD` |
| `ref_id` | TEXT | Auto-generated: `PRJ-HHMMSS` (external) or `MZ-HHMMSS` (internal) |
| `doc_id` | TEXT | Contract or document ID (e.g., `SH-001`) |
| `project_id` | INTEGER → `projects.id` | Linked project (nullable) |
| `counterparty_id` | INTEGER → `counterparties.id` | Structured counterparty (optional) |
| `responsible_id` | INTEGER → `staff.id` | Responsible staff (FK, optional) |
| `responsible` | TEXT | Responsible person (free text, for manual entry) |
| `client` | TEXT | Client/payer name |
| `paid_to` | TEXT | Recipient (outsourcing, salary) |
| `description` | TEXT | Transaction description |
| `notes` | TEXT | Free-text notes |
| `amount` | REAL | Invoiced/committed amount (UZS) |
| `paid` | REAL | Amount actually received or paid |
| `currency` | TEXT | `UZS` or `USD` |
| `exchange_rate` | REAL | Rate at time of entry (for FX calc) |
| `amount_usd` | REAL | Amount in USD |
| `contract_amount` | REAL | Contract face value |
| `contract_currency` | TEXT | Contract currency |
| `contract_amount_usd` | REAL | Contract in USD |
| `payment_type` | TEXT | `bank`, `naqd`, `karta`, `online`, `ichki` |
| `deadline` | TEXT | Payment due date |
| `status` | TEXT | `pending`, `partial`, `paid`, `To'langan`, `Kutilmoqda` |
| `supersedes_id` | INTEGER → `transactions.id` | Links to original if correction |
| `superseded_by_id` | INTEGER → `transactions.id` | Points to correction |
| `created_at`, `updated_at` | TEXT | Timestamps |

**`tx_type` values:**

| Value | Direction | Meaning |
|---|---|---|
| `tushum` | external | Client income payment |
| `outsourcing` | external | Subcontractor payment |
| `material` | external | Project materials cost |
| `mizan_monthly` | external | Recurring monthly fee |
| `yakuniy_hisob` | external | Final settlement invoice |
| `maosh` | internal | Salary disbursement |
| `premiya` | internal | Bonus payment |
| `ijara` | internal | Rent payment |
| `kommunal` | internal | Utilities |
| `soliq` | internal | Tax payment |
| `ovqat` | internal | Food expense |
| `litsenziya` | internal | License payment |
| `malaka` | internal | Training/certification |
| `overhead` | internal | General overhead |

**Used in:** `models/transactions.py → get_cash_flow_by_month()`, `models/projects.py → calculate_project_cost()`, `models/dashboard.py → get_ar_aging()`, `models/projects.py → calculate_fx_gain_loss()`, `controllers/external_bp.py`, `controllers/internal_bp.py`, `controllers/accounting_bp.py` (all writes), Excel import.

---

### `transaction_lines`
**Purpose:** Line-item detail within a transaction, allowing one transaction to be allocated across multiple projects or categories.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | Auto-increment |
| `transaction_id` | INTEGER → `transactions.id` ON DELETE CASCADE | Parent |
| `line_no` | INTEGER | Line sequence (UNIQUE with `transaction_id`) |
| `project_id` | INTEGER → `projects.id` | Project allocation |
| `project_phase_id` | INTEGER → `project_phases.id` | Phase allocation |
| `category_id` | INTEGER → `transaction_categories.id` | Structured category |
| `amount` | REAL | Line amount |
| `description` | TEXT | Line description |

**Current behaviour:** Every transaction gets at least one line (line_no=1) auto-created at insert time. Multi-line splits are supported structurally but the UI currently always creates single-line transactions.

**Used in:** `models/base.py → delete_record()` won't touch this directly (cascades from parent). Referenced in `reference_bp.py` to prevent deletion of categories that are in use.

---

### `loans`
**Purpose:** Bilateral debt ledger. Tracks both money the firm borrowed and money it lent.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | Auto-increment |
| `loan_type` | TEXT | `olgan` (firm borrowed) or `bergan` (firm lent) — CHECK |
| `counterparty` | TEXT | Lender or borrower name |
| `description` | TEXT | Purpose/notes |
| `total_amount` | REAL | Principal |
| `currency` | TEXT | `UZS` or `USD` |
| `interest_rate` | REAL | Annual rate (%) — stored; interest is tracked via `loan_payments.payment_type='foiz'` |
| `issue_date` | TEXT | Date the loan was issued |
| `due_date` | TEXT | Repayment deadline (nullable) |
| `status` | TEXT | `ochiq` (open) or `yopilgan` (closed) — CHECK |
| `notes` | TEXT | Additional notes |
| `created_at`, `updated_at` | TEXT | Timestamps |

**Used in:** `models/loans.py` (all queries), `controllers/loans_bp.py → loans_page()`, `controllers/dashboard_bp.py → dashboard()` (summary widget), `controllers/accounting_bp.py → _handle_post()` (add loan / add payment).

---

### `loan_payments`
**Purpose:** Individual repayment entries for each loan.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | Auto-increment |
| `loan_id` | INTEGER → `loans.id` | Parent loan |
| `date` | TEXT | Payment date |
| `amount` | REAL | Amount paid |
| `payment_type` | TEXT | `asosiy` (principal) or `foiz` (interest) |
| `notes` | TEXT | Notes |
| `created_at` | TEXT | Timestamp |

`remaining = loans.total_amount − SUM(loan_payments.amount)`. When `SUM >= total_amount`, the loan is auto-closed (status set to `yopilgan`).

**Used in:** `models/loans.py` (all loan computations), `controllers/accounting_bp.py` (add payment).

---

### `audit_log`
**Purpose:** Immutable change log for all updates. Written by `models/base.py → _write_audit()`.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | Auto-increment |
| `timestamp` | TEXT | `datetime('now')` |
| `user` | TEXT | Username (currently not always populated) |
| `action` | TEXT | `update`, `delete`, `snapshot`, `import` |
| `entity_type` | TEXT | Table name (e.g., `transactions`) |
| `entity_id` | INTEGER | Row ID |
| `field` | TEXT | Which column changed |
| `old_value` | TEXT | Before |
| `new_value` | TEXT | After |
| `context` | TEXT | Free-text note |

Every `update_record()` call generates one audit row per changed field. Every `delete_record()` call generates one row. Period closes and Excel imports also write audit rows.

**Currently:** No UI to browse the audit log. Data is there for future reporting.

---

### `transaction_categories`
**Purpose:** Hierarchical taxonomy for classifying transactions. Supports tree structure via `parent_id` self-reference.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | Auto-increment |
| `code` | TEXT UNIQUE | Short code (e.g., `tushum`, `outsourcing`) |
| `name_uz`, `name_en`, `name_ru` | TEXT | Localized names |
| `parent_id` | INTEGER → self | Null for root categories |
| `direction` | TEXT | `in` (income) or `out` (expense) |
| `affects_project_cost` | INTEGER | 1 = direct project cost |
| `affects_cash_flow` | INTEGER | 1 = counts in cash flow |
| `is_active` | INTEGER | 0 = archived |
| `sort_order` | INTEGER | Display order |
| `notes` | TEXT | Notes |

**Used in:** `transaction_lines.category_id`, `controllers/reference_bp.py → categories_page()` (management UI).

Seeded with three root categories (`revenue`, `direct_cost`, `indirect_cost`) and 7 child categories on first run.

---

### `fiscal_periods`
**Purpose:** Defines named accounting periods (months) and their lock status.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | Auto-increment |
| `code` | TEXT UNIQUE | `YYYY-MM` format (e.g., `2026-04`) |
| `start_date` | TEXT | First day of month |
| `end_date` | TEXT | Last day of month |
| `status` | TEXT | `open`, `soft_closed`, `hard_closed` |
| `closed_at` | TEXT | Timestamp when closed |
| `closed_by_user` | TEXT | Username (not currently populated) |
| `notes` | TEXT | Period notes |

**Used in:** `models/base.py → get_open_fiscal_period()` and `is_period_closed()` (called by `calculate_project_cost()` to decide whether to use snapshot rates), `controllers/periods_bp.py` (management UI), `models/staff.py → close_fiscal_period()`.

---

### `counterparties`
**Purpose:** Structured registry of clients, vendors, and other counterparties for linking to transactions.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | Auto-increment |
| `name` | TEXT | Display name |
| `legal_name` | TEXT | Full legal name |
| `tax_id` | TEXT | Tax ID / INN |
| `counterparty_type` | TEXT | e.g., `client`, `vendor`, `other` |
| `country` | TEXT | Country |
| `default_currency` | TEXT | Preferred currency |
| `is_active` | INTEGER | 0 = inactive |
| `notes` | TEXT | Notes |
| `created_at` | TEXT | Timestamp |

**Used in:** `transactions.counterparty_id` (FK, optional). `controllers/reference_bp.py → counterparties_page()` (management UI). Appears in `_ALLOWED_TABLES` for the generic CRUD API.

---

### `counterparty_aliases`
**Purpose:** Alternative names for a counterparty (e.g., abbreviations, previous names, import names). Used for fuzzy matching during import.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | Auto-increment |
| `counterparty_id` | INTEGER → `counterparties.id` | Owner |
| `alias` | TEXT UNIQUE | The alias text |
| `source` | TEXT | `manual` or import source |

**Used in:** `controllers/reference_bp.py` (management). No automated matching implemented yet.

---

### `tags` and `entity_tags`
**Purpose:** Flexible tagging for any entity. `tags` defines tag names/colors; `entity_tags` links any tag to any entity (transaction, project, staff, etc.) via `entity_type` + `entity_id`.

**Currently:** Tables exist and are in `_ALLOWED_TABLES` (API-accessible), but no UI for tag management or display is implemented.

---

### `period_allocations`
**Purpose:** Per-period snapshot of each production staff member's cost allocation. Written by `snapshot_period_allocations()` at period close.

| Column | Type | Notes |
|---|---|---|
| `period` | TEXT | `YYYY-MM` |
| `staff_id` | INTEGER → `staff.id` | Staff member |
| `billable_hours` | REAL | Hours in this period |
| `hours_share` | REAL | This staff member's share of total period hours |
| `admin_share_amount` | REAL | UZS allocated from admin costs |
| `overhead_share_amount` | REAL | UZS from overhead |
| `general_equipment_share_amount` | REAL | UZS from shared equipment |
| `personal_equipment_amount` | REAL | UZS from personal equipment |
| `personal_licenses_amount` | REAL | UZS from personal licenses |
| `gross_salary` | REAL | Gross salary that period |
| `tax_amount`, `social_amount` | REAL | Tax and social contributions |
| `total_monthly_cost` | REAL | Full allocated monthly cost |
| `cost_rate`, `billing_rate` | REAL | Rates at close time |
| `available_hours` | REAL | Available hours at close time |
| `snapshotted_at` | TEXT | Timestamp |

UNIQUE on `(period, staff_id)` — one row per staff per period.

**Used in:** `controllers/periods_bp.py` (displays `staff_snapshots` count), available for future historical cost reports.

---

### `project_earned_revenue_snapshots`
**Purpose:** Per-period snapshot of earned revenue metrics for each project. Written for future use.

Currently this table is created but no code actively writes to it (beyond the schema). Reserved for a future earned-revenue reporting module.

---

### `project_phases`
**Purpose:** Sub-divisions of a project (e.g., Concept, Design Development, Construction Documents). Planned hours can be set at phase level.

| Column | Type | Notes |
|---|---|---|
| `id` | PK | Auto-increment |
| `project_id` | INTEGER → `projects.id` ON DELETE CASCADE | Parent project |
| `code` | TEXT | Phase code (UNIQUE with project_id) |
| `name` | TEXT | Phase name |
| `sort_order` | INTEGER | Display order |
| `planned_hours`, `planned_cost`, `planned_revenue` | REAL | Phase budget |
| `start_date`, `end_date` | TEXT | Phase timeline |
| `completion_percent` | REAL | Progress |
| `status` | TEXT | `active` or similar |

**Used in:** `project_hours.phase_id` (link hours to phases), `transaction_lines.project_phase_id` (allocate spend to phases). **Currently:** Table exists but no UI for phase creation is implemented.

---

## 8. Models Package (`models/`)

The `models/` package contains all database logic. Route handlers must never run raw SQL queries — they call model functions. The `models/__init__.py` re-exports every public symbol so controllers can `from models import X`.

---

### `models/base.py`

The database foundation module. Everything else in `models/` imports from here.

#### `get_db()`
Opens a new SQLite connection, sets `row_factory = sqlite3.Row` (so rows behave like dicts), enables `PRAGMA foreign_keys = ON`. **Every caller must close the connection.** There is no connection pool.

#### `get_setting(key) → float | None`
Opens its own connection, fetches `settings.value` by key, closes. Returns `None` if key not found.

#### `get_rate_for_date(dt_str) → float`
Returns the UZS/USD rate for a given date using the "most recent on or before" pattern. Falls back to `get_setting('usd_rate')` if no rates exist.

#### CRUD API
Four generic helpers validated against an allowlist:

| Function | Allowlist | Notes |
|---|---|---|
| `get_record(table, id)` | `_ALLOWED_TABLES` | Returns `dict` or `None` |
| `update_record(table, id, data)` | `_ALLOWED_TABLES` | Writes audit log per changed field; auto-updates `updated_at` if column exists |
| `delete_record(table, id)` | `_DELETE_ALLOWED` | Soft-deletes (sets `is_active=0`) if column exists; hard-deletes otherwise |
| `get_all_records(table, limit, active_only)` | `_ALLOWED_TABLES` | Returns list of dicts |

`_ALLOWED_TABLES` = `transactions`, `staff`, `salary_history`, `personal_equipment`, `general_equipment`, `personal_licenses`, `overhead`, `exchange_rates`, `loans`, `loan_payments`, `counterparties`, `tags`.

`_DELETE_ALLOWED` = `transactions`, `personal_equipment`, `general_equipment`, `personal_licenses`, `overhead`, `exchange_rates`.

#### Fiscal Period Helpers
- `get_open_fiscal_period()` — returns code of the most recent `open` period, or `None`.
- `is_period_closed(period_code)` — returns `True` if `soft_closed` or `hard_closed`.

#### `init_db()`
The full schema init + migration function. Called once on startup. Creates all tables with `IF NOT EXISTS`, runs a list of `ALTER TABLE ADD COLUMN` migrations (each wrapped in `try/except OperationalError` for idempotency), seeds `settings` and `exchange_rates` defaults on a fresh install, seeds `transaction_categories`, runs the one-time `external_transactions + internal_transactions → transactions` migration, and auto-generates `staff_code` for any staff without one.

---

### `models/equipment.py`

Pure depreciation queries. No cross-module dependencies (only imports `get_db`).

| Function | What it does |
|---|---|
| `get_general_equipment_monthly()` | `SUM(price / lifespan_months)` for all active `general_equipment` |
| `get_personal_equipment_monthly(staff_id)` | `SUM(price / lifespan_months)` for a specific staff member |
| `get_personal_license_monthly(staff_id)` | `SUM(annual_cost / 12)` for a specific staff member |

All three are called from `models/staff.py → calculate_hourly_rate()`. `get_general_equipment_monthly()` is also called from `models/dashboard.py → get_burn_rate_and_runway()`.

---

### `models/staff.py`

The most complex model. Handles all staff cost logic.

| Function | What it does |
|---|---|
| `get_available_hours()` | Returns dict with yearly/monthly work days and available hours based on current settings |
| `count_staff_by_type(staff_type)` | Count of active staff of given type |
| `get_total_billable_hours(period)` | Total billable hours (all production staff, optionally filtered by period) |
| `get_staff_billable_hours(staff_id, period)` | Billable hours for a specific staff member |
| `get_admin_total_cost()` | Sum of all active admin staff gross × (1 + tax + social) |
| `get_total_overhead()` | Sum of all active overhead monthly_amount |
| `get_admin_share_for_staff(staff_id)` | Admin cost allocated to one production staff member |
| `get_overhead_share_for_staff(staff_id)` | Overhead allocated to one production staff member |
| `get_general_equipment_share_for_staff(staff_id)` | General equipment depreciation allocated |
| `calculate_hourly_rate(staff_id)` | **Core function** — returns full rate breakdown dict or 0 |
| `get_staff_kpi()` | Returns KPI list for all active production staff |
| `snapshot_period_allocations(period)` | Freezes cost allocation data for all production staff into `period_allocations` |
| `snapshot_hours_rates(period, source)` | Writes `applied_cost_rate` etc. onto `project_hours` rows |
| `close_fiscal_period(period_code, status)` | Runs both snapshot functions + updates `fiscal_periods.status` |

---

### `models/projects.py`

Project-level calculations.

| Function | What it does |
|---|---|
| `get_staff_billable_hours_for_project(project_id)` | Total hours on a project across all staff and periods |
| `calculate_fx_gain_loss(project_id)` | FX gain/loss on all USD transactions for a project |
| `get_earned_revenue(project_id)` | Earned revenue = `contract × min(actual/estimated, 1.0)` |
| `calculate_project_cost(project_id)` | Full project P&L breakdown — the main project analytics function |

`calculate_project_cost()` uses snapshot amounts for closed periods and live rates for open periods. It also caches `calculate_hourly_rate()` results per unique `staff_id` within a single call to avoid redundant DB work.

---

### `models/pricing.py`

Pre-sales quoting.

| Function | What it does |
|---|---|
| `calculate_risk_score(deadline_months, client_type, complexity, currency)` | Returns `(weighted_score, risk_coeff)` tuple |
| `pricing_estimate(staff_hours_list, risk_kwargs, outsourcing, material)` | Returns full pricing dict with minimum/target/premium contract amounts |

---

### `models/transactions.py`

Cash flow aggregations from the unified `transactions` table.

| Function | What it does |
|---|---|
| `get_cash_flow_by_month()` | Month-by-month income/expense/balance table |
| `get_payment_summary()` | Income and expense grouped by payment method |

---

### `models/loans.py`

Loan queries.

| Function | What it does |
|---|---|
| `get_all_loans()` | All loans with `total_paid`, `remaining`, `overdue`, `paid_pct` computed |
| `get_loan_detail(loan_id)` | Single loan with full payment history |
| `get_loan_summary()` | Totals for `olgan` and `bergan` in UZS (with currency conversion) + overdue count |

---

### `models/dashboard.py`

High-level aggregations for the executive dashboard.

| Function | What it does |
|---|---|
| `get_burn_rate_and_runway()` | Burn rate components + runway in months |
| `get_capacity_data()` | Total vs booked capacity |
| `get_ar_aging()` | AR outstanding bucketed by days |
| `get_dashboard_data()` | Full dashboard payload (calls all other functions) |

`get_dashboard_data()` iterates over ALL billable projects and calls `calculate_project_cost()` for each. On a database with many projects, this is the most expensive page in the app. Results are not cached.

---

### `models/import_export.py`

Excel data import utility.

| Function | What it does |
|---|---|
| `analyze_excel_for_import(filepath)` | Opens workbook, reads first 5 rows of each sheet, matches column headers to known fields via `_COL_PATTERNS`, detects sheet type |
| `import_excel_data(filepath, sheet_name, target_table, column_mapping, header_idx)` | Imports rows from a sheet into the `transactions` table |

`_COL_PATTERNS` is a dict of field → keyword list for multi-language column header matching (supports Uzbek, Russian, English column names).

Sheet type detection heuristics:
- `'tushum'` + `'harajat'` + `'proeyekt'` in headers → `external`
- `'tushum'` + `'harajat'` (no project) → `internal`
- `'plan'` or `'fakt'` → `budget`
- `'soat'` + `'narx'` → `hourly`
- `'kpi'` → `kpi`

---

## 9. Controllers (Blueprints)

Each controller is a Flask Blueprint registered in `app.py`. Controllers only handle HTTP routing, form parsing, and template rendering. All DB work goes to models.

---

### `controllers/auth_bp.py` — Blueprint: `auth`

| Route | Method | Role | Description |
|---|---|---|---|
| `/login` | GET, POST | public | Login form; sets Flask-Login session cookie |
| `/logout` | GET | any | Clears session, redirects to `/login` |
| `/admin/users` | GET | admin | Lists all users |
| `/admin/users/add` | POST | admin | Creates new user with hashed password |
| `/admin/users/<id>/toggle` | POST | admin | Activates/deactivates a user |
| `/admin/users/<id>/reset-password` | POST | admin | Sets a new password |

---

### `controllers/api_bp.py` — Blueprint: `api`

| Route | Method | Role | Description |
|---|---|---|---|
| `/set-lang/<lang>` | GET | public | Sets `mizan_lang` cookie to `uz`, `en`, or `ru` |
| `/api/get/<table>/<id>` | GET | login | Returns a single record as JSON |
| `/api/update/<table>/<id>` | POST | manager, admin | Updates a record; numeric fields auto-cast |
| `/api/delete/<table>/<id>` | POST | manager, admin | Deletes/soft-deletes a record |

---

### `controllers/dashboard_bp.py` — Blueprint: `dashboard`

| Route | Role | Description |
|---|---|---|
| `/` | login | Main dashboard — calls `get_dashboard_data()` and `get_loan_summary()` |

---

### `controllers/staff_bp.py` — Blueprint: `staff`

| Route | Role | Description |
|---|---|---|
| `/staff` | login | Staff list with salary, tax, social totals |
| `/hourly` | login | Per-production-staff hourly rate breakdown |
| `/kpi` | login | Staff KPI table (hours, utilization, revenue value) |

---

### `controllers/projects_bp.py` — Blueprint: `projects`

| Route | Role | Description |
|---|---|---|
| `/projects` | login | Project list with total hours and staff count |
| `/budget` | login | Plan vs actual budget comparison |

---

### `controllers/equipment_bp.py` — Blueprint: `equipment`

| Route | Role | Description |
|---|---|---|
| `/equipment` | login | Lists all personal equipment, general equipment, licenses, and overhead with monthly totals |

---

### `controllers/accounting_bp.py` — Blueprint: `accounting`

| Route | Role | Description |
|---|---|---|
| `/accounting` | manager, admin | Universal data entry hub (POST for all data types) |

The `/accounting` page is the primary write interface. One POST endpoint handles 7 different `section` values via a `section` form field:

| Section | What it does |
|---|---|
| `transaction` | Insert new external or internal transaction |
| `project` | Create new project |
| `staff` | Add new staff or update salary |
| `equipment` | Add personal or general equipment |
| `license` | Add personal license |
| `overhead` | Add or update overhead cost item |
| `rate` | Record USD exchange rate |
| `loan` | Add loan or record loan payment |

---

### `controllers/pricing_bp.py` — Blueprint: `pricing`

| Route | Method | Role | Description |
|---|---|---|---|
| `/pricing` | GET, POST | login (viewer read-only) | Pricing calculator — computes min/target/premium contract amounts; can save estimate to project |

---

### `controllers/cashflow_bp.py` — Blueprint: `cashflow`

| Route | Role | Description |
|---|---|---|
| `/cashflow` | login | Monthly cash flow table + payment method summary |

---

### `controllers/loans_bp.py` — Blueprint: `loans`

| Route | Role | Description |
|---|---|---|
| `/loans` | login | Loan list with `olgan`/`bergan` sections + summary |

---

### `controllers/external_bp.py` — Blueprint: `external`

| Route | Role | Description |
|---|---|---|
| `/external` | login | All external transactions (client-facing) with income/expense/pending totals |

---

### `controllers/internal_bp.py` — Blueprint: `internal`

| Route | Role | Description |
|---|---|---|
| `/internal` | login | All internal transactions (firm expenses) with totals |

---

### `controllers/import_export_bp.py` — Blueprint: `import_export`

| Route | Method | Role | Description |
|---|---|---|---|
| `/import` | GET, POST | admin | NIZAM CRM xlsx import |
| `/api/import-excel/analyze` | POST | admin | Generic Excel import with column mapping |
| `/export` | GET | manager, admin | Export page |
| `/api/export/xlsx` | GET | manager, admin | Generates multi-sheet Excel workbook with dashboard, staff, hourly rates, budget, KPI |

---

### `controllers/periods_bp.py` — Blueprint: `periods`

| Route | Method | Role | Description |
|---|---|---|---|
| `/periods` | GET, POST | manager, admin | Fiscal period management |

POST `action` values: `create`, `soft_close`, `hard_close`, `reopen`, `delete`.

---

### `controllers/settings_bp.py` — Blueprint: `settings`

| Route | Method | Role | Description |
|---|---|---|---|
| `/settings` | GET, POST | admin | Edit all settings |
| `/settings/rates` | POST | admin | Add/update an exchange rate |
| `/settings/fetch-rate` | GET | admin | Fetch rate from CBU for a given date (returns JSON) |

---

### `controllers/reference_bp.py` — Blueprint: `reference`

| Route | Method | Role | Description |
|---|---|---|---|
| `/reference/categories` | GET, POST | admin | Manage transaction category hierarchy |
| `/reference/counterparties` | GET, POST | admin | Manage counterparty registry and aliases |

---

## 10. Supporting Files

### `utils.py`

Three helpers used by every controller:

| Function | Description |
|---|---|
| `get_lang()` | Reads `mizan_lang` cookie; defaults to `uz` |
| `t(key, *args)` | Translates a key in the current language; args are substituted into `{0}`, `{1}` placeholders |
| `fmt(n, decimals=0)` | Formats a number with thousands separators: `fmt(12850000)` → `"12,850,000"` |
| `render_page(page, template_name, **ctx)` | Calls `render_template(template_name, page=page, t=t, fmt=fmt, lang=lang, **ctx)`. The `page` variable is the current page name (for active-link highlighting in the sidebar). |

Every template receives `t`, `fmt`, and `lang` automatically via `render_page`.

---

### `translations.py`

Contains one large `TRANSLATIONS` dict: `TRANSLATIONS['uz'|'en'|'ru'][key]` → translated string.

`get_text(lang, key, *args)` returns the translation for the given language and key, substituting `*args` via `.format(*args)`. Falls back to Uzbek if the key is missing in the requested language, then to the key itself.

All sidebar labels, page titles, column headers, alert messages, and form labels are stored here. New UI strings must be added to all three languages.

---

### `import_nizam.py`

NIZAM CRM integration. Contains:

- **`NIZAM_MAP`** — dict mapping NIZAM full names (uppercase) to `(v8_short_name, role, department, staff_type)`. This is the source of truth for staff-name-to-DB-record mapping.
- **`NON_BILLABLE`** — keywords in project names that mark a project as internal/non-billable.
- **`DEFAULT_SALARIES`** — dict mapping short name → `(base_salary, premium)` for seeding on first run.
- **`PERSONAL_EQUIPMENT`**, **`PERSONAL_LICENSES`**, **`GENERAL_EQUIPMENT`** — seed data lists.

**`ensure_staff_exists(conn)`** — inserts all known staff on startup. Safe to call repeatedly (`INSERT OR IGNORE`). Also seeds salaries if missing.

**`seed_equipment_and_licenses(conn)`** — inserts equipment, general equipment, and licenses once (checks if count=0 first).

**`import_nizam_file(filepath, period)`** — main import function:
1. Opens the `.xlsx` file (NIZAM time-tracking export format).
2. Reads employee column names from row 2.
3. Builds `staff_map` (NIZAM name → staff.id) using three-level matching: exact DB lookup → NIZAM_MAP exact → NIZAM_MAP fuzzy (first 3 chars of each name part).
4. Iterates rows (row 4+), skips non-billable and zero-hour rows.
5. Creates projects by name (`INSERT OR IGNORE`).
6. Inserts hours with `INSERT OR REPLACE INTO project_hours`.
7. Returns stats: `{projects, hour_entries, skipped, employees_mapped, unmatched_names}`.

The `period` parameter is stored in `project_hours.period`. Pass `'YYYY-MM'` format for a specific month or `'all'` for combined import.

---

### `database.py`

Legacy compatibility shim. The original monolithic `database.py` (~1650 lines) was refactored into the `models/` package. This file now just re-exports from `models.base` for any code that still imports directly from `database`.

---

## 11. Templates

All templates extend `base.html` via Jinja2 template inheritance (`{% extends 'base.html' %}`).

`base.html` provides:
- Full sidebar with role-conditional nav links
- Language switcher (UZ / EN / RU)
- `{% block content %}` where each page renders
- `{% block scripts %}` for page-specific JS
- User info display (role badge, username, logout)

Each template receives the standard context: `page`, `t`, `fmt`, `lang`, plus page-specific variables.

| Template | Route | Key template variables |
|---|---|---|
| `login.html` | `/login` | `error` |
| `admin_users.html` | `/admin/users` | `users` |
| `dashboard.html` | `/` | `data`, `loan_data`, CSS class flags |
| `staff.html` | `/staff` | `rows`, `depts`, `types` |
| `hourly.html` | `/hourly` | `rows`, `depts`, `eff_hrs`, `mult` |
| `kpi.html` | `/kpi` | `kpi` |
| `equipment.html` | `/equipment` | `personal`, `general`, `licenses`, `overhead`, totals |
| `projects.html` | `/projects` | `projects`, `statuses` |
| `budget.html` | `/budget` | `rows`, `totals`, `t_d_pct` |
| `accounting.html` | `/accounting` | `msg`, `today_str`, `projects`, `staff_list`, `open_loans`, `recent` |
| `pricing.html` | `/pricing` | `staff_list`, `pricing_projects`, `result` |
| `cashflow.html` | `/cashflow` | `cf`, `pay_summary` |
| `loans.html` | `/loans` | `loans`, `summary`, `taken`, `given` |
| `external.html` | `/external` | `txs`, income/expense totals, filter sets |
| `internal.html` | `/internal` | `txs`, amount/paid totals, filter sets |
| `import.html` | `/import` | `result_html` |
| `export.html` | `/export` | _(none — static UI)_ |
| `periods.html` | `/periods` | `period_info`, `stats`, `suggested`, `msg` |
| `settings.html` | `/settings` | `settings`, `rates`, `today` |
| `categories.html` | `/reference/categories` | `tree`, `roots`, `msg` |
| `counterparties.html` | `/reference/counterparties` | `cp_list`, `msg` |

---

## 12. Data Import Pipeline

### NIZAM CRM Import (`/import`)

The firm uses NIZAM CRM for time tracking. The export is a `.xlsx` file with this structure:

```
Row 1: (ignored / metadata)
Row 2: Columns 1-3 are date/project/total-hours headers; columns 4+ are employee names
Row 3: (ignored)
Row 4+: Data rows — col 2 = project name, col 3 = total hours (HH:MM format), col 4+ = per-employee hours
```

Hours are in `HH:MM` format and parsed by `parse_hhmm()` to decimal hours.

**Flow:**
1. Upload `.xlsx` on the `/import` page
2. `import_nizam_file(filepath, period)` is called
3. Staff are matched from NIZAM names → DB staff IDs
4. Unmatched names are reported back (visible in the import result UI)
5. Projects are auto-created if not in DB
6. `project_hours` rows are upserted (`INSERT OR REPLACE` — existing rows overwritten for same `period`)

### Generic Excel Import (`/api/import-excel/analyze`)

For importing historical financial transaction data:

1. Upload `.xlsx` file
2. `analyze_excel_for_import()` scans headers on each sheet and detects column types + sheet type
3. An auto-generated column mapping is built: `{column_index: db_field}`
4. `import_excel_data()` reads each data row, maps fields, and inserts into `transactions`
5. Projects referenced by name in the sheet are auto-created

---

## 13. API Endpoints Reference

| Endpoint | Method | Role | Returns |
|---|---|---|---|
| `GET /api/get/<table>/<id>` | GET | login | JSON record |
| `POST /api/update/<table>/<id>` | POST | manager, admin | `{"status":"ok"}` or 400 |
| `POST /api/delete/<table>/<id>` | POST | manager, admin | `{"status":"ok"}` or 400 |
| `GET /set-lang/<lang>` | GET | public | Redirect (sets cookie) |
| `GET /api/export/xlsx` | GET | manager, admin | `.xlsx` file download |
| `POST /api/import-excel/analyze` | POST | admin | HTML result page |
| `GET /settings/fetch-rate` | GET | admin | `{"rate": float}` or `{"error": str}` |

---

## 14. Role-Based Access Control Matrix

| Page / Action | viewer | manager | admin |
|---|---|---|---|
| Dashboard `/` | ✅ | ✅ | ✅ |
| Staff `/staff` | ✅ | ✅ | ✅ |
| Hourly `/hourly` | ✅ | ✅ | ✅ |
| Equipment `/equipment` | ✅ | ✅ | ✅ |
| Projects `/projects` | ✅ | ✅ | ✅ |
| Budget `/budget` | ✅ | ✅ | ✅ |
| KPI `/kpi` | ✅ | ✅ | ✅ |
| Pricing `/pricing` (view) | ✅ | ✅ | ✅ |
| Pricing `/pricing` (POST) | ❌ | ✅ | ✅ |
| Cash Flow `/cashflow` | ✅ | ✅ | ✅ |
| External `/external` | ✅ | ✅ | ✅ |
| Internal `/internal` | ✅ | ✅ | ✅ |
| Loans `/loans` | ✅ | ✅ | ✅ |
| Accounting `/accounting` | ❌ | ✅ | ✅ |
| Export `/export` | ❌ | ✅ | ✅ |
| Fiscal Periods `/periods` | ❌ | ✅ | ✅ |
| CRUD API update/delete | ❌ | ✅ | ✅ |
| Settings `/settings` | ❌ | ❌ | ✅ |
| NIZAM Import `/import` | ❌ | ❌ | ✅ |
| Reference Categories | ❌ | ❌ | ✅ |
| Reference Counterparties | ❌ | ❌ | ✅ |
| User Management `/admin/users` | ❌ | ❌ | ✅ |

---

## 15. Key Invariants & Rules Never to Break

These rules reflect design decisions that are load-bearing for the correctness of the financial model. Violating them silently produces wrong numbers.

1. **Risk coefficient goes on price, never on cost.** `mizan_cost` is always `Σ(hours × cost_rate)`. The `risk_coefficient` is only multiplied when computing `mizan_price` for display, or the `minimum_contract` in the Pricing Engine. It never affects `profit` or `total_expense`.

2. **Cash flow = actual paid, not invoiced.** Every cash flow calculation uses `paid` (money actually moved), never `amount` (money invoiced). The gap between `amount` and `paid` is "outstanding" tracked in AR aging.

3. **Available hours ≈ 151/month, not 176.** The `calculate_hourly_rate()` function always recomputes available hours from settings using `((365 − 104 − holidays − leave) / 12) × 8`. Never hard-code 176 or 160.

4. **Proportional distribution is by hours, not head count.** Admin costs, general equipment, and overhead are always split by each production staff member's share of billable hours in the latest period. Dividing equally by head count is only the last-resort fallback when there are zero hours at all.

5. **Salary history uses end_date=NULL for current salary.** When querying the current active salary, always filter `WHERE end_date IS NULL`. When updating a salary, always close the old row (`UPDATE SET end_date=today`) before inserting the new one.

6. **Closed periods use snapshot rates.** `calculate_project_cost()` calls `is_period_closed(period)` for every `(staff, period)` group. If closed AND `applied_cost_amount > 0`, it uses snapshot data. Never bypass this check or historical P&L reports will change retroactively.

7. **NIZAM import uses INSERT OR REPLACE.** Re-importing the same file for the same period overwrites existing hours. This is intentional — the import is idempotent. Do not change to INSERT OR IGNORE.

8. **`direction` distinguishes transaction types.** `'external'` = client-facing money. `'internal'` = firm expenses. This split must be preserved in all queries. Cash flow, AR aging, and project profitability all depend on filtering by `direction`.

9. **`is_billable = 0` projects are excluded.** Internal projects (office tasks, website, training) have `is_billable=0`. They are excluded from hourly rate calculations (share denominator), project cost reports, budget, KPI, capacity, and dashboard. NIZAM's NON_BILLABLE list maps these project names to skip them at import time.

10. **The `billing_multiplier` (2.0×) absorbs everything.** There is no separate utilization adjustment, margin on top of billing rate, or efficiency factor added elsewhere. The 2× markup is the single number that covers all of: utilization loss (75%), target gross margin (50%), and risk buffer. Do not add extra multipliers without revisiting the whole rate chain.

---

*Document generated: 2026-05-27. Source: full read of all `.py` and `.html` files in the repository.*
