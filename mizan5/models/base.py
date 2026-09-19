"""Database connection, schema init, settings, and generic CRUD helpers.

v5 is a fresh schema — there are no migrations. Every table is created by
init_db() at startup, which is safe to run repeatedly.

The ledger tables (`journal_entries`, `journal_lines`) are deliberately absent
from _ALLOWED_TABLES: nothing may write a posting except models/ledger.py, so
an unbalanced entry can never reach the database through generic CRUD.
"""
import os
import re
import sqlite3
from calendar import monthrange
from datetime import datetime

DB_PATH = os.environ.get('MIZAN5_DB') or os.path.join(
    os.path.dirname(__file__), '..', 'mizan5.db')

# Account kinds, straight from the Вид column of CHART_OF_ACCOUNTS.md.
# A/KA/P carry balances across periods; T (транзакционный) accounts are closed
# into the financial result (9910) and start each year at zero.
ACCOUNT_KINDS = ('A', 'KA', 'P', 'T')
# Kinds whose natural balance is a debit. Used by every balance query: a
# balance is (debit − credit) for these and (credit − debit) for the rest.
DEBIT_KINDS = ('A',)

# Balance tolerance in UZS. Currency conversion rounds to whole so'm, so a
# multi-line foreign-currency entry can be off by a few tiyin-equivalents;
# anything larger is a real bug and must be refused.
BALANCE_EPSILON = 1.0

DOC_TYPES = (
    'sales_invoice', 'purchase_invoice', 'cash_in', 'cash_out',
    'payroll', 'dividend', 'loan', 'manual', 'opening',
)

