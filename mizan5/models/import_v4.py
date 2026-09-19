"""One-shot migration from the v4 database into v5.

Every v4 money row becomes a real document with a real posting, so the v5
ledger reproduces v4's history rather than starting from a balance. Because
every transaction is replayed from the beginning, no opening entry is needed
and the two systems' cash figures must agree exactly — which is the check
run_migration() reports at the end.

The v4 indirect-pool semantics are preserved through the account mapping:
salary and tax rows land on accounts flagged 'direct_labor'/'excluded', so they
stay out of the overhead pool exactly as v4's INDIRECT_POOL_EXCLUDED_TX_TYPES
kept them out.
"""
import os
import sqlite3
from datetime import datetime

from .base import get_db, now_ts, normalize_name, DOC_PREFIXES
from .ledger import account_id_for, get_account_by_code, post_entry
from .documents import save_document, post_document, DocumentError
from .ledger import PostingError
from .depreciation import classify_asset

V4_INCOME_TYPES = ('tushum', 'mizan_monthly', 'yakuniy_hisob')

# v4 tx_type -> (account purpose or code, is_project_cost)
# Salary and tax rows go to accounts that are NOT in the indirect pool, which
# is how v4 treated them; office costs go to 9420, which IS the pool.
# litsenziya goes to 9420.3 for the same reason: the `licenses` register below
# already charges annual_cost/12 to every rate, so pooling the cash payment too
# would count it twice. See posting.assert_not_pooled().
TX_ACCOUNT_MAP = {
    'outsourcing': ('production_cost', True),
    'material': ('production_cost', True),
    'maosh': ('production_cost', False),
    'premiya': ('production_cost', False),
    'soliq': ('9820', False),
    'ijara': ('admin_expense', False),
    'kommunal': ('admin_expense', False),
    'ovqat': ('admin_expense', False),
    'litsenziya': ('license_expense', False),
    'malaka': ('admin_expense', False),
    'overhead': ('admin_expense', False),
}


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _v4_connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn, name):
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _ensure_tax_expense_account(conn):
    """9820 is not in the default seed but the migration needs somewhere for
    tax payments that is outside the overhead pool."""
    if not get_account_by_code('9820', conn):
        conn.execute(
            "INSERT INTO accounts (code, name_ru, name_uz, kind, cost_pool, sort_order)"
            " VALUES ('9820','Расходы по прочим налогам и другим обязательным платежам"
            " от прибыли','Boshqa soliqlar boyicha xarajatlar','T','excluded',195)")


