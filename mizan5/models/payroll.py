"""Payroll runs — зарплатная ведомость.

One payroll document per period holds a line per employee (gross, PIT, social,
net). Posting it accrues the whole period at once (posting rule 5.5), which is
what turns salary from a hand-maintained number into a booked cost the rate
engine can read back out of the ledger.

Paying the money out is a separate cash_out document with lines on 6710
(net pay), 6420.1 (PIT) and 6520 (social) — after both, all three accounts
return to zero, which is the test that the period was settled in full.
"""
from calendar import monthrange

from .base import get_db, get_setting, period_of
from .documents import save_document, get_document
from .staff import salary_at, _num


def period_bounds(period):
    """'YYYY-MM' → ('YYYY-MM-01', 'YYYY-MM-<last>')."""
    y, m = int(period[:4]), int(period[5:7])
    return f'{y:04d}-{m:02d}-01', f'{y:04d}-{m:02d}-{monthrange(y, m)[1]:02d}'


def build_payroll_rows(period, conn=None):
    """One suggested payroll line per employee active during the period.

    Gross comes from the salary in force on the last day of the month; PIT and
    social use the configured rates. Every figure is editable before posting —
    a mid-month hire or an unpaid leave is a manual correction on the row.
    """
    own = conn is None
    conn = conn or get_db()
    try:
        start, end = period_bounds(period)
        tax_rate = _num(get_setting('tax_rate', 0.12))
        social_rate = _num(get_setting('social_rate', 0.12))

        staff = conn.execute(
            "SELECT id, name, staff_code, role, staff_type, hire_date, termination_date"
            " FROM staff WHERE is_active=1"
            "   AND (hire_date IS NULL OR hire_date <= ?)"
            "   AND (termination_date IS NULL OR termination_date >= ?)"
            " ORDER BY staff_type, name", (end, start)).fetchall()

        rows = []
        for s in staff:
            sal = salary_at(s['id'], end, conn)
            if not sal:
                continue
            gross = _num(sal['base_salary']) + _num(sal['premium'])
            if gross <= 0:
                continue
            pit = round(gross * tax_rate, 2)
            social = round(gross * social_rate, 2)
            rows.append({
                'staff_id': s['id'], 'staff_name': s['name'],
                'staff_code': s['staff_code'], 'role': s['role'],
                'staff_type': s['staff_type'],
                'base_salary': _num(sal['base_salary']),
                'premium': _num(sal['premium']),
                'gross': round(gross, 2), 'pit': pit, 'social': social,
                'net': round(gross - pit, 2),
                'description': f'Ish haqi {period}',
            })
        return rows
    finally:
        if own:
            conn.close()


def payroll_totals(rows):
    gross = sum(_num(r.get('gross')) for r in rows)
    pit = sum(_num(r.get('pit')) for r in rows)
    social = sum(_num(r.get('social')) for r in rows)
    return {'gross': gross, 'pit': pit, 'social': social,
            'net': gross - pit, 'employer_cost': gross + social,
            'count': len(rows)}


def save_payroll(period, rows, date=None, doc_id=None, notes=None):
    """Create or update the payroll draft for a period."""
    _, end = period_bounds(period)
    lines = []
    for r in rows:
        if not r.get('staff_id'):
            continue
        gross = _num(r.get('gross'))
        if gross <= 0:
            continue
        lines.append({
            'staff_id': int(r['staff_id']),
            'description': r.get('description') or f'Ish haqi {period}',
            'gross': gross,
            'pit': _num(r.get('pit')),
            'social': _num(r.get('social')),
            'amount': 0,
        })
    return save_document(
        {'doc_type': 'payroll', 'date': date or end, 'description': f'Ish haqi {period}',
         'notes': notes},
        lines=lines, doc_id=doc_id)


def get_payroll_for_period(period, conn=None):
    """The payroll document covering a period, if one exists."""
    own = conn is None
    conn = conn or get_db()
    try:
        row = conn.execute(
            "SELECT id FROM documents WHERE doc_type='payroll' AND period=?"
            "   AND status != 'void' ORDER BY id DESC LIMIT 1", (period,)).fetchone()
        return get_document(row['id'], conn) if row else None
    finally:
        if own:
            conn.close()


def payroll_liabilities(as_of=None):
    """What payroll still owes: net pay, PIT and social left on their accounts.

    All three should be zero once the period's remittances are posted.
    """
    from .ledger import account_balance
    return {
        'net_pay': account_balance('6710', as_of=as_of),
        'pit': account_balance('6420.1', as_of=as_of),
        'social': account_balance('6520', as_of=as_of),
    }


def build_remittance_lines(period, conn=None):
    """Suggested cash_out lines that clear a posted payroll.

    Returns three rows (net pay, PIT, social) with their accounts already
    chosen, so paying a payroll run is one click rather than three lookups.
    """
    from .ledger import account_id_for
    own = conn is None
    conn = conn or get_db()
    try:
        doc = get_payroll_for_period(period, conn)
        if not doc or doc['status'] != 'posted':
            return []
        gross = sum(_num(l['gross']) for l in doc['lines'])
        pit = sum(_num(l['pit']) for l in doc['lines'])
        social = sum(_num(l['social']) for l in doc['lines'])
        return [
            {'account_id': account_id_for('payroll_payable', conn),
             'description': f'Ish haqi {period}', 'amount': round(gross - pit, 2)},
            {'account_id': account_id_for('pit_payable', conn),
             'description': f'JSHDS {period}', 'amount': round(pit, 2)},
            {'account_id': account_id_for('social_payable', conn),
             'description': f'Ijtimoiy soliq {period}', 'amount': round(social, 2)},
        ]
    finally:
        if own:
            conn.close()


def list_payroll_periods(limit=36):
    """Every payroll document, newest first, with its totals."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT d.id, d.number, d.date, d.period, d.status, d.total,"
            " (SELECT COUNT(*) FROM document_lines WHERE document_id = d.id) AS staff_count,"
            " (SELECT COALESCE(SUM(pit),0) FROM document_lines WHERE document_id = d.id) AS pit,"
            " (SELECT COALESCE(SUM(social),0) FROM document_lines WHERE document_id = d.id) AS social"
            " FROM documents d WHERE d.doc_type='payroll'"
            " ORDER BY d.period DESC, d.id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d['net'] = _num(d['total']) - _num(d['pit'])
            d['employer_cost'] = _num(d['total']) + _num(d['social'])
            out.append(d)
        return out
    finally:
        conn.close()
