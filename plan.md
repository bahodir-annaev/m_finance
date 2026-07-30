# MIZAN Finance v4.0 — Strategic Plan

## 1. What Is the App Business Logic?

MIZAN Finance is a **CFO-level financial management system for an architecture firm**. It replaces a legacy Excel-based workflow and integrates with the NIZAM CRM for time-tracking data. The entire backend is Python/Flask with SQLite; the UI is server-side rendered Jinja2 templates.

### Core Financial Model

The system is built around a precise hourly cost model:

```
Available Hours = (365 - 104 weekends - 14 holidays - 20 leave days) / 12 × 8 ≈ 151 h/month
Cost Rate       = Total Monthly Cost / Available Hours
Billing Rate    = Cost Rate × Markup (default 2.0×)
```

Admin/overhead costs are **not** split by head count — they are distributed proportionally to each production staff member based on their share of that period's billable hours. This avoids the common mistake of double-counting when staff sizes change.

### Pages and Modules

| Route | Module | Business Purpose |
|---|---|---|
| `/` | Dashboard (09) | CFO overview: burn rate, runway, capacity %, AR aging, FX P&L, top projects |
| `/staff` | Staff (03) | Employee register: production vs admin, salary history, staff codes (MZ-/MA-) |
| `/hourly` | Hourly Rate (07) | Derives cost rate and billing rate per staff member from salary + equipment + licenses + overhead share |
| `/equipment` | Equipment (06) | Personal (per-staff) and general equipment with straight-line depreciation; monthly amortization feeds into cost rate |
| `/projects` | Projects (04) | Project register with client, contract value, currency, risk coefficient, estimated/actual hours |
| `/budget` | Budget (08) | Plan vs actual: compares frozen planned cost/revenue against real transaction data |
| `/kpi` | KPI (10) | Staff KPIs: revenue/hour, profit/hour, utilization rate, billing efficiency |
| `/accounting` | Accounting (+) | Main data-entry hub: transactions, projects, staff, equipment, licenses, overhead, exchange rates, loan payments |
| `/pricing` | Pricing ($$) | Risk-adjusted pricing engine: 4-factor risk matrix (deadline, client type, complexity, currency) → Price = Cost × Risk |
| `/cashflow` | Cash Flow (CF) | Actual-only monthly cash flow (no theoretical accruals); FX gain/loss tracked at transaction-date rates |
| `/loans` | Loans (QZ) | Bilateral debt ledger: loans taken (olgan) and given (bergan), with repayment tracking |
| `/external` | External Tx (01) | Client income (tushum), outsourcing costs, materials, MIZAN monthly fees, final settlements |
| `/internal` | Internal Tx (02) | Salary disbursements, overhead payments, internal transfers |
| `/settings` | Settings (05) | Exchange rates (date-based), markup coefficient, tax/social rates, available-hours parameters |
| `/import` | Import | NIZAM CRM Excel import: maps employee names to internal staff records, imports project hours |
| `/export` | Export | Full financial export to multi-sheet Excel workbook (openpyxl) |

### Key Business Rules

1. **Cost allocation**: Admin staff cost + general equipment depreciation + overhead → distributed to production staff proportional to their share of current-period billable hours.
2. **Risk coefficient**: Applied only at the pricing stage (`Price = Cost × Risk`). Not baked into cost calculations, so cost data stays clean.
3. **Cash flow**: Only real transactions are recorded. Theoretical salary accruals are excluded to avoid double-counting.
4. **FX**: Each transaction stores the exchange rate at the time of entry. FX gain/loss is computed as the difference between transaction-date rate and current rate on outstanding receivables.
5. **Burn rate**: Fixed costs only (salary + overhead + depreciation), excluding variable project costs.
6. **Multi-language**: All UI strings go through `t(key)` → `translations.py` → cookie `mizan_lang`. Three languages: Uzbek (default), English, Russian.
7. **NIZAM integration**: `import_nizam.py` maps NIZAM CRM employee names to internal staff records, and seeds default salaries on first run.

### Database Tables (15 tables)

`staff`, `salary_history`, `personal_equipment`, `general_equipment`, `personal_licenses`, `overhead`, `projects`, `project_hours`, `external_transactions`, `internal_transactions`, `loans`, `loan_payments`, `settings`, `exchange_rates`, and a generic CRUD API covering all writable tables.

---

## 2. How to Implement Local-Network-Only Access with Auth and RBAC

### Current State

The app has **no authentication** and binds to all interfaces (implicitly `0.0.0.0`). Every route is publicly accessible to anyone who can reach the port.

### Threat Model

- Users: firm staff (5–20 people) on the same office LAN or VPN
- Threats to address: access from outside the LAN; accidental edits by read-only users; unauthorized access to salary/financial data
- Non-threats: encryption in transit (LAN-only, internal trust), sophisticated attacks

---

### Part A: Network Restriction (LAN-only)

