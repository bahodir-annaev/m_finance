"""Financial reports — every one a read over journal_lines.

Nothing here recomputes business logic from source documents: if the ledger is
right, the reports are right, and if a report looks wrong the fix belongs in a
posting rule. The one thing these functions do write is the period close, which
posts real closing entries rather than flipping a status flag.
"""
from calendar import monthrange
from datetime import date, datetime, timedelta

from .base import (
    get_db, now_ts, today_str, get_current_usd_rate, get_rate_for_date,
    _write_audit, _current_username, BALANCE_EPSILON,
)
from .ledger import (
    account_id_for, account_balance, natural_side, pnl_section, signed_balance,
    post_entry, monthly_turnover, balances_by_analytic, PostingError,
)


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def period_bounds(period):
    y, m = int(period[:4]), int(period[5:7])
    return f'{y:04d}-{m:02d}-01', f'{y:04d}-{m:02d}-{monthrange(y, m)[1]:02d}'


# ========== Profit & loss ==========

def get_pnl(date_from=None, date_to=None):
    """Отчёт о прибылях и убытках, built from the Т-accounts.

    Income is 90xx/92xx/93xx/95xx/97xx, expense is 91xx/94xx/96xx/98xx, and the
    difference is the period's result — the same number the close posts to 9910.
    """
    conn = get_db()
    try:
        sql = ("SELECT a.id, a.code, a.name_ru, a.name_uz, a.kind,"
               " COALESCE(SUM(jl.debit),0) AS d, COALESCE(SUM(jl.credit),0) AS c"
               " FROM accounts a JOIN journal_lines jl ON jl.account_id = a.id"
               " JOIN journal_entries je ON je.id = jl.entry_id"
               " WHERE a.kind='T'")
        params = []
        if date_from:
            sql += " AND je.date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND je.date <= ?"
            params.append(date_to)
        sql += " GROUP BY a.id ORDER BY a.code"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    income, expense, other = [], [], []
    for r in rows:
        section = pnl_section(r['code'])
        amount = signed_balance(r['code'], r['kind'], r['d'], r['c'])
        if abs(amount) < 0.005:
            continue
        item = {'code': r['code'], 'name_ru': r['name_ru'], 'name_uz': r['name_uz'],
                'amount': amount, 'debit': r['d'], 'credit': r['c']}
        if section == 'income':
            income.append(item)
        elif section == 'expense':
            expense.append(item)
        else:
            other.append(item)

    total_income = sum(i['amount'] for i in income)
    total_expense = sum(e['amount'] for e in expense)
    # Cost of sales vs period expenses, so gross margin is visible.
    cogs = sum(e['amount'] for e in expense if e['code'].startswith('91'))
    opex = total_expense - cogs
    return {
        'income': income, 'expense': expense, 'other': other,
        'total_income': total_income, 'total_expense': total_expense,
        'cogs': cogs, 'opex': opex,
        'gross_profit': total_income - cogs,
        'net_profit': total_income - total_expense,
        'margin_pct': ((total_income - total_expense) / total_income * 100)
                      if total_income else 0.0,
        'date_from': date_from, 'date_to': date_to,
    }


# ========== Balance sheet ==========

