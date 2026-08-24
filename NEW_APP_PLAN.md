# MIZAN Finance v5 — From-Scratch Rewrite Plan

> Status: **IMPLEMENTED** in [`mizan5/`](mizan5/) on branch `double_entry_version`.
> Plan written 2026-08-23; built 2026-08-24. 398 tests pass (`cd mizan5 && python run_tests.py`).
> See [`mizan5/CLAUDE.md`](mizan5/CLAUDE.md) for the as-built reference.
> Companion documents: [`CHART_OF_ACCOUNTS.md`](CHART_OF_ACCOUNTS.md) (the full НСБУ №21 chart, 696
> accounts — canonical source for account codes/names), [`KT.md`](KT.md) (v4 business rules,
> reused where noted).

## 1. Goals

1. **Primary goal (unchanged from v4):** accurately calculate each production staff member's
   man-hour cost to the firm. Same formula chain as v4, validated against industry conventions
   (§7) — the ledger exists to make the *inputs* to that formula (actual salaries, actual
   overhead, actual depreciation) trustworthy instead of hand-maintained.
2. **Double-entry bookkeeping as the source of truth.** Money facts live in `journal_lines`,
   posted in balanced entries against the Uzbek national chart of accounts. The v4 pattern of
   `amount`/`paid` columns with derived statuses is replaced by documents that generate postings.
3. **Real-world entry forms.** Users enter *documents* (счёт-фактура, платёжное поручение,
   зарплатная ведомость) with the fields those documents actually carry — number, date,
   counterparty with ИНН, line items, VAT — not a generic "transaction" form (§6).
4. **Intuitive UI.** Every list view has a **New** button opening a data-entry modal; row edit
   opens the same modal prefilled (§8). Server-rendered, no framework, no build step.

**Kept from v4:** Flask + SQLite3 + openpyxl stack; Blueprint/models/templates layering;
three-language i18n (uz default / en / ru); local-network-only guard; standalone script tests;
Plan & Price milestone pricing engine (ported, its math is already pinned by tests).

**Non-goals for v1 of the rewrite:** didox/soliq.uz e-invoicing integration; inventory/quantity
accounting (Кол flag); multi-company; accrual revenue recognition (POC earned revenue stays a
report-side calculation, not a posted entry).

## 2. Architecture

```
mizan5/
  app.py                    # factory + startup (same shape as v4)
  auth.py, utils.py, translations.py
  models/
    base.py                 # get_db(), init_db(), CRUD helpers, audit
    ledger.py               # accounts, post_entry(), balances, trial balance
    posting.py              # document → journal entry rules (the ONLY writer of journal rows)
    documents.py            # document CRUD, numbering, allocation, void
    payroll.py              # payroll run generation
    staff.py                # rate engine (ported v4 formula + §7 changes)
    projects.py, pricing.py # ported from v4
    reports.py              # P&L, balance sheet, cash flow, AR/AP aging, FX
    import_v4.py            # one-time migration from mizan_finance.db (v4)
    import_nizam.py, import_export.py   # ported
  controllers/              # one Blueprint per feature area
  templates/                # base.html + per page + _modal macros
```

Rules of the layer cake:

- **Nothing writes `journal_entries`/`journal_lines` except `posting.py`** (via
  `ledger.post_entry()`), and nothing calls `post_entry()` except document posting, the manual
  journal form, and period-close routines. The ledger tables stay out of the generic CRUD
  allowlist.
- Documents are editable while `draft`, immutable once `posted` (edits = void + re-create, or
  auto re-post for same-period edits — see §5.6). Posted documents are never hard-deleted;
  `void` writes a reversing entry.
- All ledger amounts are in **UZS (functional currency)**. Foreign-currency lines carry
  `currency`, `amount_cur`, `exchange_rate` alongside the UZS debit/credit; month-end
  revaluation posts to 9540/9620.

## 3. Core design: documents → postings

