# MIZAN Finance v4 → v5 Change List

A planned set of schema and logic changes to add reporting flexibility, protect historical numbers, and consolidate transaction data — **without** turning MIZAN into a double-entry bookkeeping system.

---

## Guiding principles

1. **Stay a management tool, not a book of record.** No chart of accounts, no debit/credit journals, no statutory reports. The actual books live elsewhere (1С:Бухгалтерия or similar).
2. **Never silently rewrite history.** Once a period is past, its numbers stop moving even if upstream inputs are corrected.
3. **Maximize report flexibility through normalized dimensions** (counterparties, categories, tags, phases) rather than schema rigidity.
4. **Backwards-compatible migrations.** The existing inline `ALTER TABLE … ADD COLUMN` pattern in `init_db()` handles most cases; the rest use SQLite's create-copy-drop-rename idiom.
5. **Snapshot at posting, recompute only the open period.**

---

## Section 1 — Critical changes (history protection + the two explicit requests)

### 1.1 Snapshot cost rates onto `project_hours`

**Problem.** Today, project cost is recomputed live from current `salary_history`, current overhead pool, and current admin allocation. Correcting any input retroactively changes every historical project's cost number. Frozen budgets are protected; actuals are not.

**Change.** Add cost/billing rate snapshot columns to `project_hours`:

```sql
ALTER TABLE project_hours ADD COLUMN applied_cost_rate REAL;
ALTER TABLE project_hours ADD COLUMN applied_billing_rate REAL;
ALTER TABLE project_hours ADD COLUMN applied_cost_amount REAL;
ALTER TABLE project_hours ADD COLUMN applied_billing_amount REAL;
ALTER TABLE project_hours ADD COLUMN rate_snapshot_at TEXT;  -- ISO timestamp
ALTER TABLE project_hours ADD COLUMN rate_snapshot_source TEXT; -- 'auto_on_post' | 'period_close' | 'manual_recalc' | 'backfill'
```

**Snapshot policy.**
- On insert/update of a `project_hours` row in the **current open period**: compute and write the snapshot values immediately using current `calculate_hourly_rate()`.
- On **period close** (see §2.3): re-snapshot all rows in the closing period one final time, then mark them locked.
- For **past closed periods**: snapshot values are immutable; any correction is a reversal (see §1.3).

**Read pattern.** All historical reports read `applied_cost_amount` / `applied_billing_amount` directly. Only the current open period falls back to live calculation. This means `calculate_project_cost()` becomes:

```
mizan_cost = SUM(
    CASE
      WHEN period IS in_closed_period THEN applied_cost_amount
      ELSE hours * live_cost_rate(staff_id)
    END
) for all project_hours of project
```

**Migration.** For existing rows, backfill `applied_cost_*` using current rates as best-effort, with `rate_snapshot_source='backfill'` so they're flagged as approximate. Show a one-time UI warning that pre-v5 numbers are reconstructions.

**Affected files.** `database.py` — `calculate_project_cost`, `calculate_hourly_rate`, `import_excel_data`, NIZAM import. Add a new `snapshot_hours_rates(period)` function.

---

### 1.2 Unify `external_transactions` + `internal_transactions` into one `transactions` table

**Problem.** Two near-identical tables, distinguished only by which "side" of the firm the cash flows on. Reports that need both (e.g., total cash flow, AR + AP) require `UNION` boilerplate. The boundary is also fuzzy — salary disbursements logically belong to project cost, but live in `internal_transactions` without project linkage.

**Change.** New unified `transactions` table:

```sql
CREATE TABLE transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    direction TEXT NOT NULL,        -- 'external' | 'internal'
    tx_type TEXT NOT NULL,          -- FK to transaction_categories.code (see §2.2)
    date TEXT NOT NULL,
    ref_id TEXT,
    doc_id TEXT,

    project_id INTEGER REFERENCES projects(id),
    counterparty_id INTEGER REFERENCES counterparties(id),  -- see §2.1
    responsible_id INTEGER REFERENCES staff(id),

    description TEXT,
    notes TEXT,

    amount REAL NOT NULL DEFAULT 0,
    paid REAL NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'UZS',
    exchange_rate REAL,
    amount_usd REAL,

    contract_amount REAL DEFAULT 0,
    contract_currency TEXT,
    contract_amount_usd REAL,

    payment_type TEXT DEFAULT 'bank',  -- 'bank' | 'cash'
    deadline TEXT,
    status TEXT DEFAULT 'pending',     -- 'pending' | 'partial' | 'paid'

    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT
);

CREATE INDEX idx_tx_date ON transactions(date);
CREATE INDEX idx_tx_project ON transactions(project_id);
CREATE INDEX idx_tx_direction_type ON transactions(direction, tx_type);
CREATE INDEX idx_tx_counterparty ON transactions(counterparty_id);
```

**Column mapping from old tables.**

| Old (external_transactions) | Old (internal_transactions) | New (transactions) |
|---|---|---|
| `tx_type` | (always 'salary' / 'overhead' / etc.) | `tx_type` |
| `client` | `paid_to` | `counterparty_id` (resolve to FK) |
| `paid_to` | `paid_to` | `counterparty_id` |
| `responsible` | `responsible` | `responsible_id` (resolve to FK) |
| `income_desc` / `expense_desc` | `description` | `description` |
| `category` | `category` | `tx_type` (unified) |
| `contract_amount` | `contract_uzs` | `contract_amount` |
| `contract_usd` | `contract_usd` | `contract_amount_usd` |
| `payment_type` | — (assume 'bank') | `payment_type` |
| `deadline` | — | `deadline` |
| `status` | — | `status` (default 'paid' for internal) |

