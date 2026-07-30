"""Cash flow and payment summary — reads from unified transactions table."""
from .base import (
    get_db, INCOME_TX_SQL, get_record, update_record, get_rate_for_date,
)


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


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
    merged = {**old, **data}
    amount = _num(merged.get('amount'))
    paid = _num(merged.get('paid'))

    data = dict(data)
    # Keep the rate stored at entry time; backfill from the rate table if absent.
    rate = _num(old.get('exchange_rate')) or _num(get_rate_for_date(merged.get('date')))
    if rate > 0:
        data['exchange_rate'] = rate
        data['amount_usd'] = round(amount / rate, 2) if amount > 0 else 0
    if amount > 0 and paid >= amount:
        data['status'] = 'paid'
    elif paid > 0:
        data['status'] = 'partial'
    else:
        data['status'] = 'pending'
    return update_record('transactions', record_id, data)


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