class Migration:
    """Carries the id maps between the two databases as rows are copied."""

    def __init__(self, v4_path, report=None):
        self.v4_path = v4_path
        self.v4 = _v4_connect(v4_path)
        self.staff = {}          # v4 staff id -> v5 staff id
        self.projects = {}
        self.phases = {}
        self.counterparties = {}  # normalized name -> v5 id
        self.invoices = {}       # v4 transaction id -> v5 document id
        self.loans = {}
        self._accounts = None
        self.report = report if report is not None else {
            'counterparties': 0, 'staff': 0, 'projects': 0, 'phases': 0,
            'hours': 0, 'equipment': 0, 'licenses': 0, 'overhead': 0,
            'sales_invoices': 0, 'purchase_invoices': 0, 'receipts': 0,
            'payments': 0, 'internal': 0, 'loans': 0, 'dividends': 0,
            'skipped': [], 'asset_classes': [],
        }

    def close(self):
        self.v4.close()

    def skip(self, kind, ident, reason):
        self.report['skipped'].append({'kind': kind, 'id': ident, 'reason': reason})

    def classify(self, name, price, quantity):
        """Assign an asset class and record the assignment for review."""
        cls, rule = classify_asset(name)
        self.report['asset_classes'].append({
            'name': name, 'asset_class': cls, 'matched': rule is not None,
            'value': _num(price) * (quantity or 1)})
        return cls

    # ── Reference data ──────────────────────────────────────────────────

    def counterparty_for(self, name, cp_type='other', conn=None):
        """Find or create a counterparty from a free-text v4 name.

        With no connection supplied this owns a short one and commits, because
        the replay must not hold a write lock while save_document opens its own
        connection — SQLite would deadlock.
        """
        norm = normalize_name(name)
        if not norm:
            return None
        if norm in self.counterparties:
            return self.counterparties[norm]

        own = conn is None
        conn = conn or get_db()
        try:
            for r in conn.execute("SELECT id, name FROM counterparties"):
                if normalize_name(r['name']) == norm:
                    self.counterparties[norm] = r['id']
                    return r['id']
            cur = conn.execute(
                "INSERT INTO counterparties (name, counterparty_type) VALUES (?,?)",
                (str(name).strip()[:120], cp_type))
            if own:
                conn.commit()
            self.counterparties[norm] = cur.lastrowid
            self.report['counterparties'] += 1
            return cur.lastrowid
        finally:
            if own:
                conn.close()

    def account_cache(self):
        """purpose/code -> account id, resolved once for the whole replay."""
        if self._accounts is None:
            conn = get_db()
            try:
                cache = {}
                for purpose, _ in set(TX_ACCOUNT_MAP.values()):
                    if purpose[0].isdigit():
                        acc = get_account_by_code(purpose, conn)
                        cache[purpose] = (acc['id'] if acc
                                          else account_id_for('other_opex', conn))
                    else:
                        cache[purpose] = account_id_for(purpose, conn)
                for purpose in ('other_opex', 'interest_expense', 'interest_income',
                                'loan_short_in', 'loan_long_in', 'loan_issued',
                                'dividends_payable'):
                    cache[purpose] = account_id_for(purpose, conn)
                self._accounts = cache
            finally:
                conn.close()
        return self._accounts

    def copy_settings(self, conn):
        for r in self.v4.execute("SELECT key, value FROM settings"):
            conn.execute("UPDATE settings SET value=? WHERE key=?", (r['value'], r['key']))
        for r in self.v4.execute("SELECT date, rate FROM exchange_rates"):
            conn.execute("INSERT INTO exchange_rates (date, rate) VALUES (?,?)"
                         " ON CONFLICT(date) DO UPDATE SET rate=excluded.rate",
                         (r['date'], r['rate']))

    def copy_staff(self, conn):
        for r in self.v4.execute("SELECT * FROM staff"):
            cur = conn.execute(
                "INSERT INTO staff (name, full_name, nizam_name, role, department,"
                " staff_type, staff_code, is_active, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(name) DO UPDATE SET role=excluded.role",
                (r['name'], r['full_name'], r['nizam_name'], r['role'], r['department'],
                 r['staff_type'], r['staff_code'], r['is_active'], r['created_at']))
            sid = cur.lastrowid or conn.execute(
                "SELECT id FROM staff WHERE name=?", (r['name'],)).fetchone()['id']
            self.staff[r['id']] = sid
            self.report['staff'] += 1

        for r in self.v4.execute("SELECT * FROM salary_history"):
            sid = self.staff.get(r['staff_id'])
            if not sid:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO salary_history"
                " (staff_id, base_salary, premium, start_date, end_date)"
                " VALUES (?,?,?,?,?)",
                (sid, r['base_salary'], r['premium'], r['start_date'], r['end_date']))

    def copy_projects(self, conn):
        for r in self.v4.execute("SELECT * FROM projects"):
            client_id = self.counterparty_for(r['client'], 'client', conn)
            responsible_id = None
            if r['responsible']:
                match = conn.execute(
                    "SELECT id FROM staff WHERE ulower(name)=ulower(?)",
                    (r['responsible'],)).fetchone()
                responsible_id = match['id'] if match else None
            cur = conn.execute(
                "INSERT INTO projects (name, counterparty_id, responsible_id,"
                " risk_coefficient, risk_score, risk_deadline_months, risk_client_type,"
                " risk_complexity, risk_currency, contract_amount, currency,"
                " start_date, end_date, status, is_billable, estimated_total_hours,"
                " planned_hours, planned_cost, planned_revenue, planned_outsourcing,"
                " planned_material, plan_frozen_date, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(name) DO NOTHING",
                (r['name'], client_id, responsible_id,
                 r['risk_coefficient'], r['risk_score'],
                 r['risk_deadline_months'] if 'risk_deadline_months' in r.keys() else None,
                 r['risk_client_type'] if 'risk_client_type' in r.keys() else None,
                 r['risk_complexity'] if 'risk_complexity' in r.keys() else None,
                 r['risk_currency'] if 'risk_currency' in r.keys() else None,
                 r['contract_amount'], r['currency'], r['start_date'], r['end_date'],
                 r['status'] if r['status'] in ('active', 'completed', 'paused') else 'active',
                 r['is_billable'], r['estimated_total_hours'],
                 r['planned_hours'], r['planned_cost'], r['planned_revenue'],
                 r['planned_outsourcing'], r['planned_material'],
                 r['plan_frozen_date'], r['created_at']))
            pid = cur.lastrowid or conn.execute(
                "SELECT id FROM projects WHERE name=?", (r['name'],)).fetchone()['id']
            self.projects[r['id']] = pid
            self.report['projects'] += 1

        for r in self.v4.execute("SELECT * FROM project_phases"):
            pid = self.projects.get(r['project_id'])
            if not pid:
                continue
            status = r['status'] if r['status'] in (
                'planned', 'in_progress', 'done', 'cancelled') else 'planned'
            cur = conn.execute(
                "INSERT INTO project_phases (project_id, code, name, sort_order,"
                " work_type, planned_hours, planned_cost, planned_revenue,"
                " planned_outsourcing, planned_material, start_date, end_date,"
                " completion_percent, status, completed_date, notes)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, r['code'], r['name'], r['sort_order'], r['work_type'],
                 r['planned_hours'], r['planned_cost'], r['planned_revenue'],
                 r['planned_outsourcing'], r['planned_material'],
                 r['start_date'], r['end_date'], r['completion_percent'],
                 status, r['completed_date'], r['notes']))
            self.phases[r['id']] = cur.lastrowid
            self.report['phases'] += 1

        if _table_exists(self.v4, 'milestone_staff'):
            for r in self.v4.execute("SELECT * FROM milestone_staff"):
                phase_id = self.phases.get(r['phase_id'])
                staff_id = self.staff.get(r['staff_id'])
                if phase_id and staff_id:
                    conn.execute(
                        "INSERT OR IGNORE INTO milestone_staff"
                        " (phase_id, staff_id, est_hours, cost_rate, billing_rate)"
                        " VALUES (?,?,?,?,?)",
                        (phase_id, staff_id, r['est_hours'], r['cost_rate'],
                         r['billing_rate']))

        for r in self.v4.execute("SELECT * FROM project_hours"):
            pid = self.projects.get(r['project_id'])
            sid = self.staff.get(r['staff_id'])
            if not pid or not sid:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO project_hours (project_id, staff_id, phase_id,"
                " hours, period, source, imported_at, applied_cost_rate,"
                " applied_billing_rate, applied_cost_amount, applied_billing_amount,"
                " rate_snapshot_at, rate_snapshot_source)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, sid, self.phases.get(r['phase_id']), r['hours'], r['period'],
                 r['source'], r['imported_at'], r['applied_cost_rate'],
                 r['applied_billing_rate'], r['applied_cost_amount'],
                 r['applied_billing_amount'], r['rate_snapshot_at'],
                 r['rate_snapshot_source']))
            self.report['hours'] += 1

    def copy_assets(self, conn):
        # v4 has no asset class — its register is one flat list on a single
        # depreciation period. Classify on the way in from the asset name, and
        # record every assignment in the report so the guesses are reviewable
        # rather than silent. The v4 lifespan_months is carried across
        # unchanged: re-lifing an asset moves every rate quoted from it, so it
        # is a decision for /staff/equipment, not a side effect of migrating.
        for r in self.v4.execute("SELECT * FROM personal_equipment"):
            cls = self.classify(r['name'], r['price'], 1)
            conn.execute(
                "INSERT INTO equipment (name, kind, asset_class, staff_id, quantity,"
                " price, lifespan_months, purchase_date, is_active)"
                " VALUES (?,'personal',?,?,1,?,?,?,?)",
                (r['name'], cls, self.staff.get(r['staff_id']), r['price'],
                 r['lifespan_months'], r['purchase_date'], r['is_active']))
            self.report['equipment'] += 1
        for r in self.v4.execute("SELECT * FROM general_equipment"):
            cls = self.classify(r['name'], r['price'], r['quantity'])
            conn.execute(
                "INSERT INTO equipment (name, kind, asset_class, quantity, price,"
                " lifespan_months, purchase_date, is_active)"
                " VALUES (?,'general',?,?,?,?,?,?)",
                (r['name'], cls, r['quantity'], r['price'], r['lifespan_months'],
                 r['purchase_date'], r['is_active']))
            self.report['equipment'] += 1
        for r in self.v4.execute("SELECT * FROM personal_licenses"):
            conn.execute(
                "INSERT INTO licenses (name, staff_id, annual_cost, license_type, is_active)"
                " VALUES (?,?,?,?,?)",
                (r['name'], self.staff.get(r['staff_id']), r['annual_cost'],
                 r['license_type'], r['is_active']))
            self.report['licenses'] += 1
        conn.execute("DELETE FROM overhead_budget")
        for r in self.v4.execute("SELECT * FROM overhead"):
            conn.execute(
                "INSERT OR IGNORE INTO overhead_budget (name, monthly_amount, is_active)"
                " VALUES (?,?,?)", (r['name'], r['monthly_amount'], r['is_active']))
            self.report['overhead'] += 1