```
 User fills a document form (modal)
        │  documents + document_lines (status='draft' or straight to 'posted')
        ▼
 posting.py rule for doc_type builds balanced lines
        │  ledger.post_entry() validates Σdebit == Σcredit (ε = 1 UZS)
        ▼
 journal_entries + journal_lines            ← single source of truth
        ▼
 Everything else is a SELECT: trial balance, P&L, balance sheet, cash flow,
 AR/AP aging, VAT report, indirect cost pool for the rate engine
```

Settlement is no longer a `paid` column: an invoice's outstanding balance is its 4010/6010
postings minus allocated payments (`payment_allocations`), which reproduces v4's
invoice-plus-follow-up-payments model in standard bookkeeping form.

## 4. Database schema

SQLite, `PRAGMA foreign_keys = ON`, `row_factory = sqlite3.Row` — all v4 conventions kept.
Grouped by concern. `※` marks tables ported from v4 with little or no change.

### 4.1 Ledger core

```sql
accounts (
    id INTEGER PK,
    code TEXT UNIQUE NOT NULL,        -- НСБУ code: '5110', '0120.1' — matches CHART_OF_ACCOUNTS.md
    name_ru TEXT NOT NULL,            -- as in НСБУ source
    name_uz TEXT, name_en TEXT,
    kind TEXT NOT NULL CHECK(kind IN ('A','KA','P','T')),   -- А/КА/П/Т from the chart
    val_flag INTEGER DEFAULT 0,       -- Вал: currency sub-accounting
    subconto TEXT,                    -- required analytic: 'counterparty','project','staff','bank_account',NULL
    cost_pool TEXT,                   -- rate-engine classification: 'direct_labor','indirect','excluded',NULL
    is_active INTEGER DEFAULT 1, sort_order INTEGER DEFAULT 100
)

journal_entries (
    id INTEGER PK,
    entry_no INTEGER UNIQUE NOT NULL, -- gapless sequence
    date TEXT NOT NULL,               -- YYYY-MM-DD
    period TEXT NOT NULL,             -- YYYY-MM, checked against fiscal_periods on post
    document_id INTEGER REFERENCES documents(id),   -- NULL only for manual entries
    memo TEXT,
    reversal_of_id INTEGER REFERENCES journal_entries(id),  -- set on void/storno entries
    created_by TEXT, created_at TEXT DEFAULT (datetime('now'))
)

journal_lines (
    id INTEGER PK,
    entry_id INTEGER NOT NULL REFERENCES journal_entries(id) ON DELETE CASCADE,
    line_no INTEGER NOT NULL,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    debit  REAL NOT NULL DEFAULT 0 CHECK(debit  >= 0),
    credit REAL NOT NULL DEFAULT 0 CHECK(credit >= 0),
    CHECK (debit = 0 OR credit = 0), CHECK (debit + credit > 0),
    -- foreign-currency memo fields (UZS is always in debit/credit):
    currency TEXT, amount_cur REAL, exchange_rate REAL,
    -- analytics (субконто) — populated per accounts.subconto:
    counterparty_id INTEGER REFERENCES counterparties(id),
    project_id INTEGER REFERENCES projects(id),
    phase_id INTEGER REFERENCES project_phases(id),
    staff_id INTEGER REFERENCES staff(id),
    description TEXT,
    UNIQUE(entry_id, line_no)
)

account_map (      -- posting rules reference accounts by purpose, not hardcoded codes
    purpose TEXT PK,                  -- 'cash_bank','cash_till','cash_fx','ar','ap',
                                      -- 'advances_received','advances_issued','revenue',
                                      -- 'vat_output','vat_input','payroll_payable','pit_payable',
                                      -- 'social_payable','production_cost','admin_expense',
                                      -- 'other_opex','interest_income','interest_expense',
                                      -- 'fx_gain','fx_loss','dividends_payable','retained_earnings',
                                      -- 'loan_short_in','loan_long_in','loan_issued','opening_offset'
    account_id INTEGER NOT NULL REFERENCES accounts(id)
)
```

