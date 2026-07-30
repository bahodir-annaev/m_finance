"""Loan and repayment queries."""
from datetime import datetime, date
from .base import get_db, get_rate_for_date


def get_all_loans():
    conn = get_db()
    loans = conn.execute("SELECT * FROM loans ORDER BY status, issue_date DESC").fetchall()
    result = []
    for loan in loans:
        # Only principal ('asosiy') payments reduce the outstanding balance;
        # interest ('foiz') is a cost of the loan, not a repayment of it.
        payments = conn.execute(
            """SELECT
                 COALESCE(SUM(CASE WHEN COALESCE(payment_type,'asosiy')='foiz' THEN 0 ELSE amount END), 0) AS principal_paid,
                 COALESCE(SUM(CASE WHEN COALESCE(payment_type,'asosiy')='foiz' THEN amount ELSE 0 END), 0) AS interest_paid
               FROM loan_payments WHERE loan_id=?""",
            (loan['id'],)
        ).fetchone()
        total_paid = payments['principal_paid']
        interest_paid = payments['interest_paid']
        remaining = loan['total_amount'] - total_paid
        overdue = False
        if loan['due_date'] and loan['status'] == 'ochiq':
            try:
                due = datetime.strptime(loan['due_date'], '%Y-%m-%d').date()
                overdue = date.today() > due
            except (ValueError, TypeError):
                pass
        result.append({
            **dict(loan),
            'total_paid': total_paid,
            'interest_paid': interest_paid,
            'remaining': remaining,
            'overdue': overdue,
            'paid_pct': (total_paid / loan['total_amount'] * 100) if loan['total_amount'] > 0 else 0,
        })
    conn.close()
    return result


def get_loan_detail(loan_id):
    conn = get_db()
    loan = conn.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone()
    if not loan:
        conn.close()
        return None
    payments = conn.execute(
        "SELECT * FROM loan_payments WHERE loan_id=? ORDER BY date", (loan_id,)
    ).fetchall()
    principal_paid = sum(p['amount'] for p in payments
                         if (p['payment_type'] or 'asosiy') != 'foiz')
    interest_paid = sum(p['amount'] for p in payments
                        if (p['payment_type'] or 'asosiy') == 'foiz')
    conn.close()
    return {
        **dict(loan),
        'payments': [dict(p) for p in payments],
        'total_paid': principal_paid,
        'interest_paid': interest_paid,
        'remaining': loan['total_amount'] - principal_paid,
    }


def get_loan_summary():
    conn = get_db()
    rows = conn.execute('''
        SELECT l.id, l.loan_type, l.total_amount, l.currency, l.issue_date,
               COALESCE((SELECT SUM(amount) FROM loan_payments
                          WHERE loan_id = l.id
                            AND COALESCE(payment_type,'asosiy') != 'foiz'), 0) AS paid
        FROM loans l WHERE l.status = 'ochiq'
    ''').fetchall()
    overdue_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM loans WHERE status='ochiq' AND due_date < date('now') AND due_date IS NOT NULL"
    ).fetchone()['cnt']
    conn.close()

    totals = {'olgan': 0.0, 'bergan': 0.0}
    paid_totals = {'olgan': 0.0, 'bergan': 0.0}
    for r in rows:
        rate = 1.0 if r['currency'] == 'UZS' else (get_rate_for_date(r['issue_date']) or 1.0)
        totals[r['loan_type']] += r['total_amount'] * rate
        paid_totals[r['loan_type']] += r['paid'] * rate

    return {
        'total_taken': totals['olgan'],
        'total_given': totals['bergan'],
        'taken_remaining': totals['olgan'] - paid_totals['olgan'],
        'given_remaining': totals['bergan'] - paid_totals['bergan'],
        'overdue_count': overdue_count,
    }
