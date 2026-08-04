"""Database connection, schema init, settings, and CRUD helpers."""
import re
import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'mizan_finance.db')

# External tx_types that represent client revenue. Every aggregation (cash flow,
# payment summary, project income, AR aging, page tiles) must classify income
# with this same set — hardcoding 'tushum' alone makes mizan_monthly /
# yakuniy_hisob receipts vanish or count as expense.
INCOME_TX_TYPES = ('tushum', 'mizan_monthly', 'yakuniy_hisob')
# Pre-rendered SQL literal for use inside CASE/WHERE expressions.
INCOME_TX_SQL = "(" + ",".join(f"'{t}'" for t in INCOME_TX_TYPES) + ")"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Unicode-aware lowercase; SQLite's built-in LOWER()/LIKE only fold ASCII,
    # so Cyrillic/Uzbek text would otherwise compare case-sensitively.
    conn.create_function("ulower", 1, lambda s: s.lower() if s is not None else None)
    return conn


def get_setting(key):
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row['value'] if row else None


def get_rate_for_date(dt_str):
    conn = get_db()
    row = conn.execute(
        "SELECT rate FROM exchange_rates WHERE date <= ? ORDER BY date DESC LIMIT 1",
        (dt_str,)
    ).fetchone()
    conn.close()
    return row['rate'] if row else get_setting('usd_rate')