def _doc_number(doc_type, date_str, seq):
    """Deterministic numbers for migrated documents, so a re-run is comparable."""
    year = int((date_str or '2026')[:4])
    return f"{DOC_PREFIXES.get(doc_type, 'DOC')}-{year}-M{seq:05d}"


def _migrate_transactions(mig):
    """Replay every v4 transaction as a v5 document, then post it."""
    rows = [dict(r) for r in mig.v4.execute(
        "SELECT * FROM transactions ORDER BY date, id")]
    parents = {r['id']: r for r in rows if not r.get('parent_tx_id')}
    seq = 0

    for r in rows:
        seq += 1
        tx_id = r['id']
        date = (r['date'] or '')[:10]
        if not date:
            mig.skip('transaction', tx_id, 'no date')
            continue
        amount = _num(r['amount'])
        paid = _num(r['paid'])
        project_id = mig.projects.get(r['project_id'])
        phase_id = mig.phases.get(r['phase_id'])
        description = (r['description'] or '')[:200]
        is_income = r['tx_type'] in V4_INCOME_TYPES
        is_child = bool(r.get('parent_tx_id'))

        try:
            # ── A follow-up payment: a receipt allocated to its invoice ──
            if is_child:
                parent = parents.get(r['parent_tx_id'])
                invoice_doc = mig.invoices.get(r['parent_tx_id'])
                if paid <= 0:
                    continue
                cp = mig.counterparty_for(
                    r['client'] or r['paid_to'] or (parent or {}).get('client'),
                    'client' if is_income else 'vendor')
                doc_type = 'cash_in' if is_income else 'cash_out'
                allocations = ([{'invoice_doc_id': invoice_doc, 'amount': paid}]
                               if invoice_doc else None)
                doc_id = save_document(
                    {'doc_type': doc_type, 'date': date, 'counterparty_id': cp,
                     'project_id': project_id, 'phase_id': phase_id,
                     'total': paid, 'payment_method': r['payment_type'] or 'bank',
                     'description': description or 'To\'lov',
                     'number': _doc_number(doc_type, date, seq)},
                    allocations=allocations)
                post_document(doc_id)
                mig.report['receipts' if is_income else 'payments'] += 1
                continue

            # ── External income: an invoice, plus its receipt if money moved ──
            if is_income:
                cp = mig.counterparty_for(r['client'] or r['description'], 'client')
                if not cp:
                    cp = mig.counterparty_for('Nomalum mijoz', 'client')
                invoice_total = amount if amount > 0 else paid
                if invoice_total <= 0:
                    mig.skip('transaction', tx_id, 'zero amount')
                    continue
                doc_id = save_document(
                    {'doc_type': 'sales_invoice', 'date': date, 'counterparty_id': cp,
                     'project_id': project_id, 'phase_id': phase_id,
                     'due_date': r['deadline'], 'contract_ref': r['doc_id'],
                     'description': description,
                     'number': _doc_number('sales_invoice', date, seq)},
                    lines=[{'description': description or 'Loyiha ishlari',
                            'amount': invoice_total, 'vat_rate': 0, 'vat_amount': 0,
                            'project_id': project_id, 'phase_id': phase_id}])
                post_document(doc_id)
                mig.invoices[tx_id] = doc_id
                mig.report['sales_invoices'] += 1

                if paid > 0:
                    pay_id = save_document(
                        {'doc_type': 'cash_in', 'date': date, 'counterparty_id': cp,
                         'project_id': project_id, 'total': paid,
                         'payment_method': r['payment_type'] or 'bank',
                         'description': description,
                         'number': _doc_number('cash_in', date, seq)},
                        allocations=[{'invoice_doc_id': doc_id,
                                      'amount': min(paid, invoice_total)}])
                    post_document(pay_id)
                    mig.report['receipts'] += 1
                continue

            # ── Everything else is a cost ──
            purpose, is_project_cost = TX_ACCOUNT_MAP.get(
                r['tx_type'], ('admin_expense', False))
            account_id = mig.account_cache()[purpose]
            cp = mig.counterparty_for(r['paid_to'] or r['client'], 'vendor')
            committed = amount if amount > 0 else paid
            line = {'description': description or r['tx_type'],
                    'amount': committed, 'vat_rate': 0, 'vat_amount': 0,
                    'account_id': account_id,
                    'project_id': project_id if is_project_cost else None,
                    'phase_id': phase_id if is_project_cost else None}

            if committed > 0 and cp and r['direction'] == 'external':
                # An external cost has a real supplier: invoice, then payment.
                doc_id = save_document(
                    {'doc_type': 'purchase_invoice', 'date': date, 'counterparty_id': cp,
                     'project_id': project_id, 'phase_id': phase_id,
                     'due_date': r['deadline'], 'description': description,
                     'number': _doc_number('purchase_invoice', date, seq)},
                    lines=[line])
                post_document(doc_id)
                mig.invoices[tx_id] = doc_id
                mig.report['purchase_invoices'] += 1
                if paid > 0:
                    pay_id = save_document(
                        {'doc_type': 'cash_out', 'date': date, 'counterparty_id': cp,
                         'total': paid, 'payment_method': r['payment_type'] or 'bank',
                         'description': description,
                         'number': _doc_number('cash_out', date, seq)},
                        allocations=[{'invoice_doc_id': doc_id,
                                      'amount': min(paid, committed)}])
                    post_document(pay_id)
                    mig.report['payments'] += 1
            elif paid > 0:
                # Internal running cost: cash out straight to the expense account.
                doc_id = save_document(
                    {'doc_type': 'cash_out', 'date': date, 'counterparty_id': cp,
                     'total': paid, 'payment_method': r['payment_type'] or 'bank',
                     'description': description,
                     'number': _doc_number('cash_out', date, seq)},
                    lines=[dict(line, amount=paid)])
                post_document(doc_id)
                mig.report['internal'] += 1
            else:
                mig.skip('transaction', tx_id, 'no amount and no payment')
        except (DocumentError, PostingError) as e:
            mig.skip('transaction', tx_id, f'{e.key}: {e.detail}')


