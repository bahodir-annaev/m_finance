-- =============================================================================
-- MIZAN Finance -- transactions INSERT template (placeholder rows)
--
-- Mirrors the manual entry forms at  /accounting/transactions
--   Block A -- "01 -- Tashqi (External)" subtab   -> direction = 'external'
--   Block B -- "02 -- Ichki (Internal)"  subtab   -> direction = 'internal'
--   Block C -- the "+ to'lov" follow-up payment modal (_tx_payment_modal.html)
--
-- Only the fields the forms actually render are exposed as editable placeholders
-- (the `form(...)` CTE at the top of each block). Everything else the app writes
-- -- ref_id, exchange_rate, amount_usd, status, the UZS conversion -- is derived
-- here by the same rules as controllers/accounting_bp.py, so rows loaded through
-- this script are indistinguishable from rows entered through the UI.
--
-- HOW TO USE
--   1. Replace the PLACEHOLDER rows in each `VALUES` list. Add/remove rows freely.
--   2. Delete any block you do not need (each block is self-contained).
--   3. Run:  sqlite3 mizan_finance.db < insert_transactions_template.sql
--   4. Check the verification SELECT at the bottom before/instead of COMMIT.
--
-- WHAT THE FORM DOES *NOT* EXPOSE (left at the handler's defaults below)
--   client, responsible, contract_val -- the POST handler reads them, but the
--       external form renders no input for them. Add them to the CTE if needed.
--   phase_id, counterparty_id, responsible_id -- only reachable via the edit
--       modal (_tx_modal.html) / reference data, never on the insert form.
--   status -- never typed in; always derived from amount vs paid.
--
-- CONVERSION RULES (identical to accounting_bp.accounting_transactions)
--   rate            = latest exchange_rates row with date <= tx date,
--                     falling back to the `usd_rate` setting
--   currency = USD  -> amount = ROUND(value * rate), amount_usd = value
--   currency = UZS  -> amount = value,               amount_usd = ROUND(value / rate, 2)
--   amount_val = 0  -> the paid amount becomes the committed amount
--   status          -> 'paid' if amount > 0 and paid >= amount,
--                      'partial' if paid > 0, else 'pending'
--   A row is skipped entirely unless tx_type is set and (amount > 0 or paid > 0).
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

-- =============================================================================
-- BLOCK A -- EXTERNAL (Tashqi) -- client invoices, outsourcing, materials
-- Form fields, in the order they appear on screen:
--   date | tx_type | category | payment_type | project | description | paid_to
--   | doc_id | amount_val | paid_val | currency | deadline | notes
--
--   tx_type      -- code from `tx_types` (tushum, mizan_monthly, yakuniy_hisob,
--                   outsourcing, material). Income types feed INCOME_TX_TYPES.
--   category     -- transaction_categories.name_uz, or NULL. Resolved by name.
--   project      -- projects.name, exact match, or NULL. Resolved by name;
--                   an unknown name silently yields project_id = NULL, exactly
--                   as the UI does.
--   doc_id       -- required on the form (SH-001 style).
--   amount_val   -- invoiced/committed amount. Leave 0 for a pure cash receipt.
--   paid_val     -- amount actually received. Leave 0 for an unpaid invoice --
--                   that is what feeds AR aging.
--   deadline     -- payment due date, or NULL.
-- =============================================================================

WITH form(date, tx_type, category, payment_type, project, description,
          paid_to, doc_id, amount_val, paid_val, currency, deadline, notes) AS (
    VALUES
    -- Unpaid invoice: committed amount only, paid = 0 -> status 'pending'
    ('2026-08-01', 'tushum',        NULL, 'bank', 'PLACEHOLDER PROJECT 1',
     'PLACEHOLDER -- 1-avans 40%',      NULL,              'SH-001',
     100000000, 0,        'UZS', '2026-09-01', 'PLACEHOLDER note'),

    -- Partially paid invoice -> status 'partial'
    ('2026-08-02', 'tushum',        NULL, 'bank', 'PLACEHOLDER PROJECT 1',
     'PLACEHOLDER -- 2-avans 30%',      NULL,              'SH-002',
     75000000,  30000000, 'UZS', '2026-09-15', NULL),

    -- Fully paid receipt -> status 'paid'
    ('2026-08-03', 'mizan_monthly', NULL, 'bank', 'PLACEHOLDER PROJECT 2',
     'PLACEHOLDER -- avgust oylik',     NULL,              'SH-003',
     50000000,  50000000, 'UZS', NULL,         NULL),

    -- USD invoice: values are entered in USD, stored converted to UZS
    ('2026-08-04', 'tushum',        NULL, 'bank', 'PLACEHOLDER PROJECT 2',
     'PLACEHOLDER -- USD contract',     NULL,              'SH-004',
     8000,      0,        'USD', '2026-10-01', NULL),

    -- External expense (subcontractor / materials)
    ('2026-08-05', 'outsourcing',   NULL, 'bank', 'PLACEHOLDER PROJECT 1',
     'PLACEHOLDER -- konstruktiv qism', 'PLACEHOLDER SUBCO', 'SH-005',
     20000000,  20000000, 'UZS', NULL,         NULL)
),
resolved AS (
    SELECT
        f.*,
        COALESCE(
            (SELECT r.rate FROM exchange_rates r
              WHERE r.date <= f.date ORDER BY r.date DESC LIMIT 1),
            (SELECT s.value FROM settings s WHERE s.key = 'usd_rate'),
            12850
        ) AS rate,
        -- blank/zero invoice amount -> the paid amount is the commitment
        CASE WHEN COALESCE(f.amount_val, 0) = 0
             THEN COALESCE(f.paid_val, 0) ELSE f.amount_val END AS amt
    FROM form f
),
converted AS (
    SELECT
        r.*,
        CASE WHEN r.currency = 'USD' THEN ROUND(r.amt * r.rate, 0)
             ELSE r.amt END                                        AS amount_uzs,
        CASE WHEN r.currency = 'USD' THEN r.amt
             WHEN r.amt > 0          THEN ROUND(r.amt / r.rate, 2)
             ELSE 0 END                                            AS amount_usd,
        CASE WHEN r.currency = 'USD' THEN ROUND(COALESCE(r.paid_val, 0) * r.rate, 0)
             ELSE COALESCE(r.paid_val, 0) END                      AS paid_uzs
    FROM resolved r
)
INSERT INTO transactions
    (direction, tx_type, date, ref_id, doc_id, project_id, category_id,
     description, client, responsible, paid_to,
     contract_amount, contract_amount_usd, amount, amount_usd, paid,
     currency, exchange_rate, payment_type, deadline, notes, status)
SELECT
    'external',
    c.tx_type,
    c.date,
    -- app format is PRJ-YYYYmmdd-HHMMSS; the counter keeps a bulk load unique
    printf('PRJ-%s-%03d',
           strftime('%Y%m%d-%H%M%S', 'now', 'localtime'),
           ROW_NUMBER() OVER (ORDER BY c.date, c.doc_id))          AS ref_id,
    NULLIF(TRIM(COALESCE(c.doc_id, '')), ''),
    (SELECT p.id FROM projects p WHERE p.name = c.project)         AS project_id,
    (SELECT tc.id FROM transaction_categories tc
      WHERE tc.name_uz = c.category
        AND tc.direction IN ('in', 'external', 'both')
        AND tc.is_active = 1 LIMIT 1)                              AS category_id,
    COALESCE(c.description, ''),
    NULL,                                     -- client      (no input on the form)
    NULL,                                     -- responsible (no input on the form)
    NULLIF(TRIM(COALESCE(c.paid_to, '')), ''),
    0, 0,                                     -- contract_amount / _usd (no input)
    c.amount_uzs,
    c.amount_usd,
    c.paid_uzs,
    c.currency,
    c.rate,
    COALESCE(c.payment_type, 'bank'),
    NULLIF(TRIM(COALESCE(c.deadline, '')), ''),
    COALESCE(c.notes, ''),
    CASE WHEN c.amount_uzs > 0 AND c.paid_uzs >= c.amount_uzs THEN 'paid'
         WHEN c.paid_uzs > 0                                  THEN 'partial'
         ELSE 'pending' END                                        AS status
FROM converted c
-- the form's own save guard
WHERE c.tx_type IS NOT NULL AND TRIM(c.tx_type) <> ''
  AND (c.amount_uzs > 0 OR c.paid_uzs > 0);

-- =============================================================================
-- BLOCK B -- INTERNAL (Ichki) -- firm expenses
-- Form fields, in the order they appear on screen:
--   date | tx_type | category | payment_type | description | paid_to | doc_id
--   | paid_val | currency | notes
--
-- The internal form renders NO invoice-amount field -- an internal expense is
-- always recorded as money already spent, so amount = paid and status = 'paid'.
-- There is no project / deadline / client on this form either.
--
--   tx_type -- maosh, premiya, ijara, kommunal, soliq, ovqat, litsenziya,
--              malaka, overhead. NOTE: maosh and soliq are excluded from the
--              indirect cost pool (they are modeled elsewhere).
-- =============================================================================

WITH form(date, tx_type, category, payment_type, description,
          paid_to, doc_id, paid_val, currency, notes) AS (
    VALUES
    ('2026-08-05', 'ijara',      NULL, 'bank',
     'PLACEHOLDER -- avgust ofis ijarasi', 'PLACEHOLDER ARENDATOR', 'MZ-0001',
     15000000, 'UZS', 'PLACEHOLDER note'),

    ('2026-08-05', 'kommunal',   NULL, 'bank',
     'PLACEHOLDER -- elektr / suv',        'PLACEHOLDER SUPPLIER',  'MZ-0002',
     2500000,  'UZS', NULL),

    ('2026-08-10', 'maosh',      NULL, 'karta',
     'PLACEHOLDER -- iyul 2026 maoshi',    'PLACEHOLDER XODIM',     'MZ-0003',
     180000000,'UZS', NULL),

    ('2026-08-12', 'litsenziya', NULL, 'online',
     'PLACEHOLDER -- Autodesk yillik',     'PLACEHOLDER VENDOR',    'MZ-0004',
     1200,     'USD', NULL)
),
resolved AS (
    SELECT
        f.*,
        COALESCE(
            (SELECT r.rate FROM exchange_rates r
              WHERE r.date <= f.date ORDER BY r.date DESC LIMIT 1),
            (SELECT s.value FROM settings s WHERE s.key = 'usd_rate'),
            12850
        ) AS rate
    FROM form f
),
converted AS (
    SELECT
        r.*,
        -- no invoice field on this form: the paid amount is also the commitment
        CASE WHEN r.currency = 'USD'
             THEN ROUND(COALESCE(r.paid_val, 0) * r.rate, 0)
             ELSE COALESCE(r.paid_val, 0) END                      AS amount_uzs,
        CASE WHEN r.currency = 'USD'         THEN COALESCE(r.paid_val, 0)
             WHEN COALESCE(r.paid_val, 0) > 0 THEN ROUND(r.paid_val / r.rate, 2)
             ELSE 0 END                                            AS amount_usd
    FROM resolved r
)
INSERT INTO transactions
    (direction, tx_type, date, ref_id, doc_id, category_id, description,
     responsible, paid_to,
     contract_amount, contract_amount_usd, amount, amount_usd, paid,
     currency, exchange_rate, payment_type, notes, status)
SELECT
    'internal',
    c.tx_type,
    c.date,
    printf('MZ-%s-%03d',
           strftime('%Y%m%d-%H%M%S', 'now', 'localtime'),
           ROW_NUMBER() OVER (ORDER BY c.date, c.doc_id))          AS ref_id,
    NULLIF(TRIM(COALESCE(c.doc_id, '')), ''),
    (SELECT tc.id FROM transaction_categories tc
      WHERE tc.name_uz = c.category
        AND tc.direction IN ('out', 'internal', 'both')
        AND tc.is_active = 1 LIMIT 1)                              AS category_id,
    COALESCE(c.description, ''),
    NULL,                                     -- responsible (no input on the form)
    NULLIF(TRIM(COALESCE(c.paid_to, '')), ''),
    0, 0,                                     -- contract_amount / _usd (no input)
    c.amount_uzs,
    c.amount_usd,
    c.amount_uzs,                             -- paid == amount for internal rows
    c.currency,
    c.rate,
    COALESCE(c.payment_type, 'bank'),
    COALESCE(c.notes, ''),
    CASE WHEN c.amount_uzs > 0 THEN 'paid' ELSE 'pending' END      AS status
FROM converted c
WHERE c.tx_type IS NOT NULL AND TRIM(c.tx_type) <> ''
  AND c.amount_uzs > 0;

-- =============================================================================
-- BLOCK C -- FOLLOW-UP PAYMENTS (the "+ to'lov" modal, v6.2)
-- Form fields:  date | amount | payment_type | currency | notes
-- The parent invoice is chosen by clicking its row, so it is not a form input --
-- identify it here by doc_id (must match exactly one invoice).
--
-- A payment row is its OWN row in `transactions` with amount = 0 and
-- parent_tx_id pointing at the invoice; it inherits direction, tx_type,
-- project_id, phase_id, category_id, client, responsible and paid_to from the
-- invoice. amount = 0 is what stops SUM(amount) double counting. Payment rows
-- must never be chained -- a parent with parent_tx_id set is rejected below.
-- =============================================================================

WITH form(parent_doc_id, date, amount, payment_type, currency, notes) AS (
    VALUES
    -- closes the partially paid SH-002 invoice from Block A
    ('SH-002', '2026-09-10', 45000000, 'bank', 'UZS', 'PLACEHOLDER -- yakuniy to''lov'),
    -- first payment against the USD invoice SH-004 (entered in USD)
    ('SH-004', '2026-09-20', 3000,     'bank', 'USD', NULL)
),
resolved AS (
    SELECT
        f.*,
        p.id AS parent_id,
        COALESCE(
            (SELECT r.rate FROM exchange_rates r
              WHERE r.date <= f.date ORDER BY r.date DESC LIMIT 1),
            (SELECT s.value FROM settings s WHERE s.key = 'usd_rate'),
            12850
        ) AS rate
    FROM form f
    JOIN transactions p
      ON p.doc_id = f.parent_doc_id
     AND p.parent_tx_id IS NULL          -- never hang a payment off a payment
)
INSERT INTO transactions
    (direction, tx_type, date, ref_id, doc_id, project_id, phase_id,
     category_id, counterparty_id, description, client, responsible, paid_to,
     amount, amount_usd, paid, currency, exchange_rate, payment_type,
     notes, status, parent_tx_id)
SELECT
    p.direction, p.tx_type, r.date,
    printf('PAY-%s-%03d',
           strftime('%Y%m%d-%H%M%S', 'now', 'localtime'),
           ROW_NUMBER() OVER (ORDER BY r.date, r.parent_id))       AS ref_id,
    p.doc_id, p.project_id, p.phase_id,
    p.category_id, p.counterparty_id,
    COALESCE(p.description, ''),
    p.client, p.responsible, p.paid_to,
    0, 0,                                     -- a payment carries no commitment
    CASE WHEN r.currency = 'USD' THEN ROUND(r.amount * r.rate, 0)
         ELSE r.amount END                                         AS paid,
    r.currency,
    r.rate,
    COALESCE(r.payment_type, p.payment_type, 'bank'),
    COALESCE(r.notes, ''),
    'paid',
    r.parent_id
FROM resolved r
JOIN transactions p ON p.id = r.parent_id
WHERE r.amount > 0;

-- Re-derive every invoice's status from settled = own paid + SUM(children.paid),
-- the SQL equivalent of models/transactions.recompute_parent_status().
-- Idempotent: safe to run even if Block C inserted nothing.
UPDATE transactions
   SET status = CASE
           WHEN amount > 0
            AND paid + COALESCE((SELECT SUM(ch.paid) FROM transactions ch
                                  WHERE ch.parent_tx_id = transactions.id), 0) >= amount
               THEN 'paid'
           WHEN paid + COALESCE((SELECT SUM(ch.paid) FROM transactions ch
                                  WHERE ch.parent_tx_id = transactions.id), 0) > 0
               THEN 'partial'
           ELSE 'pending' END,
       updated_at = strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')
 WHERE parent_tx_id IS NULL
   AND id IN (SELECT DISTINCT parent_tx_id FROM transactions
               WHERE parent_tx_id IS NOT NULL);

COMMIT;

-- =============================================================================
-- Verification -- run before COMMIT (swap COMMIT for ROLLBACK) or after.
-- `settled` is the invoice's own paid plus every linked payment row; comparing
-- raw `paid < amount` would report a settled invoice as still outstanding.
-- =============================================================================
SELECT
    t.id, t.direction, t.tx_type, t.date, t.ref_id, t.doc_id,
    p.name AS project, t.description,
    t.amount, t.paid,
    t.paid + COALESCE((SELECT SUM(ch.paid) FROM transactions ch
                        WHERE ch.parent_tx_id = t.id), 0) AS settled,
    t.currency, t.exchange_rate, t.amount_usd,
    t.payment_type, t.deadline, t.status, t.parent_tx_id
FROM transactions t
LEFT JOIN projects p ON p.id = t.project_id
WHERE t.doc_id LIKE 'SH-%' OR t.doc_id LIKE 'MZ-%'
ORDER BY t.id;
