"""Loans — the register, and every movement of money on it as a document.

The register holds the terms. The balance is never stored: it is what the
ledger says on 6820/7820 (money we borrowed) or 5820 (money we lent) for the
entries that carry this loan's documents, so a loan can never disagree with
the books. Only principal moves the balance — interest goes to 9610/9530 and
is tracked separately, exactly as v4 kept `asosiy` apart from `foiz`.
"""
from .base import get_db, now_ts, today_str, _write_audit
from .ledger import account_id_for, account_balance
from .documents import save_document, post_document, list_documents


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def principal_purpose(loan):
    if loan['loan_type'] == 'olgan':
        return 'loan_long_in' if loan['term'] == 'long' else 'loan_short_in'
    return 'loan_issued'


def loan_balance(loan_id, conn=None):
    """Outstanding principal from the ledger, restricted to this loan's own
    documents — so two loans with one counterparty do not share a balance."""
    own = conn is None
    conn = conn or get_db()
    try:
        loan = conn.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone()
        if not loan:
            return 0.0
        acc = account_id_for(principal_purpose(loan), conn)
        row = conn.execute(
            "SELECT COALESCE(SUM(jl.debit),0) AS dr, COALESCE(SUM(jl.credit),0) AS cr"
            " FROM journal_lines jl JOIN journal_entries je ON je.id = jl.entry_id"
            " JOIN documents d ON d.id = je.document_id"
            " WHERE d.loan_id = ? AND jl.account_id = ?", (loan_id, acc)).fetchone()
    finally:
        if own:
            conn.close()
    dr, cr = _num(row['dr']), _num(row['cr'])
    # Borrowed money is a liability (credit-normal); lent money an asset.
    return round(cr - dr, 2) if loan['loan_type'] == 'olgan' else round(dr - cr, 2)


def loan_documents(loan_id, conn=None):
    """Every posted or draft document on this loan, oldest first, with the
    principal / interest split read off its lines."""
    own = conn is None
    conn = conn or get_db()
    try:
        loan = conn.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone()
        if not loan:
            return []
        principal_acc = account_id_for(principal_purpose(loan), conn)
        interest_acc = account_id_for(
            'interest_expense' if loan['loan_type'] == 'olgan' else 'interest_income', conn)
        rows = [dict(r) for r in conn.execute(
            "SELECT d.id, d.doc_type, d.number, d.date, d.total, d.status,"
            " d.payment_method, d.description,"
            " COALESCE((SELECT SUM(dl.amount) FROM document_lines dl"
            "   WHERE dl.document_id = d.id AND dl.account_id = ?), 0) AS principal,"
            " COALESCE((SELECT SUM(dl.amount) FROM document_lines dl"
            "   WHERE dl.document_id = d.id AND dl.account_id = ?), 0) AS interest"
            " FROM documents d WHERE d.loan_id = ? AND d.status != 'void'"
            " ORDER BY d.date, d.id", (principal_acc, interest_acc, loan_id))]
    finally:
        if own:
            conn.close()
    for r in rows:
        if r['doc_type'] == 'loan':
            r['kind'] = 'issue'
            r['principal'] = r['total']
        else:
            r['kind'] = 'repayment'
    return rows


def list_loans(loan_type=None, status=None):
    conn = get_db()
    try:
        sql = ("SELECT l.*, c.name AS counterparty_name FROM loans l"
               " LEFT JOIN counterparties c ON c.id = l.counterparty_id WHERE 1=1")
        params = []
        if loan_type:
            sql += " AND l.loan_type = ?"
            params.append(loan_type)
        if status:
            sql += " AND l.status = ?"
            params.append(status)
        sql += " ORDER BY l.status, l.issue_date DESC, l.id DESC"
        rows = [dict(r) for r in conn.execute(sql, params)]
        for r in rows:
            r['ledger_balance'] = loan_balance(r['id'], conn)
            docs = loan_documents(r['id'], conn)
            r['documents'] = docs
            posted = [d for d in docs if d['status'] == 'posted']
            r['issued'] = sum(d['principal'] for d in posted if d['kind'] == 'issue')
            r['principal_paid'] = sum(d['principal'] for d in posted if d['kind'] == 'repayment')
            r['interest_paid'] = sum(d['interest'] for d in posted if d['kind'] == 'repayment')
            r['is_overdue'] = bool(r['due_date'] and r['status'] == 'ochiq'
                                   and r['due_date'] < today_str()
                                   and r['ledger_balance'] > 0.005)
        return rows
    finally:
        conn.close()


