"""Documents — the operational layer users actually type into.

A document is what happened in the real world (счёт-фактура, платёжное
поручение, зарплатная ведомость). Posting it produces a balanced journal
entry via models/posting.py; the ledger is derived, the document is the input.

Lifecycle: draft → posted → void.
  draft   — freely editable, no ledger effect
  posted  — carries entry_id, immutable
  void    — a reversing entry exists; the pair nets to zero on every account

Money is stored in UZS on both `documents` and `document_lines`. Foreign
currency is kept alongside (currency / exchange_rate / total_cur) so the
original figures stay recoverable, but the ledger only ever sees UZS.
"""
import sqlite3
from datetime import datetime

from .base import (
    get_db, now_ts, today_str, period_of, get_rate_for_date,
    _write_audit, _current_username, DOC_PREFIXES, get_setting,
)
from .ledger import PostingError, reverse_entry

# Documents whose total is derived from their line items rather than typed.
INVOICE_TYPES = ('sales_invoice', 'purchase_invoice')
CASH_TYPES = ('cash_in', 'cash_out')
# Which side of a settlement each cash document works on.
CASH_IN_TYPES = ('cash_in',)


class DocumentError(Exception):
    def __init__(self, key, detail=''):
        self.key = key
        self.detail = detail
        super().__init__(f'{key}: {detail}' if detail else key)


def _num(v, default=0.0):
    try:
        return float(v if v not in (None, '') else default)
    except (TypeError, ValueError):
        return default


# ========== Numbering ==========

def next_document_number(conn, doc_type, date_str=None):
    """Reserve the next number for a type/year: 'SF-2026-0001'.

    The counter lives in doc_sequences, so a deleted draft never makes a
    number reusable — sequences in accounting must not be recycled.
    """
    year = int((date_str or today_str())[:4])
    row = conn.execute(
        "SELECT next_no FROM doc_sequences WHERE doc_type=? AND year=?",
        (doc_type, year)).fetchone()
    if row:
        n = row['next_no']
        conn.execute("UPDATE doc_sequences SET next_no=? WHERE doc_type=? AND year=?",
                     (n + 1, doc_type, year))
    else:
        n = 1
        conn.execute("INSERT INTO doc_sequences (doc_type, year, next_no) VALUES (?,?,?)",
                     (doc_type, year, 2))
    prefix = DOC_PREFIXES.get(doc_type, 'DOC')
    return f'{prefix}-{year}-{n:04d}'


# ========== Read ==========

def get_document(doc_id, conn=None):
    """One document with its lines, allocations and posting status."""
    own = conn is None
    conn = conn or get_db()
    try:
        row = conn.execute(
            "SELECT d.*, c.name AS counterparty_name, c.inn AS counterparty_inn,"
            " p.name AS project_name, ph.name AS phase_name"
            " FROM documents d"
            " LEFT JOIN counterparties c ON c.id = d.counterparty_id"
            " LEFT JOIN projects p ON p.id = d.project_id"
            " LEFT JOIN project_phases ph ON ph.id = d.phase_id"
            " WHERE d.id=?", (doc_id,)).fetchone()
        if not row:
            return None
        doc = dict(row)
        doc['lines'] = [dict(r) for r in conn.execute(
            "SELECT dl.*, a.code AS account_code, a.name_ru AS account_name,"
            " s.name AS staff_name, p.name AS project_name"
            " FROM document_lines dl"
            " LEFT JOIN accounts a ON a.id = dl.account_id"
            " LEFT JOIN staff s ON s.id = dl.staff_id"
            " LEFT JOIN projects p ON p.id = dl.project_id"
            " WHERE dl.document_id=? ORDER BY dl.line_no", (doc_id,))]
        doc['allocations'] = [dict(r) for r in conn.execute(
            "SELECT pa.*, d.number AS invoice_number, d.date AS invoice_date,"
            " d.total AS invoice_total, d.doc_type AS invoice_type"
            " FROM payment_allocations pa JOIN documents d ON d.id = pa.invoice_doc_id"
            " WHERE pa.payment_doc_id=?", (doc_id,))]
        doc['allocated_total'] = sum(a['amount'] for a in doc['allocations'])
        return doc
    finally:
        if own:
            conn.close()