def get_current_usd_rate():
    """Today's UZS/USD rate — the latest row of the exchange_rates table.

    The manual `usd_rate` setting is only a fallback for empty installs;
    using it as "current" made every FX figure depend on the admin
    remembering to update two places (see USER_STORY_AND_UX_AUDIT.md A7).
    """
    conn = get_db()
    row = conn.execute(
        "SELECT rate FROM exchange_rates WHERE date <= date('now') ORDER BY date DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return row['rate'] if row else (get_setting('usd_rate') or 12850)


# ========== Entity name resolution (projects / counterparties from free text) ==========

_ENTITY_TABLES = {
    'project': ('projects', 'project_aliases', 'project_id'),
    'counterparty': ('counterparties', 'counterparty_aliases', 'counterparty_id'),
}


def _normalize_name(name):
    """Lowercase, strip punctuation/extra whitespace so 'Loyiha-A' == 'loyiha a'."""
    if not name:
        return ''
    n = re.sub(r'[^\w]+', ' ', str(name).strip().lower(), flags=re.UNICODE)
    return re.sub(r'\s+', ' ', n).strip()


def resolve_entity_id(conn, entity_type, raw_name):
    """Look up raw_name against an entity's alias table, then a normalized exact
    match on the canonical table. Returns an id or None — never creates rows."""
    table, alias_table, fk_col = _ENTITY_TABLES[entity_type]
    norm = _normalize_name(raw_name)
    if not norm:
        return None
    for row in conn.execute(f"SELECT alias, {fk_col} FROM {alias_table}"):
        if _normalize_name(row['alias']) == norm:
            return row[fk_col]
    for row in conn.execute(f"SELECT id, name FROM {table}"):
        if _normalize_name(row['name']) == norm:
            return row['id']
    return None


def apply_entity_resolution(conn, entity_type, raw_name, target_id):
    """Backfill every transaction carrying raw_name onto target_id, remember the
    alias for future imports, and clear the unresolved flag for this raw_name."""
    table, alias_table, fk_col = _ENTITY_TABLES[entity_type]
    tx_ids = [r['transaction_id'] for r in conn.execute(
        "SELECT transaction_id FROM unresolved_imports WHERE entity_type=? AND raw_name=? AND resolved=0",
        (entity_type, raw_name)
    )]
    for tx_id in tx_ids:
        conn.execute(f"UPDATE transactions SET {fk_col} = ? WHERE id = ?", (target_id, tx_id))
    conn.execute(
        f"INSERT OR IGNORE INTO {alias_table} (alias, {fk_col}, source) VALUES (?,?,?)",
        (raw_name, target_id, 'reconcile')
    )
    conn.execute(
        "UPDATE unresolved_imports SET resolved=1 WHERE entity_type=? AND raw_name=?",
        (entity_type, raw_name)
    )
    conn.commit()


def record_unresolved_import(conn, entity_type, raw_name, transaction_id, field=None):
    """Track a transaction whose project/counterparty name didn't resolve, so it
    can be fixed later from the reconciliation screen instead of silently
    creating a duplicate project or leaving the link blank forever."""
    raw_name = (raw_name or '').strip()
    if not raw_name:
        return
    conn.execute(
        "INSERT INTO unresolved_imports (entity_type, raw_name, transaction_id, field) VALUES (?,?,?,?)",
        (entity_type, raw_name, transaction_id, field)
    )


# ========== CRUD API ==========

_ALLOWED_TABLES = [
    'transactions', 'staff', 'salary_history',
    'personal_equipment', 'general_equipment', 'personal_licenses', 'overhead',
    'exchange_rates', 'loans', 'loan_payments',
    'counterparties', 'tags', 'dividends', 'project_phases',
]

_DELETE_ALLOWED = [
    'transactions', 'personal_equipment',
    'general_equipment', 'personal_licenses', 'overhead', 'exchange_rates',
    'dividends', 'staff',
]


def _write_audit(conn, action, entity_type, entity_id=None, field=None,
                 old_value=None, new_value=None, context=None):
    try:
        conn.execute(
            "INSERT INTO audit_log (action, entity_type, entity_id, field, old_value, new_value, context)"
            " VALUES (?,?,?,?,?,?,?)",
            (action, entity_type, entity_id, field,
             str(old_value) if old_value is not None else None,
             str(new_value) if new_value is not None else None, context)
        )
    except Exception:
        pass


def write_audit_log(action, entity_type, entity_id=None, context=None):
    """Log a single audit event; opens its own connection."""
    conn = get_db()
    _write_audit(conn, action, entity_type, entity_id, context=context)
    conn.commit()
    conn.close()


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
    cols = [desc[1] for desc in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    old_row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone()
    old_vals = dict(old_row) if old_row else {}
    updates = {k: v for k, v in data.items() if k in cols and k != 'id'}
    if not updates:
        conn.close()
        return False
    if 'updated_at' in cols:
        updates['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    set_clause = ', '.join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [record_id]
    conn.execute(f"UPDATE {table} SET {set_clause} WHERE id=?", values)
    for field, new_val in updates.items():
        if field == 'updated_at':
            continue
        old_val = old_vals.get(field)
        if str(old_val) != str(new_val):
            _write_audit(conn, 'update', table, record_id, field, old_val, new_val)
    conn.commit()
    conn.close()
    return True


def delete_record(table, record_id):
    if table not in _DELETE_ALLOWED:
        return False
    conn = get_db()
    cols = [desc[1] for desc in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if 'is_active' in cols:
        if 'updated_at' in cols:
            conn.execute(
                f"UPDATE {table} SET is_active=0, updated_at=? WHERE id=?",
                (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), record_id)
            )
        else:
            conn.execute(f"UPDATE {table} SET is_active=0 WHERE id=?", (record_id,))
        _write_audit(conn, 'delete', table, record_id, context='soft delete')
    else:
        conn.execute(f"DELETE FROM {table} WHERE id=?", (record_id,))
        _write_audit(conn, 'delete', table, record_id, context='hard delete')
    conn.commit()
    conn.close()
    return True


def get_all_records(table, limit=100, active_only=True):
    if table not in _ALLOWED_TABLES:
        return []
    conn = get_db()
    cols = [desc[1] for desc in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    where = " WHERE is_active=1" if active_only and 'is_active' in cols else ""
    order = " ORDER BY id DESC" if 'id' in cols else ""
    rows = conn.execute(f"SELECT * FROM {table}{where}{order} LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ========== Fiscal period helpers ==========

def get_open_fiscal_period():
    """Return code of the most recent open fiscal period, or None."""
    conn = get_db()
    row = conn.execute(
        "SELECT code FROM fiscal_periods WHERE status='open' ORDER BY code DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return row['code'] if row else None


def is_period_closed(period_code):
    """True if the given period code is soft_closed or hard_closed."""
    if not period_code:
        return False
    conn = get_db()
    row = conn.execute(
        "SELECT status FROM fiscal_periods WHERE code=?", (period_code,)
    ).fetchone()
    conn.close()
    return bool(row and row['status'] in ('soft_closed', 'hard_closed'))


# ========== Schema init ==========

def init_db():
    conn = get_db()
    c = conn.cursor()

    # ── Auth ─────────────────────────────────────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('admin', 'manager', 'viewer')),
        is_active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # Seed default admin on first run.
    # Password comes from MIZAN_ADMIN_PASSWORD, or is randomly generated and
    # printed once — no credential is hardcoded in source.
    if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        import os
        import secrets
        from werkzeug.security import generate_password_hash
        admin_password = os.environ.get('MIZAN_ADMIN_PASSWORD') or secrets.token_urlsafe(12)
        c.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
            ('admin', generate_password_hash(admin_password), 'admin')
        )
        if not os.environ.get('MIZAN_ADMIN_PASSWORD'):
            print("\n" + "!" * 60)
            print("  Yangi admin foydalanuvchisi yaratildi / Admin user created")
            print("    login:  admin")
            print(f"    parol / password:  {admin_password}")
            print("  Bu parolni saqlang — u faqat bir marta ko'rsatiladi.")
            print("  Save this password — it is shown only once.")
            print("!" * 60 + "\n")

    # ── Core tables ──────────────────────────────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value REAL NOT NULL, label TEXT, unit TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS exchange_rates (
        id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL UNIQUE, rate REAL NOT NULL
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS staff (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, full_name TEXT,
        nizam_name TEXT, role TEXT NOT NULL, department TEXT NOT NULL,
        staff_type TEXT NOT NULL CHECK(staff_type IN ('production', 'admin')),
        is_active INTEGER DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        staff_code TEXT, updated_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS salary_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, staff_id INTEGER NOT NULL REFERENCES staff(id),
        base_salary REAL NOT NULL DEFAULT 0, premium REAL NOT NULL DEFAULT 0,
        start_date TEXT NOT NULL, end_date TEXT, updated_at TEXT,
        UNIQUE(staff_id, start_date)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS personal_equipment (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        staff_id INTEGER REFERENCES staff(id), price REAL NOT NULL,
        lifespan_months INTEGER NOT NULL DEFAULT 36, purchase_date TEXT, is_active INTEGER DEFAULT 1
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS general_equipment (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        quantity INTEGER NOT NULL DEFAULT 1, price REAL NOT NULL,
        lifespan_months INTEGER NOT NULL DEFAULT 36, purchase_date TEXT, is_active INTEGER DEFAULT 1
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS personal_licenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        staff_id INTEGER REFERENCES staff(id),
        annual_cost REAL NOT NULL DEFAULT 0, license_type TEXT DEFAULT 'named', is_active INTEGER DEFAULT 1
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS overhead (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
        monthly_amount REAL NOT NULL DEFAULT 0, is_active INTEGER DEFAULT 1,
        updated_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, client TEXT,
        responsible TEXT, risk_coefficient REAL NOT NULL DEFAULT 1.15, risk_score REAL DEFAULT 0,
        contract_amount REAL DEFAULT 0, currency TEXT DEFAULT 'UZS', start_date TEXT, end_date TEXT,
        status TEXT DEFAULT 'active', is_billable INTEGER DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        estimated_total_hours REAL DEFAULT 0,
        planned_hours REAL DEFAULT 0, planned_cost REAL DEFAULT 0,
        planned_revenue REAL DEFAULT 0, planned_outsourcing REAL DEFAULT 0,
        planned_material REAL DEFAULT 0, plan_frozen_date TEXT,
        updated_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS project_hours (
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL REFERENCES projects(id),
        staff_id INTEGER NOT NULL REFERENCES staff(id), hours REAL NOT NULL DEFAULT 0,
        period TEXT NOT NULL, source TEXT DEFAULT 'nizam',
        imported_at TEXT DEFAULT CURRENT_TIMESTAMP,
        applied_cost_rate REAL, applied_billing_rate REAL,
        applied_cost_amount REAL, applied_billing_amount REAL,
        rate_snapshot_at TEXT, rate_snapshot_source TEXT,
        phase_id INTEGER, supersedes_id INTEGER, superseded_by_id INTEGER,
        UNIQUE(project_id, staff_id, period)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS loans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        loan_type TEXT NOT NULL CHECK(loan_type IN ('olgan','bergan')),
        counterparty TEXT NOT NULL, description TEXT, total_amount REAL NOT NULL DEFAULT 0,
        currency TEXT DEFAULT 'UZS', interest_rate REAL DEFAULT 0, issue_date TEXT NOT NULL,
        due_date TEXT, status TEXT DEFAULT 'ochiq' CHECK(status IN ('ochiq','yopilgan')),
        notes TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS loan_payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, loan_id INTEGER NOT NULL REFERENCES loans(id),
        date TEXT NOT NULL, amount REAL NOT NULL DEFAULT 0,
        payment_type TEXT DEFAULT 'asosiy', notes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # ── v5.1 Dimension tables ─────────────────────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL DEFAULT (datetime('now')),
        user TEXT, action TEXT NOT NULL, entity_type TEXT NOT NULL,
        entity_id INTEGER, field TEXT, old_value TEXT, new_value TEXT, context TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS transaction_categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL,
        name_uz TEXT NOT NULL, name_en TEXT, name_ru TEXT,
        parent_id INTEGER REFERENCES transaction_categories(id),
        direction TEXT NOT NULL, affects_project_cost INTEGER DEFAULT 0,
        affects_cash_flow INTEGER DEFAULT 1, is_active INTEGER DEFAULT 1,
        sort_order INTEGER DEFAULT 100, notes TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS fiscal_periods (
        id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL,
        start_date TEXT NOT NULL, end_date TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        closed_at TEXT, closed_by_user TEXT, notes TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS counterparties (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        legal_name TEXT, tax_id TEXT, counterparty_type TEXT NOT NULL DEFAULT 'other',
        country TEXT, default_currency TEXT DEFAULT 'UZS',
        is_active INTEGER DEFAULT 1, notes TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS counterparty_aliases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        counterparty_id INTEGER NOT NULL REFERENCES counterparties(id),
        alias TEXT NOT NULL, source TEXT, UNIQUE(alias)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS project_aliases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id),
        alias TEXT NOT NULL, source TEXT, UNIQUE(alias)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS unresolved_imports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_type TEXT NOT NULL,
        raw_name TEXT NOT NULL,
        transaction_id INTEGER NOT NULL REFERENCES transactions(id),
        field TEXT,
        resolved INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_unresolved_lookup ON unresolved_imports(entity_type, raw_name, resolved)")
    c.execute('''CREATE TABLE IF NOT EXISTS import_skip_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_name TEXT,
        sheet_name TEXT,
        row_num INTEGER,
        reason TEXT NOT NULL,
        raw TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_skip_log_created ON import_skip_log(created_at)")
    c.execute('''CREATE TABLE IF NOT EXISTS tags (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL,
        color TEXT, category TEXT, is_active INTEGER DEFAULT 1
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS entity_tags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tag_id INTEGER NOT NULL REFERENCES tags(id),
        entity_type TEXT NOT NULL, entity_id INTEGER NOT NULL,
        UNIQUE(tag_id, entity_type, entity_id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS tx_types (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        label_uz TEXT NOT NULL,
        label_en TEXT,
        label_ru TEXT,
        direction TEXT NOT NULL DEFAULT 'both',
        is_active INTEGER DEFAULT 1,
        sort_order INTEGER DEFAULT 100
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS payment_types (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        label_uz TEXT NOT NULL,
        label_en TEXT,
        label_ru TEXT,
        is_active INTEGER DEFAULT 1,
        sort_order INTEGER DEFAULT 100
    )''')

    # ── v5.2 Snapshot + allocation tables ────────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS period_allocations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, period TEXT NOT NULL,
        staff_id INTEGER NOT NULL REFERENCES staff(id),
        billable_hours REAL DEFAULT 0, hours_share REAL DEFAULT 0,
        admin_share_amount REAL DEFAULT 0, overhead_share_amount REAL DEFAULT 0,
        general_equipment_share_amount REAL DEFAULT 0,
        personal_equipment_amount REAL DEFAULT 0, personal_licenses_amount REAL DEFAULT 0,
        gross_salary REAL DEFAULT 0, tax_amount REAL DEFAULT 0, social_amount REAL DEFAULT 0,
        total_monthly_cost REAL DEFAULT 0, cost_rate REAL DEFAULT 0,
        billing_rate REAL DEFAULT 0, available_hours REAL DEFAULT 0,
        snapshotted_at TEXT NOT NULL, UNIQUE(period, staff_id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS project_earned_revenue_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id),
        period TEXT NOT NULL, as_of_date TEXT NOT NULL,
        contract_amount REAL, estimated_total_hours REAL,
        actual_hours_to_date REAL, completion_ratio REAL,
        earned_revenue REAL, cumulative_cost REAL,
        cumulative_invoiced REAL, cumulative_received REAL,
        snapshotted_at TEXT NOT NULL, UNIQUE(project_id, period)
    )''')

    # ── v5.3 Project phases — since v6 these are first-class milestones ───────
    # planned_revenue = expected income, planned_cost = expected labor
    # (risk-EXCLUSIVE, same semantics as projects.planned_cost; the risk
    # uplift lives only in planned_revenue).
    c.execute('''CREATE TABLE IF NOT EXISTS project_phases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        code TEXT, name TEXT NOT NULL, sort_order INTEGER DEFAULT 100,
        planned_hours REAL DEFAULT 0, planned_cost REAL DEFAULT 0,
        planned_revenue REAL DEFAULT 0, start_date TEXT, end_date TEXT,
        completion_percent REAL DEFAULT 0, status TEXT DEFAULT 'planned',
        work_type TEXT, planned_outsourcing REAL DEFAULT 0, planned_material REAL DEFAULT 0,
        completed_date TEXT, notes TEXT, updated_at TEXT,
        UNIQUE(project_id, code)
    )''')
    # ── v7 Milestone staff plan — per-employee hour estimates behind a phase ──
    # Single source of truth for the milestone's labor plan: planned_hours and
    # planned_cost on project_phases are recomputed from these rows. Rates are
    # snapshotted at save time (frozen-plan pattern). Written only through
    # set_milestone_staff() — deliberately NOT in _ALLOWED_TABLES.
    c.execute('''CREATE TABLE IF NOT EXISTS milestone_staff (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phase_id INTEGER NOT NULL REFERENCES project_phases(id) ON DELETE CASCADE,
        staff_id INTEGER NOT NULL REFERENCES staff(id),
        est_hours REAL NOT NULL DEFAULT 0,
        cost_rate REAL DEFAULT 0,
        billing_rate REAL DEFAULT 0,
        UNIQUE(phase_id, staff_id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS work_types (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        label_uz TEXT NOT NULL,
        label_en TEXT,
        label_ru TEXT,
        is_active INTEGER DEFAULT 1,
        sort_order INTEGER DEFAULT 100
    )''')
    # Staff departments lookup. staff.department stores the label_uz value
    # (free text is preserved for backward compatibility); this table drives
    # the dropdown on the staff-creation form and the lookup-tables editor.
    c.execute('''CREATE TABLE IF NOT EXISTS departments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        label_uz TEXT NOT NULL,
        label_en TEXT,
        label_ru TEXT,
        is_active INTEGER DEFAULT 1,
        sort_order INTEGER DEFAULT 100
    )''')
    # Staff roles (job titles) lookup — same contract as `departments`:
    # staff.role stores the label_uz value, free text stays valid.
    c.execute('''CREATE TABLE IF NOT EXISTS staff_roles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        label_uz TEXT NOT NULL,
        label_en TEXT,
        label_ru TEXT,
        is_active INTEGER DEFAULT 1,
        sort_order INTEGER DEFAULT 100
    )''')

    # ── Unified transactions table (v5.2) ─────────────────────────────────────
    c.execute('''CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        direction TEXT NOT NULL,
        tx_type TEXT NOT NULL,
        date TEXT NOT NULL,
        ref_id TEXT, doc_id TEXT,
        project_id INTEGER REFERENCES projects(id),
        phase_id INTEGER REFERENCES project_phases(id),
        counterparty_id INTEGER REFERENCES counterparties(id),
        responsible_id INTEGER REFERENCES staff(id),
        responsible TEXT, client TEXT, paid_to TEXT,
        description TEXT, notes TEXT,
        amount REAL NOT NULL DEFAULT 0,
        paid REAL NOT NULL DEFAULT 0,
        currency TEXT NOT NULL DEFAULT 'UZS',
        exchange_rate REAL, amount_usd REAL,
        contract_amount REAL DEFAULT 0, contract_currency TEXT, contract_amount_usd REAL,
        payment_type TEXT DEFAULT 'bank',
        deadline TEXT,
        status TEXT DEFAULT 'pending',
        supersedes_id INTEGER REFERENCES transactions(id),
        superseded_by_id INTEGER REFERENCES transactions(id),
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS transaction_lines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
        line_no INTEGER NOT NULL,
        project_id INTEGER REFERENCES projects(id),
        project_phase_id INTEGER REFERENCES project_phases(id),
        category_id INTEGER REFERENCES transaction_categories(id),
        amount REAL NOT NULL, description TEXT,
        UNIQUE(transaction_id, line_no)
    )''')

    # ── Dividends (founder distributions from company balance) ─────────────────
    # Mirrors the transactions shape but carries no `direction` column — a
    # dividend is always money leaving the company balance to a founder.
    c.execute('''CREATE TABLE IF NOT EXISTS dividends (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        ref_id TEXT, doc_id TEXT,
        founder TEXT,
        counterparty_id INTEGER REFERENCES counterparties(id),
        responsible_id INTEGER REFERENCES staff(id),
        responsible TEXT,
        description TEXT, notes TEXT,
        amount REAL NOT NULL DEFAULT 0,
        paid REAL NOT NULL DEFAULT 0,
        currency TEXT NOT NULL DEFAULT 'UZS',
        exchange_rate REAL, amount_usd REAL,
        payment_type TEXT DEFAULT 'bank',
        status TEXT DEFAULT 'paid',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT
    )''')
    conn.commit()

    # ── Indices ────────────────────────────────────────────────────────────────
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_ph_project_period ON project_hours(project_id, period)",
        "CREATE INDEX IF NOT EXISTS idx_ph_staff_period ON project_hours(staff_id, period)",
        "CREATE INDEX IF NOT EXISTS idx_sh_staff_dates ON salary_history(staff_id, start_date, end_date)",
        "CREATE INDEX IF NOT EXISTS idx_xr_date ON exchange_rates(date)",
        "CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status)",
        "CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id)",
        "CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_etag_entity ON entity_tags(entity_type, entity_id)",
        "CREATE INDEX IF NOT EXISTS idx_etag_tag ON entity_tags(tag_id)",
        "CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(date)",
        "CREATE INDEX IF NOT EXISTS idx_tx_project ON transactions(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_tx_direction_type ON transactions(direction, tx_type)",
        "CREATE INDEX IF NOT EXISTS idx_tx_counterparty ON transactions(counterparty_id)",
        "CREATE INDEX IF NOT EXISTS idx_div_date ON dividends(date)",
        "CREATE INDEX IF NOT EXISTS idx_phases_project ON project_phases(project_id)",
    ]:
        c.execute(idx_sql)

    # ── Defaults (fresh install) ───────────────────────────────────────────────
    if c.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0:
        defaults = [
            ('usd_rate', 12850, 'Dollar kursi (1 USD)', 'UZS'),
            ('work_hours', 176, 'Nazariy ish soatlari (oyiga)', 'soat'),
            ('effective_hours', 132, 'Effective billable soatlar (oyiga)', 'soat'),
            ('billing_multiplier', 2.0, 'Markup koeffitsienti', 'x'),
            ('work_days', 22, 'Ish kunlari (oyiga)', 'kun'),
            ('tax_rate', 0.12, 'Soliq stavkasi', '%'),
            ('social_rate', 0.12, 'JSSM stavkasi', '%'),
            ('default_risk', 1.15, 'Risk koeffitsienti', ''),
            ('export_risk', 1.25, 'Valyuta risk koeff.', ''),
            ('kgs_rate', 89.5, 'KGS kursi (1 USD)', 'KGS'),
            ('target_margin', 0.50, 'Target foyda marjasi', '%'),
            ('holidays_per_year', 14, "Davlat bayramlari (yiliga)", 'kun'),
            ('avg_leave_days', 20, "O'rtacha ta'til + kasallik (yiliga)", 'kun'),
            ('utilization_rate', 0.75, 'Maqsadli utilization rate', '%'),
            ('kpi_months', 43, 'KPI hisoblash davri (oylar)', 'oy'),
        ]
        c.executemany("INSERT INTO settings VALUES (?,?,?,?)", defaults)
        rates = [
            ('2022-07-01', 10850), ('2023-01-01', 11200), ('2023-07-01', 11500),
            ('2024-01-01', 12200), ('2024-07-01', 12550), ('2025-01-01', 12700),
            ('2025-04-01', 12750), ('2025-07-01', 12800), ('2025-10-01', 12850),
            ('2026-01-01', 12850),
        ]
        c.executemany("INSERT INTO exchange_rates (date, rate) VALUES (?,?)", rates)
        overhead_items = [
            ('Ofis ijarasi', 25000000), ("Kommunal to'lovlar", 5000000),
            ('Internet', 3000000), ('Ovqat xarajatlari', 8000000),
            ('Ofis jihozlari', 2000000), ('Transport', 3000000),
            ('Umumiy litsenziyalar (MS365, server)', 5000000),
            ('Boshqa xarajatlar', 4000000),
        ]
        c.executemany("INSERT INTO overhead (name, monthly_amount) VALUES (?,?)", overhead_items)
        conn.commit()

    # ── Column migrations — backward-compat only; new installs use CREATE TABLE ──
    # These are kept for existing databases predating each column addition.
    # They silently no-op (try/except OperationalError) on fresh installs.
    migrations = [
        # v4 additions to projects
        "ALTER TABLE projects ADD COLUMN estimated_total_hours REAL DEFAULT 0",
        "ALTER TABLE projects ADD COLUMN planned_hours REAL DEFAULT 0",
        "ALTER TABLE projects ADD COLUMN planned_cost REAL DEFAULT 0",
        "ALTER TABLE projects ADD COLUMN planned_revenue REAL DEFAULT 0",
        "ALTER TABLE projects ADD COLUMN planned_outsourcing REAL DEFAULT 0",
        "ALTER TABLE projects ADD COLUMN planned_material REAL DEFAULT 0",
        "ALTER TABLE projects ADD COLUMN plan_frozen_date TEXT",
        # v4 additions to staff
        "ALTER TABLE staff ADD COLUMN staff_code TEXT",
        # v5.1 — snapshot columns on project_hours
        "ALTER TABLE project_hours ADD COLUMN applied_cost_rate REAL",
        "ALTER TABLE project_hours ADD COLUMN applied_billing_rate REAL",
        "ALTER TABLE project_hours ADD COLUMN applied_cost_amount REAL",
        "ALTER TABLE project_hours ADD COLUMN applied_billing_amount REAL",
        "ALTER TABLE project_hours ADD COLUMN rate_snapshot_at TEXT",
        "ALTER TABLE project_hours ADD COLUMN rate_snapshot_source TEXT",
        "ALTER TABLE project_hours ADD COLUMN phase_id INTEGER",
        "ALTER TABLE project_hours ADD COLUMN supersedes_id INTEGER",
        "ALTER TABLE project_hours ADD COLUMN superseded_by_id INTEGER",
        # v5.1 — updated_at on mutable tables
        "ALTER TABLE staff ADD COLUMN updated_at TEXT",
        "ALTER TABLE projects ADD COLUMN updated_at TEXT",
        "ALTER TABLE salary_history ADD COLUMN updated_at TEXT",
        "ALTER TABLE overhead ADD COLUMN updated_at TEXT",
        "ALTER TABLE loans ADD COLUMN updated_at TEXT",
        # v5.3 — plain-text fields lost during external/internal → unified migration
        "ALTER TABLE transactions ADD COLUMN responsible TEXT",
        "ALTER TABLE transactions ADD COLUMN paid_to TEXT",
        "ALTER TABLE transactions ADD COLUMN client TEXT",
        # category directly on transactions (replaces transaction_lines)
        "ALTER TABLE transactions ADD COLUMN category_id INTEGER REFERENCES transaction_categories(id)",
        "CREATE INDEX IF NOT EXISTS idx_tx_category ON transactions(category_id)",
        # v6 — milestones: project_phases become first-class, transactions link to a phase.
        # The two CREATE INDEX lines must stay after the ALTERs that add their columns.
        "ALTER TABLE project_phases ADD COLUMN work_type TEXT",
        "ALTER TABLE project_phases ADD COLUMN planned_outsourcing REAL DEFAULT 0",
        "ALTER TABLE project_phases ADD COLUMN planned_material REAL DEFAULT 0",
        "ALTER TABLE project_phases ADD COLUMN completed_date TEXT",
        "ALTER TABLE project_phases ADD COLUMN notes TEXT",
        "ALTER TABLE project_phases ADD COLUMN updated_at TEXT",
        "ALTER TABLE transactions ADD COLUMN phase_id INTEGER REFERENCES project_phases(id)",
        "CREATE INDEX IF NOT EXISTS idx_tx_phase ON transactions(phase_id)",
        "CREATE INDEX IF NOT EXISTS idx_ph_phase ON project_hours(phase_id)",
        "UPDATE project_phases SET status='in_progress' WHERE status='active'",
        # v6.1 — canonical transaction statuses: paid / partial / pending only
        "UPDATE transactions SET status='paid' WHERE status='To''langan'",
        "UPDATE transactions SET status='pending' WHERE status='Kutilmoqda'",
        "UPDATE transactions SET status='partial' WHERE status='Qisman'",
    ]
    for sql in migrations:
        try:
            c.execute(sql)
        except sqlite3.OperationalError:
            pass

    # ── Seed transaction_categories ────────────────────────────────────────────
    if c.execute("SELECT COUNT(*) FROM transaction_categories").fetchone()[0] == 0:
        c.execute(
            "INSERT INTO transaction_categories (code, name_uz, name_en, name_ru, parent_id, direction, affects_project_cost, sort_order)"
            " VALUES (?,?,?,?,?,?,?,?)",
            ('revenue', "Daromadlar", 'Revenue', 'Доходы', None, 'in', 0, 10)
        )
        c.execute(
            "INSERT INTO transaction_categories (code, name_uz, name_en, name_ru, parent_id, direction, affects_project_cost, sort_order)"
            " VALUES (?,?,?,?,?,?,?,?)",
            ('direct_cost', "To'g'ridan-to'g'ri xarajatlar", 'Direct Costs', 'Прямые затраты', None, 'out', 1, 20)
        )
        c.execute(
            "INSERT INTO transaction_categories (code, name_uz, name_en, name_ru, parent_id, direction, affects_project_cost, sort_order)"
            " VALUES (?,?,?,?,?,?,?,?)",
            ('indirect_cost', "Bilvosita xarajatlar", 'Indirect Costs', 'Косвенные затраты', None, 'out', 0, 30)
        )
        conn.commit()
        rev_id = c.execute("SELECT id FROM transaction_categories WHERE code='revenue'").fetchone()[0]
        dir_id = c.execute("SELECT id FROM transaction_categories WHERE code='direct_cost'").fetchone()[0]
        ind_id = c.execute("SELECT id FROM transaction_categories WHERE code='indirect_cost'").fetchone()[0]
        c.executemany(
            "INSERT OR IGNORE INTO transaction_categories"
            " (code, name_uz, name_en, name_ru, parent_id, direction, affects_project_cost, sort_order)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [
                ('tushum', "Klient to'lovi", 'Client Payment', 'Платёж клиента', rev_id, 'in', 0, 11),
                ('mizan_monthly', "Oylik to'lov (MIZAN)", 'Monthly Fee', 'Ежемесячный платёж', rev_id, 'in', 0, 12),
                ('yakuniy_hisob', "Yakuniy hisob-kitob", 'Final Settlement', 'Окончательный расчёт', rev_id, 'in', 0, 13),
                ('outsourcing', "Autsorising", 'Outsourcing', 'Аутсорсинг', dir_id, 'out', 1, 21),
                ('material', "Materiallar", 'Materials', 'Материалы', dir_id, 'out', 1, 22),
                ('salary_disbursement', "Ish haqi to'lovi", 'Salary Disbursement', 'Выплата зарплаты', ind_id, 'out', 0, 31),
                ('overhead_payment', "Overhead to'lov", 'Overhead Payment', 'Оплата накладных', ind_id, 'out', 0, 32),
            ]
        )

    # ── Seed tx_types ─────────────────────────────────────────────────────────
    if c.execute("SELECT COUNT(*) FROM tx_types").fetchone()[0] == 0:
        c.executemany(
            "INSERT OR IGNORE INTO tx_types (code, label_uz, label_en, label_ru, direction, sort_order) VALUES (?,?,?,?,?,?)",
            [
                ('tushum',       "Shartnoma tushumi",      'Contract Income',    'Доход по контракту', 'external', 10),
                ('outsourcing',  "Outsourcing",             'Outsourcing',        'Аутсорсинг',         'external', 20),
                ('material',     "Material xaridi",         'Material Purchase',  'Закупка материалов', 'external', 30),
                ('yakuniy_hisob',"Yakuniy hisob",           'Final Settlement',   'Окончательный расчёт','external', 40),
                ('mizan_monthly',"MIZAN ulushi",            'MIZAN Monthly',      'Ежемесячный платёж', 'external', 50),
                ('maosh',        "Xodimlar ish haqi",       'Staff Salaries',     'Зарплата сотрудников','internal', 10),
                ('premiya',      "Premiyalar",              'Bonuses',            'Премии',             'internal', 20),
                ('ijara',        "Ofis ijarasi",            'Office Rent',        'Аренда офиса',       'internal', 30),
                ('kommunal',     "Kommunal xarajatlar",     'Utilities',          'Коммунальные услуги','internal', 40),
                ('soliq',        "Soliqlar",                'Taxes',              'Налоги',             'internal', 50),
                ('ovqat',        "Ovqat xarajatlari",       'Food Expenses',      'Питание',            'internal', 60),
                ('litsenziya',   "Dasturlar litsenziyasi",  'Software Licenses',  'Лицензии ПО',        'internal', 70),
                ('malaka',       "Malaka oshirish",         'Training',           'Обучение',           'internal', 80),
                ('overhead',     "Boshqa xarajat",          'Other Expenses',     'Прочие расходы',     'internal', 90),
            ]
        )

    # ── Seed payment_types ────────────────────────────────────────────────────
    if c.execute("SELECT COUNT(*) FROM payment_types").fetchone()[0] == 0:
        c.executemany(
            "INSERT OR IGNORE INTO payment_types (code, label_uz, label_en, label_ru, sort_order) VALUES (?,?,?,?,?)",
            [
                ('bank',   "Bank o'tkazmasi", 'Bank Transfer',   'Банковский перевод', 10),
                ('naqd',   "Naqd pul",         'Cash',            'Наличные',           20),
                ('karta',  "Karta orqali",     'Card Payment',    'Картой',             30),
                ('online', "Onlayn to'lov",    'Online Payment',  'Онлайн оплата',      40),
                ('ichki',  "Ichki hisob",      'Internal Account','Внутренний счёт',    50),
            ]
        )

    # ── Seed work_types (architecture work stages for milestones) ─────────────
    if c.execute("SELECT COUNT(*) FROM work_types").fetchone()[0] == 0:
        c.executemany(
            "INSERT OR IGNORE INTO work_types (code, label_uz, label_en, label_ru, sort_order) VALUES (?,?,?,?,?)",
            [
                ('agr',     "AGR / Topshiriq",        'Assignment / Permits', 'АГР / Задание',          10),
                ('eskiz',   "Eskiz loyiha",           'Concept Design',       'Эскизный проект',        20),
                ('ar',      "Arxitektura yechimlari", 'Architectural Design', 'Архитектурные решения',  30),
                ('kj',      "Konstruktiv yechimlar",  'Structural Design',    'Конструктивные решения', 40),
                ('im',      "Muhandislik tarmoqlari", 'Engineering Systems',  'Инженерные сети',        50),
                ('smeta',   "Smeta hujjatlari",       'Cost Estimation',      'Сметная документация',   60),
                ('nazorat', "Avtorlik nazorati",      'Site Supervision',     'Авторский надзор',       70),
                ('boshqa',  "Boshqa ishlar",          'Other Work',           'Прочие работы',          80),
            ]
        )

    # ── Seed departments / staff_roles from what staff rows already contain ────
    def _seed_lookup_from_staff(table, staff_column, code_fallback):
        if c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] != 0:
            return
        existing = [r[0] for r in c.execute(
            f"SELECT DISTINCT {staff_column} FROM staff"
            f" WHERE {staff_column} IS NOT NULL AND TRIM({staff_column}) != ''"
            f" ORDER BY {staff_column}"
        ).fetchall()]
        seen_codes = set()
        for i, name in enumerate(existing):
            name = name.strip()
            code = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_') or f'{code_fallback}_{i + 1}'
            while code in seen_codes:
                code = f'{code}_{i + 1}'
            seen_codes.add(code)
            c.execute(
                f"INSERT OR IGNORE INTO {table} (code, label_uz, sort_order) VALUES (?,?,?)",
                (code, name, (i + 1) * 10)
            )

    _seed_lookup_from_staff('departments', 'department', 'dept')
    _seed_lookup_from_staff('staff_roles', 'role', 'role')

    # ── One-time migration: external/internal → unified transactions ───────────
    tables_in_db = {r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}

    if 'external_transactions' in tables_in_db and '_legacy_external_transactions' not in tables_in_db:
        try:
            c.execute('''INSERT INTO transactions
                (direction, tx_type, date, ref_id, doc_id, project_id, description, notes,
                 amount, paid, currency, exchange_rate, amount_usd,
                 contract_amount, contract_amount_usd, payment_type, deadline, status, created_at)
                SELECT 'external', tx_type, date, ref_id, doc_id, project_id,
                    CASE WHEN COALESCE(income_desc,'') != '' THEN income_desc
                         WHEN COALESCE(expense_desc,'') != '' THEN expense_desc
                         ELSE '' END,
                    notes, amount, paid, COALESCE(currency,'UZS'),
                    NULLIF(exchange_rate, 0), NULLIF(amount_usd, 0),
                    COALESCE(contract_amount, 0), NULLIF(contract_usd, 0),
                    COALESCE(payment_type,'bank'), deadline,
                    COALESCE(status,'pending'), created_at
                FROM external_transactions
            ''')
        except Exception:
            pass
        try:
            c.execute('''INSERT INTO transactions
                (direction, tx_type, date, ref_id, doc_id, description, notes,
                 amount, paid, currency, amount_usd,
                 contract_amount, contract_amount_usd, payment_type, status, created_at)
                SELECT 'internal', COALESCE(NULLIF(category,''),'overhead'), date, ref_id, doc_id,
                    CASE WHEN COALESCE(paid_to,'') != '' THEN description || ' → ' || paid_to
                         ELSE description END,
                    notes, amount, paid, 'UZS',
                    NULLIF(amount_usd, 0), COALESCE(contract_uzs, 0), NULLIF(contract_usd, 0),
                    'bank', 'paid', created_at
                FROM internal_transactions
            ''')
        except Exception:
            pass
        try:
            c.execute("ALTER TABLE external_transactions RENAME TO _legacy_external_transactions")
        except Exception:
            pass
        try:
            c.execute("ALTER TABLE internal_transactions RENAME TO _legacy_internal_transactions")
        except Exception:
            pass
        _write_audit(conn, 'snapshot', 'transactions', context='v5 migration: unified external+internal into transactions')

    # ── Upsert additional settings ─────────────────────────────────────────────
    new_settings = [
        ('effective_hours', 152, 'Available soatlar (oyiga, yillik asosda)', 'soat'),
        ('billing_multiplier', 2.0, 'Markup koeffitsienti', 'x'),
        ('target_margin', 0.50, 'Target foyda marjasi', '%'),
        ('holidays_per_year', 14, "Davlat bayramlari (yiliga)", 'kun'),
        ('avg_leave_days', 20, "O'rtacha ta'til + kasallik (yiliga)", 'kun'),
        ('utilization_rate', 0.75, 'Maqsadli utilization rate', '%'),
        ('kpi_months', 43, 'KPI hisoblash davri (oylar)', 'oy'),
    ]
    for key, val, label, unit in new_settings:
        if not c.execute("SELECT key FROM settings WHERE key=?", (key,)).fetchone():
            c.execute("INSERT INTO settings VALUES (?,?,?,?)", (key, val, label, unit))

    for s in c.execute("SELECT id, staff_type FROM staff WHERE staff_code IS NULL").fetchall():
        prefix = 'MZ' if s['staff_type'] == 'production' else 'MA'
        c.execute("UPDATE staff SET staff_code=? WHERE id=?", (f"{prefix}-{s['id']:03d}", s['id']))

    conn.commit()
    conn.close()