**Approach: IP whitelist middleware**

Do not rely solely on bind address — add a Flask `before_request` hook that rejects any request not originating from a local network range.

```python
# In app.py
ALLOWED_NETWORKS = [
    '127.0.0.1',
    '::1',
    '192.168.',   # Class C private
    '10.',         # Class A private
    '172.',        # Class B private (172.16–172.31)
]

@app.before_request
def restrict_to_local_network():
    ip = request.remote_addr
    if not any(ip.startswith(prefix) for prefix in ALLOWED_NETWORKS):
        return 'Access denied: this app is only available on the local network.', 403
```

> **Note on reverse proxies**: If the app is ever placed behind nginx/Caddy, use `X-Forwarded-For` with a trusted proxy check instead of `remote_addr`.

---

### Part B: Authentication

**Approach: Session-based login with `Flask-Login`**

Flask-Login is the standard, well-maintained solution. It handles session cookies, `@login_required`, and the "remember me" flow.

**Step 1 — Add `users` table** (in `database.py → init_db()`):

```sql
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'manager', 'viewer')),
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
```

**Step 2 — Password hashing**: Use `werkzeug.security.generate_password_hash` / `check_password_hash` (already a Flask dependency, no new package needed).

**Step 3 — Seed a default admin** on first run if the `users` table is empty:

```python
if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
    from werkzeug.security import generate_password_hash
    c.execute("INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
              ('admin', generate_password_hash('mizan2024'), 'admin'))
```

**Step 4 — Login / logout routes** (`/login`, `/logout`):
- `GET /login`: render a simple login form (standalone template, no sidebar nav)
- `POST /login`: validate credentials, call `login_user()`, redirect to `/`
- `GET /logout`: call `logout_user()`, redirect to `/login`

**Step 5 — Protect all routes** with `@login_required` decorator from Flask-Login.

**Dependencies to add**:
```
flask-login
```

---

### Part C: Role-Based Authorization (RBAC)

**Three roles:**

| Role | Description |
|---|---|
| `admin` | Full access: all reads + writes + settings + import/export + user management |
| `manager` | Operational access: data entry, accounting, projects, transactions; no settings, no user management |
| `viewer` | Read-only access: dashboard, reports, KPI, cash flow |

**Route permission matrix:**

| Route | viewer | manager | admin |
|---|---|---|---|
| `GET /` (dashboard) | ✅ | ✅ | ✅ |
| `GET /staff`, `/hourly`, `/equipment`, `/projects`, `/budget`, `/kpi` | ✅ | ✅ | ✅ |
| `GET /cashflow`, `/loans`, `/external`, `/internal` | ✅ | ✅ | ✅ |
| `GET /pricing` | ✅ | ✅ | ✅ |
| `POST /accounting` (write transactions, add staff, add equipment) | ❌ | ✅ | ✅ |
| `POST /pricing` (freeze plan) | ❌ | ✅ | ✅ |
| `POST /api/update/*`, `POST /api/delete/*` | ❌ | ✅ | ✅ |
| `GET/POST /settings` | ❌ | ❌ | ✅ |
| `GET/POST /import` | ❌ | ❌ | ✅ |
| `GET /export` | ❌ | ✅ | ✅ |
| User management (new `/admin/users`) | ❌ | ❌ | ✅ |

**Implementation — role decorator:**

```python
from functools import wraps
from flask_login import current_user
from flask import abort

def require_role(*roles):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect('/login')
            if current_user.role not in roles:
                abort(403)
            return f(*args, **kwargs)
        return wrapped
    return decorator

# Usage:
@app.route('/settings', methods=['GET', 'POST'])
@require_role('admin')
def settings():
    ...

@app.route('/accounting', methods=['GET', 'POST'])
@require_role('admin', 'manager')
def accounting_page():
    ...
```

For viewer-accessible pages that have write operations embedded (like the edit modals on `/external`), the write API endpoints (`/api/update/*`, `/api/delete/*`) are separately protected — the page renders but the action buttons can be hidden in the template with `{% if current_user.role != 'viewer' %}`.

---

### Part D: User Management Page

Add a `/admin/users` route (admin only) to:
- List all users
- Add a new user (username, password, role)
- Deactivate/reactivate users
- Reset passwords

This avoids any need to touch the database manually.

---

### Implementation Effort Estimate

| Task | Estimated Time |
|---|---|
| Add `users` table + seed logic | 1 hour |
| Flask-Login setup + login/logout pages | 2 hours |
| IP whitelist middleware | 30 min |
| Role decorator + apply to all routes | 3 hours |
| User management page | 2 hours |
| Hide write-action UI elements for viewers | 2 hours |
| Add user context to sidebar (show username, logout button) | 1 hour |
| **Total** | **~12 hours** |

This is a purely additive change — no existing business logic is touched.

---

## 3. What Would It Take to Rewrite the App in NestJS?

