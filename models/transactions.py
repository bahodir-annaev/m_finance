"""Cash flow, payment summary and follow-up payments — unified transactions table."""
import sqlite3
from datetime import datetime

from .base import (
    get_db, INCOME_TX_SQL, get_record, update_record, _write_audit,
    get_rate_for_date, write_audit_log,
)


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def derive_status(amount, settled):
    """Canonical status from committed vs settled: paid / partial / pending.

    `settled` is the invoice's own paid plus any linked payment rows — not the
    raw `paid` column. The single source of this rule; accounting_bp imports it.
    """
    if amount > 0 and settled >= amount:
        return 'paid'
    if settled > 0:
        return 'partial'
    return 'pending'


def get_settled(conn, tx_id):
    """Total cash against an invoice: its own `paid` + all its payment rows."""
    row = conn.execute(
        "SELECT t.paid + COALESCE(("
        "  SELECT SUM(paid) FROM transactions WHERE parent_tx_id = t.id"
        "), 0) AS settled FROM transactions t WHERE t.id = ?",
        (tx_id,)
    ).fetchone()
    return _num(row['settled']) if row else 0.0


def recompute_parent_status(conn, parent_id):
    """Re-derive an invoice's status after its payment rows changed.

    Does not commit — the caller owns the transaction boundary.
    """
    parent = conn.execute(
        "SELECT amount FROM transactions WHERE id=?", (parent_id,)
    ).fetchone()
    if not parent:
        return
    status = derive_status(_num(parent['amount']), get_settled(conn, parent_id))
    conn.execute(
        "UPDATE transactions SET status=?, updated_at=? WHERE id=?",
        (status, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), parent_id)
    )


