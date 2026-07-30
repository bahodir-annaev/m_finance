"""Dividend queries — founder distributions taken from the company balance.

A dividend is structurally like a transaction but always represents money
leaving the company balance, so there is no `direction` column.
"""
from datetime import datetime
from .base import get_db, get_rate_for_date


def get_all_dividends(limit=500):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM dividends ORDER BY date DESC, id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_dividend_summary():
    """Totals in UZS. Like transactions, `amount`/`paid` are stored in UZS."""
    conn = get_db()
    row = conn.execute(
        "SELECT COALESCE(SUM(amount),0) a, COALESCE(SUM(paid),0) p, COUNT(*) c FROM dividends"
    ).fetchone()
    conn.close()
    return {
        'total_amount': row['a'],
        'total_paid': row['p'],
        'total_pending': row['a'] - row['p'],
        'count': row['c'],
    }


def add_dividend(date, founder, amount, paid, currency='UZS', payment_type='bank',
                 doc_id=None, responsible=None, description=None, notes=None):
    """Insert one dividend distribution. `amount`/`paid` are given in the entry
    currency, but stored in UZS (mirroring the transactions table) so they feed
    directly into the UZS running cash balance; the USD figure is kept separately."""
    conn = get_db()
    try:
        rate = get_rate_for_date(date) or 1.0
        if currency == 'USD':
            amount_usd = amount
            amount_uzs = round(amount * rate, 0)
            paid_uzs = round(paid * rate, 0)
        else:
            amount_uzs = amount
            amount_usd = round(amount / rate, 2) if amount and rate else 0
            paid_uzs = paid
        status = 'paid' if (paid_uzs or 0) >= (amount_uzs or 0) and amount_uzs else 'pending'
        conn.execute('''INSERT INTO dividends
            (date, ref_id, doc_id, founder, responsible, description, notes,
             amount, paid, currency, exchange_rate, amount_usd, payment_type, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (date, f"DIV-{datetime.now().strftime('%Y%m%d-%H%M%S')}", doc_id or None,
             founder or None, responsible or None, description or None, notes or None,
             amount_uzs, paid_uzs, currency, rate, amount_usd, payment_type, status))
        conn.commit()
    finally:
        conn.close()
