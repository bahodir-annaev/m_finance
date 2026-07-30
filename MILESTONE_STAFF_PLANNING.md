# Per-Employee Milestone Planning

## Context

Today a project milestone (`project_phases`) stores only scalar plan figures — a single
`planned_hours` and `planned_cost` typed in by hand. There is no way to say *which*
employees will work a milestone or for how long, so the labor expense is a guess rather
than a build-up from real people and real cost rates.

This change makes each milestone hold a **list of employees with estimated hours**. The
milestone's labor plan is then derived from those rows:

- `planned_hours` = Σ(estimated hours)
- `planned_cost`  = Σ(hours × cost_rate)   ← the milestone **expense**
- a suggested billing = Σ(hours × billing_rate) → pre-fills `planned_revenue` (editable)

Decisions locked in: the employee rows are the **single source of truth** (manual
hours/cost fields are replaced by computed displays); each row's `cost_rate`/`billing_rate`
is **snapshotted at save time** (frozen, like the existing frozen-plan pattern); and billing
is computed in addition to expense.

This reuses the pricing-engine pattern (`pricing_estimate`, `models/pricing.py:36-47`)
and the dynamic add/remove-row UI pattern from `templates/pricing.html` (`addMsRow`/`getlist`).

## New data model

Add a child table (mirrors `transaction_lines`, `models/base.py:535-544`) in the `init_db`
migration block of `models/base.py`:

```sql
CREATE TABLE IF NOT EXISTS milestone_staff (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phase_id INTEGER NOT NULL REFERENCES project_phases(id) ON DELETE CASCADE,
    staff_id INTEGER NOT NULL REFERENCES staff(id),
    est_hours REAL NOT NULL DEFAULT 0,
    cost_rate REAL DEFAULT 0,      -- snapshot at save
    billing_rate REAL DEFAULT 0,   -- snapshot at save
    UNIQUE(phase_id, staff_id)
)
```

Place it in a new `v7` migration section alongside the existing versioned blocks. No entry
in `_ALLOWED_TABLES` is needed — this table is only written through the dedicated functions
below, never the generic `/api/update` route.

## Model layer (`models/`)

**`models/staff.py`** — add a reusable helper that gives both the dropdown and the JS
live-preview rate map in one call:

```python
def get_production_staff_rates():
    """Active production staff with current cost/billing rates for milestone planning."""
    # SELECT id, name, role FROM staff WHERE staff_type='production' AND is_active=1 ORDER BY name
    # for each: info = calculate_hourly_rate(id); cost_rate/billing_rate (0 if not dict)
    # returns list of {id, name, role, cost_rate, billing_rate}
```
Reuses the `staff_type='production' AND is_active=1` query (`controllers/pricing_bp.py:40`)
and `calculate_hourly_rate` (`models/staff.py:138`).

**`models/milestones.py`** — add:

- `set_milestone_staff(phase_id, staff_rows)` — `staff_rows` = list of `{staff_id, est_hours}`.
  For each row, snapshot `cost_rate`/`billing_rate` via `calculate_hourly_rate(sid)` (cache
  per sid like `calculate_project_cost`, `models/projects.py:121-122`; guard `isinstance(...,dict)`).
  Replace all `milestone_staff` rows for the phase in a transaction, then recompute and write
  `project_phases.planned_hours = Σhours` and `planned_cost = Σ(hours×cost_rate)`. Return the
  computed totals `{planned_hours, planned_cost, planned_billing}`.
- `get_milestone_staff(phase_id)` — join `milestone_staff` → `staff`, returning
  `{staff_id, name, role, est_hours, cost_rate, billing_rate, cost, billing}` per row.

Extend `get_project_milestones` (`models/milestones.py:220`) enrichment to attach
`m['staff'] = get_milestone_staff(m['id'])` so the detail table can serialize the rows into a
`data-staff` JSON attribute for the edit modal (established `data-*` repopulation pattern).

`add_milestone` stays as-is (scalar insert); staff rows are set in a second step by the
controller. `delete_milestone` needs no change — the `ON DELETE CASCADE` clears child rows.
`generate_milestones_from_pricing` is unaffected: generated milestones simply have no
`milestone_staff` rows and keep their directly-written `planned_hours`/`planned_cost`.

## Controller layer (`controllers/milestones_bp.py`)

- Add `_collect_staff_rows(form)` mirroring `_collect_schedule_rows`
  (`controllers/pricing_bp.py:11-30`): zip `form.getlist('ms_staff_id')` with
  `form.getlist('ms_staff_hours')`, skipping blank/zero rows.
