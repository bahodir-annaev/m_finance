# Periodic Plan with Milestones — Design Proposals

> **Status: proposal, nothing implemented.** This document describes two alternative designs for
> a new feature and ends with a comparison and a recommendation. Choose one (or the phased path)
> and the chosen version becomes the implementation spec.

## The problem

The pricing calculator produces a **single overall price** for a whole project. On save
("PLAN ga saqlash"), `controllers/pricing_bp.py:44-55` freezes flat totals onto `projects`
(`planned_hours`, `planned_cost`, `planned_revenue`, `planned_outsourcing`, `planned_material`,
`plan_frozen_date`), and the Budget page (`controllers/projects_bp.py:28-94`) compares those
whole-project totals against whole-project actuals. Nothing on the plan side is period-aware, so
today the app cannot answer:

1. **Is the project on course?** (are we where the plan said we'd be by now?)
2. **Is it profitable month by month?** (plan income − expense vs actual income − expense, per month)

What is needed: a **periodic plan with milestones** — each milestone carrying a specific **type of
work**, an expected **income**, and an expected **expense** — compared against actuals period by
period.

Two designs follow. Both are grounded in scaffolding that already exists in the schema but has
never been wired up:

| Dormant asset | Where | State |
|---|---|---|
| `project_phases` table (name, code, sort order, planned hours/cost/revenue, start/end dates, completion %, status) | `models/base.py:450-458` | created on every init, zero reads/writes, no UI |
| `project_hours.phase_id` column | `models/base.py:596` (migration) | never populated |
| `transaction_lines` table with `project_phase_id` FK | `models/base.py:485-494` | dead — abandoned in favor of flat `transactions.category_id` (see comment at `models/base.py:609-610`) |
| `project_earned_revenue_snapshots` (per project × period) | `models/base.py:438-447` | never written or read |

---

# Part 0 — Shared foundation: the project × month actuals aggregator

Both versions need the same building block: **"what actually happened on project P in month M"**.
It must be defined so that its totals reconcile *exactly* with `calculate_project_cost()`
(`models/projects.py:51-177`) — otherwise the new pages, `/budget`, and project totals would show
three different numbers for the same project and nobody would trust any of them.

**Definition of monthly actuals:**

| Component | Source | Basis | Consistent with |
|---|---|---|---|
| **Actual income** | `transactions.paid` where `tx_type='tushum'`, bucketed by `strftime('%Y-%m', date)` | cash received | `models/projects.py:71-75` |
| **Actual labor expense** | `project_hours` grouped by `period` ('YYYY-MM'). Closed period with a snapshot → `applied_cost_amount`; otherwise `hours × calculate_hourly_rate(staff_id)['cost_rate']`, rate cached once per staff member | accrual (hours worked) | `models/projects.py:93-124` |
| **Actual direct expense** | `transactions.amount` where `tx_type IN ('outsourcing','material')`, bucketed by month of `date` | committed amount | `models/projects.py:76-85` |

`actual_expense(month) = labor + direct`. `actual_profit(month) = income − expense`.

Decisions baked into this definition:

- **The existing paid-vs-amount asymmetry is kept** (income counts cash `paid`; outsourcing and
  material count committed `amount`). That is exactly what FAKT already means on `/budget` and in
  the project P&L, which makes *"sum of the monthly rows equals `calculate_project_cost` totals"*
  a testable invariant rather than an approximation.
- **Labor on the fact side is raw cost, without the risk coefficient** — same as the Budget page
  (`pc['mizan_cost']` = `total_cost`, `models/projects.py:137`). The **plan** side carries risk
  (pricing freezes `planned_cost ← mizan_cost = total_cost × risk_coeff`, `models/pricing.py:63`,
  `controllers/pricing_bp.py:52`). Plan-includes-risk-buffer / fact-is-raw is the established
  budget-page semantic; both versions preserve it.
- `yakuniy_hisob` / `mizan_monthly` income types remain excluded from project income, exactly as
  in `calculate_project_cost` (documented limitation — if the firm later wants them counted, both
  functions change together).
- Monthly bucketing by `strftime('%Y-%m', date)` follows the existing precedent in
  `get_cash_flow_by_month()` (`models/transactions.py:5-43`). Hours already carry a `period`
  column in the same 'YYYY-MM' format, with a supporting index `idx_ph_project_period`.

SQL sketch (two grouped queries + a small Python merge that mirrors the closed-period logic of
`calculate_project_cost`):

```sql
-- money, bucketed by transaction date month
SELECT strftime('%Y-%m', date) AS period,
       SUM(CASE WHEN tx_type='tushum' THEN COALESCE(paid,0) ELSE 0 END)                 AS income,
       SUM(CASE WHEN tx_type IN ('outsourcing','material') THEN COALESCE(amount,0)
                ELSE 0 END)                                                             AS direct
FROM transactions
WHERE project_id = ?
GROUP BY period;

-- labor, bucketed by NIZAM period
SELECT ph.period, ph.staff_id,
       SUM(ph.hours)                            AS hours,
       SUM(COALESCE(ph.applied_cost_amount,0))  AS snapped_cost
FROM project_hours ph
WHERE ph.project_id = ?
GROUP BY ph.period, ph.staff_id;
```

Python merge, per `(period, staff)` row: if `is_period_closed(period)` and `snapped_cost > 0` →
`labor += snapped_cost`; else `labor += hours × rate_cache[staff_id]['cost_rate']` (one
`calculate_hourly_rate` call per staff member, cached — the same pattern as
`models/projects.py:96-121`).

---

# Part 1 — VERSION 1: "Oylik plan" (monthly plan grid)

**Philosophy: a milestone *is* a month.** One editable row per project per month, carrying the
work type, expected income, expected expense, and (optionally) planned hours. Actuals are matched
to plan rows **automatically by `project_id + month`** — nobody has to tag transactions or hours.
This answers both owner questions with **zero new process** for the team: hours keep arriving via
the NIZAM import, transactions keep being entered as today, and the plan/fact comparison falls out
of dates that are already recorded.

## 1.1 What the user sees

Two new pages plus one nav link:

**`/plan` — overview (all projects):**

```
[ Yo'lda: 4 ]  [ Ortda: 2 ]  [ Plan foyda (to-date): 310M ]  [ Fakt foyda (to-date): 268M ]

Proekt        | Oylar          | PLAN to-date            | FAKT to-date            | Farq %  | Holat
              |                | tushum | xarajat | foyda| tushum | xarajat | foyda|         |
Navoiy Tower  | 5 (01–05.2026) |  500M  |  300M   | 200M |  430M  |  310M   | 120M | −16.0%  | Ortda
Silk Plaza    | 3 (03–05.2026) |  240M  |  150M   |  90M |  250M  |  147M   | 103M |  +2.9%  | Yo'lda
```

**`/plan/<project_id>` — month grid for one project:**

```
Oy       | Ish turi   | PLAN                    | FAKT                    | Oy foydasi     | Kumulyativ foyda | Holat
         |            | tushum | xarajat | soat | tushum | xarajat | soat | plan  | fakt   | plan  | fakt     |
2026-01  | Eskiz      |  100M  |   60M   | 320  |  100M  |   55M   | 300  |  40M  |  45M ✓ |  40M  |  45M     |
2026-02  | AR         |  100M  |   60M   | 320  |    0   |   68M   | 350  |  40M  | −68M ✗ |  80M  | −23M     |
2026-03  | AR         |  100M  |   60M   | 320  |  180M  |   64M   | 330  |  40M  | 116M ✓ | 120M  |  93M     |
2026-04  | —          |    0   |    0    |   0  |   20M  |    5M   |  40  |   0   |  15M   | 120M  | 108M     | Rejadan tashqari
2026-05  | KJ (joriy) |  100M  |   60M   | 320  |   …    |   …     |  …   |   …   |  …     |   …   |  …       | joriy
2026-06  | Smeta      |  100M  |   60M   | 320  |        |         |      |       |        |       |          | (kelajak)
```

Rows are editable in a small modal (manager/admin), "+ Oy qo'shish" adds a month, and a collapsed
card can (re)generate the grid from the frozen pricing plan. A thin inline-CSS bar in the
cumulative cell visualizes fact vs plan (no chart library, matching the app).

Note the February row: the invoice that was planned for February actually landed in March. The
month grid shows exactly that (Feb fact income 0, Mar fact income 180M), and the **cumulative**
columns absorb the timing shift — which is why on-course status is judged on cumulative figures,
not single months.

## 1.2 Data model

**One new table** (added to `init_db()` next to the phases block, `models/base.py` ~line 458):

```sql
CREATE TABLE IF NOT EXISTS project_monthly_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    period TEXT NOT NULL,                     -- 'YYYY-MM'
    work_type TEXT,                           -- free text, datalist-assisted
    planned_income REAL NOT NULL DEFAULT 0,   -- UZS
    planned_expense REAL NOT NULL DEFAULT 0,  -- UZS, total (labor + outsourcing + material, risk-inclusive)
    planned_hours REAL DEFAULT 0,
    notes TEXT,
    source TEXT DEFAULT 'manual',             -- 'manual' | 'pricing'
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT,
    UNIQUE(project_id, period)
)
```

plus `CREATE INDEX IF NOT EXISTS idx_pmp_period ON project_monthly_plans(period)` in the indices
loop (`models/base.py:520-536`; the UNIQUE constraint already indexes `(project_id, period)`).

The table is appended to `_ALLOWED_TABLES` and `_DELETE_ALLOWED` (`models/base.py:105,112`), which
gives it the generic JSON API (`/api/update/project_monthly_plans/<id>`,
`/api/delete/project_monthly_plans/<id>`) **with per-field audit logging for free**. Hard delete
is correct for plan rows (no `is_active` column).

**Why a new table instead of reusing what exists:**

- `project_phases` is milestone-keyed (`UNIQUE(project_id, code)`) — the wrong natural key for a
  period grid, whose upsert key is `(project_id, period)`. It is also exactly the table Version 2
  needs intact.
- `project_earned_revenue_snapshots` has the right key but is semantically an *actuals snapshot*
  table (`actual_hours_to_date`, `earned_revenue`, …); repurposing it as an editable *plan* would
  be misleading and block its intended use.
- `transaction_lines` stays dead, per the codebase's explicit direction (`models/base.py:609-610`).

**Work type stays free text in V1**, assisted by a `<datalist>` (distinct values already used on
the project + a few seeded suggestions rendered in the template). No new dimension table, no
reference-page change — that is Version 2 territory.

**No changes to `projects` columns.** The frozen `planned_*` totals keep their current role for
`/budget`.

## 1.3 Model layer — new `models/monthly_plan.py`

| Function | Spec |
|---|---|
| `_add_months(period, n)` | 'YYYY-MM' arithmetic helper (pure). |
| `get_project_monthly_actuals(project_id)` | The Part 0 aggregator. Returns `{period: {income, labor, direct, expense, hours}}`. |
| `get_project_plan_rows(project_id)` | `SELECT * FROM project_monthly_plans WHERE project_id=? ORDER BY period`. |
| `upsert_plan_row(project_id, period, *, work_type, planned_income, planned_expense, planned_hours, notes, source='manual')` | `INSERT … ON CONFLICT(project_id, period) DO UPDATE`, sets `updated_at`. Validates `period` against `^\d{4}-(0[1-9]\|1[0-2])$`. Refuses edits to hard-closed fiscal months for non-admins (admin override, audited). Writes per-field audit entries like `update_record()` does. |
| `delete_plan_rows_for_project(project_id, source=None)` | Bulk delete used by regeneration; audit-logged. Row-level deletes go through the generic API. |
| `get_plan_vs_actual(project_id)` | Merges plan rows and actual months over `sorted(set(plan_periods) ∪ set(actual_periods))`. Per month: plan/fact income, expense, profit, deltas, flags `is_current`, `is_future`, `unplanned`. Plus cumulative series and the on-course verdict (§1.6). Returns `{months, cum, status, totals}`. |
| `get_plan_overview()` | One row per billable project having plan rows or hours: plan-to-date vs fact-to-date income/expense/profit, variance %, status badge, `plan_frozen_date`, planned month count. Grouped queries, no N+1. |
| `generate_plan_from_pricing(project_id, start_period, months, total_income, total_expense, total_hours, work_type=None, overwrite=False)` | Even spread: each month gets `round(total/months)`; the **last month absorbs the rounding remainder** so `Σ rows == frozen totals` exactly. Rows get `source='pricing'`. If rows already exist and `overwrite` is false → returns 0 and the controller shows a warning; with `overwrite` → deletes existing rows first (audit context "plan regenerated from pricing"). |

All public names re-exported through `models/__init__.py` (import block + `__all__`), because
controllers import from the `models` package.

## 1.4 Controllers / routes — new `controllers/plan_bp.py`

| Method & path | Access | Purpose |
|---|---|---|
| GET `/plan` | `@login_required` | Overview page (`render_page('plan', 'plan.html', …)`). |
| GET `/plan/<int:project_id>` | `@login_required` | Month-grid detail; passes `can_edit` (manager/admin — the dividends pattern). |
| POST `/api/plan/<int:project_id>/row` | `@require_role('manager','admin')` | JSON upsert of one month row (FormData via `fetch`; `{'status':'ok'}` / `{'error':…}, 400`). Dedicated endpoint because the natural key is `(project_id, period)`, not `id`. |
| POST `/api/plan/<int:project_id>/generate` | `@require_role('manager','admin')` | (Re)generate the spread from the current frozen plan. Params: `start`, `months`, `work_type`, `overwrite`. |

Row deletes reuse the existing generic `POST /api/delete/project_monthly_plans/<id>` — no new
code. The blueprint is registered in `app.py → create_app()` (one import line, one list entry).

**Change to `controllers/pricing_bp.py`:** the existing freeze `UPDATE projects SET …`
(lines 46-53) stays **byte-identical** (backward compatibility for `/budget`). Immediately after
it, if the new opt-in form fields are set:

```python
if request.form.get('gen_monthly_plan'):
    proj = conn2.execute("SELECT id FROM projects WHERE name=?", (save_to_project,)).fetchone()
    if proj:
        generate_plan_from_pricing(
            proj['id'],
            start_period=request.form.get('plan_start') or datetime.now().strftime('%Y-%m'),
            months=int(request.form.get('plan_months', risk_kwargs['deadline_months']) or 6),
            total_income=result['target_contract'],
            total_expense=result['minimum_contract'],   # mizan_cost + outsourcing + material
            total_hours=result['total_hours'],
            work_type=request.form.get('plan_work_type') or None,
            overwrite=bool(request.form.get('plan_overwrite')))
```

Using `minimum_contract` as the spread expense keeps the plan grid's expense semantics identical
to what `/budget` compares against (risk-inclusive labor + outsourcing + material).
`models/pricing.py` itself is untouched.

## 1.5 Templates / UI

- **`templates/plan.html`** (new) — extends `base.html`. `.stats` row: on-course count (green),
  off-course count (red), Σ plan-to-date profit, Σ fact-to-date profit. `.card > table` as in the
  §1.1 sketch; project names link to `/plan/<id>`; client-side search/filter/sort JS copied from
  `budget.html`; Excel link `/api/export/xlsx?sheet=plan`.
- **`templates/plan_detail.html`** (new) — header stats (contract, frozen-plan date, cumulative
  verdict badge); the month grid; per-row badges `Rejadan tashqari` (fact with no plan row),
  `joriy` (current month), muted style for future months; thin inline-CSS cumulative bar. With
  `can_edit`: rows carry `data-*` attributes, clicking opens a small modal (the `_tx_modal.html`
  mechanics: hidden overlay, form, `fetch('/api/plan/<pid>/row', {body: new FormData(f)})`,
  `location.reload()`); "+ Oy qo'shish" opens the same modal empty; delete inside the modal calls
  the generic delete API with a translated `confirm()` string; a collapsed "Plandan yaratish" card
  exposes the generate endpoint (`<input type="month">` start, month count, overwrite checkbox).
- **`templates/pricing.html`** — ~15 new lines inside the existing "PLAN ga saqlash" block:
  checkbox `gen_monthly_plan`, start month, months count (defaulted from the deadline select),
  optional work type, overwrite checkbox.
- **`templates/base.html`** — one nav line in the Data group, under Budget:
  `<a href="/plan" class="{% if page=='plan' %}active{% endif %}">…{{ t('nav_plan') }}</a>`.
- **Excel export** (`controllers/import_export_bp.py`, optional but cheap) — new sheet
  `'11-Oylik plan'` (Proekt, Oy, Ish turi, Plan tushum, Plan xarajat, Fakt tushum, Fakt xarajat,
  Farq %, Holat) + `SHEET_MAP['plan']` entry.

## 1.6 On-course / profitability logic (exact formulas)

Let `M0` = the current month. **"To-date" = all months strictly before `M0`.** The current month
is displayed but marked *joriy* and excluded from the verdict — income typically lands late in a
month, so including a half-finished month produces false alarms.

Per month `m`:

```
plan_profit(m)   = plan_income(m) − plan_expense(m)
fact_profit(m)   = fact_income(m) − fact_expense(m)
d_expense_pct(m) = (fact_expense − plan_expense) / max(plan_expense, 1) × 100
```

Cumulative to date: sums over months `< M0`. Months with fact but no plan contribute plan = 0;
months with plan but no fact contribute fact = 0 — **both deliberately count**, since both are
real deviations from the plan.

Three signals, one headline:

| Signal | Formula | Bands |
|---|---|---|
| **Expense badge** (continuity with `/budget`, `controllers/projects_bp.py:68-73`) | `(cum_fact_expense − cum_plan_expense) / max(cum_plan_expense,1) × 100` | `> +5%` Oshgan (red) · `< −10%` Byudjet ichida (green) · else Chegarada |
| **Collections badge** | `cum_fact_income / max(cum_plan_income, 1)` | `≥ 0.95` good · `0.85–0.95` amber · `< 0.85` red |
| **Headline on-course** | `profit_drift = (cum_fact_profit − cum_plan_profit) / max(cum_plan_income, 1) × 100` | `≥ −5` **Yo'lda** (green) · `−5…−15` **Chegarada** (amber) · `< −15` **Ortda** (red) · no completed months → **Boshlanmagan** |

`profit_drift` is normalized by planned **income**, not planned profit, so projects planned near
break-even cannot produce exploding percentages. The −5 sensitivity mirrors the budget page's
existing 5% convention.

Month profitability is shown directly per row: plan profit and fact profit side by side, the fact
cell colored `profit`/`loss` by sign.

## 1.7 Translations

New section `# ==================== MONTHLY PLAN ====================` in all three language
dicts (`translations.py`: uz ~line 8 block, en ~414, ru ~802), ~45 keys:

`nav_plan, plan_title, plan_subtitle, plan_detail_title` ({0} = project), `plan_detail_subtitle,
plan_th_project, plan_th_months, plan_th_period, plan_th_work_type, plan_th_income,
plan_th_expense, plan_th_hours, plan_th_profit, plan_group_plan, plan_group_fact, plan_group_diff,
plan_th_month_profit, plan_th_cum_profit, plan_th_status, plan_status_on, plan_status_edge,
plan_status_off, plan_status_none, plan_badge_unplanned, plan_badge_current, plan_to_date_note,
plan_add_month, plan_edit_title, plan_del_confirm, plan_save_ok, plan_closed_period_block,
plan_gen_title, plan_gen_btn, plan_gen_start, plan_gen_months, plan_gen_overwrite, plan_gen_done`
({0} = rows), `plan_gen_skipped, plan_stat_on, plan_stat_off, plan_stat_plan_profit,
plan_stat_fact_profit, pricing_gen_plan, pricing_gen_start, pricing_gen_months,
pricing_gen_worktype, pricing_gen_overwrite`.

## 1.8 Edge cases

- **USD contracts** — plan rows are stored in UZS only (pricing outputs UZS; `transactions.paid /
  amount` are UZS). The detail page can show a read-only USD hint at the current `usd_rate`. FX
  drift between plan-time and payment-time rates surfaces as income variance; pure FX effects
  remain visible via the existing `calculate_fx_gain_loss` (`models/projects.py:16-31`).
- **Plan revision after freeze** — allowed for manager/admin; every upsert/delete is audit-logged
  with old/new values; `source` distinguishes pricing-generated rows from manual ones;
  regeneration requires the explicit overwrite flag and is audited.
- **Actuals but no plan row** — month appears with plan = 0 and badge `Rejadan tashqari`; still
  enters the cumulative (drags the status — correct: unplanned activity is a deviation).
- **Plan but no actuals (past month)** — fact = 0, enters the cumulative (drags the status —
  correct: a planned month that didn't happen is a deviation). Future months never enter it.
- **Closed fiscal periods** — labor uses `applied_cost_amount` snapshots (identical rule to
  `calculate_project_cost`); plan-row edits for closed months are blocked for managers (admin
  override, audited).
- **Rounding** — the last generated month absorbs the remainder, so generated plan totals equal
  the frozen totals exactly.
- **NIZAM re-import** — harmless: `INSERT OR REPLACE` on `project_hours` changes hours, and the
  aggregator recomputes live.

## 1.9 Every file added or changed

| File | Change |
|---|---|
| `models/base.py` | + `project_monthly_plans` DDL in `init_db()`; + index; + entries in `_ALLOWED_TABLES` and `_DELETE_ALLOWED` |
| `models/monthly_plan.py` | **new** (~250 LOC) |
| `models/__init__.py` | + re-exports |
| `controllers/plan_bp.py` | **new** (~120 LOC) |
| `controllers/pricing_bp.py` | + read new form fields; + `generate_plan_from_pricing` call after the untouched freeze UPDATE |
| `app.py` | + import + register `plan_bp` |
| `templates/plan.html` | **new** |
| `templates/plan_detail.html` | **new** |
| `templates/pricing.html` | + plan-generation fields in the "PLAN ga saqlash" block |
| `templates/base.html` | + 1 nav line |
| `translations.py` | + ~45 keys × 3 languages |
| `controllers/import_export_bp.py` | (optional) + `'11-Oylik plan'` sheet + `SHEET_MAP` entry |

## 1.10 Effort and trade-offs

**Effort:** 12 files touched (5 new), ≈ 900–1,100 new LOC. Roughly one-and-a-half
"dividends features".

**Pros**
- Zero new process — actuals match themselves by month; NIZAM import and transaction entry are
  untouched.
- Answers both owner questions directly; numbers reconcile with `/budget` by construction.
- Small surface: fast to ship, easy to keep correct, low risk of stale data.
- Fully forward-compatible with Version 2 (disjoint tables, shared aggregator).

**Cons**
- No work breakdown inside a month — a month with two work stages is one blended row with one
  work-type text.
- No schedule awareness — the system sees money/hour deviations but cannot say a *stage* is late.
- Attribution is purely by date: a late invoice shifts income into the "wrong" month (mitigated by
  judging status on cumulative figures).
- Work type is free text: no i18n, typo-prone.

---

# Part 2 — VERSION 2: "Bosqichlar" (milestone objects with lifecycle)

**Philosophy: milestones are first-class objects, months are derived.** Revive the dormant
`project_phases` table as real milestones — named work stages (Eskiz, AR, KJ, …) with date ranges,
a proper work-type dimension, planned hours/income/expense split, a status workflow, completion %,
**explicit attribution of actuals** (transactions and hour-periods are tagged to milestones),
per-milestone earned value, a project detail page (the first in the app), a milestone schedule
generated at pricing freeze, and an on-course indicator on the dashboard. Monthly plan figures are
*derived* from milestones by day-weighted allocation, then compared with the same Part 0 actuals.

## 2.1 What the user sees

**`/projects/<id>` — new project detail page (the centerpiece):**

```
Navoiy Tower                                    [ Yo'lda ● ]  (money ✓ · schedule ✓ · collections ~)
[ Kontrakt: 900M ] [ Plan jami: 900M ] [ Earned value: 410M ] [ Fakt foyda: 93M ]

BOSQICHLAR
Bosqich   | Ish turi | Muddat            | Holat        | Bajarilish | PLAN            | FAKT            | EV   | CPI / SPI
Eskiz     | eskiz    | 01.01 – 15.02     | ✔ done       | ████ 100%  | 150M / 90M      | 150M / 84M      | 150M | 1.79 / 1.00
AR        | ar       | 16.02 – 30.04     | ● in_progress| ██▌  60%   | 350M / 210M     | 200M / 195M     | 210M | 1.08 / 0.87
KJ        | kj       | 01.05 – 30.06     | ○ planned    |      0%    | 250M / 150M     |   0  /  12M     |   0  |  —
Smeta     | smeta    | 15.06 – 15.07 ⚠   | ○ planned    |      0%    | 150M /  90M     |   0  /   0      |   0  |  —

[timeline strip: one CSS bar per milestone positioned by its date range]

OYLIK REJA VS FAKT   (derived from milestones, day-weighted)
… the same month grid as Version 1, read-only …

BELGILANMAGAN YOZUVLAR
• 3 transactions on this project have no milestone → [select: assign]
• Period 2026-04 hours (410 h) unassigned → suggested: AR [assign]
```

Plus: milestone add/edit modal, a "Bosqich" select in the transaction modal, a schedule-editor
card on the pricing page, a "Loyihalar yo'lda X/Y" stat card on the dashboard, and a `work_types`
section on the reference lookup page.

## 2.2 Data model

**Revive and extend `project_phases`** (`models/base.py:450-458`). Justification: the table
already has the right shape (name/code/sort order, planned hours/cost/revenue, date range,
completion %, status, `UNIQUE(project_id, code)`), and `project_hours.phase_id` proves it was
designed as the milestone anchor. Creating a parallel table would strand that scaffolding.

Column semantics: `planned_revenue` = expected income; `planned_cost` = expected **labor**
(risk-inclusive, matching `projects.planned_cost`); outsourcing/material split out as new columns
so totals stay comparable with `/budget`.

Additions to the `migrations` list (`models/base.py:578`), mirrored into the CREATE for fresh
installs:

```python
# v6 — milestones
"ALTER TABLE project_phases ADD COLUMN work_type TEXT",
"ALTER TABLE project_phases ADD COLUMN planned_outsourcing REAL DEFAULT 0",
"ALTER TABLE project_phases ADD COLUMN planned_material REAL DEFAULT 0",
"ALTER TABLE project_phases ADD COLUMN completed_date TEXT",
"ALTER TABLE project_phases ADD COLUMN notes TEXT",
"ALTER TABLE project_phases ADD COLUMN updated_at TEXT",
"ALTER TABLE transactions ADD COLUMN phase_id INTEGER REFERENCES project_phases(id)",
"CREATE INDEX IF NOT EXISTS idx_tx_phase ON transactions(phase_id)",
"CREATE INDEX IF NOT EXISTS idx_ph_phase ON project_hours(phase_id)",
"CREATE INDEX IF NOT EXISTS idx_phases_project ON project_phases(project_id)",
```

`transactions.phase_id` as a **flat column** follows the codebase direction — exactly how
`category_id` replaced `transaction_lines` (`models/base.py:609-610`). `transaction_lines` stays
dead.

**Status workflow** (enforced in code — SQLite can't add a CHECK via ALTER):
`planned → in_progress → done`, plus `cancelled`. Legacy `'active'` rows are normalized to
`in_progress` on read. **`late` is always derived, never stored**
(`end_date < today AND status ∉ {done, cancelled}`), so it can never go stale.

**New dimension table `work_types`** (mirrors `tx_types`, `models/base.py:405-414`):

```sql
CREATE TABLE IF NOT EXISTS work_types (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    label_uz TEXT NOT NULL, label_en TEXT, label_ru TEXT,
    is_active INTEGER DEFAULT 1,
    sort_order INTEGER DEFAULT 100
)
```

Seeded (count == 0 guard, like `tx_types`) with architecture stages: `agr` (AGR/Topshiriq),
`eskiz` (Eskiz loyiha), `ar` (Arxitektura yechimlari), `kj` (Konstruktiv yechimlar), `im`
(Muhandislik tarmoqlari), `smeta` (Smeta hujjatlari), `nazorat` (Avtorlik nazorati), `boshqa`.
Managed on the reference lookup page.

**Generic-API registration:** `'project_phases'` goes into `_ALLOWED_TABLES` only (field edits via
`/api/update/project_phases/<id>` with auto-audit) — **not** into `_DELETE_ALLOWED`: a generic
hard delete would either be blocked opaquely by the `transactions.phase_id` FK or leave
`project_hours.phase_id` dangling (plain INTEGER, no FK). A dedicated guarded delete route handles
it instead.

## 2.3 Model layer — new `models/milestones.py` (~350 LOC)

| Function | Spec |
|---|---|
| `get_work_types(active_only=True)` | Dimension rows for selects. |
| `get_project_milestones(project_id)` | Phases ordered by `sort_order, start_date`, enriched with actuals, derived `is_late`, `expected_completion`, earned value, variances. |
| `get_milestone_actuals(project_id)` | **Explicit attribution:** income `SUM(paid)` where `tx_type='tushum' AND phase_id=?`; direct `SUM(amount)` where `tx_type IN ('outsourcing','material') AND phase_id=?`; labor from `project_hours WHERE phase_id=?` with the same closed-period/snapshot/live-rate rule as Part 0. Also returns an `unassigned` bucket (`phase_id IS NULL`) so the UI can show tagging debt. |
| `add_milestone(project_id, name, *, code, work_type, start_date, end_date, planned_hours, planned_cost, planned_outsourcing, planned_material, planned_revenue, sort_order, notes)` | Code auto-derived from work type + sequence when blank (`UNIQUE(project_id, code)`); audited. |
| `delete_milestone(milestone_id)` | Refuses when attributed transactions exist (count reported); NULLs `project_hours.phase_id`; deletes; audits. |
| `set_milestone_status(milestone_id, status)` | Validates vocabulary; `done` stamps `completed_date` and raises completion to 100 if lower; audited. |
| `assign_hours_to_milestone(project_id, period, phase_id_or_none)` | `UPDATE project_hours SET phase_id=? WHERE project_id=? AND period=?` — whole NIZAM periods are the assignment grain, matching how hours arrive; audited. |
| `suggest_phase_for_period(project_id, period)` | The milestone whose date range overlaps that month most; used to pre-select in the assign UI, never auto-written. |
| `allocate_plan_to_months(milestone)` | **Day-weighted linear spread**: for each calendar month overlapped by `[start_date, end_date]`, share = overlapping days ÷ total days; allocates `planned_revenue`, `planned_expense_total` (= cost + outsourcing + material) and `planned_hours`. Justification: stage work is roughly continuous, and the rule is deterministic with no extra input. The rejected alternative — all-on-end-month, matching end-of-stage invoicing — would make every mid-stage month look unprofitable, defeating "profitable month by month". Undated milestones are excluded and flagged. |
| `get_project_monthly_rollup(project_id)` | Monthly PLAN = Σ `allocate_plan_to_months` across milestones; FAKT = the shared Part 0 aggregator (date/period-based, independent of tagging, so untagged actuals still land in the right month); merged with **Version 1's variance/cumulative/status math verbatim** (§1.6). |
| `milestone_earned_value(m, actuals)` | `EV = planned_revenue × completion% / 100`; `planned_value_to_date` = day-allocated plan through today; `CPI = EV / max(actual_expense, 1)`; `SPI = EV / max(planned_value_to_date, 1)`. Completion is manual, with a one-click "hours-based" helper `min(actual_hours/planned_hours, 1) × 100` (mirrors `get_earned_revenue`, `models/projects.py:34-48`) — suggested, never silently applied. |
| `get_projects_on_course_summary()` | Dashboard counts: on-course / at-edge / off-course / has-late-milestones. |
| `generate_milestones_from_pricing(project_id, schedule_rows, result, outsourcing, material)` | See §2.6. |

Re-exported via `models/__init__.py`.

## 2.4 Controllers / routes — new `controllers/milestones_bp.py`

| Method & path | Access | Purpose |
|---|---|---|
| GET `/projects/<int:project_id>` | `@login_required` | Project detail page (§2.1). |
| POST `/api/milestones/add` | manager/admin | Create milestone. |
| POST `/api/milestones/<int:mid>/delete` | manager/admin | Guarded delete (refuses when transactions attached). |
| POST `/api/milestones/<int:mid>/status` | manager/admin | Workflow transition. |
| POST `/api/milestones/assign-hours` | manager/admin | `project_id` + `period` + `phase_id` (blank = unassign). |

Milestone field edits reuse the generic `POST /api/update/project_phases/<id>` (auto-audit).

## 2.5 Every file added or changed

| File | Change |
|---|---|
| `models/base.py` | phases DDL extension (fresh installs), `work_types` DDL + seed, migrations + 3 indexes, `'project_phases'` → `_ALLOWED_TABLES` |
| `models/milestones.py` | **new** (~350 LOC) |
| `models/monthly_plan.py` (or shared module) | the Part 0 aggregator (shared with V1 if phased) |
| `models/__init__.py` | + re-exports |
| `models/dashboard.py` | + on-course summary into `get_dashboard_data` |
| `controllers/milestones_bp.py` | **new** (~150 LOC) |
| `controllers/projects_bp.py` | `/projects` rows become links to `/projects/<id>`; + milestone-status mini column data |
| `controllers/pricing_bp.py` | + schedule-array parsing + `generate_milestones_from_pricing` call (freeze UPDATE untouched) |
| `controllers/api_bp.py` | + `'phase_id'` in the FK int-coercion list (blank → NULL from the tx modal) |
| `controllers/reference_bp.py` | + `work_types` in the lookup whitelist; delete-guard counts phases using the code |
| `controllers/import_export_bp.py` | (optional) + `'12-Bosqichlar'` sheet + `SHEET_MAP` entry |
| **`import_nizam.py`** | **required fix, easy to miss:** `INSERT OR REPLACE INTO project_hours` (line ~320) deletes-and-reinserts on conflict, silently wiping `phase_id` **and** the `applied_*` rate snapshots on every re-import. Must become `INSERT … ON CONFLICT(project_id, staff_id, period) DO UPDATE SET hours=excluded.hours, imported_at=excluded.imported_at`. |
| `app.py` | + import + register `milestones_bp` |
| `templates/project_detail.html` | **new** — the §2.1 page |
| `templates/_milestone_modal.html` | **new** — clone of `_tx_modal.html` mechanics (name, code, work-type select, dates, planned hours/labor/outsourcing/material/income, completion %, status, notes) |
| `templates/projects.html` | project name cell becomes a link; + "Bosqich holati" column (e.g. `2/5 done, 1 late`) |
| `templates/_tx_modal.html` + `templates/external.html` | + "Bosqich" select for external transactions (per-project phase map rendered server-side, filtered client-side on project change); rows carry `data-phase-id` |
| `templates/pricing.html` | + "Bosqichlar jadvali" schedule-editor card (§2.6) |
| `templates/dashboard.html` | + "Loyihalar yo'lda X/Y" stat card (red/amber when any off-course) |
| `templates/lookup_tables.html` | + `work_types` section (copy of the payment-types section) |
| `translations.py` | + `# ==== MILESTONES ====` ~60 keys × 3 languages (`ms_*`, `tx_phase_label`, `proj_th_milestones`, `dash_on_course*`, `pricing_ms_*`, `lu_work_types`; reuses V1's monthly-grid keys for the roll-up card) |

## 2.6 Pricing engine integration

The pricing page gains a **"Bosqichlar jadvali"** card: a small JS-managed table of schedule rows —
name, work-type select, start month, end month, % of income, % of hours — with a "default
template" button that pre-fills the seeded stages with typical splits, and a "% remaining" hint.
Percentages must sum to 100 (±0.5 tolerance; remainder onto the last row).

On freeze, the existing `UPDATE projects SET planned_* …` runs verbatim; then each schedule row
becomes a milestone:

```
planned_revenue = target_contract × income_pct
planned_hours   = total_hours × hours_pct
planned_cost    = mizan_cost × hours_pct        # risk-inclusive labor follows hours
outsourcing / material → assigned to the row(s) the user marks (default: last milestone)
start/end dates from the month inputs · status = 'planned' · code from work type + index
```

If the project already has milestones, freeze skips generation with a warning unless "overwrite"
is checked; delete-and-regenerate is **blocked when any milestone has attributed transactions**
(must be resolved on the detail page first). `models/pricing.py` is untouched.

## 2.7 On-course / profitability logic

Monthly and cumulative money math: **identical formulas and thresholds to Version 1 (§1.6)**,
applied to the day-allocated milestone plan — deliberate consistency between versions and with
`/budget`. Additional milestone-level signals:

| Signal | Rule | Display |
|---|---|---|
| **Late** | `end_date < today AND status ∉ {done, cancelled}` (derived) | red `KECHIKKAN` badge; project schedule light red if any |
| **Behind** | `completion% < expected% − 15`, where `expected = clamp(days_elapsed / days_total, 0, 1) × 100` | amber badge (15 pt ≈ half a month on a 3-month stage; coarser than money thresholds because completion % is subjective) |
| **Per-milestone cost** | `(fact_expense − plan_expense) / max(plan_expense, 1) × 100` | budget-style +5 / −10 badges |
| **Project headline** | three lights: money (§1.6 `profit_drift`), schedule (no late milestones), collections (`income_ratio ≥ 0.95` to date) | on course = money AND schedule green-or-amber; any red → off course. Dashboard counts projects by headline. |

## 2.8 Edge cases

- **Milestones spanning months** — day-weighted allocation (§2.3); a single-month milestone
  allocates 100% to that month. **Overlapping milestones** are allowed (parallel stages are real);
  allocations are independent and additive. **Undated milestones** are excluded from the roll-up
  and listed in a warning bucket; their totals still show in the milestone table.
- **Untagged actuals** — the monthly roll-up is unaffected (it is date-based); per-milestone
  figures are only as good as the tagging, and the "unassigned" panel makes the debt visible
  instead of hiding it.
- **NIZAM re-import** — without the `import_nizam.py` fix (§2.5), every re-import silently
  destroys hour attribution and rate snapshots. The fix is part of this version's definition of
  done.
- **Deleting milestones** — blocked while transactions reference them (friendly message with the
  count); hour links are auto-unassigned.
- **USD contracts / plan revisions / closed periods** — as in Version 1 (§1.8); milestone `done`
  in a closed fiscal period stays allowed (statuses are operational, not fiscal).
- **Legacy `status='active'` rows** — normalized to `in_progress` on read.
- **No frozen plan / no milestones** — the detail page still renders (actuals + empty plan);
  headline = `Boshlanmagan`.

## 2.9 Effort and trade-offs

**Effort:** 19–21 files touched (5 new), ≈ 2,200–2,600 new LOC. Roughly 2.5× Version 1.

**Pros**
- True work breakdown: per-stage profitability, not just per-month.
- Schedule awareness — the only version that can say **why** a project is off course (which stage
  is late or over budget).
- Earned value / CPI / SPI for managers who want them.
- The project detail page is a long-missing navigation anchor for the whole app.
- Explicit attribution survives odd billing timing (income tagged to the stage it pays for, not
  just the month it happens to land in).

**Cons**
- Demands ongoing discipline: tagging transactions, assigning hour-periods, updating completion %
  and statuses. With a small team the "unassigned" panels can rot into permanent backlogs.
- Much larger surface: modal, timeline, schedule editor, dashboard, reference page, import change.
- Pricing freeze UX gets noticeably heavier.
- Per-milestone numbers are only as trustworthy as the tagging habit.

---

# Part 3 — Comparison and recommendation

| Criterion | V1 — Oylik plan (monthly grid) | V2 — Bosqichlar (milestone lifecycle) |
|---|---|---|
| "Profitable month by month?" | Directly | Via day-weighted allocation of milestones |
| "On course?" | Money-only (cumulative plan vs fact) | Money + schedule + collections lights |
| New process burden on the team | **None** (auto-match by month) | Tagging transactions, assigning hours, statuses, completion % |
| Schema footprint | 1 new table | 6 phase columns + 1 transactions column + 1 dimension table |
| Existing pages touched | pricing form + nav | projects, tx modal, pricing, dashboard, lookup, NIZAM import |
| Per-stage profitability, lateness, EV | No | Yes |
| Effort | ~12 files, ~1,000 LOC | ~20 files, ~2,400 LOC |
| Risk of stale/garbage data | Low | Medium-high (attribution debt) |
| Fit with repo idioms (flat tables, server-rendered, generic API) | Perfect | Good, but pushes limits (form arrays, filtered selects) |

**The two versions are forward-compatible — V1 → V2 is a clean phased path.**
`project_monthly_plans` and `project_phases` are disjoint; both consume the same Part 0
aggregator (build it once in `models/monthly_plan.py`); V2's roll-up can later replace or coexist
with V1's manual grid (rule: explicit monthly rows win when present, milestone allocation
otherwise); a trivial converter turns V1 rows into single-month milestones; and V1's
"spread over N months" checkbox naturally evolves into V2's default schedule template.

## Recommendation

**Ship Version 1 now; treat Version 2 as phase 2, gated on demonstrated use.** Reasons anchored in
this codebase and team:

1. Actuals arrive via Excel import, there is no manual hours UI and no project detail page today —
   V2's attribution workflow has no natural home in current habits, while V1's auto-matching
   requires none.
2. The owner's two stated questions ("on course?", "profitable month by month?") are exactly V1's
   output.
3. The repo's whole direction (flat columns, the dividends-recipe feature pattern, no detail
   pages) makes V1 a one-recipe feature and V2 a mini-project.
4. V1's usage data — which months actually get plan revisions, whether free-text work types
   converge on stage names — is the best evidence for whether milestone lifecycle discipline
   would actually be maintained.

If stage-level lateness and per-stage margins are needed **from day one**, choose Version 2 — but
budget for the `import_nizam.py` fix and the tagging routine as part of the rollout, not as an
afterthought.