def _migrate_loans(mig):
    seq = 0
    for r in mig.v4.execute("SELECT * FROM loans ORDER BY issue_date, id"):
        seq += 1
        cp = mig.counterparty_for(r['counterparty'], 'other')
        conn = get_db()
        try:
            cur = conn.execute(
                "INSERT INTO loans (loan_type, counterparty_id, description,"
                " total_amount, currency, interest_rate, issue_date, due_date,"
                " status, notes) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (r['loan_type'], cp, r['description'], r['total_amount'],
                 r['currency'], r['interest_rate'], r['issue_date'], r['due_date'],
                 r['status'] if r['status'] in ('ochiq', 'yopilgan') else 'ochiq',
                 r['notes']))
            loan_id = cur.lastrowid
            conn.commit()
        finally:
            conn.close()
        mig.loans[r['id']] = loan_id
        if _num(r['total_amount']) <= 0:
            continue
        try:
            doc_id = save_document({
                'doc_type': 'loan', 'date': (r['issue_date'] or '')[:10],
                'counterparty_id': cp, 'loan_id': loan_id,
                'currency': r['currency'] or 'UZS',
                'total': _num(r['total_amount']),
                'description': r['description'] or 'Qarz',
                'number': _doc_number('loan', r['issue_date'], seq)})
            post_document(doc_id)
            mig.report['loans'] += 1
        except (DocumentError, PostingError) as e:
            mig.skip('loan', r['id'], f'{e.key}: {e.detail}')

    for r in mig.v4.execute("SELECT lp.*, l.loan_type, l.counterparty"
                            " FROM loan_payments lp JOIN loans l ON l.id = lp.loan_id"
                            " ORDER BY lp.date, lp.id"):
        seq += 1
        loan_id = mig.loans.get(r['loan_id'])
        if not loan_id or _num(r['amount']) <= 0:
            continue
        borrowed = r['loan_type'] == 'olgan'
        is_interest = r['payment_type'] == 'foiz'
        if is_interest:
            purpose = 'interest_expense' if borrowed else 'interest_income'
        else:
            purpose = 'loan_short_in' if borrowed else 'loan_issued'
        cp = mig.counterparty_for(r['counterparty'], 'other')
        try:
            doc_id = save_document({
                'doc_type': 'cash_out' if borrowed else 'cash_in',
                'date': (r['date'] or '')[:10], 'counterparty_id': cp,
                'loan_id': loan_id, 'total': _num(r['amount']),
                'description': r['notes'] or 'Qarz to\'lovi',
                'number': _doc_number('cash_out' if borrowed else 'cash_in',
                                      r['date'], seq)},
                lines=[{'account_id': mig.account_cache()[purpose],
                        'amount': _num(r['amount']),
                        'counterparty_id': cp,
                        'description': r['notes'] or 'Qarz'}])
            post_document(doc_id)
            mig.report['payments'] += 1
        except (DocumentError, PostingError) as e:
            mig.skip('loan_payment', r['id'], f'{e.key}: {e.detail}')