Seeded `account_map` defaults (codes verified against `CHART_OF_ACCOUNTS.md`):
5110 расчётный счёт, 5010 касса, 5210 валютный счёт, 4010 покупатели, 6010 поставщики,
6310 авансы полученные, 4310 авансы выданные, 9030 доходы от услуг, 6410.1 НДС начисленный,
4410 НДС по приобретённым ценностям, 6710 оплата труда, 6420.1 НДФЛ, 6520 гос. целевые фонды
(ЕСП), 2010 основное производство, 9420 административные расходы, 9430 прочие операционные,
9530/9610 проценты, 9540/9620 курсовые разницы, 6610 дивиденды, 8710 нераспределённая прибыль,
6820/7820 займы полученные, 0000 вспомогательный счёт (opening-balance offset — the standard
1С convention).

The `accounts` seed = every account referenced by `account_map` plus their parents, ~40 rows;
an admin screen lets more be activated from the full 696-account list.

### 4.2 Documents (operational layer)

```sql
documents (
    id INTEGER PK,
    doc_type TEXT NOT NULL CHECK(doc_type IN (
        'sales_invoice',      -- счёт-фактура выданный (AR + VAT output)
        'purchase_invoice',   -- счёт-фактура полученный (AP + VAT input)
        'cash_in',            -- поступление денег (bank/cash receipt)
        'cash_out',           -- списание денег (bank/cash payment)
        'payroll',            -- зарплатная ведомость (one per period)
        'dividend',           -- начисление/выплата дивидендов
        'loan',               -- выдача/получение займа (linked to loans register)
        'manual',             -- ручная проводка (adjustments)
        'opening')),          -- ввод начальных остатков
    number TEXT NOT NULL,             -- per-type per-year sequence, e.g. 'СФ-2026-0012'
    date TEXT NOT NULL,
    counterparty_id INTEGER REFERENCES counterparties(id),
    project_id INTEGER REFERENCES projects(id),
    phase_id INTEGER REFERENCES project_phases(id),
    loan_id INTEGER REFERENCES loans(id),
    contract_ref TEXT,                -- договор №/дата (free text)
    currency TEXT NOT NULL DEFAULT 'UZS',
    exchange_rate REAL,               -- rate on document date (auto from exchange_rates)
    subtotal REAL NOT NULL DEFAULT 0, -- net of VAT, UZS
    vat_amount REAL NOT NULL DEFAULT 0,
    total REAL NOT NULL DEFAULT 0,    -- subtotal + vat_amount, UZS
    total_cur REAL,                   -- total in document currency if not UZS
    payment_method TEXT,              -- bank/naqd/karta/online (cash docs)
    bank_account TEXT,                -- which расчётный счёт (cash docs)
    due_date TEXT,                    -- срок оплаты (invoices) → drives AR/AP aging
    description TEXT, notes TEXT,
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','posted','void')),
    entry_id INTEGER REFERENCES journal_entries(id),   -- set when posted
    created_by TEXT, created_at TEXT DEFAULT (datetime('now')), updated_at TEXT,
    UNIQUE(doc_type, number)
)

document_lines (
    id INTEGER PK,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    line_no INTEGER NOT NULL,
    description TEXT NOT NULL,        -- наименование работ/услуг
    quantity REAL DEFAULT 1, unit TEXT,
    unit_price REAL DEFAULT 0,
    amount REAL NOT NULL,             -- net line amount, UZS
    vat_rate REAL DEFAULT 0,          -- % (0 or 12; from settings default)
    vat_amount REAL DEFAULT 0,
    account_id INTEGER REFERENCES accounts(id),  -- income/expense account override
    project_id INTEGER REFERENCES projects(id),
    phase_id INTEGER REFERENCES project_phases(id),
    staff_id INTEGER REFERENCES staff(id),       -- payroll lines
    UNIQUE(document_id, line_no)
)

payment_allocations (   -- which invoices a payment settles; replaces v4 parent_tx_id
    id INTEGER PK,
    payment_doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    invoice_doc_id INTEGER NOT NULL REFERENCES documents(id),
    amount REAL NOT NULL CHECK(amount > 0),      -- UZS
    UNIQUE(payment_doc_id, invoice_doc_id)
)

doc_sequences ( doc_type TEXT, year INTEGER, next_no INTEGER, PK(doc_type, year) )
```