def get_balance_sheet(as_of=None):
    """Баланс: assets against liabilities, equity and the unclosed result.

    Assets net of contra-assets must equal liabilities plus equity plus the
    period's profit. That identity follows from every entry balancing, so a
    mismatch here means something bypassed post_entry().
    """
    as_of = as_of or today_str()
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT a.id, a.code, a.name_ru, a.name_uz, a.kind,"
            " COALESCE(SUM(jl.debit),0) AS d, COALESCE(SUM(jl.credit),0) AS c"
            " FROM accounts a JOIN journal_lines jl ON jl.account_id = a.id"
            " JOIN journal_entries je ON je.id = jl.entry_id"
            " WHERE je.date <= ? GROUP BY a.id ORDER BY a.code", (as_of,)).fetchall()
    finally:
        conn.close()

    assets, contra, liabilities, equity = [], [], [], []
    result = 0.0
    for r in rows:
        bal = signed_balance(r['code'], r['kind'], r['d'], r['c'])
        if abs(bal) < 0.005:
            continue
        item = {'code': r['code'], 'name_ru': r['name_ru'], 'name_uz': r['name_uz'],
                'amount': bal, 'kind': r['kind']}
        if r['kind'] == 'A':
            assets.append(item)
        elif r['kind'] == 'KA':
            contra.append(item)
        elif r['kind'] == 'P':
            # 8xxx is equity, everything else in P is a liability.
            (equity if r['code'].startswith('8') else liabilities).append(item)
        else:
            section = pnl_section(r['code'])
            if section == 'income':
                result += bal
            elif section == 'expense':
                result -= bal
            else:
                # 99xx and technical accounts sit with equity as a residual.
                equity.append(item)

    total_assets = sum(a['amount'] for a in assets) - sum(c['amount'] for c in contra)
    total_liabilities = sum(l['amount'] for l in liabilities)
    total_equity = sum(e['amount'] for e in equity)
    right_side = total_liabilities + total_equity + result
    return {
        'assets': assets, 'contra': contra,
        'liabilities': liabilities, 'equity': equity,
        'total_assets': total_assets,
        'total_liabilities': total_liabilities,
        'total_equity': total_equity,
        'period_result': result,
        'total_liabilities_equity': right_side,
        'difference': total_assets - right_side,
        'is_balanced': abs(total_assets - right_side) <= BALANCE_EPSILON,
        'as_of': as_of,
    }


# ========== Cash flow ==========

def cash_account_ids(conn=None):
    own = conn is None
    conn = conn or get_db()
    try:
        return [account_id_for(p, conn) for p in ('cash_bank', 'cash_till', 'cash_fx')]
    finally:
        if own:
            conn.close()


def get_cash_flow(date_from=None, date_to=None):
    """Direct-method monthly cash movement, straight off the money accounts.

    Inflow is every debit to 5xxx, outflow every credit, so the running balance
    is the ledger's cash balance by construction — it can never drift from the
    dashboard tile the way a reconstructed figure could.
    """
    ids = cash_account_ids()
    turnover = monthly_turnover(ids, date_from, date_to)
    opening = 0.0
    if date_from:
        conn = get_db()
        try:
            ph = ','.join('?' * len(ids))
            row = conn.execute(
                f"SELECT COALESCE(SUM(jl.debit),0) - COALESCE(SUM(jl.credit),0) AS b"
                f" FROM journal_lines jl JOIN journal_entries je ON je.id = jl.entry_id"
                f" WHERE jl.account_id IN ({ph}) AND je.date < ?",
                ids + [date_from]).fetchone()
            opening = _num(row['b'])
        finally:
            conn.close()

    rows = []
    running = opening
    for period in sorted(turnover):
        inflow = turnover[period]['debit']
        outflow = turnover[period]['credit']
        net = inflow - outflow
        running += net
        rows.append({'period': period, 'inflow': inflow, 'outflow': outflow,
                     'net': net, 'running_balance': running})
    return {'rows': rows, 'opening': opening, 'closing': running,
            'total_in': sum(r['inflow'] for r in rows),
            'total_out': sum(r['outflow'] for r in rows)}


def cash_balance(as_of=None):
    return sum(account_balance(account_id=aid, as_of=as_of) for aid in cash_account_ids())


def cash_by_account(as_of=None):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT a.code, a.name_ru, a.name_uz, a.id FROM accounts a"
            " WHERE a.code IN ('5010','5110','5210') ORDER BY a.code").fetchall()
    finally:
        conn.close()
    return [{'code': r['code'], 'name_ru': r['name_ru'], 'name_uz': r['name_uz'],
             'balance': account_balance(account_id=r['id'], as_of=as_of)} for r in rows]


# ========== Aging ==========

