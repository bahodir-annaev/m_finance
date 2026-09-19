# v4 → v5 feature gap

What the v4 app (parent directory) does that v5 does not — first written 2026-09-16 on branch
`double_entry_version` by diffing every v4 route, sidebar link and public model function
against the v5 equivalents. Section C lists what was replaced on purpose so nobody re-ports it.

**Status as of 2026-09-18** — ported (see `test_v4_features.py`): A2 NIZAM import, A7 KPI,
A8 scratch quote, A9 CBU rate fetch, and every row of section B except B17 (intentional).
**Still open:** A1 local AI agent, A3 generic Excel transaction import, A4 Excel export,
A5 import reconciliation page, A6 counterparty aliases UI, A10 a dedicated dividends page
(the `dividend` document type works at `/documents/dividend`; it has no nav link or founder
field). The tables below are kept as the original audit; the "Ported" column says where
each item landed.

## A. Whole features absent in v5

| # | v4 feature | v4 location | v5 status | Ported |
|---|---|---|---|---|
| A1 | **Local AI agent** — chat, CFO summary, anomaly detection, transaction auto-categorisation | `controllers/ai_bp.py`, `models/ai_agent.py`, `templates/ai.html` | Nothing | open |
| A2 | **NIZAM CRM hours import** — upload `.xlsx`, `NIZAM_MAP` name matching, unmatched-names warning | `import_nizam.py`, `/import` | Nothing. `/staff/hours` is manual one-row entry. `staff.nizam_name` exists but nothing reads it | `models/import_nizam.py`, `/staff/hours/import` |
| A3 | **Generic Excel transaction import** — sheet analysis, fuzzy column matching, external/internal/mixed targets, skip log with reasons | `models/import_export.py`, `/api/import-excel/analyze`, `templates/import.html` | Nothing. `openpyxl` is not imported anywhere in v5 (only `import_v4.py`, a one-shot DB migration) | open |
| A4 | **Excel export** — 5-sheet workbook (Dashboard, Salaries, Hourly rates, Budget, KPI) + per-page "export this page" buttons on 8 templates | `/export`, `/api/export/xlsx`, `test_excel_per_page.py` | Nothing. No download of any kind | open |
| A5 | **Import reconciliation page** — link / create / ignore unmatched project and counterparty names from imports | `/reference/reconcile`, `models/base.py::apply_entity_resolution` | `unresolved_imports` and `counterparty_aliases` tables are created in `models/base.py`, but no page, no route, no writer | open |
| A6 | **Counterparty aliases UI** — add/delete alias per counterparty | `/reference/counterparties` | `/counterparties` has no alias section | open |
| A7 | **KPI page** — per-employee project count, hours, cost value, revenue value (UZS + USD), utilisation over `kpi_months` | `/kpi`, `models/staff.py::get_staff_kpi` | Only firm-level utilisation on `/rates` | `staff.get_staff_kpi`, `/kpi` |
| A8 | **Prospect pricing page** — project picker + stateless scratch quote for a prospect with no project record, "save quote into project" | `/pricing`, `/api/pricing/quote`, `/api/pricing/quote/save`, `models/pricing.py::save_quote_to_project` | `quote_schedule()` is ported to `models/pricing.py` but has no route or page; `save_quote_to_project` not ported | `controllers/pricing_bp.py`, `/pricing`, `pricing.save_quote_to_project` |
| A9 | **CBU exchange-rate fetch** — pull the official UZS/USD rate for a date from `cbu.uz` | `/settings/fetch-rate` | Manual rate entry only | `/settings/fetch-rate` |
| A10 | **Dividends page** — list, summary tiles, founder / paid / payment type form | `/dividends`, `models/dividends.py` | `dividend` doc type and posting rule exist and `/documents/dividend` resolves, but there is no sidebar link, no summary and no founder field — effectively hidden | open |
| A11 | **Transaction categories tree** (`transaction_categories`, `category_id` on lines) | `/reference/categories` | Replaced by the chart of accounts — *intentional* | — intentional, not ported |
| A12 | **`tx_types` lookup table** (user-editable type codes with direction) | `/reference/lookup-tables` | `/settings/lookup` manages `payment_types`, `work_types`, `departments`, `staff_roles`; `tx_types` replaced by doc types + accounts — *intentional* | — intentional, not ported |
| A13 | **Accounting quick-entry pages** `/accounting/transactions|projects|staff|equipment|licenses|overhead|loans` | `controllers/accounting_bp.py` | Superseded by the list + modal pattern — *intentional* | — intentional, not ported |
| A14 | **Static overhead vs indirect pool per `tx_type`** | `/accounting/overhead` | `/rates/overhead` shows ledger pool vs budget with a per-account breakdown — equivalent, not a gap | — equivalent already |