**Migration.** SQLite can't drop/rename tables in place trivially, so the standard idiom:
1. Create new `transactions` table
2. `INSERT INTO transactions (...) SELECT ..., 'external' AS direction, ... FROM external_transactions`
3. `INSERT INTO transactions (...) SELECT ..., 'internal' AS direction, ... FROM internal_transactions`
4. Rename old tables to `_legacy_external_transactions` / `_legacy_internal_transactions` (don't drop in first migration — keep for rollback safety)
5. After two stable releases, drop the legacy tables.

**Affected files.** All of `database.py` query logic touching transactions; `import_excel_data` (writes to both today); cash flow / AR aging / burn rate / runway calculations; UI list views.

**Note on `income_desc` vs `expense_desc`.** The dual-description columns on `external_transactions` are a smell — one row shouldn't describe both sides of a flow. Resolve at migration time: for rows where both are present, concatenate into `description` (e.g., `"income: X | expense: Y"`), or split into two rows if they describe genuinely separate events. Audit log entries for the migration choices.

---

### 1.3 Soft reversal pattern (replaces in-place editing of past transactions)

**Problem.** Today any row in any table is editable. Correcting yesterday's salary silently changes every historical hourly rate calculation that touched it.

**Change.** Two-part:
- Add `superseded_by_id` and `supersedes_id` (nullable, self-referential FK) to `transactions` and to `project_hours`.
- In closed periods (§2.3), the UI replaces the "Edit" button with "Reverse and re-enter." The reversal creates a new row in the current open period with `supersedes_id` pointing to the original, plus an offsetting amount.

**Behavior.**
- Open period: edit in place, audit log captures the change.
- Closed period: insert reversing + corrected entries dated today; both link via `supersedes_id` / `superseded_by_id`. Net effect on project lifetime totals is correct; per-period totals show the reversal in the current month.

---

## Section 2 — Dimension normalization (the big report-flexibility win)

### 2.1 `counterparties` — unified clients, vendors, lenders

**Problem.** `client`, `paid_to`, `counterparty` (on loans) are free-text strings. "Acme LLC" / "Acme Ltd" / "Acme" become three counterparties. No way to report "all money in/out with Acme."

**Change.**

```sql
CREATE TABLE counterparties (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                  -- canonical display name
    legal_name TEXT,
    tax_id TEXT,                         -- ИНН
    counterparty_type TEXT NOT NULL,     -- 'client' | 'vendor' | 'lender' | 'borrower' | 'employee' | 'other'
    country TEXT,
    default_currency TEXT DEFAULT 'UZS',
    is_active INTEGER DEFAULT 1,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE counterparty_aliases (   -- handles "Acme LLC" / "Acme Ltd" / "Acme"
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    counterparty_id INTEGER NOT NULL REFERENCES counterparties(id),
    alias TEXT NOT NULL,
    source TEXT,                          -- 'nizam' | 'manual' | 'import'
    UNIQUE(alias)
);
```

**Use.** `transactions.counterparty_id`, `loans.counterparty_id` (replace string column). Aliases let imports resolve "Acme Ltd" automatically to the canonical record.

**Migration.** Walk every distinct string in `client` / `paid_to` / `loans.counterparty`, dedupe with a fuzzy match assist (Levenshtein < 3 → propose merge), and let the user confirm consolidations one-time. Unresolved strings become new counterparties with type='other'.

**Reports enabled.** "Top 10 clients by revenue", "outstanding balance per counterparty (across all transactions and loans)", "concentration risk" (how much of revenue is from top-3 clients).

---

### 2.2 `transaction_categories` — hierarchical, replaces string `tx_type` and `category`

**Problem.** `tx_type` values (`tushum`, `outsourcing`, `material`, `mizan_monthly`, `yakuniy_hisob`) and `category` are hardcoded strings sprinkled through code and translation files. Adding a new category means changing code in multiple places. No grouping (e.g., "all variable costs").

**Change.**

```sql
CREATE TABLE transaction_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,           -- 'tushum', 'outsourcing', 'salary_disbursement', etc.
    name_uz TEXT NOT NULL,
    name_en TEXT,
    name_ru TEXT,
    parent_id INTEGER REFERENCES transaction_categories(id),
    direction TEXT NOT NULL,             -- 'in' | 'out' | 'both'
    affects_project_cost INTEGER DEFAULT 0,   -- 1 if this should appear in project P&L
    affects_cash_flow INTEGER DEFAULT 1,
    is_active INTEGER DEFAULT 1,
    sort_order INTEGER DEFAULT 100,
    notes TEXT
);
```

**Seed.** Migrate existing `tx_type` values as top-level rows. Build a 2-level hierarchy: e.g., `revenue` → (`tushum`, `mizan_monthly`, `yakuniy_hisob`); `direct_cost` → (`outsourcing`, `material`); `indirect_cost` → (`salary_disbursement`, `overhead_payment`).

**Use.** `transactions.tx_type` becomes FK to `transaction_categories.code` (or `category_id` INT). Reports can group at any level via parent traversal.

**Reports enabled.** Income statement-style summaries (revenue / direct cost / indirect cost / net), reorganization without code change, multi-language category names without scattered translation keys.

---

### 2.3 `fiscal_periods` — soft period close

**Problem.** No notion of "this month is closed." Reports change as data is corrected anywhere in time. The plan-vs-actual report can silently shift after the fact.

**Change.**

```sql
CREATE TABLE fiscal_periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,           -- '2026-04'
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open', -- 'open' | 'soft_closed' | 'hard_closed'
    closed_at TEXT,
    closed_by_user TEXT,
    notes TEXT
);
```

**Status meanings.**
- `open`: normal editing, live recomputation.
- `soft_closed`: rates and allocations are snapshotted; new entries discouraged via UI warning but allowed.
- `hard_closed`: no new entries in this period; corrections must use the reversal pattern (§1.3).

**Triggered actions on close.**
- Run `snapshot_hours_rates(period)` — finalizes `applied_cost_*` on all `project_hours` in that period.
- Run `snapshot_period_allocations(period)` — see §3.1.
- Run `snapshot_earned_revenue(period)` — see §3.2.

**UI.** A "Close period" button on a new admin screen. Closing is reversible to `soft_closed` → `open` until someone enters new data in a later period.

**Reports enabled.** Stable retrospective P&L, reliable plan-vs-actual that doesn't shift, KPI history that the firm can quote externally.

---

## Section 3 — Historical snapshots (stable retrospective reports)

### 3.1 `period_allocations` — frozen overhead/admin/equipment splits per period

**Problem.** The admin share, overhead share, and general equipment share per production staff member are recalculated live every time `calculate_hourly_rate()` runs. Changing any input (someone's hours, a new admin hire, an updated overhead row) ripples backward.

**Change.**

```sql
CREATE TABLE period_allocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period TEXT NOT NULL,                  -- 'YYYY-MM'
    staff_id INTEGER NOT NULL REFERENCES staff(id),
    billable_hours REAL DEFAULT 0,         -- hours used in the share calc
    hours_share REAL DEFAULT 0,            -- 0..1
    admin_share_amount REAL DEFAULT 0,     -- UZS
    overhead_share_amount REAL DEFAULT 0,  -- UZS
    general_equipment_share_amount REAL DEFAULT 0,
    personal_equipment_amount REAL DEFAULT 0,
    personal_licenses_amount REAL DEFAULT 0,
    gross_salary REAL DEFAULT 0,
    tax_amount REAL DEFAULT 0,
    social_amount REAL DEFAULT 0,
    total_monthly_cost REAL DEFAULT 0,
    cost_rate REAL DEFAULT 0,              -- the locked rate for the period
    billing_rate REAL DEFAULT 0,
    available_hours REAL DEFAULT 0,
    snapshotted_at TEXT NOT NULL,
    UNIQUE(period, staff_id)
);
```

**When written.** Automatically on period close (§2.3). For the current open period, this table is not populated — calculations remain live.

**Use.** `calculate_hourly_rate(staff_id, as_of_date)` checks if the date's period is in `period_allocations`; if yes, return the locked rate; if no, compute live.

**Reports enabled.** "What was Aziza's loaded cost in March 2025?" answerable exactly, even if her salary was retroactively edited yesterday. Same goes for project costs reconstructed for an old quarter.

---

### 3.2 `project_earned_revenue_snapshots` — monthly earned revenue lock

**Problem.** Earned revenue formula `contract_amount × min(actual_hours / estimated_hours, 1.0)` recomputes whenever any of those three numbers changes. There's no way to say "as of March 31, this project's earned revenue was X."

**Change.**

```sql
CREATE TABLE project_earned_revenue_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    period TEXT NOT NULL,                  -- 'YYYY-MM'
    as_of_date TEXT NOT NULL,
    contract_amount REAL,
    estimated_total_hours REAL,
    actual_hours_to_date REAL,
    completion_ratio REAL,                 -- min(actual/estimated, 1.0)
    earned_revenue REAL,
    cumulative_cost REAL,                  -- snapshot of project lifetime cost at period end
    cumulative_invoiced REAL,
    cumulative_received REAL,
    snapshotted_at TEXT NOT NULL,
    UNIQUE(project_id, period)
);
```

**Reports enabled.** Earned-vs-invoiced gap over time (WIP-like view without a balance sheet), revenue recognition trail for management, "at the end of Q1 we had recognized X of revenue on this project, by Q2 we'd recognized Y."

---

## Section 4 — New dimensions for flexible reporting

### 4.1 `transaction_lines` — split a transaction across projects/categories

**Problem.** Today every transaction has at most one `project_id`. A single bank payment to a vendor that covers work on three projects must be entered as three separate transactions or attributed to one and have its split tracked in notes.

**Change.**

```sql
CREATE TABLE transaction_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    line_no INTEGER NOT NULL,
    project_id INTEGER REFERENCES projects(id),
    project_phase_id INTEGER REFERENCES project_phases(id),  -- see §4.3
    category_id INTEGER REFERENCES transaction_categories(id),
    amount REAL NOT NULL,
    description TEXT,
    UNIQUE(transaction_id, line_no)
);

CREATE INDEX idx_txline_project ON transaction_lines(project_id);
CREATE INDEX idx_txline_category ON transaction_lines(category_id);
```

**Constraint.** `SUM(transaction_lines.amount) = transactions.amount` for each transaction. Enforced in application code (SQLite triggers are an option but fragile).

**Backward compatibility.** Existing transactions with a single `project_id` are represented by either:
- A virtual single-line view (no actual row in `transaction_lines`), or
- A one-line row auto-created on migration.

Pick the latter for simplicity — every transaction has at least one line.

**Reports enabled.** True "project share of vendor X spend", clean cost allocation across multiple projects, ability to attribute overhead payments partially to specific projects when justified.

---

### 4.2 `tags` and `entity_tags` — free-form many-to-many labels

**Problem.** Some report dimensions don't fit into hierarchies (office location, business unit, client segment, marketing campaign, internal initiative). Adding a column for each would explode the schema.

**Change.**

```sql
CREATE TABLE tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    color TEXT,
    category TEXT,                         -- 'location' | 'segment' | 'initiative' | 'custom'
    is_active INTEGER DEFAULT 1
);

CREATE TABLE entity_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tag_id INTEGER NOT NULL REFERENCES tags(id),
    entity_type TEXT NOT NULL,             -- 'project' | 'transaction' | 'staff' | 'counterparty'
    entity_id INTEGER NOT NULL,
    UNIQUE(tag_id, entity_type, entity_id)
);

CREATE INDEX idx_etag_entity ON entity_tags(entity_type, entity_id);
CREATE INDEX idx_etag_tag ON entity_tags(tag_id);
```

**Reports enabled.** Slice any report by any tag combination. "Revenue from the public-sector segment in Tashkent in Q2." No schema change required when management invents a new way to slice the business.

---

### 4.3 `project_phases` — optional WBS-lite per project

**Problem.** Projects are atomic in current schema. Larger projects (e.g., a multi-stage architecture commission: concept → schematic → construction documents → site supervision) can't track plan-vs-actual at phase level.

**Change.**

```sql
CREATE TABLE project_phases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    code TEXT,                             -- 'CD', 'SD', etc.
    name TEXT NOT NULL,
    sort_order INTEGER DEFAULT 100,
    planned_hours REAL DEFAULT 0,
    planned_cost REAL DEFAULT 0,
    planned_revenue REAL DEFAULT 0,
    start_date TEXT,
    end_date TEXT,
    completion_percent REAL DEFAULT 0,
    status TEXT DEFAULT 'active',          -- 'active' | 'completed' | 'paused'
    UNIQUE(project_id, code)
);
```

**Use.** Optional. `project_hours.phase_id` (nullable). `transaction_lines.project_phase_id` (nullable). Projects without phases continue working unchanged.

**Reports enabled.** Phase-level burn rate, earned revenue by phase (more honest than overall % complete for multi-stage projects), staffing forecast per phase.

---

## Section 5 — Operational improvements

### 5.1 `audit_log` — lightweight change tracking

**Problem.** No record of who changed what when. Reports can shift and nobody knows why.

**Change.**

```sql
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    user TEXT,                             -- if user auth is added; else 'system' / hostname
    action TEXT NOT NULL,                  -- 'insert' | 'update' | 'delete' | 'period_close' | 'snapshot' | 'reverse'
    entity_type TEXT NOT NULL,             -- table name
    entity_id INTEGER,
    field TEXT,                            -- for updates: column name
    old_value TEXT,                        -- JSON or string
    new_value TEXT,
    context TEXT                           -- optional free-text (e.g. "monthly import from NIZAM")
);

CREATE INDEX idx_audit_entity ON audit_log(entity_type, entity_id);
CREATE INDEX idx_audit_timestamp ON audit_log(timestamp);
```

**Hook points.** Centralize in the existing `update_record` / `delete_record` helpers in `database.py` — wrap them so every mutation writes one row to `audit_log` before completing. Inserts and bulk imports log one summary row per batch.

**Storage policy.** Never auto-purge. The table is append-only. At 1000 changes/day it's ~365K rows/year — trivial for SQLite.

---

### 5.2 Indices and query performance

Most existing tables lack indices on the columns reports filter by. Add:

```sql
CREATE INDEX IF NOT EXISTS idx_ph_project_period ON project_hours(project_id, period);
CREATE INDEX IF NOT EXISTS idx_ph_staff_period ON project_hours(staff_id, period);
CREATE INDEX IF NOT EXISTS idx_sh_staff_dates ON salary_history(staff_id, start_date, end_date);
CREATE INDEX IF NOT EXISTS idx_xr_date ON exchange_rates(date);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
```

Plus indices created with each new table in §1–§4.

---

### 5.3 `updated_at` on mutable tables

Every table that allows updates should carry `updated_at TEXT`, set in `update_record`. Pairs with `audit_log` for "show me what changed in the last 24 hours."

---

### 5.4 Currency handling cleanup

The `amount` / `currency` / `amount_usd` / `exchange_rate` pattern on transactions is correct but inconsistently applied. Standardize:

- Every monetary column lives next to an explicit currency column or implicitly uses the row's `currency`.
- Add `currency` to `overhead.monthly_amount` and to `personal_licenses.annual_cost` (currently implicit UZS).
- Reports always compute totals in a chosen display currency, converting via `exchange_rates` with the snapshot rule (stored rate for closed periods, today's rate for open periods).

---

## Section 6 — What NOT to do

To keep scope contained, explicitly **out of scope**:

- No `journal_entries` / `journal_lines` / debit-credit balance enforcement
- No `accounts` / chart of accounts table
- No trial balance, no general ledger views
- No VAT/НДС module (taxes flow through outside MIZAN)
- No invoice/payment split (today's `amount` vs `paid` on a single row stays — adding `invoices` and `payments` separately is a deferred decision)
- No full earned-value PV/EV/AC system — the simple `actual / estimated` ratio stays

These exclusions are deliberate. They're the line between "management tool" and "becoming 1С."

---

## Recommended implementation order

Group changes into three migrations so each lands cleanly:

**Migration v5.1 — Dimension foundations** *(no behavior change, sets up FK targets)*
- §2.1 `counterparties` + aliases (with one-time consolidation UI)
- §2.2 `transaction_categories` (seeded from existing strings)
- §4.2 `tags` + `entity_tags`
- §5.1 `audit_log` (start logging immediately)
- §5.2 indices

**Migration v5.2 — Transaction unification + snapshots** *(the explicit requests, plus history protection)*
- §1.1 `applied_cost_*` columns on `project_hours` + snapshot function
- §2.3 `fiscal_periods` + soft close mechanism
- §3.1 `period_allocations`
- §3.2 `project_earned_revenue_snapshots`
- §1.2 unified `transactions` table (with legacy tables kept side-by-side for one release)
- §4.1 `transaction_lines`
- §1.3 reversal pattern (`supersedes_id` / `superseded_by_id`)

**Migration v5.3 — Optional flexibility extensions** *(when needed)*
- §4.3 `project_phases`
- §5.3 `updated_at` everywhere
- §5.4 currency normalization sweep
- Drop the legacy transaction tables once v5.2 has run stably for two months

---

## What this unlocks (the report wishlist made possible)

After all three migrations, reports that become trivial to write:

1. **Counterparty 360** — every flow with Acme Ltd: invoices, payments, outstanding, project links, multi-year revenue trend.
2. **Stable retrospective P&L** — March 2025 numbers reported on April 1 2025 match the numbers reported on April 1 2026.
3. **Cost category breakdown** — direct labor / outsourcing / materials / overhead / admin, with drill-down to source transactions.
4. **Tag-sliced anything** — revenue by client segment, by office location, by initiative.
5. **Audit trail** — "this month's revenue went up by 4M UZS yesterday — who changed what?"
6. **Multi-project payment splits** — true allocation of vendor payments across projects.
7. **Phase-level plan vs actual** — earned revenue and burn by phase for long projects.
8. **Reversal-aware totals** — corrections visible in current month, project-lifetime totals stay correct.
9. **Per-counterparty AR/AP** — outstanding payables and receivables consolidated across the unified `transactions` table.
10. **Currency-aware historical reports** — past periods reported at posting-date FX, current period at today's FX, with FX gain/loss isolated.
