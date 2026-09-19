"""The firm's own bank accounts — the 1С «Банковские счета» subconto.

A 4-digit ledger account (5110 расчётные счета, 5210 валютные счета) is one
code in the chart but several real 20-digit accounts at the bank. The ledger
code stays single; each real account is a row here, and every posting line on
such an account carries `journal_lines.bank_account_id`. Balances per bank
account are a GROUP BY on that analytic, the same way AR by counterparty
works — nothing that sums cash by ledger account changes.

Which ledger accounts are subdivided is decided by `accounts.subconto =
'bank_account'`; the posting rule (ledger._check_bank_accounts) only switches
on for an account once it has at least one active row here.
"""
from .base import get_db, now_ts, _write_audit
from .ledger import account_balance, balances_by_analytic


def subdividable_accounts(conn=None):
    """Ledger accounts that may carry bank accounts (subconto='bank_account')."""
    own = conn is None
    conn = conn or get_db()
    try:
        rows = conn.execute(
            "SELECT id, code, name_ru, name_uz, name_en FROM accounts"
            " WHERE subconto = 'bank_account' AND is_active = 1"
            " ORDER BY sort_order, code").fetchall()
        return [dict(r) for r in rows]
    finally:
        if own:
            conn.close()


def list_bank_accounts(active_only=True, account_id=None, conn=None):
    """Bank accounts with their ledger code, ordered for a <select>: default first."""
    own = conn is None
    conn = conn or get_db()
    try:
        sql = ("SELECT b.*, a.code AS account_code, a.name_ru AS account_name"
               " FROM bank_accounts b JOIN accounts a ON a.id = b.account_id WHERE 1=1")
        params = []
        if active_only:
            sql += " AND b.is_active = 1"
        if account_id:
            sql += " AND b.account_id = ?"
            params.append(account_id)
        sql += " ORDER BY a.sort_order, a.code, b.is_default DESC, b.name"
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        if own:
            conn.close()


def get_bank_account(bank_account_id, conn=None):
    own = conn is None
    conn = conn or get_db()
    try:
        row = conn.execute(
            "SELECT b.*, a.code AS account_code FROM bank_accounts b"
            " JOIN accounts a ON a.id = b.account_id WHERE b.id=?",
            (bank_account_id,)).fetchone()
        return dict(row) if row else None
    finally:
        if own:
            conn.close()


def default_bank_account_id(account_id, conn=None):
    """The default bank account for a ledger account, or None if none registered."""
    own = conn is None
    conn = conn or get_db()
    try:
        row = conn.execute(
            "SELECT id FROM bank_accounts WHERE account_id=? AND is_active=1"
            " ORDER BY is_default DESC, id LIMIT 1", (account_id,)).fetchone()
        return row['id'] if row else None
    finally:
        if own:
            conn.close()


def save_bank_account(fields, bank_account_id=None):
    """Create or update one bank account. Returns its id.

    Only one row per ledger account may be the default: setting is_default
    clears it on the siblings, so the form's pre-selection is never ambiguous.
    """
    if not fields.get('name') or not fields.get('account_id'):
        raise ValueError('required_field')
    conn = get_db()
    try:
        if bank_account_id:
            sets = ', '.join(f'{k}=?' for k in fields)
            conn.execute(f"UPDATE bank_accounts SET {sets}, updated_at=? WHERE id=?",
                         list(fields.values()) + [now_ts(), bank_account_id])
        else:
            cols = ','.join(fields)
            ph = ','.join('?' * len(fields))
            cur = conn.execute(f"INSERT INTO bank_accounts ({cols}) VALUES ({ph})",
                               list(fields.values()))
            bank_account_id = cur.lastrowid
        if fields.get('is_default'):
            conn.execute("UPDATE bank_accounts SET is_default=0"
                         " WHERE account_id=? AND id<>?",
                         (fields['account_id'], bank_account_id))
        conn.commit()
        return bank_account_id
    finally:
        conn.close()


def bank_account_balances(as_of=None, active_only=False):
    """Every bank account with its balance, plus an 'unassigned' row per
    ledger account for lines posted before the register existed.

    Σ(bank balances) + unassigned == account_balance(ledger account) by
    construction, so the dashboard cash tile is unchanged by the subdivision.
    """
    conn = get_db()
    try:
        ledgers = subdividable_accounts(conn)
        rows = list_bank_accounts(active_only=active_only, conn=conn)
    finally:
        conn.close()

    out = []
    for acc in ledgers:
        by_bank = {b['key']: b['balance'] for b in
                   balances_by_analytic([acc['id']], 'bank_account_id', as_of=as_of,
                                        min_abs=0)}
        total = account_balance(account_id=acc['id'], as_of=as_of)
        assigned = 0.0
        banks = []
        for b in rows:
            if b['account_id'] != acc['id']:
                continue
            bal = by_bank.get(b['id'], 0.0)
            assigned += bal
            banks.append(dict(b, balance=bal))
        out.append({
            'account': acc, 'banks': banks, 'total': total,
            'unassigned': round(total - assigned, 2),
            'unassigned_lines': _unassigned_count(acc['id']),
        })
    return out


def _unassigned_count(account_id, conn=None):
    own = conn is None
    conn = conn or get_db()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM journal_lines"
            " WHERE account_id=? AND bank_account_id IS NULL", (account_id,)).fetchone()[0]
    finally:
        if own:
            conn.close()


def assign_unassigned_lines(bank_account_id):
    """One-time backfill: tag every line on the bank account's ledger account
    that has no bank account yet, and the cash documents behind them.

    Deliberately the one place outside post_entry() that touches
    journal_lines: it sets an analytic column only — never an amount, account,
    date or entry — so every ledger invariant (balance, period, mirror) is
    untouched. Meant for the day the register is first filled, when the
    history on 5110 belongs to the firm's single pre-existing account.
    Returns the number of lines updated.
    """
    conn = get_db()
    try:
        bank = conn.execute("SELECT * FROM bank_accounts WHERE id=?",
                            (bank_account_id,)).fetchone()
        if not bank:
            raise ValueError('bank_account_missing')
        cur = conn.execute(
            "UPDATE journal_lines SET bank_account_id=?"
            " WHERE account_id=? AND bank_account_id IS NULL",
            (bank_account_id, bank['account_id']))
        n = cur.rowcount
        # The documents whose posted entry now carries the bank account, and
        # any draft cash document routed to the same ledger account, so the
        # detail page and the ledger tell the same story.
        conn.execute(
            "UPDATE documents SET bank_account_id=?"
            " WHERE bank_account_id IS NULL AND doc_type IN ('cash_in','cash_out','loan')"
            " AND currency=? AND entry_id IN"
            "   (SELECT DISTINCT entry_id FROM journal_lines"
            "     WHERE account_id=? AND bank_account_id=?)",
            (bank_account_id, bank['currency'], bank['account_id'], bank_account_id))
        _write_audit(conn, 'update', 'bank_accounts', bank_account_id,
                     context=f'assigned {n} unassigned lines on account '
                             f'{bank["account_id"]} to {bank["name"]}')
        conn.commit()
        return n
    finally:
        conn.close()