def list_documents(doc_type=None, status=None, date_from=None, date_to=None,
                   counterparty_id=None, project_id=None, search=None,
                   limit=200, offset=0):
    """Filtered document list for a list view, newest first."""
    conn = get_db()
    try:
        sql = ("SELECT d.*, c.name AS counterparty_name, p.name AS project_name,"
               " (SELECT COALESCE(SUM(amount),0) FROM payment_allocations"
               "   WHERE invoice_doc_id = d.id) AS settled,"
               " (SELECT COALESCE(SUM(amount),0) FROM payment_allocations"
               "   WHERE payment_doc_id = d.id) AS allocated"
               " FROM documents d"
               " LEFT JOIN counterparties c ON c.id = d.counterparty_id"
               " LEFT JOIN projects p ON p.id = d.project_id WHERE 1=1")
        params = []
        if doc_type:
            if isinstance(doc_type, (list, tuple)):
                sql += f" AND d.doc_type IN ({','.join('?' * len(doc_type))})"
                params += list(doc_type)
            else:
                sql += " AND d.doc_type = ?"
                params.append(doc_type)
        if status:
            sql += " AND d.status = ?"
            params.append(status)
        if date_from:
            sql += " AND d.date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND d.date <= ?"
            params.append(date_to)
        if counterparty_id:
            sql += " AND d.counterparty_id = ?"
            params.append(counterparty_id)
        if project_id:
            sql += " AND d.project_id = ?"
            params.append(project_id)
        if search:
            sql += (" AND (ulower(d.number) LIKE ulower(?) OR ulower(d.description)"
                    " LIKE ulower(?) OR ulower(c.name) LIKE ulower(?)"
                    " OR ulower(d.external_number) LIKE ulower(?))")
            like = f'%{search}%'
            params += [like, like, like, like]
        sql += " ORDER BY d.date DESC, d.id DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        rows = [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()

    for r in rows:
        if r['doc_type'] in INVOICE_TYPES:
            r['outstanding'] = round(r['total'] - (r['settled'] or 0), 2)
        else:
            r['outstanding'] = 0.0
            r['unallocated'] = round(r['total'] - (r['allocated'] or 0), 2)
    return rows


def invoice_outstanding(doc_id, conn=None):
    """Invoice total minus everything allocated against it (UZS)."""
    own = conn is None
    conn = conn or get_db()
    try:
        row = conn.execute(
            "SELECT d.total - COALESCE((SELECT SUM(amount) FROM payment_allocations"
            " WHERE invoice_doc_id = d.id), 0) AS outstanding"
            " FROM documents d WHERE d.id=?", (doc_id,)).fetchone()
        return round(row['outstanding'], 2) if row else 0.0
    finally:
        if own:
            conn.close()


def open_invoices(counterparty_id=None, doc_type='sales_invoice', conn=None,
                  include_doc_id=None):
    """Posted invoices with something still outstanding — the allocation grid.

    include_doc_id keeps an invoice in the list even when fully settled, so
    re-opening a saved payment still shows the rows it already allocated.
    """
    own = conn is None
    conn = conn or get_db()
    try:
        sql = ("SELECT d.id, d.number, d.date, d.due_date, d.total, d.currency,"
               " d.description, c.name AS counterparty_name, d.counterparty_id,"
               " COALESCE((SELECT SUM(amount) FROM payment_allocations"
               "   WHERE invoice_doc_id = d.id), 0) AS settled"
               " FROM documents d LEFT JOIN counterparties c ON c.id = d.counterparty_id"
               " WHERE d.doc_type = ? AND d.status = 'posted'")
        params = [doc_type]
        if counterparty_id:
            sql += " AND d.counterparty_id = ?"
            params.append(counterparty_id)
        sql += " ORDER BY d.date, d.id"
        rows = [dict(r) for r in conn.execute(sql, params)]
    finally:
        if own:
            conn.close()

    out = []
    for r in rows:
        r['outstanding'] = round(r['total'] - r['settled'], 2)
        if r['outstanding'] > 0.005 or (include_doc_id and r['id'] == include_doc_id):
            out.append(r)
    return out