Invoice outstanding = `total − Σ allocations` (plus advances netting via 6310/4310).
Unallocated receipts post to 6310 авансы полученные; allocating later moves 6310 → 4010.

### 4.3 Dimensions & admin

```sql
counterparties (         -- ※ extended with fiscal fields real documents need
    id INTEGER PK, name TEXT NOT NULL, legal_name TEXT,
    inn TEXT,                          -- ИНН/ПИНФЛ
    vat_reg_code TEXT,                 -- регистрационный код плательщика НДС
    counterparty_type TEXT DEFAULT 'other',  -- client/vendor/founder/bank/state/staff/other
    country TEXT, default_currency TEXT DEFAULT 'UZS',
    bank_details TEXT, address TEXT,
    is_active INTEGER DEFAULT 1, notes TEXT, created_at TEXT
)
counterparty_aliases ( ※ v4 )         -- import name resolution
settings ( ※ v4 )                     -- + 'vat_rate' (12), 'allocation_base' (§7)
exchange_rates ( ※ v4 )
users ( ※ v4 )        audit_log ( ※ v4 )
fiscal_periods ( ※ v4 )               -- period close now actually blocks posting:
                                      -- post_entry() refuses date in a closed period
payment_types, departments, staff_roles, work_types ( ※ v4 lookups )
-- v4's tx_types and transaction_categories are RETIRED: the ledger account is the category.
```

### 4.4 Staff & man-hour cost engine (the core goal)

```sql
staff (               -- ※ v4 + employment dates for correct period math
    id INTEGER PK, name TEXT UNIQUE NOT NULL, full_name TEXT, nizam_name TEXT,
    role TEXT NOT NULL, department TEXT NOT NULL,
    staff_type TEXT NOT NULL CHECK(staff_type IN ('production','admin')),
    staff_code TEXT, hire_date TEXT, termination_date TEXT,
    counterparty_id INTEGER REFERENCES counterparties(id),  -- payroll analytic link
    is_active INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT
)
salary_history ( ※ v4: staff_id, base_salary, premium, start_date, end_date,
                 UNIQUE(staff_id, start_date) )
project_hours  ( ※ v4: project_id, staff_id, hours, period, source, phase_id,
                 applied_* rate snapshot columns, UNIQUE(project_id, staff_id, period) )
equipment (           -- merges v4 personal_equipment + general_equipment
    id INTEGER PK, name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('personal','general')),
    staff_id INTEGER REFERENCES staff(id),        -- required when kind='personal'
    quantity INTEGER DEFAULT 1, price REAL NOT NULL,
    lifespan_months INTEGER NOT NULL DEFAULT 36, purchase_date TEXT,
    purchase_doc_id INTEGER REFERENCES documents(id),  -- reconciles register ↔ ledger 0150/0200
    is_active INTEGER DEFAULT 1
)
licenses ( ※ v4 personal_licenses: staff_id, annual_cost, license_type, is_active )
overhead_budget ( ※ v4 overhead )     -- static monthly amounts; FALLBACK ONLY —
                                      -- primary overhead source is the ledger (§7)
period_allocations ( ※ v4 )           -- per-period per-staff cost snapshot:
                                      -- billable_hours, hours_share, admin_share_amount,
                                      -- overhead_share_amount, equipment/license amounts,
                                      -- gross/tax/social, total_monthly_cost, cost_rate,
                                      -- billing_rate, available_hours, + NEW: labor_cost_share,
                                      -- overhead_rate_pct, utilization_pct, net_multiplier
)
```

### 4.5 Projects, pricing, loans