def _migrate_dividends(mig):
    if not _table_exists(mig.v4, 'dividends'):
        return
    seq = 0
    for r in mig.v4.execute("SELECT * FROM dividends ORDER BY date, id"):
        seq += 1
        if _num(r['paid']) <= 0:
            continue
        cp = mig.counterparty_for(conn, r['founder'] or 'Ta\'sischi', 'founder')
        try:
            decl = save_document({
                'doc_type': 'dividend', 'date': (r['date'] or '')[:10],
                'counterparty_id': cp, 'total': _num(r['paid']),
                'description': r['description'] or 'Dividend',
                'number': _doc_number('dividend', r['date'], seq)})
            post_document(decl)
            payout = save_document({
                'doc_type': 'cash_out', 'date': (r['date'] or '')[:10],
                'counterparty_id': cp, 'total': _num(r['paid']),
                'payment_method': r['payment_type'] or 'bank',
                'description': r['description'] or 'Dividend',
                'number': _doc_number('cash_out', r['date'], 90000 + seq)},
                lines=[{'account_id': mig.account_cache()['dividends_payable'],
                        'amount': _num(r['paid']), 'counterparty_id': cp,
                        'description': 'Dividend'}])
            post_document(payout)
            mig.report['dividends'] += 1
        except (DocumentError, PostingError) as e:
            mig.skip('dividend', r['id'], f'{e.key}: {e.detail}')