## B. Page exists in v5, but v4 capabilities are missing

### Dashboard `/`

| # | v4 | v5 | Ported |
|---|---|---|---|
| B1 | Top-10 projects table (hours, cost, billing value, FX, margin) | Only portfolio totals (`get_portfolio_summary`) | `projects.get_top_projects` |
| B2 | Loan summary tile (`get_loan_summary`) | Only on `/loans` | `loans.loan_summary` |
| B3 | Projects on-course tile (tracked / on / off) | No time-phased verdict exists (see B7) | `projects.get_projects_on_course_summary` |
| B4 | FX gain/loss tile | `fx_position()` is on `/periods` only | `fx_position` on the dashboard |
| B5 | Break-even revenue, revenue per employee, profit per hour, avg cost/billing rate (UZS + USD), utilisation, staff counts, total hours | Avg rates and utilisation are on `/rates`; break-even, revenue/employee, profit/hour are not computed anywhere | `reports.get_dashboard` → `cfo`, `staff` |
| B6 | AR aging buckets 0-30 / 31-60 / 61-90 / 90+ on the dashboard | Total + overdue only; buckets on `/reports/aging` | `ar_buckets` on the dashboard |

### Projects, Plan & Price `/projects/<id>`, `/budget`

| # | v4 | v5 | Ported |
|---|---|---|---|
| B7 | **Time-phased "to date" lens** — monthly PLAN (day-weighted from milestones) vs FACT with cumulative drift and on/edge/off verdict, on the project page and as the second lens on `/budget` (`get_project_monthly_rollup`, `get_plan_overview`) | Missing. `allocate_plan_to_months` and `get_project_monthly_actuals` are ported to `models/milestones.py` but no controller or template calls them. `get_budget_overview` has only the whole-project lens | `milestones.get_project_monthly_rollup`, `/budget` second table |
| B8 | **Unassigned actuals → milestone** — hour periods not tagged to a phase, suggested phase, one-click assign (`/api/milestones/assign-hours`) | `assign_hours_to_milestone()` is ported but has no route or UI; `get_unassigned_actuals`, `suggest_phase_for_period` not ported | `get_unassigned_actuals`, `assign_document_to_milestone`, project page |
| B9 | Per-project FX gain/loss (`calculate_fx_gain_loss`) | Firm-level FX revaluation only | `projects.calculate_fx_gain_loss` |
| B10 | Earned value total across milestones | Per-milestone `earned_value` computed, total not shown | project tile |
| B11 | Milestone `code` and `sort_order` editable in the modal | Form omits both | milestone modal |
| B12 | Projects list: total hours, staff count, late-milestone count, sorted by hours | `list_projects` has milestone total/done only; sorted by status, name | `list_projects(sort=…)` |

### Money entry `/external`, `/internal` → `/documents/*`