def get_aging(kind='ar', as_of=None):
    """Receivable or payable aging from open invoices, bucketed by days overdue.

    Buckets run from the due date where one exists, otherwise the invoice date;
    anything past 60 days counts as overdue.
    """
    as_of = as_of or today_str()
    doc_type = 'sales_invoice' if kind == 'ar' else 'purchase_invoice'
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT d.id, d.number, d.date, d.due_date, d.total, d.currency,"
            " d.description, d.project_id, c.name AS counterparty_name,"
            " d.counterparty_id, p.name AS project_name,"
            " COALESCE((SELECT SUM(amount) FROM payment_allocations"
            "   WHERE invoice_doc_id = d.id), 0) AS settled"
            " FROM documents d"
            " LEFT JOIN counterparties c ON c.id = d.counterparty_id"
            " LEFT JOIN projects p ON p.id = d.project_id"
            " WHERE d.doc_type=? AND d.status='posted' AND d.date <= ?"
            " ORDER BY d.date", (doc_type, as_of)).fetchall()
    finally:
        conn.close()

    ref = datetime.strptime(as_of, '%Y-%m-%d').date()
    buckets = {'b0_30': 0.0, 'b31_60': 0.0, 'b61_90': 0.0, 'b90_plus': 0.0}
    items = []
    for r in rows:
        outstanding = round(_num(r['total']) - _num(r['settled']), 2)
        if outstanding <= 0.005:
            continue
        basis = r['due_date'] or r['date']
        try:
            days = (ref - datetime.strptime(basis, '%Y-%m-%d').date()).days
        except (TypeError, ValueError):
            days = 0
        if days <= 30:
            key = 'b0_30'
        elif days <= 60:
            key = 'b31_60'
        elif days <= 90:
            key = 'b61_90'
        else:
            key = 'b90_plus'
        buckets[key] += outstanding
        items.append({**dict(r), 'outstanding': outstanding, 'days': days,
                      'bucket': key, 'is_overdue': days > 60})

    total = sum(buckets.values())
    return {'items': items, 'buckets': buckets, 'total': total,
            'overdue': buckets['b61_90'] + buckets['b90_plus'],
            'kind': kind, 'as_of': as_of}


def aging_by_counterparty(kind='ar', as_of=None):
    aging = get_aging(kind, as_of)
    out = {}
    for item in aging['items']:
        key = item['counterparty_id']
        b = out.setdefault(key, {'counterparty_id': key,
                                 'name': item['counterparty_name'] or '—',
                                 'total': 0.0, 'overdue': 0.0, 'count': 0})
        b['total'] += item['outstanding']
        b['count'] += 1
        if item['is_overdue']:
            b['overdue'] += item['outstanding']
    return sorted(out.values(), key=lambda x: -x['total'])


# ========== VAT ==========

def get_vat_report(date_from=None, date_to=None):
    """Output VAT (6410.1) against input VAT (4410) for a period."""
    conn = get_db()
    try:
        out_id = account_id_for('vat_output', conn)
        in_id = account_id_for('vat_input', conn)
        sql = ("SELECT jl.account_id AS aid, COALESCE(SUM(jl.debit),0) AS d,"
               " COALESCE(SUM(jl.credit),0) AS c"
               " FROM journal_lines jl JOIN journal_entries je ON je.id = jl.entry_id"
               " WHERE jl.account_id IN (?,?)")
        params = [out_id, in_id]
        if date_from:
            sql += " AND je.date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND je.date <= ?"
            params.append(date_to)
        sql += " GROUP BY jl.account_id"
        rows = {r['aid']: dict(r) for r in conn.execute(sql, params)}

        doc_sql = ("SELECT d.doc_type, d.id, d.number, d.date, d.subtotal, d.vat_amount,"
                   " d.total, c.name AS counterparty_name, c.inn"
                   " FROM documents d LEFT JOIN counterparties c ON c.id = d.counterparty_id"
                   " WHERE d.status='posted' AND d.vat_amount != 0"
                   "   AND d.doc_type IN ('sales_invoice','purchase_invoice')")
        doc_params = []
        if date_from:
            doc_sql += " AND d.date >= ?"
            doc_params.append(date_from)
        if date_to:
            doc_sql += " AND d.date <= ?"
            doc_params.append(date_to)
        doc_sql += " ORDER BY d.doc_type, d.date"
        docs = [dict(r) for r in conn.execute(doc_sql, doc_params)]
    finally:
        conn.close()

    out_row = rows.get(out_id, {'d': 0, 'c': 0})
    in_row = rows.get(in_id, {'d': 0, 'c': 0})
    output_vat = _num(out_row['c']) - _num(out_row['d'])
    input_vat = _num(in_row['d']) - _num(in_row['c'])
    return {
        'output_vat': output_vat, 'input_vat': input_vat,
        'payable': output_vat - input_vat,
        'sales': [d for d in docs if d['doc_type'] == 'sales_invoice'],
        'purchases': [d for d in docs if d['doc_type'] == 'purchase_invoice'],
        'date_from': date_from, 'date_to': date_to,
    }