def loan_summary(rows=None):
    """Dashboard / page tiles: open count and outstanding per currency, each side."""
    rows = rows if rows is not None else list_loans()
    out = {}
    for side in ('olgan', 'bergan'):
        mine = [r for r in rows if r['loan_type'] == side]
        open_rows = [r for r in mine if r['status'] == 'ochiq']
        by_cur = {}
        for r in open_rows:
            by_cur[r['currency'] or 'UZS'] = by_cur.get(r['currency'] or 'UZS', 0.0) \
                + r['ledger_balance']
        out[side] = {'count': len(mine), 'open': len(open_rows),
                     'closed': len(mine) - len(open_rows),
                     'outstanding': sum(r['ledger_balance'] for r in open_rows),
                     'by_currency': by_cur,
                     'overdue': sum(1 for r in open_rows if r['is_overdue'])}
    out['borrowed'] = account_balance('6820') + account_balance('7820')
    out['lent'] = account_balance('5820')
    return out


def save_loan(fields, loan_id=None):
    conn = get_db()
    try:
        if loan_id:
            sets = ', '.join(f'{k}=?' for k in fields)
            conn.execute(f"UPDATE loans SET {sets}, updated_at=? WHERE id=?",
                         list(fields.values()) + [now_ts(), loan_id])
            _write_audit(conn, 'update', 'loans', loan_id)
        else:
            cols = ','.join(fields)
            ph = ','.join('?' * len(fields))
            cur = conn.execute(f"INSERT INTO loans ({cols}) VALUES ({ph})",
                               list(fields.values()))
            loan_id = cur.lastrowid
            _write_audit(conn, 'create', 'loans', loan_id)
        conn.commit()
        return loan_id
    finally:
        conn.close()


def sync_loan_status(loan_id, conn=None):
    """Close a loan once its principal is fully repaid; reopen if a void
    brings the balance back. Interest never counts toward closing.

    Returns the status the loan now has.
    """
    own = conn is None
    conn = conn or get_db()
    try:
        loan = conn.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone()
        if not loan:
            return None
        issued = conn.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE loan_id=? AND doc_type='loan'"
            "   AND status='posted'", (loan_id,)).fetchone()['n']
        balance = loan_balance(loan_id, conn)
        # Only a loan that has actually been booked can close itself: an
        # unbooked register row has a zero balance for the wrong reason.
        new_status = 'yopilgan' if (issued and balance <= 0.005) else 'ochiq'
        if new_status != loan['status']:
            conn.execute("UPDATE loans SET status=?, updated_at=? WHERE id=?",
                         (new_status, now_ts(), loan_id))
            _write_audit(conn, 'update', 'loans', loan_id, 'status',
                         loan['status'], new_status, context='auto by balance')
            conn.commit()
        return new_status
    finally:
        if own:
            conn.close()


def post_loan_issue(loan, payment_method='bank', bank_account_id=None, description=None):
    """Book the disbursement of a registered loan as a posted `loan` document."""
    doc_id = save_document({
        'doc_type': 'loan', 'date': loan['issue_date'],
        'counterparty_id': loan['counterparty_id'], 'loan_id': loan['id'],
        'currency': loan['currency'] or 'UZS',
        'total': loan['total_amount'],
        'payment_method': payment_method or 'bank',
        'bank_account_id': bank_account_id,
        'description': description or loan.get('description') or f"Qarz #{loan['id']}"})
    post_document(doc_id)
    sync_loan_status(loan['id'])
    return doc_id


def record_loan_payment(loan_id, principal=0.0, interest=0.0, date=None,
                        payment_method='bank', bank_account_id=None, label=None):
    """Repayment: principal against the loan account, interest to 9610/9530.

    Posts a cash_out (money we borrowed) or cash_in (money we lent), then
    re-derives the loan's status. Returns the document id.
    """
    principal, interest = _num(principal), _num(interest)
    if principal <= 0 and interest <= 0:
        raise ValueError('required_field')
    conn = get_db()
    try:
        loan = conn.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone()
        if not loan:
            raise ValueError('not_found')
        borrowed = loan['loan_type'] == 'olgan'
        principal_acc = account_id_for(principal_purpose(loan), conn)
        interest_acc = account_id_for(
            'interest_expense' if borrowed else 'interest_income', conn)
    finally:
        conn.close()

    label = label or 'Qarz'
    lines = []
    if principal > 0:
        lines.append({'account_id': principal_acc, 'amount': principal,
                      'counterparty_id': loan['counterparty_id'],
                      'description': f'{label} — asosiy qarz'})
    if interest > 0:
        lines.append({'account_id': interest_acc, 'amount': interest,
                      'counterparty_id': loan['counterparty_id'],
                      'description': f'{label} — foiz'})
    doc_id = save_document({
        'doc_type': 'cash_out' if borrowed else 'cash_in',
        'date': date or today_str(),
        'counterparty_id': loan['counterparty_id'], 'loan_id': loan_id,
        'total': principal + interest,
        'payment_method': payment_method or 'bank',
        'bank_account_id': bank_account_id,
        'description': f'{label} #{loan_id}'}, lines=lines)
    post_document(doc_id)
    sync_loan_status(loan_id)
    return doc_id


def list_loan_documents(limit=100):
    return list_documents(doc_type='loan', limit=limit)