```sql
projects ( ※ v4, two changes: client TEXT → counterparty_id FK;
           responsible TEXT → responsible_id FK. Keeps contract_amount, currency,
           risk_* four factors + risk_score + risk_coefficient, planned_* frozen
           baseline + plan_frozen_date, estimated_total_hours, is_billable, status )
project_phases  ( ※ v4 milestones, unchanged )
milestone_staff ( ※ v4, unchanged — set_milestone_staff() single write path )
project_aliases ( ※ v4 )
loans (               -- register; money movement happens via documents(doc_type='loan')
    id INTEGER PK, loan_type TEXT CHECK(loan_type IN ('olgan','bergan')),
    counterparty_id INTEGER REFERENCES counterparties(id),
    total_amount REAL, currency TEXT DEFAULT 'UZS', interest_rate REAL DEFAULT 0,
    issue_date TEXT NOT NULL, due_date TEXT,
    status TEXT DEFAULT 'ochiq' CHECK(status IN ('ochiq','yopilgan')), notes TEXT
)
-- v4 loan_payments RETIRED: repayments are cash_in/cash_out documents with loan_id;
-- balance = ledger balance on 6820/7820 (olgan) or 5830/4620-series (bergan) per loan.
-- v4 dividends table RETIRED: dividend documents + 6610/8710 postings.
unresolved_imports, import_skip_log ( ※ v4 )
```

## 5. Posting rules (`models/posting.py`)

All amounts UZS at document-date rate; ε = 1 UZS balance tolerance (rounded conversions).

| # | Document | Debit | Credit |
|---|---|---|---|
| 5.1 | sales_invoice | 4010 (counterparty) `total` | 9030 (project) `subtotal`; 6410.1 `vat_amount` |
| 5.2 | purchase_invoice | line account — default 2010 (project) for direct costs, 9420/9430 for office `amount`; 4410 `vat_amount` | 6010 (counterparty) `total` |
| 5.3 | cash_in | 5110/5010/5210 `total` | 4010 (allocated invoices) / 6310 (unallocated = advance) / 6820·7820 (loan received) / 9530 (interest received) |
| 5.4 | cash_out | 6010 (allocated) / 4310 (advance issued) / 9420·9430 (direct expense, no invoice) / 6820·7820 (loan repaid) / 9610 (interest paid) / 6610 (dividend payout) / 6710·6420.1·6520 (payroll/tax remittance) | 5110/5010/5210 `total` |
| 5.5 | payroll (per period, lines per staff) | 2010 (staff, production) / 9420 (admin) `gross + ЕСП` | 6710 `gross`; 6420.1 `НДФЛ withheld` (Dr 6710); 6520 `ЕСП` |
| 5.6 | void (any posted doc) | mirror-image reversing entry, `reversal_of_id` set | |
| 5.7 | dividend (declaration) | 8710 | 6610 (founder) |
| 5.8 | opening | each opening balance vs **0000 вспомогательный счёт**; posting the full set must leave 0000 at zero | |
| 5.9 | month-end FX revaluation (routine, not user doc) | 5210/4010/6010 currency balances → 9540 gains / 9620 losses at latest rate | |
| 5.10 | manual | user-entered lines, balance validated | |

Period close (`fiscal_periods`): closing routine posts 9030 → 9910 ← 9130/9410/9420/9430
(the Т-accounts zero out into финансовый результат), then 9910 → 8710 at year end.
`post_entry()` refuses dates inside `soft_closed`/`hard_closed` periods — v4 had the
periods table but never enforced it.

## 6. Entry forms — field re-evaluation

The v4 single "transaction" form (direction + tx_type + amount + paid + free-text names)
becomes per-document modals. Fields marked ● are new vs v4.

**Sales invoice (счёт-фактура выданный):** number (auto ●), date, customer (picker w/ ИНН ●,
VAT code ●), contract ref ●, project + milestone, currency + rate (auto), line items ●
(description, qty, unit, price, VAT % → VAT amount auto), subtotal/VAT/total (auto ●),
due date, notes. *Replaces: amount/paid/client-free-text/doc_id.*

**Incoming payment (поступление):** date, bank doc № ●, payer (picker), method + bank
account ●, currency + rate, amount, **allocation grid ●** — open invoices of this payer with
outstanding balances, amount-to-allocate per invoice (multi-invoice split; unallocated
remainder flagged as advance), purpose text. *Replaces: editing `paid` on the invoice row +
follow-up payment rows.*