# ========== FX revaluation ==========

def fx_position(as_of=None):
    """Book value against current value for every foreign-currency balance.

    A line carries the original amount_cur; revaluing it at today's rate and
    comparing with the UZS actually booked gives the unrealised difference.
    """
    as_of = as_of or today_str()
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT jl.account_id, a.code, a.name_ru, a.name_uz, a.kind, jl.currency,"
            " COALESCE(SUM(jl.debit - jl.credit),0) AS book_uzs,"
            " COALESCE(SUM(CASE WHEN jl.debit > 0 THEN jl.amount_cur"
            "                   ELSE -COALESCE(jl.amount_cur,0) END),0) AS book_cur"
            " FROM journal_lines jl JOIN journal_entries je ON je.id = jl.entry_id"
            " JOIN accounts a ON a.id = jl.account_id"
            " WHERE jl.currency IS NOT NULL AND jl.currency != 'UZS' AND je.date <= ?"
            " GROUP BY jl.account_id, jl.currency", (as_of,)).fetchall()
    finally:
        conn.close()

    current_rate = get_current_usd_rate()
    positions = []
    for r in rows:
        book_cur = _num(r['book_cur'])
        book_uzs = _num(r['book_uzs'])
        if abs(book_cur) < 0.005:
            continue
        current_value = book_cur * current_rate
        positions.append({
            'account_id': r['account_id'], 'code': r['code'],
            'name_ru': r['name_ru'], 'name_uz': r['name_uz'], 'kind': r['kind'],
            'currency': r['currency'], 'amount_cur': book_cur,
            'book_uzs': book_uzs, 'current_uzs': current_value,
            'difference': current_value - book_uzs,
        })
    return {'positions': positions, 'rate': current_rate, 'as_of': as_of,
            'total_difference': sum(p['difference'] for p in positions)}


def post_fx_revaluation(as_of=None, memo=None):
    """Book the unrealised FX difference to 9540 (gain) or 9620 (loss).

    Returns (entry_id, total) or (None, 0) when there is nothing to revalue.
    """
    as_of = as_of or today_str()
    position = fx_position(as_of)
    if not position['positions']:
        return None, 0.0

    conn = get_db()
    try:
        gain_id = account_id_for('fx_gain', conn)
        loss_id = account_id_for('fx_loss', conn)
        lines = []
        total = 0.0
        for p in position['positions']:
            diff = round(p['difference'], 2)
            if abs(diff) < 0.5:
                continue
            # A debit-natured balance gains when the currency strengthens; a
            # credit-natured one (a payable) moves the opposite way.
            debit_side = natural_side(p['code'], p['kind']) == 'debit'
            if debit_side:
                lines.append({'account_id': p['account_id'],
                              'debit': diff if diff > 0 else 0,
                              'credit': -diff if diff < 0 else 0,
                              'currency': p['currency'],
                              'description': 'Kurs farqi qayta baholash'})
            else:
                lines.append({'account_id': p['account_id'],
                              'debit': -diff if diff < 0 else 0,
                              'credit': diff if diff > 0 else 0,
                              'currency': p['currency'],
                              'description': 'Kurs farqi qayta baholash'})
            total += diff if debit_side else -diff
        if not lines:
            return None, 0.0

        total = round(total, 2)
        if total > 0:
            lines.append({'account_id': gain_id, 'credit': total,
                          'description': 'Kurs farqi foydasi'})
        else:
            lines.append({'account_id': loss_id, 'debit': -total,
                          'description': 'Kurs farqi zarari'})

        entry_id = post_entry(conn, as_of, lines,
                              memo=memo or f'Kurs farqi qayta baholash {as_of}')
        conn.commit()
        return entry_id, total
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ========== Period close ==========