| # | v4 | v5 | Ported |
|---|---|---|---|
| B13 | **Pagination** (25/50/100) and **column sorting** on transaction lists | `list_documents(limit=200, offset=0)` supports both, but `documents_bp.document_list` passes no offset and the template has no pager — the list silently stops at 200 rows (the migrated v4 DB has 1,028) | `documents.count_documents`, `DOCUMENT_SORTS`, pager macro |
| B14 | Filter by project and by type; search across client / paid_to / notes / project name | Filters: date range, status, counterparty, direction, `q`. No project filter | document list filters |
| B15 | Responsible person on a transaction | Documents have no responsible field | `documents.responsible_id` |
| B16 | Payment summary by payment type (bank / naqd / karta …) on `/cashflow` | Missing on `/reports/cashflow`; per-bank-account balances are shown instead | `reports.payment_method_summary` |
| B17 | Inline edit / delete of posted rows | Replaced by draft → post → void — *intentional* | — intentional, not ported |

### Loans `/loans`

| # | v4 | v5 | Ported |
|---|---|---|---|
| B18 | Per-loan payment history (date, principal/interest, notes) | Not shown. `loans_bp.py` attaches `list_documents(doc_type='loan')` (all loans) and the template does not render it | `loans.loan_documents` |
| B19 | Auto-close when principal repaid ≥ `total_amount` | `status` is a manual dropdown | `loans.sync_loan_status` |
| B20 | Separate taken / given tables with open-count and per-currency outstanding tiles | One table; `borrowed` / `lent` ledger totals | `/loans` two tables + tiles |

### Staff & rates `/staff`, `/hourly` → `/rates`

| # | v4 | v5 | Ported |
|---|---|---|---|
| B21 | Staff list filter bar (department, type) | None | `/staff` filter bar |
| B22 | Billing rate in USD per employee | `/rates` has no USD column (`usd_rate` is in the overview dict only) | `/rates` $ column |

### Periods `/periods`

| # | v4 | v5 | Ported |
|---|---|---|---|
| B23 | Manually create a period (with notes) and delete an open one | Auto-created by `ensure_fiscal_period()`; no create / delete / notes | `create_fiscal_period` / `delete_fiscal_period` |
| B24 | Hard-closed periods cannot be reopened | `reopen_period()` reopens any status | `reopen_period(allow_hard=False)` |
| B25 | Snapshot completeness columns (staff snapshots, hours snapped / total) | Entry count only | `list_periods` snapshot counts |

### Settings / admin

| # | v4 | v5 | Ported |
|---|---|---|---|
| B26 | Cannot deactivate your own user | No guard in `/settings/user` | `/settings/user` guard |
| B27 | Full exchange-rate history | Last 40 rows | full rate history |
| B28 | Equipment page filters (staff, license type) | None | `/staff/equipment` filters |

## C. Replaced on purpose — do not re-port

- `amount` / `paid` / derived `status` + follow-up payment rows (`parent_tx_id`) → `payment_allocations`
- Stored `direction` column → derived in `models/direction.py`
- `transaction_categories`, `tx_types` → chart of accounts + document types
- Static `overhead` table as the only source → ledger pool with `overhead_budget` fallback
- Inline edit / delete of money rows → draft / post / void lifecycle
- `/accounting/*` quick-entry forms → list + modal on every page
- `get_cash_flow_by_month` reconstruction → `get_cash_flow` over 5xxx movements
- `/api/get/<table>/<id>` → modals prefilled from `data-row`

## D. Promised in `../NEW_APP_PLAN.md` but not delivered

- §2 lists `import_nizam.py, import_export.py  # ported` — neither exists in `models/` (A2, A3)
- Phase 8: "Port NIZAM hours import and Excel import (now creating documents)" — NIZAM
  done (2026-09-18, on `/staff/hours`); the generic Excel import is still open
- §8 sidebar plan: "Admin — … users, import" — the NIZAM import lives under Staff →
  Timesheets instead

## Suggested port order (what is left)

1. **A4 Excel export** — the firm's reporting hand-off; v4 had an integration test for it.
2. **A3 generic Excel transaction import** — now creating documents rather than rows.
3. **A6 + A5** aliases UI and reconcile page — `project_aliases`, `counterparty_aliases`
   and `unresolved_imports` already exist, and the NIZAM importer already queues unknown
   projects there when *create unknown projects* is off.
4. **A10** dividends nav entry + founder field.
5. **A1** AI agent.