**Purchase invoice / expense (расход):** vendor (picker w/ ИНН ●), their invoice № ●, date,
lines with expense account picker ● (friendly grouped labels: "Аутсорсинг → 2010",
"Аренда офиса → 9420"…), project/milestone on direct-cost lines, VAT input ●, due date.

**Payroll (ведомость ●):** period; auto-filled grid from `salary_history` (staff, gross,
premium, НДФЛ, ЕСП, net); adjustable per row; one click accrues (5.5), remittances are
cash_out docs. *Replaces: manual 'maosh'/'soliq' internal transactions.*

**Loan:** type, counterparty, principal, currency, rate %, issue/due dates → issue doc posts
5.3/5.4; repayment = cash doc with loan picker splitting principal (6820/7820) vs interest
(9610/9530) ●.

**Manual journal entry ●:** date, memo, line grid (account picker, debit, credit, analytics),
live running Σdebit/Σcredit footer, post button disabled until balanced.

**Opening balances ●:** one grid per account group (cash, AR per counterparty, AP, loans,
equity), posts rule 5.8.

## 7. Man-hour cost engine — formula kept, checked against industry standards

The v4 formula chain is **retained**:

```
available_hours = (365 − 104 weekends − holidays − leave_days) / 12 × 8    ≈ 151 h/mo
total_monthly   = gross + tax(12%) + social(12%) + admin_share
                + personal_equipment + personal_licenses
                + general_equipment_share + overhead_share
cost_rate       = total_monthly / available_hours
billing_rate    = cost_rate × markup (2.0 default)
```

Verification against industry conventions (A/E-firm standards: AIA/PSMJ benchmarks, FAR Part 31
/ AASHTO audit-guide cost principles):

| Element | v4 practice | Industry convention | Verdict / change |
|---|---|---|---|
| Available hours | annual-basis net hours ≈ 1,816/yr | "net available (chargeable) hours" = 2,080 − holidays − PTO ≈ 1,760–1,880 | ✔ **matches — keep** |
| Burden composition | gross + employer taxes + equipment + licenses + admin + overhead | fully burdened labor rate = direct labor + fringe + overhead ("wrap rate") | ✔ **matches — keep** |
| Overhead source | hand-maintained `overhead` table (+opt-in pool) | actual booked indirect costs | ✔→ **improved: pool now reads the LEDGER** — trailing-window balances of accounts flagged `cost_pool='indirect'` (9420, 9430, depreciation…); `overhead_budget` is the fallback for empty ledgers |
| Allocation base | proportional to **billable hours** share | standard base is **direct labor cost** (a senior hour absorbs more overhead than a junior hour); hours base is acceptable but non-standard | ⚠ **change default**: `allocation_base` setting = `labor_cost` (new default) \| `hours` (v4-compatible) \| head-count fallback |
| Overhead rate visibility | not reported | overhead expressed as % of direct labor; A/E benchmark ≈ 150–180% | ● **add**: compute & display firm overhead rate on the rates page + snapshot |
| Markup | 2.0× on *burdened* cost, absorbing utilization + margin | industry quotes "net multiplier" ≈ 2.75–3.25 on *raw* direct labor — different basis, same economics | ✔ keep formula; ● **display the equivalent net multiplier** (billing_rate ÷ raw labor rate) so the firm can benchmark |
| Utilization | absorbed into markup, invisible | tracked explicitly; production target 75–85% chargeable | ● **add**: per-staff utilization % (billable ÷ available) on rates page + snapshot; does not change the math |
| Depreciation | straight-line from equipment register | accrual depreciation in overhead | ✔ keep register as engine input; ● reconciliation report register ↔ ledger 0200/0230 balances |
| Rate snapshots | `period_allocations` frozen per period | rates frozen per audit period | ✔ **keep** + new columns: `labor_cost_share`, `overhead_rate_pct`, `utilization_pct`, `net_multiplier` |