# Document number prefixes, per doc_type (see documents.next_document_number).
DOC_PREFIXES = {
    'sales_invoice': 'SF',      # счёт-фактура выданный
    'purchase_invoice': 'SP',   # счёт-фактура полученный
    'cash_in': 'PK',            # приходный / поступление
    'cash_out': 'RS',           # расходный / списание
    'payroll': 'ZP',            # зарплатная ведомость
    'dividend': 'DV',
    'loan': 'ZM',
    'manual': 'MJ',             # manual journal
    'opening': 'OB',            # opening balances
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Unicode-aware lowercase; SQLite's LOWER()/LIKE fold ASCII only, so
    # Cyrillic and Uzbek text would otherwise compare case-sensitively.
    conn.create_function("ulower", 1, lambda s: s.lower() if s is not None else None)
    return conn


def now_ts():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def today_str():
    return datetime.now().strftime('%Y-%m-%d')


def period_of(date_str):
    """'YYYY-MM-DD' → 'YYYY-MM'."""
    return (date_str or '')[:7]


def period_end(period):
    """'YYYY-MM' → 'YYYY-MM-<last day>'. The as-of date for costing a period."""
    y, m = int(period[:4]), int(period[5:7])
    return f'{y:04d}-{m:02d}-{monthrange(y, m)[1]:02d}'


# ========== Settings & rates ==========

def get_setting(key, default=None):
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    if row is None:
        return default
    return row['value']


def set_setting(key, value):
    conn = get_db()
    conn.execute("UPDATE settings SET value=? WHERE key=?", (float(value), key))
    conn.commit()
    conn.close()


def get_rate_for_date(dt_str):
    """UZS per 1 USD on or before dt_str — the most recent published rate."""
    conn = get_db()
    row = conn.execute(
        "SELECT rate FROM exchange_rates WHERE date <= ? ORDER BY date DESC LIMIT 1",
        (dt_str,)
    ).fetchone()
    conn.close()
    return row['rate'] if row else (get_setting('usd_rate') or 12850)


def get_current_usd_rate():
    """Today's rate — the latest exchange_rates row, not the usd_rate setting.

    The setting is only a fallback for an empty install; using it as "current"
    would make every FX figure depend on someone updating two places.
    """
    conn = get_db()
    row = conn.execute(
        "SELECT rate FROM exchange_rates WHERE date <= date('now')"
        " ORDER BY date DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return row['rate'] if row else (get_setting('usd_rate') or 12850)


# ========== Audit ==========

def _write_audit(conn, action, entity_type, entity_id=None, field=None,
                 old_value=None, new_value=None, context=None, user=None):
    """Append an audit row on an open connection. Never raises — an audit
    failure must not roll back the business write it is describing."""
    try:
        conn.execute(
            "INSERT INTO audit_log (user, action, entity_type, entity_id, field,"
            " old_value, new_value, context) VALUES (?,?,?,?,?,?,?,?)",
            (user or _current_username(), action, entity_type, entity_id, field,
             str(old_value) if old_value is not None else None,
             str(new_value) if new_value is not None else None, context)
        )
    except Exception:
        pass


def _current_username():
    """Logged-in username, or None outside a request context."""
    try:
        from flask_login import current_user
        if current_user and current_user.is_authenticated:
            return current_user.username
    except Exception:
        pass
    return None


def write_audit_log(action, entity_type, entity_id=None, context=None):
    conn = get_db()
    _write_audit(conn, action, entity_type, entity_id, context=context)
    conn.commit()
    conn.close()


# ========== Generic CRUD ==========

# Tables the generic /api/update and /api/delete endpoints may touch.
# journal_entries / journal_lines / documents are absent on purpose: postings
# go through ledger.post_entry() and documents through documents.py, both of
# which enforce invariants a blind UPDATE would skip.
_ALLOWED_TABLES = [
    'counterparties', 'projects', 'project_phases', 'staff', 'salary_history',
    'equipment', 'licenses', 'overhead_budget', 'exchange_rates', 'loans',
    'accounts', 'work_types', 'departments', 'staff_roles', 'payment_types',
    'project_hours', 'asset_classes', 'bank_accounts',
]

_DELETE_ALLOWED = [
    'counterparties', 'equipment', 'licenses', 'overhead_budget',
    'exchange_rates', 'staff', 'project_hours',
]


def get_record(table, record_id):
    if table not in _ALLOWED_TABLES:
        return None
    conn = get_db()
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_record(table, record_id, data):
    if table not in _ALLOWED_TABLES or not data:
        return False
    conn = get_db()
    try:
        cols = [d[1] for d in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        old_row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone()
        old_vals = dict(old_row) if old_row else {}
        updates = {k: v for k, v in data.items() if k in cols and k != 'id'}
        if not updates:
            return False
        if 'updated_at' in cols:
            updates['updated_at'] = now_ts()
        set_clause = ', '.join(f"{k}=?" for k in updates)
        conn.execute(f"UPDATE {table} SET {set_clause} WHERE id=?",
                     list(updates.values()) + [record_id])
        for field, new_val in updates.items():
            if field == 'updated_at':
                continue
            old_val = old_vals.get(field)
            if str(old_val) != str(new_val):
                _write_audit(conn, 'update', table, record_id, field, old_val, new_val)
        conn.commit()
        return True
    finally:
        conn.close()


def delete_record(table, record_id):
    """Soft-delete where the table has is_active, hard-delete otherwise."""
    if table not in _DELETE_ALLOWED:
        return False
    conn = get_db()
    try:
        cols = [d[1] for d in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if 'is_active' in cols:
            if 'updated_at' in cols:
                conn.execute(f"UPDATE {table} SET is_active=0, updated_at=? WHERE id=?",
                             (now_ts(), record_id))
            else:
                conn.execute(f"UPDATE {table} SET is_active=0 WHERE id=?", (record_id,))
            _write_audit(conn, 'delete', table, record_id, context='soft delete')
        else:
            conn.execute(f"DELETE FROM {table} WHERE id=?", (record_id,))
            _write_audit(conn, 'delete', table, record_id, context='hard delete')
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        conn.rollback()
        return False
    finally:
        conn.close()


def get_all_records(table, limit=500, active_only=True):
    if table not in _ALLOWED_TABLES:
        return []
    conn = get_db()
    cols = [d[1] for d in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    where = " WHERE is_active=1" if active_only and 'is_active' in cols else ""
    rows = conn.execute(
        f"SELECT * FROM {table}{where} ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ========== Entity name resolution (imports) ==========

_ENTITY_TABLES = {
    'project': ('projects', 'project_aliases', 'project_id'),
    'counterparty': ('counterparties', 'counterparty_aliases', 'counterparty_id'),
}


def normalize_name(name):
    """Lowercase, strip punctuation and extra whitespace: 'Loyiha-A' == 'loyiha a'."""
    if not name:
        return ''
    n = re.sub(r'[^\w]+', ' ', str(name).strip().lower(), flags=re.UNICODE)
    return re.sub(r'\s+', ' ', n).strip()


def resolve_entity_id(conn, entity_type, raw_name):
    """Alias table first, then a normalized match on the canonical table.
    Returns an id or None — never creates rows."""
    table, alias_table, fk_col = _ENTITY_TABLES[entity_type]
    norm = normalize_name(raw_name)
    if not norm:
        return None
    for row in conn.execute(f"SELECT alias, {fk_col} FROM {alias_table}"):
        if normalize_name(row['alias']) == norm:
            return row[fk_col]
    for row in conn.execute(f"SELECT id, name FROM {table}"):
        if normalize_name(row['name']) == norm:
            return row['id']
    return None


# ========== Fiscal periods ==========

def get_period_status(period_code):
    """'open' | 'soft_closed' | 'hard_closed', or None when the period has no row.

    A period with no row is treated as open — periods are created on demand,
    and refusing to post to an unlisted month would block a fresh install.
    """
    if not period_code:
        return None
    conn = get_db()
    row = conn.execute(
        "SELECT status FROM fiscal_periods WHERE code=?", (period_code,)
    ).fetchone()
    conn.close()
    return row['status'] if row else None


def is_period_closed(period_code):
    return get_period_status(period_code) in ('soft_closed', 'hard_closed')


def ensure_fiscal_period(conn, period_code):
    """Create the period row if absent (status 'open'). Does not commit."""
    if not period_code or len(period_code) != 7:
        return
    try:
        y, m = int(period_code[:4]), int(period_code[5:7])
    except ValueError:
        return
    from calendar import monthrange
    start = f"{y:04d}-{m:02d}-01"
    end = f"{y:04d}-{m:02d}-{monthrange(y, m)[1]:02d}"
    conn.execute(
        "INSERT OR IGNORE INTO fiscal_periods (code, start_date, end_date, status)"
        " VALUES (?,?,?,'open')", (period_code, start, end))


def create_fiscal_period(code, notes=None):
    """Open a period by hand (YYYY-MM). Returns (ok, error) with error in
    (None, 'bad_code', 'exists')."""
    code = (code or '').strip()
    if len(code) != 7 or code[4] != '-':
        return False, 'bad_code'
    try:
        y, m = int(code[:4]), int(code[5:7])
        if not 1 <= m <= 12:
            return False, 'bad_code'
    except ValueError:
        return False, 'bad_code'
    conn = get_db()
    try:
        if conn.execute("SELECT 1 FROM fiscal_periods WHERE code=?", (code,)).fetchone():
            return False, 'exists'
        ensure_fiscal_period(conn, code)
        if notes:
            conn.execute("UPDATE fiscal_periods SET notes=? WHERE code=?", (notes.strip(), code))
        _write_audit(conn, 'create', 'fiscal_periods', None, context=f'period {code} created')
        conn.commit()
        return True, None
    finally:
        conn.close()


def delete_fiscal_period(code):
    """Remove an OPEN period that has no journal entries. Returns (ok, error)
    with error in (None, 'not_found', 'not_open', 'has_entries')."""
    conn = get_db()
    try:
        row = conn.execute("SELECT status FROM fiscal_periods WHERE code=?", (code,)).fetchone()
        if not row:
            return False, 'not_found'
        if row['status'] != 'open':
            return False, 'not_open'
        n = conn.execute("SELECT COUNT(*) FROM journal_entries WHERE period=?",
                         (code,)).fetchone()[0]
        if n:
            return False, 'has_entries'
        conn.execute("DELETE FROM fiscal_periods WHERE code=?", (code,))
        _write_audit(conn, 'delete', 'fiscal_periods', None, context=f'period {code} deleted')
        conn.commit()
        return True, None
    finally:
        conn.close()


def set_period_notes(code, notes):
    conn = get_db()
    try:
        conn.execute("UPDATE fiscal_periods SET notes=? WHERE code=?",
                     ((notes or '').strip() or None, code))
        conn.commit()
    finally:
        conn.close()


# ========== Schema ==========

SCHEMA = [
    # ── Auth & audit ────────────────────────────────────────────────────────
    '''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('admin','manager','viewer')),
        is_active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now'))
    )''',
    '''CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL DEFAULT (datetime('now')),
        user TEXT, action TEXT NOT NULL, entity_type TEXT NOT NULL,
        entity_id INTEGER, field TEXT, old_value TEXT, new_value TEXT, context TEXT
    )''',

    # ── Settings & rates ────────────────────────────────────────────────────
    '''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value REAL NOT NULL, label TEXT, unit TEXT
    )''',
    '''CREATE TABLE IF NOT EXISTS exchange_rates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL UNIQUE, rate REAL NOT NULL
    )''',
    '''CREATE TABLE IF NOT EXISTS fiscal_periods (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        start_date TEXT NOT NULL, end_date TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open'
            CHECK(status IN ('open','soft_closed','hard_closed')),
        closed_at TEXT, closed_by_user TEXT, notes TEXT
    )''',

    # ── Ledger core ─────────────────────────────────────────────────────────
    '''CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        name_ru TEXT NOT NULL, name_uz TEXT, name_en TEXT,
        kind TEXT NOT NULL CHECK(kind IN ('A','KA','P','T')),
        val_flag INTEGER DEFAULT 0,
        subconto TEXT,
        cost_pool TEXT,
        is_active INTEGER DEFAULT 1,
        sort_order INTEGER DEFAULT 100,
        updated_at TEXT
    )''',
    '''CREATE TABLE IF NOT EXISTS journal_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_no INTEGER UNIQUE NOT NULL,
        date TEXT NOT NULL,
        period TEXT NOT NULL,
        document_id INTEGER REFERENCES documents(id),
        memo TEXT,
        reversal_of_id INTEGER REFERENCES journal_entries(id),
        created_by TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )''',
    '''CREATE TABLE IF NOT EXISTS journal_lines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_id INTEGER NOT NULL REFERENCES journal_entries(id) ON DELETE CASCADE,
        line_no INTEGER NOT NULL,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        debit REAL NOT NULL DEFAULT 0 CHECK(debit >= 0),
        credit REAL NOT NULL DEFAULT 0 CHECK(credit >= 0),
        currency TEXT, amount_cur REAL, exchange_rate REAL,
        counterparty_id INTEGER REFERENCES counterparties(id),
        project_id INTEGER REFERENCES projects(id),
        phase_id INTEGER REFERENCES project_phases(id),
        staff_id INTEGER REFERENCES staff(id),
        bank_account_id INTEGER REFERENCES bank_accounts(id),
        description TEXT,
        CHECK (debit = 0 OR credit = 0),
        CHECK (debit + credit > 0),
        UNIQUE(entry_id, line_no)
    )''',
    '''CREATE TABLE IF NOT EXISTS account_map (
        purpose TEXT PRIMARY KEY,
        account_id INTEGER NOT NULL REFERENCES accounts(id)
    )''',

    # ── Documents ───────────────────────────────────────────────────────────
    '''CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doc_type TEXT NOT NULL CHECK(doc_type IN (
            'sales_invoice','purchase_invoice','cash_in','cash_out',
            'payroll','dividend','loan','manual','opening')),
        number TEXT NOT NULL,
        date TEXT NOT NULL,
        period TEXT,
        counterparty_id INTEGER REFERENCES counterparties(id),
        project_id INTEGER REFERENCES projects(id),
        phase_id INTEGER REFERENCES project_phases(id),
        loan_id INTEGER REFERENCES loans(id),
        responsible_id INTEGER REFERENCES staff(id),
        contract_ref TEXT,
        external_number TEXT,
        currency TEXT NOT NULL DEFAULT 'UZS',
        exchange_rate REAL,
        subtotal REAL NOT NULL DEFAULT 0,
        vat_amount REAL NOT NULL DEFAULT 0,
        total REAL NOT NULL DEFAULT 0,
        total_cur REAL,
        payment_method TEXT,
        bank_account TEXT,
        bank_account_id INTEGER REFERENCES bank_accounts(id),
        due_date TEXT,
        cash_purpose TEXT,
        description TEXT, notes TEXT,
        status TEXT NOT NULL DEFAULT 'draft'
            CHECK(status IN ('draft','posted','void')),
        entry_id INTEGER REFERENCES journal_entries(id),
        voided_at TEXT, void_reason TEXT,
        created_by TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT,
        UNIQUE(doc_type, number)
    )''',
    '''CREATE TABLE IF NOT EXISTS document_lines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        line_no INTEGER NOT NULL,
        description TEXT,
        quantity REAL DEFAULT 1, unit TEXT,
        unit_price REAL DEFAULT 0,
        amount REAL NOT NULL DEFAULT 0,
        vat_rate REAL DEFAULT 0,
        vat_amount REAL DEFAULT 0,
        account_id INTEGER REFERENCES accounts(id),
        project_id INTEGER REFERENCES projects(id),
        phase_id INTEGER REFERENCES project_phases(id),
        staff_id INTEGER REFERENCES staff(id),
        counterparty_id INTEGER REFERENCES counterparties(id),
        -- payroll columns: gross/PIT/social per employee row
        gross REAL DEFAULT 0, pit REAL DEFAULT 0, social REAL DEFAULT 0,
        -- manual-entry columns: an explicit Dr/Cr pair for rule 5.10
        debit REAL DEFAULT 0, credit REAL DEFAULT 0,
        UNIQUE(document_id, line_no)
    )''',
    '''CREATE TABLE IF NOT EXISTS payment_allocations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        payment_doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        invoice_doc_id INTEGER NOT NULL REFERENCES documents(id),
        amount REAL NOT NULL CHECK(amount > 0),
        UNIQUE(payment_doc_id, invoice_doc_id)
    )''',
    '''CREATE TABLE IF NOT EXISTS doc_sequences (
        doc_type TEXT NOT NULL, year INTEGER NOT NULL,
        next_no INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (doc_type, year)
    )''',

    # ── Dimensions ──────────────────────────────────────────────────────────
    '''CREATE TABLE IF NOT EXISTS counterparties (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE, legal_name TEXT,
        inn TEXT, vat_reg_code TEXT,
        counterparty_type TEXT NOT NULL DEFAULT 'other',
        country TEXT, default_currency TEXT DEFAULT 'UZS',
        bank_details TEXT, address TEXT, phone TEXT,
        is_active INTEGER DEFAULT 1, notes TEXT,
        created_at TEXT DEFAULT (datetime('now')), updated_at TEXT
    )''',
    # The firm's own bank accounts — the 1С «Банковские счета» subconto. A
    # ledger account whose accounts.subconto = 'bank_account' is subdivided
    # into these rows on journal_lines.bank_account_id; the ledger code itself
    # stays single, so nothing that sums 5xxx by account changes.
    '''CREATE TABLE IF NOT EXISTS bank_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        name TEXT NOT NULL,
        account_number TEXT,
        bank_name TEXT, mfo TEXT,
        currency TEXT NOT NULL DEFAULT 'UZS',
        is_default INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 1,
        notes TEXT,
        created_at TEXT DEFAULT (datetime('now')), updated_at TEXT
    )''',
    '''CREATE TABLE IF NOT EXISTS counterparty_aliases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        counterparty_id INTEGER NOT NULL REFERENCES counterparties(id) ON DELETE CASCADE,
        alias TEXT NOT NULL UNIQUE, source TEXT
    )''',
    '''CREATE TABLE IF NOT EXISTS project_aliases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        alias TEXT NOT NULL UNIQUE, source TEXT
    )''',

    # ── Projects & pricing ──────────────────────────────────────────────────
    '''CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE, code TEXT,
        counterparty_id INTEGER REFERENCES counterparties(id),
        responsible_id INTEGER REFERENCES staff(id),
        risk_coefficient REAL NOT NULL DEFAULT 1.15,
        risk_score REAL DEFAULT 0,
        risk_deadline_months INTEGER, risk_client_type TEXT,
        risk_complexity TEXT, risk_currency TEXT,
        contract_amount REAL DEFAULT 0, currency TEXT DEFAULT 'UZS',
        start_date TEXT, end_date TEXT,
        status TEXT DEFAULT 'active' CHECK(status IN ('active','completed','paused')),
        is_billable INTEGER DEFAULT 1,
        estimated_total_hours REAL DEFAULT 0,
        planned_hours REAL DEFAULT 0, planned_cost REAL DEFAULT 0,
        planned_revenue REAL DEFAULT 0, planned_outsourcing REAL DEFAULT 0,
        planned_material REAL DEFAULT 0, plan_frozen_date TEXT,
        notes TEXT,
        created_at TEXT DEFAULT (datetime('now')), updated_at TEXT
    )''',
    '''CREATE TABLE IF NOT EXISTS project_phases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        code TEXT, name TEXT NOT NULL, sort_order INTEGER DEFAULT 100,
        work_type TEXT,
        planned_hours REAL DEFAULT 0, planned_cost REAL DEFAULT 0,
        planned_revenue REAL DEFAULT 0,
        planned_outsourcing REAL DEFAULT 0, planned_material REAL DEFAULT 0,
        start_date TEXT, end_date TEXT,
        completion_percent REAL DEFAULT 0,
        status TEXT DEFAULT 'planned'
            CHECK(status IN ('planned','in_progress','done','cancelled')),
        completed_date TEXT, notes TEXT, updated_at TEXT,
        UNIQUE(project_id, code)
    )''',
    '''CREATE TABLE IF NOT EXISTS milestone_staff (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phase_id INTEGER NOT NULL REFERENCES project_phases(id) ON DELETE CASCADE,
        staff_id INTEGER NOT NULL REFERENCES staff(id),
        est_hours REAL NOT NULL DEFAULT 0,
        cost_rate REAL DEFAULT 0, billing_rate REAL DEFAULT 0,
        UNIQUE(phase_id, staff_id)
    )''',

    # ── Staff & cost engine ─────────────────────────────────────────────────
    '''CREATE TABLE IF NOT EXISTS staff (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE, full_name TEXT, nizam_name TEXT,
        role TEXT NOT NULL, department TEXT NOT NULL,
        staff_type TEXT NOT NULL CHECK(staff_type IN ('production','admin')),
        staff_code TEXT,
        hire_date TEXT, termination_date TEXT,
        counterparty_id INTEGER REFERENCES counterparties(id),
        is_active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now')), updated_at TEXT
    )''',
    '''CREATE TABLE IF NOT EXISTS salary_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        staff_id INTEGER NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
        base_salary REAL NOT NULL DEFAULT 0,
        premium REAL NOT NULL DEFAULT 0,
        start_date TEXT NOT NULL, end_date TEXT, updated_at TEXT,
        UNIQUE(staff_id, start_date)
    )''',
    '''CREATE TABLE IF NOT EXISTS project_hours (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        staff_id INTEGER NOT NULL REFERENCES staff(id),
        phase_id INTEGER REFERENCES project_phases(id),
        hours REAL NOT NULL DEFAULT 0,
        period TEXT NOT NULL,
        source TEXT DEFAULT 'manual',
        imported_at TEXT DEFAULT (datetime('now')),
        applied_cost_rate REAL, applied_billing_rate REAL,
        applied_cost_amount REAL, applied_billing_amount REAL,
        rate_snapshot_at TEXT, rate_snapshot_source TEXT,
        UNIQUE(project_id, staff_id, period)
    )''',
    # `kind` and `asset_class` answer two different questions and must not be
    # merged: kind is WHO BEARS THE COST (personal -> one employee's rate,
    # general -> spread across production staff), asset_class is WHAT THE THING
    # IS (which useful life it gets and which pair of ledger accounts it lands
    # on). A desk and a laptop can both be 'personal'; they are never the same
    # asset class.
    '''CREATE TABLE IF NOT EXISTS equipment (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('personal','general')),
        asset_class TEXT NOT NULL DEFAULT 'computer',
        staff_id INTEGER REFERENCES staff(id),
        quantity INTEGER NOT NULL DEFAULT 1,
        price REAL NOT NULL DEFAULT 0,
        lifespan_months INTEGER NOT NULL DEFAULT 36,
        purchase_date TEXT,
        purchase_doc_id INTEGER REFERENCES documents(id),
        is_active INTEGER DEFAULT 1, updated_at TEXT
    )''',
    # Asset classes carry a DEFAULT lifespan, not the authoritative one: the
    # per-row equipment.lifespan_months always wins, so editing a class default
    # can never silently re-depreciate assets already on the books. The default
    # pre-fills new rows, and depreciation_schedule() reports rows that differ
    # from it so the drift is visible rather than lost.
    '''CREATE TABLE IF NOT EXISTS asset_classes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL,
        label_uz TEXT NOT NULL, label_en TEXT, label_ru TEXT,
        default_lifespan_months INTEGER NOT NULL DEFAULT 36,
        asset_account TEXT, accum_account TEXT,
        is_active INTEGER DEFAULT 1, sort_order INTEGER DEFAULT 100
    )''',
    '''CREATE TABLE IF NOT EXISTS licenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        staff_id INTEGER REFERENCES staff(id),
        annual_cost REAL NOT NULL DEFAULT 0,
        license_type TEXT DEFAULT 'named',
        is_active INTEGER DEFAULT 1, updated_at TEXT
    )''',
    '''CREATE TABLE IF NOT EXISTS overhead_budget (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        monthly_amount REAL NOT NULL DEFAULT 0,
        is_active INTEGER DEFAULT 1, updated_at TEXT
    )''',
    '''CREATE TABLE IF NOT EXISTS period_allocations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        period TEXT NOT NULL,
        staff_id INTEGER NOT NULL REFERENCES staff(id),
        billable_hours REAL DEFAULT 0,
        hours_share REAL DEFAULT 0,
        labor_cost_share REAL DEFAULT 0,
        admin_share_amount REAL DEFAULT 0,
        overhead_share_amount REAL DEFAULT 0,
        general_equipment_share_amount REAL DEFAULT 0,
        personal_equipment_amount REAL DEFAULT 0,
        personal_licenses_amount REAL DEFAULT 0,
        gross_salary REAL DEFAULT 0, tax_amount REAL DEFAULT 0,
        social_amount REAL DEFAULT 0, total_monthly_cost REAL DEFAULT 0,
        cost_rate REAL DEFAULT 0, billing_rate REAL DEFAULT 0,
        available_hours REAL DEFAULT 0,
        overhead_rate_pct REAL DEFAULT 0, utilization_pct REAL DEFAULT 0,
        net_multiplier REAL DEFAULT 0,
        allocation_base TEXT, overhead_source TEXT,
        snapshotted_at TEXT NOT NULL,
        UNIQUE(period, staff_id)
    )''',

    # ── Loans ───────────────────────────────────────────────────────────────
    '''CREATE TABLE IF NOT EXISTS loans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        loan_type TEXT NOT NULL CHECK(loan_type IN ('olgan','bergan')),
        counterparty_id INTEGER REFERENCES counterparties(id),
        description TEXT,
        total_amount REAL NOT NULL DEFAULT 0,
        currency TEXT DEFAULT 'UZS',
        interest_rate REAL DEFAULT 0,
        term TEXT DEFAULT 'short' CHECK(term IN ('short','long')),
        issue_date TEXT NOT NULL, due_date TEXT,
        status TEXT DEFAULT 'ochiq' CHECK(status IN ('ochiq','yopilgan')),
        notes TEXT,
        created_at TEXT DEFAULT (datetime('now')), updated_at TEXT
    )''',

    # ── Lookups ─────────────────────────────────────────────────────────────
    '''CREATE TABLE IF NOT EXISTS payment_types (
        id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL,
        label_uz TEXT NOT NULL, label_en TEXT, label_ru TEXT,
        is_active INTEGER DEFAULT 1, sort_order INTEGER DEFAULT 100
    )''',
    '''CREATE TABLE IF NOT EXISTS work_types (
        id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL,
        label_uz TEXT NOT NULL, label_en TEXT, label_ru TEXT,
        is_active INTEGER DEFAULT 1, sort_order INTEGER DEFAULT 100
    )''',
    '''CREATE TABLE IF NOT EXISTS departments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL,
        label_uz TEXT NOT NULL, label_en TEXT, label_ru TEXT,
        is_active INTEGER DEFAULT 1, sort_order INTEGER DEFAULT 100
    )''',
    '''CREATE TABLE IF NOT EXISTS staff_roles (
        id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL,
        label_uz TEXT NOT NULL, label_en TEXT, label_ru TEXT,
        is_active INTEGER DEFAULT 1, sort_order INTEGER DEFAULT 100
    )''',

    # ── Import bookkeeping ──────────────────────────────────────────────────
    '''CREATE TABLE IF NOT EXISTS unresolved_imports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_type TEXT NOT NULL, raw_name TEXT NOT NULL,
        document_id INTEGER REFERENCES documents(id),
        field TEXT, resolved INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    )''',
    '''CREATE TABLE IF NOT EXISTS import_skip_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_name TEXT, sheet_name TEXT, row_num INTEGER,
        reason TEXT NOT NULL, raw TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )''',
]

INDICES = [
    "CREATE INDEX IF NOT EXISTS idx_jl_entry ON journal_lines(entry_id)",
    "CREATE INDEX IF NOT EXISTS idx_jl_account ON journal_lines(account_id)",
    "CREATE INDEX IF NOT EXISTS idx_jl_project ON journal_lines(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_jl_counterparty ON journal_lines(counterparty_id)",
    "CREATE INDEX IF NOT EXISTS idx_jl_staff ON journal_lines(staff_id)",
    "CREATE INDEX IF NOT EXISTS idx_jl_bank_account ON journal_lines(bank_account_id)",
    "CREATE INDEX IF NOT EXISTS idx_je_date ON journal_entries(date)",
    "CREATE INDEX IF NOT EXISTS idx_je_period ON journal_entries(period)",
    "CREATE INDEX IF NOT EXISTS idx_je_document ON journal_entries(document_id)",
    "CREATE INDEX IF NOT EXISTS idx_doc_type_date ON documents(doc_type, date)",
    "CREATE INDEX IF NOT EXISTS idx_doc_counterparty ON documents(counterparty_id)",
    "CREATE INDEX IF NOT EXISTS idx_doc_project ON documents(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_doc_status ON documents(status)",
    "CREATE INDEX IF NOT EXISTS idx_dl_document ON document_lines(document_id)",
    "CREATE INDEX IF NOT EXISTS idx_pa_invoice ON payment_allocations(invoice_doc_id)",
    "CREATE INDEX IF NOT EXISTS idx_pa_payment ON payment_allocations(payment_doc_id)",
    "CREATE INDEX IF NOT EXISTS idx_ph_project_period ON project_hours(project_id, period)",
    "CREATE INDEX IF NOT EXISTS idx_ph_staff_period ON project_hours(staff_id, period)",
    "CREATE INDEX IF NOT EXISTS idx_sh_staff ON salary_history(staff_id, start_date)",
    "CREATE INDEX IF NOT EXISTS idx_phases_project ON project_phases(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id)",
]


# ── Chart of accounts seed ───────────────────────────────────────────────────
# Codes, Russian names and kinds are taken verbatim from CHART_OF_ACCOUNTS.md
# (НСБУ №21). cost_pool classifies an account for the man-hour rate engine:
#   'direct_labor' — production labor accumulating on projects
#   'indirect'     — the overhead pool spread across production staff
#   'excluded'     — real costs modeled elsewhere in the formula (salary, tax)
# (code, name_ru, name_uz, kind, subconto, cost_pool, val_flag, sort)
ACCOUNT_SEED = [
    ('0000', 'Вспомогательный счет', 'Yordamchi hisob', 'T', None, None, 0, 1),
    # Fixed assets, paired with their accumulated-depreciation contra account.
    # One pair per asset class (see ASSET_CLASS_SEED). Depreciation credits the
    # pair member, never the 0200 parent, so the balance sheet can show what is
    # worn out by class instead of one undifferentiated lump.
    ('0120.1', 'Здания', 'Binolar', 'A', None, None, 0, 6),
    ('0130', 'Машины и оборудование', 'Mashina va uskunalar', 'A', None, None, 0, 7),
    ('0140', 'Мебель и офисное оборудование', 'Mebel va ofis jihozlari',
     'A', None, None, 0, 8),
    ('0150', 'Компьютерное оборудование и вычислительная техника',
     'Kompyuter jihozlari', 'A', None, None, 0, 10),
    ('0160', 'Транспортные средства', 'Transport vositalari', 'A', None, None, 0, 11),
    ('0190', 'Прочие основные средства в организации', 'Boshqa asosiy vositalar',
     'A', None, None, 0, 12),
    # 0200 stays as the fallback for a class with no accum_account configured.
    ('0200', 'Амортизация основных средств', 'Asosiy vositalar amortizatsiyasi',
     'KA', None, None, 0, 20),
    ('0220.1', 'Амортизация зданий', 'Binolar amortizatsiyasi', 'KA', None, None, 0, 20.5),
    ('0230', 'Амортизация машин и оборудования',
     'Mashina va uskunalar amortizatsiyasi', 'KA', None, None, 0, 21),
    ('0240', 'Амортизация мебели и офисного оборудования',
     'Mebel va ofis jihozlari amortizatsiyasi', 'KA', None, None, 0, 22),
    ('0250', 'Амортизация компьютерного оборудования и вычислительной техники',
     'Kompyuter jihozlari amortizatsiyasi', 'KA', None, None, 0, 23),
    ('0260', 'Амортизация транспортных средств',
     'Transport vositalari amortizatsiyasi', 'KA', None, None, 0, 24),
    ('0290', 'Амортизация прочих ОС', 'Boshqa asosiy vositalar amortizatsiyasi',
     'KA', None, None, 0, 25),
    ('0410', 'Патенты, лицензии, ноу-хау', 'Patentlar, litsenziyalar',
     'A', None, None, 0, 30),
    ('2010', 'Основное производство', 'Asosiy ishlab chiqarish',
     'A', 'project', 'direct_labor', 0, 40),
    ('4010', 'Расчеты с покупателями и заказчиками', 'Xaridorlar bilan hisob-kitob',
     'A', 'counterparty', None, 0, 50),
    ('4310', 'Расчеты по авансам выданным', 'Berilgan avanslar',
     'A', 'counterparty', None, 0, 55),
    ('4410', 'НДС по приобретенным ценностям', 'Olingan QQS',
     'A', 'counterparty', None, 0, 60),
    ('5010', 'Основная касса организации', 'Asosiy kassa', 'A', None, None, 0, 70),
    ('5110', 'Расчетные счета', "Hisob-kitob raqami", 'A', 'bank_account', None, 0, 71),
    ('5210', 'Валютные счета внутри Республики', 'Valyuta hisobvarag\'i',
     'A', 'bank_account', None, 1, 72),
    ('5820', 'Краткосрочные займы, выданные', 'Berilgan qisqa muddatli qarzlar',
     'A', 'counterparty', None, 0, 80),
    ('6010', 'Расчеты с поставщиками и подрядчиками', "Ta'minotchilar bilan hisob-kitob",
     'P', 'counterparty', None, 0, 90),
    ('6310', 'Расчеты по авансам полученным', 'Olingan avanslar',
     'P', 'counterparty', None, 0, 95),
    ('6410.1', 'Налог на добавленную стоимость начисленный при реализации',
     'Hisoblangan QQS', 'P', None, None, 0, 100),
    ('6420.1', "НДФЛ (оплате труда)", 'JSHDS (ish haqidan)', 'P', None, None, 0, 101),
    ('6430', 'Налог на прибыль (ЕНП при УСН)', 'Foyda solig\'i', 'P', None, None, 0, 102),
    ('6520', 'Расчеты с государственными целевыми фондами',
     'Davlat maqsadli jamg\'armalari', 'P', None, None, 0, 103),
    ('6610', 'Расчеты по выплате доходов (дивидендов)', 'Dividendlar bo\'yicha hisob',
     'P', 'counterparty', None, 0, 110),
    ('6710', 'Расчеты с персоналом по оплате труда', 'Xodimlar bilan ish haqi hisobi',
     'P', 'staff', None, 0, 120),
    ('6820', 'Краткосрочные займы', 'Qisqa muddatli qarzlar',
     'P', 'counterparty', None, 0, 130),
    ('7820', 'Долгосрочные займы', 'Uzoq muddatli qarzlar',
     'P', 'counterparty', None, 0, 131),
    ('8710', 'Нераспределенная прибыль (непокрытый убыток) отчетного периода',
     'Taqsimlanmagan foyda', 'P', None, None, 0, 140),
    ('9030', 'Доходы от выполнения работ, оказания услуг',
     'Ish va xizmatlardan daromad', 'T', 'project', None, 0, 150),
    # Where an unattributed cash receipt lands. Deliberately outside the pool:
    # the pool nets debits against credits, so income booked to an 'indirect'
    # account would subtract itself from overhead and lower every rate.
    ('9390', 'Прочие операционные доходы', 'Boshqa operatsion daromadlar',
     'T', None, None, 0, 155),
    ('9130', 'Себестоимость выполненных работ, оказанных услуг',
     'Bajarilgan ishlar tannarxi', 'T', 'project', 'excluded', 0, 160),
    ('9410', 'Расходы на продажу', 'Sotish xarajatlari', 'T', None, 'indirect', 0, 170),
    ('9420', 'Административные расходы', 'Ma\'muriy xarajatlar',
     'T', None, 'indirect', 0, 171),
    ('9430', 'Прочие операционные расходы', 'Boshqa operatsion xarajatlar',
     'T', None, 'indirect', 0, 172),
    # ── The children of 9420 that must never join the overhead pool ──────
    # THE RULE: an account may not carry cost_pool='indirect' if the same cost
    # is already modeled by a register that calculate_hourly_rate() reads.
    # Each of these three is a real administrative expense in the P&L, but the
    # rate engine already charges it from its own register — the equipment
    # register, the staff/salary register, the license register. Leaving them
    # on 9420 would let the ledger charge them a second time and inflate every
    # rate. posting.assert_not_pooled() enforces this at post time.
    ('9420.1', 'Амортизация основных средств', 'Asosiy vositalar amortizatsiyasi',
     'T', None, 'excluded', 0, 1715),
    ('9420.2', 'Административный персонал', 'Ma\'muriy xodimlar ish haqi',
     'T', None, 'excluded', 0, 1716),
    ('9420.3', 'Лицензии и программное обеспечение',
     'Litsenziyalar va dasturiy ta\'minot', 'T', None, 'excluded', 0, 1717),
    ('9530', 'Доходы в виде процентов', 'Foiz daromadlari', 'T', None, None, 0, 180),
    ('9540', 'Доходы в виде курсовых разниц', 'Kurs farqi daromadi',
     'T', None, 'excluded', 0, 181),
    ('9610', 'Расходы в виде процентов', 'Foiz xarajatlari', 'T', None, 'excluded', 0, 190),
    ('9620', 'Убытки от курсовых разниц', 'Kurs farqi zarari',
     'T', None, 'excluded', 0, 191),
    ('9910', 'Финансовый результат по деятельности с основной системой налогообложения',
     'Moliyaviy natija', 'T', None, None, 0, 200),
]

# Accounts whose cost the man-hour rate engine already charges from a register
# of its own, and which therefore must stay out of the overhead pool. Kept as
# one list so the seed correction, the posting guard and the tests all agree on
# what "register-modeled" means.
REGISTER_MODELED_CODES = ('9420.1', '9420.2', '9420.3')

# purpose → account code. Posting rules never name a code directly; they ask
# for a purpose, so a firm on a different chart only edits this mapping.
ACCOUNT_MAP_SEED = {
    'cash_bank': '5110',
    'cash_till': '5010',
    'cash_fx': '5210',
    'ar': '4010',
    'ap': '6010',
    'advances_received': '6310',
    'advances_issued': '4310',
    'revenue': '9030',
    'vat_output': '6410.1',
    'vat_input': '4410',
    'payroll_payable': '6710',
    'pit_payable': '6420.1',
    'social_payable': '6520',
    'profit_tax_payable': '6430',
    'production_cost': '2010',
    'cogs': '9130',
    'selling_expense': '9410',
    'admin_expense': '9420',
    'admin_salary_expense': '9420.2',
    'license_expense': '9420.3',
    'other_opex': '9430',
    'other_income': '9390',
    'interest_income': '9530',
    'interest_expense': '9610',
    'fx_gain': '9540',
    'fx_loss': '9620',
    'dividends_payable': '6610',
    'retained_earnings': '8710',
    'financial_result': '9910',
    'loan_short_in': '6820',
    'loan_long_in': '7820',
    'loan_issued': '5820',
    'equipment_asset': '0150',
    'equipment_depreciation': '0200',
    'depreciation_expense': '9420.1',
    'opening_offset': '0000',
}

DEFAULT_SETTINGS = [
    ('usd_rate', 12850, 'Dollar kursi (1 USD)', 'UZS'),
    ('tax_rate', 0.12, 'Daromad solig\'i (JSHDS) stavkasi', '%'),
    ('social_rate', 0.12, 'Ijtimoiy soliq stavkasi', '%'),
    ('vat_rate', 12, "QQS stavkasi", '%'),
    ('billing_multiplier', 2.0, 'Markup koeffitsienti', 'x'),
    ('target_margin', 0.50, 'Target foyda marjasi', '%'),
    ('holidays_per_year', 14, 'Davlat bayramlari (yiliga)', 'kun'),
    ('avg_leave_days', 20, "O'rtacha ta'til + kasallik (yiliga)", 'kun'),
    ('utilization_rate', 0.75, 'Maqsadli utilization rate', '%'),
    ('kpi_months', 43, 'KPI hisoblash davri (oylar)', 'oy'),
    # 1 = overhead pool from posted ledger costs; 0 = static overhead_budget table
    ('overhead_from_ledger', 1, 'Overhead manbai: 1=buxgalteriya, 0=jadval', '0/1'),
    ('overhead_window_months', 12, 'Overhead oynasi (oylar)', 'oy'),
    # 'labor_cost' (industry standard) | 'hours' (v4-compatible)
    ('allocation_base_labor_cost', 1, 'Taqsimlash bazasi: 1=ish haqi, 0=soat', '0/1'),
    ('default_vat_on_sales', 1, 'Sotuvda QQS sukut bo\'yicha', '0/1'),
]

DEFAULT_RATES = [
    ('2024-01-01', 12200), ('2024-07-01', 12550), ('2025-01-01', 12700),
    ('2025-04-01', 12750), ('2025-07-01', 12800), ('2025-10-01', 12850),
    ('2026-01-01', 12850),
]

PAYMENT_TYPE_SEED = [
    ('bank', "Bank o'tkazmasi", 'Bank Transfer', 'Банковский перевод', 10),
    ('naqd', 'Naqd pul', 'Cash', 'Наличные', 20),
    ('karta', 'Karta orqali', 'Card Payment', 'Картой', 30),
    ('online', "Onlayn to'lov", 'Online Payment', 'Онлайн оплата', 40),
]

WORK_TYPE_SEED = [
    ('agr', 'AGR / Topshiriq', 'Assignment / Permits', 'АГР / Задание', 10),
    ('eskiz', 'Eskiz loyiha', 'Concept Design', 'Эскизный проект', 20),
    ('ar', 'Arxitektura yechimlari', 'Architectural Design', 'Архитектурные решения', 30),
    ('kj', 'Konstruktiv yechimlar', 'Structural Design', 'Конструктивные решения', 40),
    ('im', 'Muhandislik tarmoqlari', 'Engineering Systems', 'Инженерные сети', 50),
    ('smeta', 'Smeta hujjatlari', 'Cost Estimation', 'Сметная документация', 60),
    ('nazorat', 'Avtorlik nazorati', 'Site Supervision', 'Авторский надзор', 70),
    ('boshqa', 'Boshqa ishlar', 'Other Work', 'Прочие работы', 80),
]

DEPARTMENT_SEED = [
    ('arxitektura', 'Arxitektura', 'Architecture', 'Архитектура', 10),
    ('konstruktiv', 'Konstruktiv', 'Structural', 'Конструктив', 20),
    ('muhandislik', 'Muhandislik', 'Engineering', 'Инженерия', 30),
    ('vizualizatsiya', 'Vizualizatsiya', 'Visualization', 'Визуализация', 40),
    ('boshqaruv', 'Boshqaruv', 'Management', 'Управление', 50),
    ('moliya', 'Moliya', 'Finance', 'Финансы', 60),
]

STAFF_ROLE_SEED = [
    ('arxitektor', 'Arxitektor', 'Architect', 'Архитектор', 10),
    ('bim', 'BIM muhandis', 'BIM Engineer', 'BIM-инженер', 20),
    ('konstruktor', 'Konstruktor', 'Structural Engineer', 'Конструктор', 30),
    ('vizualizator', 'Vizualizator', 'Visualizer', 'Визуализатор', 40),
    ('loyiha_rahbari', 'Loyiha rahbari', 'Project Manager', 'Руководитель проекта', 50),
    ('direktor', 'Direktor', 'Director', 'Директор', 60),
    ('buxgalter', 'Buxgalter', 'Accountant', 'Бухгалтер', 70),
]

# Asset classes: what a thing IS, which decides its useful life and which pair
# of ledger accounts it lands on. (code, uz, en, ru, months, asset, accum, sort)
#
# Every class ships at 36 months — the value the whole register was imported
# with, from a source spreadsheet that had a single depreciation column. That
# is deliberately NOT changed here: re-lifing an asset moves every man-hour
# rate and every price quoted from one, so it is the firm's decision, made on
# /staff/equipment, not a silent side effect of adding classes.
#
# For reference when setting them, the Tax Code (Art. 306 §30) ceilings are
# 20%/yr for computers and peripherals (60 months) and 15%/yr for furniture and
# office equipment (80 months); 36 months is 33.3%/yr, above both. Those are
# tax ceilings — under NSBU No.5 the book life is the firm's own estimate of
# useful life, which is why this is a setting and not a constant.
ASSET_CLASS_SEED = [
    ('computer', 'Kompyuter texnikasi', 'Computer equipment',
     'Компьютерное оборудование', 36, '0150', '0250', 10),
    ('furniture', 'Mebel va ofis jihozlari', 'Furniture & office equipment',
     'Мебель и офисное оборудование', 36, '0140', '0240', 20),
    ('machinery', 'Mashina va uskunalar', 'Machinery & equipment',
     'Машины и оборудование', 36, '0130', '0230', 30),
    ('vehicle', 'Transport vositalari', 'Vehicles',
     'Транспортные средства', 36, '0160', '0260', 40),
    ('building', 'Bino va inshootlar', 'Buildings & structures',
     'Здания и сооружения', 36, '0120.1', '0220.1', 50),
    ('other', 'Boshqa asosiy vositalar', 'Other fixed assets',
     'Прочие основные средства', 36, '0190', '0290', 60),
]

OVERHEAD_SEED = [
    ('Ofis ijarasi', 25000000),
    ("Kommunal to'lovlar", 5000000),
    ('Internet', 3000000),
    ('Ovqat xarajatlari', 8000000),
    ('Transport', 3000000),
    ('Boshqa xarajatlar', 4000000),
]


# Columns added after a table first shipped. CREATE TABLE IF NOT EXISTS does
# nothing for an existing table, so each is also applied here as an
# ALTER TABLE on databases created before it. Additive only — SQLite cannot
# add a CHECK or change a constraint this way.
# (table, column, column DDL)
MIGRATED_COLUMNS = [
    ('journal_lines', 'bank_account_id', 'INTEGER REFERENCES bank_accounts(id)'),
    ('documents', 'bank_account_id', 'INTEGER REFERENCES bank_accounts(id)'),
    ('documents', 'responsible_id', 'INTEGER REFERENCES staff(id)'),
]


def _migrate_columns(c):
    for table, column, ddl in MIGRATED_COLUMNS:
        cols = [d[1] for d in c.execute(f"PRAGMA table_info({table})").fetchall()]
        if column not in cols:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db():
    """Create every table, index and seed row. Safe to run on every startup."""
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = get_db()
    c = conn.cursor()
    # Tables reference each other in both directions (documents ↔ journal_entries),
    # so creation order cannot satisfy every FK — SQLite only resolves them at
    # write time, which is why this block runs with enforcement off.
    conn.execute("PRAGMA foreign_keys = OFF")
    for stmt in SCHEMA:
        c.execute(stmt)
    _migrate_columns(c)
    for stmt in INDICES:
        c.execute(stmt)
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")

    _seed_admin_user(c)
    _seed_settings(c)
    _seed_accounts(c)
    _seed_lookups(c)
    conn.commit()
    conn.close()


def _seed_admin_user(c):
    if c.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
        return
    import secrets
    from werkzeug.security import generate_password_hash
    pw = os.environ.get('MIZAN_ADMIN_PASSWORD') or secrets.token_urlsafe(12)
    c.execute("INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
              ('admin', generate_password_hash(pw), 'admin'))
    if not os.environ.get('MIZAN_ADMIN_PASSWORD'):
        print('\n' + '!' * 60)
        print('  Admin foydalanuvchisi yaratildi / Admin user created')
        print('    login:  admin')
        print(f'    parol / password:  {pw}')
        print('  Bu parol faqat bir marta ko\'rsatiladi.')
        print('!' * 60 + '\n')


def _seed_settings(c):
    for key, val, label, unit in DEFAULT_SETTINGS:
        c.execute("INSERT OR IGNORE INTO settings (key, value, label, unit) VALUES (?,?,?,?)",
                  (key, val, label, unit))
    if not c.execute("SELECT COUNT(*) FROM exchange_rates").fetchone()[0]:
        c.executemany("INSERT INTO exchange_rates (date, rate) VALUES (?,?)", DEFAULT_RATES)
    if not c.execute("SELECT COUNT(*) FROM overhead_budget").fetchone()[0]:
        c.executemany("INSERT INTO overhead_budget (name, monthly_amount) VALUES (?,?)",
                      OVERHEAD_SEED)


def _seed_accounts(c):
    for code, ru, uz, kind, subconto, pool, val, sort in ACCOUNT_SEED:
        c.execute(
            "INSERT OR IGNORE INTO accounts"
            " (code, name_ru, name_uz, kind, subconto, cost_pool, val_flag, sort_order)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (code, ru, uz, kind, subconto, pool, val, sort))
    for purpose, code in ACCOUNT_MAP_SEED.items():
        row = c.execute("SELECT id FROM accounts WHERE code=?", (code,)).fetchone()
        if row:
            c.execute("INSERT OR IGNORE INTO account_map (purpose, account_id) VALUES (?,?)",
                      (purpose, row[0]))
    # The seed above is INSERT OR IGNORE, so an install where someone created
    # these by hand could have them sitting in the overhead pool. That would
    # make the cost count twice — once from its register and once from the
    # pool. Correct them, but only from 'indirect': a deliberate NULL is left
    # alone. See the comment on REGISTER_MODELED_CODES for the rule.
    for code in REGISTER_MODELED_CODES:
        c.execute("UPDATE accounts SET cost_pool='excluded'"
                  " WHERE code=? AND cost_pool='indirect'", (code,))


def _seed_lookups(c):
    seeds = [
        ('payment_types', PAYMENT_TYPE_SEED),
        ('work_types', WORK_TYPE_SEED),
        ('departments', DEPARTMENT_SEED),
        ('staff_roles', STAFF_ROLE_SEED),
    ]
    for table, rows in seeds:
        if c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]:
            continue
        c.executemany(
            f"INSERT OR IGNORE INTO {table} (code, label_uz, label_en, label_ru, sort_order)"
            f" VALUES (?,?,?,?,?)", rows)
    # INSERT OR IGNORE per row rather than skip-if-any: a class added to the
    # seed later must still appear on an existing install, and an edited
    # default_lifespan_months must survive — that value is the firm's setting.
    c.executemany(
        "INSERT OR IGNORE INTO asset_classes (code, label_uz, label_en, label_ru,"
        " default_lifespan_months, asset_account, accum_account, sort_order)"
        " VALUES (?,?,?,?,?,?,?,?)", ASSET_CLASS_SEED)


def asset_class_map(conn=None, active_only=False):
    """{code: asset class row} — the useful life and account pair per class.

    active_only defaults to False here, unlike get_lookup(): a deactivated
    class must still resolve for assets already recorded against it, or their
    depreciation would silently lose its accounts.
    """
    own = conn is None
    conn = conn or get_db()
    try:
        where = " WHERE is_active=1" if active_only else ""
        rows = conn.execute(
            f"SELECT * FROM asset_classes{where} ORDER BY sort_order, code").fetchall()
        return {r['code']: dict(r) for r in rows}
    finally:
        if own:
            conn.close()


def default_lifespan_for(asset_class, conn=None):
    """The class default, or 36 if the class is unknown — never 0.

    A zero would make the monthly charge a division by zero in the rate engine
    and NULLIF() it away in the SQL, silently dropping the asset from costs.
    """
    row = asset_class_map(conn).get(asset_class)
    months = int((row or {}).get('default_lifespan_months') or 0)
    return months if months > 0 else 36


def get_lookup(table, active_only=True):
    """Rows of a lookup table, ordered for a <select>."""
    if table not in ('payment_types', 'work_types', 'departments', 'staff_roles',
                     'asset_classes'):
        return []
    conn = get_db()
    where = " WHERE is_active=1" if active_only else ""
    rows = conn.execute(f"SELECT * FROM {table}{where} ORDER BY sort_order, code").fetchall()
    conn.close()
    return [dict(r) for r in rows]