def add_transaction_payment(parent_id, data):
    """Record a follow-up payment against a partially-paid transaction.

    The payment is its own row in `transactions` — it is a real cash movement on
    its own date, and booking it that way is what keeps cash flow by month right.
    It carries amount=0 so the invoice's committed amount is never double counted,
    and inherits direction/tx_type/project/phase from the invoice so that project
    income, milestone actuals and the indirect pool pick it up unchanged.

    Returns (new_row_id, None) on success or (None, error_key) on failure.
    """
    conn = get_db()
    try:
        parent = conn.execute(
            "SELECT * FROM transactions WHERE id=?", (parent_id,)
        ).fetchone()
        if not parent:
            return None, 'tx_pay_parent_missing'
        # A payment row is a leaf: allowing chains would make `settled` recursive
        # and every aggregation would have to walk the tree.
        if parent['parent_tx_id']:
            return None, 'tx_pay_on_payment'

        pay_amount = _num(data.get('amount'))
        if pay_amount <= 0:
            return None, 'tx_pay_amount_err'

        pay_date = (data.get('date') or '').strip() or datetime.now().strftime('%Y-%m-%d')
        currency = data.get('currency') or parent['currency'] or 'UZS'
        rate = _num(get_rate_for_date(pay_date))
        # `paid` is always stored in UZS; the payment-date rate is kept alongside
        # so the original foreign-currency figure stays recoverable.
        paid_uzs = round(pay_amount * rate, 0) if currency == 'USD' else pay_amount

        cur = conn.execute('''INSERT INTO transactions
            (direction, tx_type, date, ref_id, doc_id, project_id, phase_id,
             category_id, counterparty_id, description, client, responsible, paid_to,
             amount, amount_usd, paid, currency, exchange_rate, payment_type,
             notes, status, parent_tx_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (parent['direction'], parent['tx_type'], pay_date,
             f"PAY-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
             parent['doc_id'], parent['project_id'], parent['phase_id'],
             parent['category_id'], parent['counterparty_id'],
             data.get('description') or parent['description'],
             parent['client'], parent['responsible'], parent['paid_to'],
             0, 0, paid_uzs, currency, rate or None,
             data.get('payment_type') or parent['payment_type'] or 'bank',
             data.get('notes') or '', 'paid', parent_id))
        new_id = cur.lastrowid
        recompute_parent_status(conn, parent_id)
        conn.commit()
    finally:
        conn.close()

    write_audit_log('create', 'transactions', new_id,
                    context=f'payment of {paid_uzs} on transaction #{parent_id}')
    return new_id, None


def update_transaction(record_id, data):
    """Update a transaction, re-deriving the dependent columns.

    The generic update_record() writes raw form values; for transactions the
    amount/paid/date fields drive derived columns — status, amount_usd,
    exchange_rate — which would otherwise silently go stale (wrong AR aging,
    drifting FX gain/loss). Status is always derived, never taken from the form.
    """
    old = get_record('transactions', record_id)
    if not old:
        return False
    data = dict(data)
    parent_id = old.get('parent_tx_id')

    if parent_id:
        # A payment row carries no commitment of its own — the invoice already
        # holds it. amount is forced to 0 here and not merely defaulted, because
        # the shared edit modal posts every column: without this, editing a
        # payment would double count in every SUM(amount) (project outsourcing /
        # material cost, milestone `direct`, the expense tiles), none of which
        # filter on parent_tx_id. Same reason amount_usd stays 0.
        data['amount'] = 0
        data['amount_usd'] = 0

    merged = {**old, **data}
    amount = _num(merged.get('amount'))

    # Keep the rate stored at entry time; backfill from the rate table if absent.
    rate = _num(old.get('exchange_rate')) or _num(get_rate_for_date(merged.get('date')))
    if rate > 0:
        data['exchange_rate'] = rate
        if not parent_id:
            data['amount_usd'] = round(amount / rate, 2) if amount > 0 else 0

    if parent_id:
        # A payment row settles itself in full; derive_status would read its
        # amount=0 and wrongly call it 'partial'.
        data['status'] = 'paid'
    else:
        conn = get_db()
        child_paid = _num(conn.execute(
            "SELECT COALESCE(SUM(paid),0) AS s FROM transactions WHERE parent_tx_id=?",
            (record_id,)
        ).fetchone()['s'])
        conn.close()
        data['status'] = derive_status(amount, _num(merged.get('paid')) + child_paid)

    ok = update_record('transactions', record_id, data)
    if ok and parent_id:
        conn = get_db()
        recompute_parent_status(conn, parent_id)
        conn.commit()
        conn.close()
    return ok


def delete_transaction(record_id):
    """Delete a transaction, keeping invoice/payment links consistent.

    Deletes on `transactions` are hard deletes (no is_active column), so removing
    an invoice that still has payment rows would orphan them — or trip the FK and
    surface as a 500. Refuse it and say why. Removing a payment re-derives the
    invoice's status.

    Returns (True, None) or (False, error_key).
    """
    conn = get_db()
    row = conn.execute(
        "SELECT parent_tx_id FROM transactions WHERE id=?", (record_id,)
    ).fetchone()
    if not row:
        conn.close()
        return False, 'tx_del_not_found'
    child_count = conn.execute(
        "SELECT COUNT(*) AS n FROM transactions WHERE parent_tx_id=?", (record_id,)
    ).fetchone()['n']
    if child_count:
        conn.close()
        return False, 'tx_del_has_payments'
    parent_id = row['parent_tx_id']

    try:
        # Rows owned by this transaction go first, in the same DB transaction.
        # `unresolved_imports.transaction_id` is NOT NULL with no ON DELETE action,
        # so an imported row whose project/counterparty name never resolved makes
        # the DELETE raise IntegrityError (FOREIGN KEY constraint failed) straight
        # out to the route. `transaction_lines` cascades in the current schema but
        # not in databases created before that clause existed — delete it here too
        # rather than trusting the deployed FK. Neither means anything once the
        # transaction is gone.
        conn.execute("DELETE FROM transaction_lines WHERE transaction_id=?", (record_id,))
        conn.execute("DELETE FROM unresolved_imports WHERE transaction_id=?", (record_id,))
        conn.execute("DELETE FROM transactions WHERE id=?", (record_id,))
        _write_audit(conn, 'delete', 'transactions', record_id, context='hard delete')
        if parent_id:
            recompute_parent_status(conn, parent_id)
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        return False, 'tx_del_failed'
    finally:
        conn.close()
    return True, None


def get_cash_flow_by_month():
    conn = get_db()
    rows = conn.execute(f'''
        SELECT strftime('%Y-%m', date) as month,
               SUM(CASE WHEN tx_type IN {INCOME_TX_SQL} AND paid > 0 THEN paid ELSE 0 END) as income,
               SUM(CASE WHEN direction='external' AND tx_type NOT IN {INCOME_TX_SQL} AND paid > 0 THEN paid ELSE 0 END) as ext_expense,
               SUM(CASE WHEN direction='internal' AND paid > 0 THEN paid ELSE 0 END) as int_expense
        FROM transactions
        WHERE paid > 0
        GROUP BY month
    ''').fetchall()
    # Dividends are cash leaving the company balance — a real outflow.
    div_rows = conn.execute('''
        SELECT strftime('%Y-%m', date) as month, SUM(paid) as dividends
        FROM dividends WHERE paid > 0 GROUP BY month
    ''').fetchall()
    # Loans move real cash too: receiving a loan (olgan) / a repayment on money
    # we lent (bergan) is cash in; lending out / repaying our debt is cash out.
    # Without these the running balance — and runway — is wrong for a firm
    # that borrows. USD loans are converted at the rate of the flow date.
    loan_issues = conn.execute('''
        SELECT strftime('%Y-%m', issue_date) AS month, loan_type, currency,
               issue_date AS flow_date, total_amount AS amount
        FROM loans
    ''').fetchall()
    loan_pays = conn.execute('''
        SELECT strftime('%Y-%m', lp.date) AS month, l.loan_type, l.currency,
               lp.date AS flow_date, lp.amount
        FROM loan_payments lp JOIN loans l ON lp.loan_id = l.id
    ''').fetchall()
    conn.close()

    loan_by_month = {}   # month → net loan flow (in − out), UZS

    def _add_loan_flow(row, sign):
        if not row['month'] or not (row['amount'] or 0):
            return
        amt = row['amount']
        if row['currency'] and row['currency'] != 'UZS':
            amt *= get_rate_for_date(row['flow_date']) or 1.0
        loan_by_month[row['month']] = loan_by_month.get(row['month'], 0.0) + sign * amt

    for r in loan_issues:
        _add_loan_flow(r, +1 if r['loan_type'] == 'olgan' else -1)
    for r in loan_pays:
        _add_loan_flow(r, -1 if r['loan_type'] == 'olgan' else +1)

    tx_by_month = {r['month']: r for r in rows}
    div_by_month = {r['month']: (r['dividends'] or 0) for r in div_rows}
    all_months = sorted(set(tx_by_month) | set(div_by_month) | set(loan_by_month))

    result = []
    running_balance = 0
    for month in all_months:
        r = tx_by_month.get(month)
        inc = (r['income'] or 0) if r else 0
        ext_exp = (r['ext_expense'] or 0) if r else 0
        int_exp = (r['int_expense'] or 0) if r else 0
        dividends = div_by_month.get(month, 0)
        loan_flow = loan_by_month.get(month, 0)
        total_exp = ext_exp + int_exp + dividends
        net = inc - total_exp + loan_flow
        running_balance += net
        result.append({
            'month': month, 'income': inc, 'ext_expense': ext_exp,
            'int_expense': int_exp, 'dividends': dividends, 'loan_flow': loan_flow,
            'total_expense': total_exp,
            'net_cash_flow': net, 'running_balance': running_balance,
        })
    return result


def get_payment_summary():
    conn = get_db()
    rows = conn.execute(f'''
        SELECT COALESCE(payment_type,'bank') as payment_type,
               SUM(CASE WHEN tx_type IN {INCOME_TX_SQL} THEN paid ELSE 0 END) as income,
               SUM(CASE WHEN tx_type NOT IN {INCOME_TX_SQL} THEN paid ELSE 0 END) as expense
        FROM transactions WHERE paid > 0
        GROUP BY payment_type
    ''').fetchall()
    div_rows = conn.execute('''
        SELECT COALESCE(payment_type,'bank') as payment_type, SUM(paid) as expense
        FROM dividends WHERE paid > 0 GROUP BY payment_type
    ''').fetchall()
    conn.close()

    result = {}
    for row in rows:
        pt = row['payment_type']
        result[pt] = {'income': row['income'] or 0, 'expense': row['expense'] or 0,
                      'net': (row['income'] or 0) - (row['expense'] or 0)}
    # Fold dividend outflows into the same payment-type buckets.
    for row in div_rows:
        pt = row['payment_type']
        bucket = result.setdefault(pt, {'income': 0, 'expense': 0, 'net': 0})
        bucket['expense'] += row['expense'] or 0
        bucket['net'] = bucket['income'] - bucket['expense']
    if 'bank' not in result:
        result['bank'] = {'income': 0, 'expense': 0, 'net': 0}

    total_income = sum(v['income'] for v in result.values())
    total_expense = sum(v['expense'] for v in result.values())
    return {
        'by_type': result,
        'total_income': total_income,
        'total_expense': total_expense,
        'net_balance': total_income - total_expense,
    }