The engine's inputs become auditable: salaries from posted payroll documents, overhead from
posted expense documents, depreciation reconcilable — the trial balance proves nothing was
missed, which is precisely what the hand-maintained v4 tables could not do.

## 8. UI plan

**Layout.** v4 sidebar layout kept, regrouped:

- **Хужжатлар / Documents** — invoices (sales / purchase), payments (in / out), payroll, loans
- **Бухгалтерия / Ledger** — journal, оборотно-сальдовая ведомость (trial balance w/ opening /
  turnover / closing per account), P&L, balance sheet, VAT report, period close
- **Ходимлар / Staff & Rates** — staff, salaries, hours, **rates page** (the headline:
  per-person cost_rate/billing_rate + overhead rate, utilization, net multiplier), equipment & licenses
- **Лойиҳалар / Projects** — list, detail (Plan & Price card ported), budget plan-vs-fact
- **Ҳисоботлар / Reports** — dashboard (cash, AR/AP, burn/runway, capacity), cash flow, aging
- **Созламалар / Admin** — chart of accounts, counterparties, settings, periods, users, import

**List + modal pattern (every list view).** One Jinja macro set (`_modal.html`,
`_list_page.html`): filter bar → table → **[+ Yangi]** button top-right. The button opens a
native `<dialog>` modal; row **✎** opens the same modal prefilled (form action switches
create/update). Vanilla JS only (open/close, line-item grid add/remove rows, live totals,
allocation-grid math); forms POST normally, server redirects back to the list. Posted
documents open read-only with **Void** (confirm) and **Copy to new**. Status chips reuse the
v4 `_status_macros.html` approach.

**Modal forms that need line grids:** invoice lines, payment allocations, payroll grid,
manual entry lines, opening balances — one reusable JS grid helper (~100 lines), no framework.

## 9. Reports (all pure SELECTs over journal_lines)

Trial balance (must total zero — rendered proof of books integrity) · P&L from Т-accounts ·
Balance sheet from А/КА/П balances · Cash flow = 5xxx movements by month (direct method;
replaces v4 `get_cash_flow_by_month` reconstruction) · AR/AP aging from open allocations by
`due_date` · VAT: 6410.1 output vs 4410 input per period · Project profitability: 9030(project)
− 2010(project) − allocated labor from `period_allocations` · FX position & revaluation history ·
Dashboard tiles: cash = Σ5xxx, receivable = 4010, payable = 6010, burn/runway/capacity (v4
logic, ledger-fed).

## 10. Step-by-step build plan

Each phase ends with a standalone test script (house style: temp DB, no server) and a working
app — phases are shippable increments.

**Phase 0 — Scaffold (½ day).** New folder `mizan5/`; app factory, auth, network guard,
`base.html` + sidebar, `utils.render_page`, translations skeleton, settings + exchange_rates +
users + audit + fiscal_periods in `init_db()`. *Accept: app boots, login works, empty pages.*

**Phase 1 — Ledger core (1–2 days).** `accounts` + seed (~40 from `account_map` set) +
chart-of-accounts admin page; `journal_entries`/`journal_lines`; `post_entry()` with balance
validation + closed-period refusal; entry_no sequence; trial balance page; manual journal modal.
*Accept: `test_ledger.py` — balanced-only enforcement, trial balance zero, closed-period refusal.*

**Phase 2 — Dimensions (1 day).** Counterparties (+aliases) CRUD with ИНН/VAT fields;
projects + phases skeleton (port v4 tables); list+modal pattern extracted into macros here —
every later page reuses it. *Accept: CRUD via modals on three lists.*

**Phase 3 — Documents engine (3–4 days, the heart).** `documents`/`document_lines`/
`doc_sequences`/`payment_allocations`; posting rules 5.1–5.4, 5.6, 5.8; numbering; void; four
document modals (sales invoice, purchase invoice, cash in w/ allocation grid, cash out);
document list views; opening-balance form; AR/AP aging. *Accept: `test_documents.py` — post →
entry created & balanced; allocation math; void reverses; aging matches open allocations;
advance flow 6310→4010.*