- `api_add_milestone` (line 70): after `add_milestone(...)` returns `mid`, call
  `set_milestone_staff(mid, _collect_staff_rows(f))`.
- Add **`POST /api/milestones/<int:mid>/update`**: update the scalar columns via
  `update_record('project_phases', mid, {...})` (name, code, work_type, status, dates,
  planned_revenue, planned_outsourcing, planned_material, completion_percent, sort_order,
  notes), then `set_milestone_staff(mid, _collect_staff_rows(f))`. This replaces the modal's
  current use of the generic `/api/update/project_phases/<id>` route, which cannot handle the
  child array.
- `project_detail` (line 35): pass `staff_rates=get_production_staff_rates()` into the render
  context (imported from `models`).

Re-export the new functions in `models/__init__.py`.

## Template layer

**`templates/_milestone_modal.html`**
- Replace the manual `planned_hours` (line 62) and `planned_cost` (line 50) inputs with
  **read-only computed displays** that JS updates live.
- Add an employee table with `<tbody id="msStaffRows">` and a `+ add employee` button. A JS
  `addStaffRow(staffId, hours)` factory (copied from `addMsRow`, `pricing.html:155-167`) emits
  per row: `<select name="ms_staff_id">` (options from `staff_rates`) + a
  `<input name="ms_staff_hours" oninput="recalcMs()">` + an inline `× remove` button.
- Embed a JS rate map from `staff_rates` (`{id: {cost_rate, billing_rate}}`). `recalcMs()`
  sums hours and hours×rate across `#msStaffRows` (pattern of `updPct`, `pricing.html:180-187`)
  to update the computed hours/cost displays and pre-fill `planned_revenue` with the billing
  total when it is empty/zero (still editable).
- `openMsModalNew` clears the rows; `openMsModal(row)` parses `row.dataset.staff` JSON and
  rebuilds rows via `addStaffRow`, then calls `recalcMs()`.
- `saveMs` (line 131): point the edit branch at `/api/milestones/<id>/update` instead of
  `/api/update/project_phases/<id>`. `FormData` already serializes the dynamic rows — no other
  wiring needed.

**`templates/project_detail.html`** — on each milestone `<tr>` (data-* block, lines 80-86)
add `data-staff='{{ m.staff|tojson }}'` so the modal can repopulate on edit. Optionally show
the employee count / hours in the row.

**`translations.py`** — add keys in all three languages (uz default, en, ru): e.g.
`ms_staff_title`, `ms_staff_add`, `ms_staff_employee`, `ms_staff_hours`, `ms_computed_hours`,
`ms_computed_cost`, `ms_computed_billing`.

## Tests (`test_milestones.py`)

Add cases: (1) `set_milestone_staff` writes child rows, snapshots rates, and recomputes
`project_phases.planned_hours`/`planned_cost` to match Σ(hours×cost_rate); (2) editing rows
replaces (not duplicates) prior rows; (3) deleting a milestone cascades `milestone_staff`;
(4) snapshot stability — changing a staff salary afterward does not alter a saved milestone's
stored `planned_cost`; (5) Flask smoke test hitting `/api/milestones/add` and
`/api/milestones/<id>/update` with `ms_staff_id`/`ms_staff_hours` arrays.

## Verification

1. `python test_milestones.py` — all existing + new assertions pass (no server needed).
2. `python app.py`, open a project detail page → add a milestone, attach 2 employees with
   hours, confirm the computed hours/cost/billing update live and `planned_cost` saved equals
   Σ(hours × cost_rate) shown on the milestone row.
3. Re-open the milestone → employee rows repopulate from `data-staff`; edit hours → save →
   values recompute.
4. Delete the milestone → confirm no orphan `milestone_staff` rows
   (`SELECT * FROM milestone_staff WHERE phase_id=?`).

## Key files

- `models/base.py` — new `milestone_staff` table (v7 migration)
- `models/staff.py` — `get_production_staff_rates()`
- `models/milestones.py` — `set_milestone_staff`, `get_milestone_staff`, enrich `get_project_milestones`
- `models/__init__.py` — re-export new symbols
- `controllers/milestones_bp.py` — `_collect_staff_rows`, extend add, new update route, pass `staff_rates`
- `templates/_milestone_modal.html` — employee rows UI + live recompute + save-URL change
- `templates/project_detail.html` — `data-staff` attribute
- `translations.py` — new i18n keys
- `test_milestones.py` — new tests