### Why Consider It

NestJS would provide: TypeScript type safety across the full stack, better separation of concerns (controllers/services/modules), a built-in dependency injection system, and a richer ecosystem for things like API documentation (Swagger), WebSockets, and mobile/SPA frontend integration.

### Architecture Decision

There are two viable NestJS architectures:

**Option A — SSR (Server-Side Rendered), same UX as today**
- NestJS + Handlebars or EJS templates
- Similar to current Flask approach
- No React/Vue, no build step beyond TypeScript compilation
- Closest 1-to-1 replacement

**Option B — REST API + SPA frontend**
- NestJS as a pure REST/GraphQL API
- React or Vue frontend (Vite)
- Larger scope, but better long-term if mobile clients or third-party integrations are planned

For a like-for-like rewrite, **Option A** is recommended. Option B adds significant frontend scope on top of an already complex backend rewrite.

---

### Module Breakdown

| Module | Current (Python) | NestJS Equivalent | Lines of Work |
|---|---|---|---|
| Database schema | `database.py init_db()` ~300 lines | TypeORM entities (15 entities + migrations) | ~500 lines |
| Financial calculation engine | `database.py` ~1350 lines | `FinanceService`, `HourlyService`, `ProjectService`, `KpiService`, `CashFlowService` | ~2000 lines (TypeScript is more verbose) |
| Route handlers / UI | `app.py` ~2500 lines | NestJS controllers + Handlebars/EJS templates | ~3000 lines |
| Import/Export | `import_nizam.py` + `app.py export route` | `ImportModule` using `xlsx` npm package | ~400 lines |
| i18n | `translations.py` | `nestjs-i18n` package + JSON locale files | ~200 lines migration + package setup |
| Auth + RBAC | (new) | `@nestjs/passport` + `passport-local` + Guards | ~300 lines |
| Tests | 3 standalone Python scripts | Jest + Supertest integration tests | ~500 lines to port |

---

### Effort Estimate

| Phase | Task | Days |
|---|---|---|
| 1 | NestJS project setup, TypeORM config, SQLite connection, all 15 entities | 3 |
| 2 | Port financial calculation engine (hourly rates, cost model, KPI, cash flow, AR aging, burn rate) | 6–8 |
| 3 | Port all route handlers + build Handlebars templates for all 15 pages | 8–10 |
| 4 | Port accounting data-entry (all form POST handlers, 8 sections) | 3 |
| 5 | Port pricing engine (risk matrix, plan freeze) | 2 |
| 6 | Port import (NIZAM Excel parser) + export (multi-sheet xlsx) | 3 |
| 7 | Auth module (login, session, RBAC guards) | 2 |
| 8 | i18n (3 languages, all keys) | 2 |
| 9 | Testing + bug fixes + financial calculation validation | 5 |
| **Total** | | **34–38 developer-days** |

For a single developer: **7–8 calendar weeks** at full pace, or ~3 months part-time.

---

### Key Risks of a NestJS Rewrite

1. **Financial calculation correctness**: The calculation engine in `database.py` is the most complex part and contains carefully audited fixes (8 documented corrections). Re-implementing this in TypeScript without introducing regressions requires line-by-line validation against the Python output. A single off-by-one in available hours or overhead distribution silently produces wrong cost rates.

2. **SQLite migration strategy**: TypeORM handles schema migrations differently — the current "ALTER TABLE ... try/except" inline migration pattern needs to be replaced with a proper migration file system. All existing production data must survive the migration.

3. **Excel import**: `openpyxl` is more capable than most Node xlsx libraries for reading complex cell formats. The NIZAM import logic may need debugging in the Node ecosystem.

4. **SSR template complexity**: The current `HTML` string in `app.py` is ~350 lines of CSS + layout. Porting this to Handlebars/EJS is mechanical but tedious.

5. **No business gain from rewrite alone**: The rewrite does not add features — it only changes the runtime. All auth/RBAC improvements described in Section 2 can be added to the existing Flask app in ~12 hours, vs 34–38 days for a full NestJS rewrite.

---

### Recommendation

| Scenario | Recommendation |
|---|---|
| Need auth + LAN restriction quickly | Add to existing Flask app (Section 2), ~12 hours |
| Plan to build a mobile app or public API later | NestJS rewrite (Option B with REST API), budget 8–10 weeks |
| Team is TypeScript-first and wants long-term maintainability | NestJS rewrite (Option A, SSR), budget 7–8 weeks |
| Current team is Python-only | Stay on Flask; a forced migration adds risk with no user-visible gain |

If a NestJS rewrite is decided, the recommended sequence is:
1. First implement auth/RBAC in Flask (ship it, use it)
2. Then do the NestJS rewrite in a separate branch with the Flask app as a reference
3. Run both in parallel during validation — compare financial outputs table-by-table
4. Cut over only after 2+ weeks of parallel operation with matching outputs