**Phase 4 — Payroll (1–2 days).** Staff + salary_history (port); payroll document + grid modal
(rule 5.5); НДФЛ/ЕСП from settings; remittance cash_out shortcuts. *Accept: `test_payroll.py` —
accrual entry balances; 6710/6420/6520 balances settle to zero after remittances.*

**Phase 5 — Man-hour cost engine (2–3 days).** Port v4 `staff.py` rate math verbatim, then
apply §7 changes: ledger-driven indirect pool (`cost_pool` flags), `allocation_base` setting
(labor_cost default), new snapshot columns, rates page with overhead %, utilization, net
multiplier; equipment/licenses lists + depreciation reconciliation report. *Accept:
`test_rates.py` — golden parity test: with identical inputs and `allocation_base='hours'`, v5
rates == v4 rates to 0.01; ledger-pool vs budget-fallback switch; allocation-base variants.*

**Phase 6 — Projects & pricing (2 days).** Port Plan & Price card, milestones, freeze/baseline,
budget page; project income/cost now read the ledger (9030/2010 by project). *Accept: port
`test_pricing_plan.py` + `test_milestones.py` — the price-ladder linearity identity must
survive the port.*

**Phase 7 — Reports & close (2 days).** P&L, balance sheet, cash flow, VAT report, dashboard;
FX revaluation routine (5.9); period close posting Т-accounts → 9910 → 8710. *Accept:
`test_reports.py` — balance sheet balances; P&L == 9910 movement; cash flow == 5xxx deltas.*

**Phase 8 — Migration & imports (2–3 days).** `import_v4.py`: one-shot read of v4
`mizan_finance.db` → counterparties (from client/paid_to text via aliases), projects/phases/
hours/staff/salaries verbatim, v4 transactions → documents + postings (invoice rows →
invoices, follow-up payment rows → payments with allocations, internal → purchase/cash docs,
loans/dividends → their documents), opening balances at cutover date. Port NIZAM hours import
and Excel import (now creating documents). *Accept: `test_migration.py` on a copy of the real
DB — v5 cash balance == v4 running balance; v5 AR == v4 AR aging total; row-count report of
anything skipped.*

**Phase 9 — Polish (1–2 days).** i18n completeness test (port `test_loans_i18n.py` pattern),
role permissions (viewer read-only, manager no period-close), backup port, seed script, docs
(`CLAUDE.md` for v5). *Accept: full test suite green; manual smoke checklist.*

## 11. Pinned invariants (the test suite's spine)

1. Every journal entry balances (Σdebit == Σcredit, ε = 1 UZS) — enforced and tested.
2. Trial balance totals zero at any date.
3. A document's `total` equals its entry's total on the control account (4010/6010/5xxx).
4. Σ allocations against an invoice ≤ invoice total; AR aging == open 4010 by due_date.
5. Cash on dashboard == Σ balances of 5xxx accounts == cash-flow report cumulative net.
6. Rate-engine parity: v5 (`allocation_base='hours'`) reproduces v4 rates on identical inputs.
7. Pricing ladder linearity: Σ per-milestone target == target(Σ costs) (ported v4 invariant).
8. Void is a perfect mirror: entry + reversal net to zero on every account.
9. Post-migration: v5 cash/AR/AP equal v4's computed equivalents on the real database.

## 12. Open decisions (defaults chosen, flag if wrong)

- **VAT default**: firm assumed VAT-registered at 12%; `vat_rate=0` supported per line for
  exempt operations. If the firm is on упрощённый режим without VAT, set default 0 — schema
  unchanged.
- **Editing posted documents**: default = void + copy (clean audit trail). Same-open-period
  in-place re-post is a convenience toggle we can add later.
- **Loan interest accrual**: cash-basis (interest expense on payment) as in v4, not monthly
  accrual — acceptable at this scale, revisit if loans grow.
- **Earned revenue / POC**: stays a report calculation (as v4), not posted to the ledger.
- **App naming**: folder `mizan5/`, DB `mizan5.db` — separate from v4 so both run side by side
  during parallel-run month.