def v4_expected_totals(v4_path):
    """v4's own numbers, computed the way v4 computed them — the target."""
    conn = _v4_connect(v4_path)
    try:
        cash = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN tx_type IN ('tushum','mizan_monthly',"
            "'yakuniy_hisob') THEN paid ELSE -paid END),0) AS net"
            " FROM transactions WHERE paid > 0").fetchone()['net']
        div = 0.0
        if _table_exists(conn, 'dividends'):
            div = conn.execute(
                "SELECT COALESCE(SUM(paid),0) AS d FROM dividends").fetchone()['d']
        loan_in = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN loan_type='olgan' THEN total_amount"
            " ELSE -total_amount END),0) AS n FROM loans").fetchone()['n']
        loan_pay = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN l.loan_type='olgan' THEN -lp.amount"
            " ELSE lp.amount END),0) AS n FROM loan_payments lp"
            " JOIN loans l ON l.id = lp.loan_id").fetchone()['n']
        receivable = conn.execute(
            "SELECT COALESCE(SUM(t.amount - (t.paid + COALESCE(cp.child_paid,0))),0) AS ar"
            " FROM transactions t"
            " LEFT JOIN (SELECT parent_tx_id AS pid, SUM(paid) AS child_paid"
            "   FROM transactions WHERE parent_tx_id IS NOT NULL"
            "   GROUP BY parent_tx_id) cp ON cp.pid = t.id"
            " WHERE t.direction='external' AND t.parent_tx_id IS NULL"
            "   AND t.tx_type IN ('tushum','mizan_monthly','yakuniy_hisob')"
            "   AND (t.paid + COALESCE(cp.child_paid,0)) < t.amount").fetchone()['ar']
        return {'cash': cash - div + loan_in + loan_pay, 'receivable': receivable,
                'transactions': conn.execute(
                    "SELECT COUNT(*) AS n FROM transactions").fetchone()['n']}
    finally:
        conn.close()