def close_period(period, status='hard_closed', post_closing=True, snapshot=True):
    """Close a fiscal period: snapshot rates, close the Т-accounts, lock it.

    The closing entry moves every income and expense balance into 9910, so the
    period's result stands on one account and the next period starts clean.
    Locking is what makes post_entry() refuse later back-dating.

    Returns (result, error) with error in (None, 'already_closed', 'unbalanced').
    """
    from .staff import snapshot_period_allocations, snapshot_hours_rates
    from .base import get_period_status, ensure_fiscal_period

    if get_period_status(period) in ('soft_closed', 'hard_closed'):
        return None, 'already_closed'

    start, end = period_bounds(period)
    pnl = get_pnl(start, end)

    written = 0
    if snapshot:
        written = snapshot_period_allocations(period)
        snapshot_hours_rates(period, source='period_close')

    entry_id = None
    conn = get_db()
    try:
        ensure_fiscal_period(conn, period)
        if post_closing and (pnl['income'] or pnl['expense']):
            result_id = account_id_for('financial_result', conn)
            lines = []
            for item in pnl['income']:
                acc = conn.execute("SELECT id FROM accounts WHERE code=?",
                                   (item['code'],)).fetchone()
                lines.append({'account_id': acc['id'], 'debit': item['amount'],
                              'description': f'Yopish {period}'})
            for item in pnl['expense']:
                acc = conn.execute("SELECT id FROM accounts WHERE code=?",
                                   (item['code'],)).fetchone()
                lines.append({'account_id': acc['id'], 'credit': item['amount'],
                              'description': f'Yopish {period}'})
            net = pnl['net_profit']
            if net > 0:
                lines.append({'account_id': result_id, 'credit': net,
                              'description': f'Moliyaviy natija {period}'})
            elif net < 0:
                lines.append({'account_id': result_id, 'debit': -net,
                              'description': f'Moliyaviy natija {period}'})
            if lines:
                entry_id = post_entry(conn, end, lines,
                                      memo=f'Davr yopilishi {period}')

        conn.execute(
            "UPDATE fiscal_periods SET status=?, closed_at=?, closed_by_user=?"
            " WHERE code=?", (status, now_ts(), _current_username(), period))
        _write_audit(conn, 'close', 'fiscal_periods', None,
                     context=f'period {period} closed as {status};'
                             f' result {pnl["net_profit"]:.0f}')
        conn.commit()
    except PostingError:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {'period': period, 'status': status, 'entry_id': entry_id,
            'net_profit': pnl['net_profit'], 'snapshots': written}, None


def reopen_period(period):
    """Reopen a period and reverse its closing entry, if one was posted."""
    from .ledger import reverse_entry

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id FROM journal_entries WHERE period=? AND memo LIKE 'Davr yopilishi%'"
            "   AND reversal_of_id IS NULL"
            "   AND id NOT IN (SELECT reversal_of_id FROM journal_entries"
            "                   WHERE reversal_of_id IS NOT NULL)"
            " ORDER BY id DESC LIMIT 1", (period,)).fetchone()
        conn.execute("UPDATE fiscal_periods SET status='open', closed_at=NULL"
                     " WHERE code=?", (period,))
        if row:
            reverse_entry(conn, row['id'], memo=f'Davr qayta ochildi {period}',
                          allow_closed=True)
        _write_audit(conn, 'reopen', 'fiscal_periods', None,
                     context=f'period {period} reopened')
        conn.commit()
        return True
    finally:
        conn.close()