# ========== Write ==========

def _recalc_totals(conn, doc_id):
    """Derive subtotal / vat / total from the document's own lines.

    Invoices are the sum of their line items — the whole point of a line-item
    document. Cash documents keep the typed payment amount: their lines are a
    breakdown of purpose, not the source of the amount.
    """
    doc = conn.execute("SELECT doc_type, total FROM documents WHERE id=?",
                       (doc_id,)).fetchone()
    if not doc:
        return
    row = conn.execute(
        "SELECT COALESCE(SUM(amount),0) AS net, COALESCE(SUM(vat_amount),0) AS vat,"
        " COALESCE(SUM(gross),0) AS gross, COALESCE(SUM(debit),0) AS dr"
        " FROM document_lines WHERE document_id=?", (doc_id,)).fetchone()

    if doc['doc_type'] in INVOICE_TYPES:
        subtotal, vat = row['net'], row['vat']
        total = subtotal + vat
    elif doc['doc_type'] == 'payroll':
        subtotal = vat = 0.0
        total = row['gross']
    elif doc['doc_type'] in ('manual', 'opening'):
        subtotal = vat = 0.0
        total = row['dr']
    else:  # cash documents keep their typed amount
        return
    conn.execute("UPDATE documents SET subtotal=?, vat_amount=?, total=? WHERE id=?",
                 (round(subtotal, 2), round(vat, 2), round(total, 2), doc_id))


LINE_COLUMNS = ('description', 'quantity', 'unit', 'unit_price', 'amount',
                'vat_rate', 'vat_amount', 'account_id', 'project_id', 'phase_id',
                'staff_id', 'counterparty_id', 'gross', 'pit', 'social',
                'debit', 'credit')