def run_migration(v4_path, verbose=True):
    """Migrate a v4 database into the current v5 database.

    Returns a report dict including the reconciliation between v4's computed
    cash/receivable and the v5 ledger's.
    """
    if not os.path.exists(v4_path):
        raise FileNotFoundError(v4_path)

    mig = Migration(v4_path)
    conn = get_db()
    try:
        _ensure_tax_expense_account(conn)
        mig.copy_settings(conn)
        mig.copy_staff(conn)
        mig.copy_projects(conn)
        mig.copy_assets(conn)
        conn.commit()
    finally:
        conn.close()

    # The replay owns its own short connections per document; holding one open
    # here would lock the database against save_document().
    _migrate_transactions(mig)
    _migrate_loans(mig)
    _migrate_dividends(mig)

    from .reports import cash_balance
    from .ledger import account_balance, get_trial_balance, verify_all_entries

    expected = v4_expected_totals(v4_path)
    actual = {'cash': cash_balance(), 'receivable': account_balance('4010')}
    mig.report['reconciliation'] = {
        'v4_cash': expected['cash'], 'v5_cash': actual['cash'],
        'cash_diff': actual['cash'] - expected['cash'],
        'v4_receivable': expected['receivable'], 'v5_receivable': actual['receivable'],
        'receivable_diff': actual['receivable'] - expected['receivable'],
        'v4_transactions': expected['transactions'],
        'trial_balance_ok': get_trial_balance()['is_balanced'],
        'unbalanced_entries': len(verify_all_entries()),
    }
    mig.close()

    if verbose:
        print('\n--- migration report ---')
        for key, value in mig.report.items():
            if key == 'skipped':
                print(f'  skipped: {len(value)}')
                for s in value[:10]:
                    print(f'    {s["kind"]} #{s["id"]}: {s["reason"]}')
            elif key == 'asset_classes':
                # Every assignment is reported, not just the totals: these are
                # keyword guesses on free-text names and the only way they get
                # corrected is if someone can see them.
                by_class = {}
                for a in value:
                    b = by_class.setdefault(a['asset_class'],
                                            {'n': 0, 'value': 0.0, 'fallback': 0})
                    b['n'] += 1
                    b['value'] += a['value']
                    b['fallback'] += 0 if a['matched'] else 1
                print(f'  asset_classes: {len(value)} assets classified')
                for cls, b in sorted(by_class.items(), key=lambda kv: -kv[1]['value']):
                    note = (f"  ({b['fallback']} unmatched -> fallback)"
                            if b['fallback'] else '')
                    print(f"    {cls:10} n={b['n']:3}  {b['value']:>15,.0f}{note}")
                unmatched = [a for a in value if not a['matched']]
                if unmatched:
                    print(f"    -- review these {len(unmatched)}, they matched no rule:")
                    for a in unmatched[:15]:
                        print(f"       {a['name'][:64]}")
            elif key == 'reconciliation':
                print('  reconciliation:')
                for k, v in value.items():
                    print(f'    {k}: {v}')
            else:
                print(f'  {key}: {value}')
    return mig.report


if __name__ == '__main__':
    import sys
    default = os.path.join(os.path.dirname(__file__), '..', '..', 'mizan_finance.db')
    run_migration(sys.argv[1] if len(sys.argv) > 1 else default)