def list_periods(limit=36):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT fp.*,"
            " (SELECT COUNT(*) FROM journal_entries je WHERE je.period = fp.code) AS entries"
            " FROM fiscal_periods fp ORDER BY fp.code DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ========== Dashboard ==========

def get_burn_rate():
    """Monthly fixed cash cost: all-staff payroll burden plus overhead.

    Depreciation is deliberately excluded — it is a real cost but not a cash
    outflow, and runway is a cash question.
    """
    from .staff import admin_total_cost, overhead_monthly, employer_burden

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT sh.base_salary, sh.premium FROM staff s"
            " JOIN salary_history sh ON sh.staff_id = s.id"
            " WHERE s.is_active=1 AND sh.end_date IS NULL").fetchall()
        overhead = overhead_monthly(conn)
    finally:
        conn.close()
    gross = sum(_num(r['base_salary']) + _num(r['premium']) for r in rows)
    payroll = employer_burden(gross)['total']
    total = payroll + overhead['monthly']
    return {'payroll': payroll, 'gross': gross, 'overhead': overhead['monthly'],
            'overhead_source': overhead['source'], 'total': total}


def get_runway():
    burn = get_burn_rate()
    cash = cash_balance()
    months = (cash / burn['total']) if burn['total'] > 0 else None
    return {'cash': cash, 'burn': burn['total'], 'months': months,
            'burn_detail': burn}


def get_capacity():
    """Production capacity against hours actually booked on billable projects."""
    from .staff import available_hours_value, get_setting

    conn = get_db()
    try:
        staff_count = conn.execute(
            "SELECT COUNT(*) AS n FROM staff WHERE staff_type='production'"
            "   AND is_active=1").fetchone()['n']
        booked = conn.execute(
            "SELECT COALESCE(SUM(ph.hours),0) AS h FROM project_hours ph"
            " JOIN projects p ON p.id = ph.project_id"
            " WHERE p.is_billable=1 AND p.status='active'").fetchone()['h']
    finally:
        conn.close()
    months = _num(get_setting('kpi_months', 43)) or 43
    total = staff_count * available_hours_value() * months
    pct = (_num(booked) / total * 100) if total else 0.0
    return {'staff_count': staff_count, 'total_hours': total, 'booked_hours': _num(booked),
            'utilization_pct': pct, 'is_tight': pct > 85, 'months': months}


def get_dashboard():
    """Everything the landing page shows, in one pass."""
    from .projects import get_portfolio_summary

    today = today_str()
    ar = get_aging('ar', today)
    ap = get_aging('ap', today)
    year_start = f'{today[:4]}-01-01'
    pnl = get_pnl(year_start, today)
    runway = get_runway()

    conn = get_db()
    try:
        recent = [dict(r) for r in conn.execute(
            "SELECT d.id, d.doc_type, d.number, d.date, d.total, d.status,"
            " c.name AS counterparty_name FROM documents d"
            " LEFT JOIN counterparties c ON c.id = d.counterparty_id"
            " ORDER BY d.date DESC, d.id DESC LIMIT 8")]
        drafts = conn.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE status='draft'").fetchone()['n']
    finally:
        conn.close()

    return {
        'cash': runway['cash'], 'cash_accounts': cash_by_account(today),
        'receivable': ar['total'], 'receivable_overdue': ar['overdue'],
        'payable': ap['total'], 'payable_overdue': ap['overdue'],
        'pnl': pnl, 'runway': runway, 'capacity': get_capacity(),
        'portfolio': get_portfolio_summary(),
        'recent_documents': recent, 'draft_count': drafts,
        'usd_rate': get_current_usd_rate(), 'as_of': today,
    }