def save_document(data, lines=None, allocations=None, doc_id=None):
    """Create or update a DRAFT document with its lines and allocations.

    A posted document is refused outright — correcting one means voiding it and
    entering a replacement, which is what keeps the audit trail honest.

    Returns the document id.
    """
    doc_type = data.get('doc_type')
    if doc_type not in DOC_PREFIXES:
        raise DocumentError('doc_type_unknown', str(doc_type))

    conn = get_db()
    try:
        if doc_id:
            existing = conn.execute("SELECT status FROM documents WHERE id=?",
                                    (doc_id,)).fetchone()
            if not existing:
                raise DocumentError('doc_not_found', str(doc_id))
            if existing['status'] != 'draft':
                raise DocumentError('doc_posted_readonly', str(doc_id))

        date_str = (data.get('date') or '').strip() or today_str()
        currency = (data.get('currency') or 'UZS').strip() or 'UZS'
        rate = _num(data.get('exchange_rate')) or (
            _num(get_rate_for_date(date_str)) if currency != 'UZS' else 1.0)

        fields = {
            'doc_type': doc_type,
            'date': date_str,
            'period': period_of(date_str),
            'counterparty_id': data.get('counterparty_id') or None,
            'project_id': data.get('project_id') or None,
            'phase_id': data.get('phase_id') or None,
            'loan_id': data.get('loan_id') or None,
            'contract_ref': (data.get('contract_ref') or '').strip() or None,
            'external_number': (data.get('external_number') or '').strip() or None,
            'currency': currency,
            'exchange_rate': rate,
            'total_cur': _num(data.get('total_cur')) or None,
            'payment_method': (data.get('payment_method') or '').strip() or None,
            'bank_account': (data.get('bank_account') or '').strip() or None,
            'due_date': (data.get('due_date') or '').strip() or None,
            'cash_purpose': (data.get('cash_purpose') or '').strip() or None,
            'description': (data.get('description') or '').strip() or None,
            'notes': (data.get('notes') or '').strip() or None,
        }
        # Cash documents carry a typed amount; invoice totals come from lines.
        if doc_type in CASH_TYPES:
            fields['total'] = round(_num(data.get('total')), 2)

        if doc_id:
            sets = ', '.join(f'{k}=?' for k in fields)
            conn.execute(f"UPDATE documents SET {sets}, updated_at=? WHERE id=?",
                         list(fields.values()) + [now_ts(), doc_id])
            _write_audit(conn, 'update', 'documents', doc_id, context=f'{doc_type} edited')
        else:
            number = (data.get('number') or '').strip() or next_document_number(
                conn, doc_type, date_str)
            cols = ['number', 'status', 'created_by'] + list(fields)
            vals = [number, 'draft', _current_username()] + list(fields.values())
            ph = ','.join('?' * len(cols))
            try:
                cur = conn.execute(
                    f"INSERT INTO documents ({','.join(cols)}) VALUES ({ph})", vals)
            except sqlite3.IntegrityError:
                raise DocumentError('doc_number_taken', number)
            doc_id = cur.lastrowid
            _write_audit(conn, 'create', 'documents', doc_id,
                         context=f'{doc_type} {number} created')

        if lines is not None:
            conn.execute("DELETE FROM document_lines WHERE document_id=?", (doc_id,))
            for i, ln in enumerate(lines, start=1):
                vals = {k: ln.get(k) for k in LINE_COLUMNS}
                for numeric in ('quantity', 'unit_price', 'amount', 'vat_rate',
                                'vat_amount', 'gross', 'pit', 'social', 'debit', 'credit'):
                    vals[numeric] = round(_num(vals.get(numeric)), 2)
                cols = ['document_id', 'line_no'] + list(LINE_COLUMNS)
                ph = ','.join('?' * len(cols))
                conn.execute(
                    f"INSERT INTO document_lines ({','.join(cols)}) VALUES ({ph})",
                    [doc_id, i] + [vals[k] for k in LINE_COLUMNS])

        if allocations is not None:
            conn.execute("DELETE FROM payment_allocations WHERE payment_doc_id=?", (doc_id,))
            for al in allocations:
                amount = round(_num(al.get('amount')), 2)
                inv_id = al.get('invoice_doc_id')
                if amount <= 0 or not inv_id:
                    continue
                conn.execute(
                    "INSERT INTO payment_allocations (payment_doc_id, invoice_doc_id, amount)"
                    " VALUES (?,?,?)", (doc_id, int(inv_id), amount))

        _recalc_totals(conn, doc_id)
        conn.commit()
        return doc_id
    finally:
        conn.close()


def validate_allocations(conn, doc_id):
    """Allocations must fit inside both the payment and each invoice.

    Checked at post time rather than at save time so a half-finished draft can
    still be saved, but a posted payment can never over-settle an invoice.
    """
    doc = conn.execute("SELECT total FROM documents WHERE id=?", (doc_id,)).fetchone()
    rows = conn.execute(
        "SELECT pa.invoice_doc_id, pa.amount, d.status, d.total,"
        " COALESCE((SELECT SUM(amount) FROM payment_allocations"
        "   WHERE invoice_doc_id = pa.invoice_doc_id AND payment_doc_id != ?), 0) AS other"
        " FROM payment_allocations pa JOIN documents d ON d.id = pa.invoice_doc_id"
        " WHERE pa.payment_doc_id = ?", (doc_id, doc_id)).fetchall()

    allocated = sum(r['amount'] for r in rows)
    if allocated - (doc['total'] or 0) > 0.01:
        raise DocumentError('alloc_exceeds_payment',
                            f'{allocated:.2f} > {doc["total"]:.2f}')
    for r in rows:
        if r['status'] != 'posted':
            raise DocumentError('alloc_invoice_not_posted', str(r['invoice_doc_id']))
        if r['other'] + r['amount'] - r['total'] > 0.01:
            raise DocumentError('alloc_exceeds_invoice', str(r['invoice_doc_id']))


def post_document(doc_id):
    """Build the document's journal entry and mark it posted.

    Document and posting are written in one database transaction: either both
    land or neither does, so a document can never claim to be posted without a
    balanced entry behind it.

    Returns (entry_id, None) or raises DocumentError / PostingError.
    """
    from .posting import build_entry_lines   # local import: posting imports this module

    conn = get_db()
    try:
        doc = get_document(doc_id, conn)
        if not doc:
            raise DocumentError('doc_not_found', str(doc_id))
        if doc['status'] == 'posted':
            raise DocumentError('doc_already_posted', doc['number'])
        if doc['status'] == 'void':
            raise DocumentError('doc_not_found', doc['number'])

        validate_allocations(conn, doc_id)
        lines, memo = build_entry_lines(conn, doc)

        from .ledger import post_entry
        entry_id = post_entry(conn, doc['date'], lines, memo=memo, document_id=doc_id)
        conn.execute("UPDATE documents SET status='posted', entry_id=?, updated_at=?"
                     " WHERE id=?", (entry_id, now_ts(), doc_id))
        _write_audit(conn, 'post', 'documents', doc_id,
                     context=f"{doc['doc_type']} {doc['number']} posted as entry {entry_id}")
        conn.commit()
        return entry_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def void_document(doc_id, reason=None):
    """Reverse a posted document's entry and mark it void.

    The document row survives with its number — a voided счёт-фактура must stay
    visible in the register, not vanish.
    """
    conn = get_db()
    try:
        doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not doc:
            raise DocumentError('doc_not_found', str(doc_id))
        if doc['status'] != 'posted':
            raise DocumentError('doc_not_posted', doc['number'])

        # An invoice that other payments point at cannot be voided while those
        # allocations exist — they would reference a reversed receivable.
        used = conn.execute(
            "SELECT COUNT(*) AS n FROM payment_allocations pa"
            " JOIN documents p ON p.id = pa.payment_doc_id"
            " WHERE pa.invoice_doc_id=? AND p.status='posted'", (doc_id,)).fetchone()['n']
        if used:
            raise DocumentError('doc_void_has_payments', str(used))

        reverse_entry(conn, doc['entry_id'],
                      memo=f"Storno {doc['number']}" + (f' — {reason}' if reason else ''))
        conn.execute("UPDATE documents SET status='void', voided_at=?, void_reason=?,"
                     " updated_at=? WHERE id=?", (now_ts(), reason, now_ts(), doc_id))
        _write_audit(conn, 'void', 'documents', doc_id,
                     context=f"{doc['number']} voided" + (f': {reason}' if reason else ''))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_draft(doc_id):
    """Delete a draft. Posted and voided documents are never removed."""
    conn = get_db()
    try:
        doc = conn.execute("SELECT status, number FROM documents WHERE id=?",
                           (doc_id,)).fetchone()
        if not doc:
            raise DocumentError('doc_not_found', str(doc_id))
        if doc['status'] != 'draft':
            raise DocumentError('doc_posted_readonly', doc['number'])
        conn.execute("DELETE FROM payment_allocations WHERE payment_doc_id=?", (doc_id,))
        conn.execute("DELETE FROM document_lines WHERE document_id=?", (doc_id,))
        conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        _write_audit(conn, 'delete', 'documents', doc_id,
                     context=f"draft {doc['number']} deleted")
        conn.commit()
        return True
    finally:
        conn.close()


def default_vat_rate():
    return _num(get_setting('vat_rate'), 12.0)


def split_vat(gross_amount, vat_rate):
    """Split a VAT-inclusive amount into (net, vat) — the way invoices are typed."""
    gross_amount = _num(gross_amount)
    vat_rate = _num(vat_rate)
    if vat_rate <= 0:
        return round(gross_amount, 2), 0.0
    net = gross_amount / (1 + vat_rate / 100.0)
    return round(net, 2), round(gross_amount - net, 2)


def add_vat(net_amount, vat_rate):
    """VAT on top of a net amount — returns (net, vat)."""
    net_amount = _num(net_amount)
    vat_rate = _num(vat_rate)
    if vat_rate <= 0:
        return round(net_amount, 2), 0.0
    return round(net_amount, 2), round(net_amount * vat_rate / 100.0, 2)
