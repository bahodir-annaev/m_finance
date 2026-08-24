"""The double-entry ledger: accounts, balanced posting, balances, trial balance.

post_entry() is the ONLY way a row reaches journal_entries / journal_lines.
It enforces the two rules that make the books trustworthy:

  1. Σdebit == Σcredit for every entry (within BALANCE_EPSILON).
  2. No entry may land in a closed fiscal period.

Everything else in the app reads the ledger; nothing else writes it.
"""
from .base import (
    get_db, now_ts, period_of, BALANCE_EPSILON, DEBIT_KINDS,
    _write_audit, _current_username, get_period_status, ensure_fiscal_period,
)


class PostingError(Exception):
    """Raised when an entry would violate a ledger invariant.

    Carries a translation key so controllers can show a localized message
    without parsing the text.
    """

    def __init__(self, key, detail=''):
        self.key = key
        self.detail = detail
        super().__init__(f'{key}: {detail}' if detail else key)


# ========== Account helpers ==========

def get_accounts(active_only=True, conn=None):
    own = conn is None
    conn = conn or get_db()
    try:
        where = " WHERE is_active=1" if active_only else ""
        rows = conn.execute(
            f"SELECT * FROM accounts{where} ORDER BY sort_order, code").fetchall()
        return [dict(r) for r in rows]
    finally:
        if own:
            conn.close()


def get_account_by_code(code, conn=None):
    own = conn is None
    conn = conn or get_db()
    try:
        row = conn.execute("SELECT * FROM accounts WHERE code=?", (code,)).fetchone()
        return dict(row) if row else None
    finally:
        if own:
            conn.close()


def account_id_for(purpose, conn=None):
    """Resolve a posting purpose ('ar', 'revenue', …) to an account id.

    Posting rules ask for purposes, never codes, so a firm on a different chart
    of accounts only has to re-point account_map.
    """
    own = conn is None
    conn = conn or get_db()
    try:
        row = conn.execute(
            "SELECT account_id FROM account_map WHERE purpose=?", (purpose,)).fetchone()
        if not row:
            raise PostingError('ledger_no_account_map', purpose)
        return row['account_id']
    finally:
        if own:
            conn.close()


def account_map_all(conn=None):
    """{purpose: {id, code, name_ru, kind}} for every mapped purpose."""
    own = conn is None
    conn = conn or get_db()
    try:
        rows = conn.execute(
            "SELECT m.purpose, a.id, a.code, a.name_ru, a.name_uz, a.kind"
            " FROM account_map m JOIN accounts a ON a.id = m.account_id"
        ).fetchall()
        return {r['purpose']: dict(r) for r in rows}
    finally:
        if own:
            conn.close()


def pnl_section(code):
    """Which side of the P&L a Т-account belongs to, per НСБУ code blocks.

    90xx/92xx/93xx/95xx/97xx are income, 91xx/94xx/96xx/98xx are expense, and
    99xx is the financial result itself. Кind alone cannot tell them apart —
    every one of them is Вид='Т'.
    """
    try:
        block = int(str(code)[:2])
    except (TypeError, ValueError):
        return None
    if block == 99:
        return 'result'
    if block in (90, 92, 93, 95, 97):
        return 'income'
    if block in (91, 94, 96, 98):
        return 'expense'
    return None


def natural_side(code, kind):
    """'debit' or 'credit' — the side on which this account normally carries
    its balance. Drives the sign of every balance query."""
    if kind in DEBIT_KINDS:
        return 'debit'
    if kind == 'T':
        return 'debit' if pnl_section(code) == 'expense' else 'credit'
    return 'credit'   # KA (contra-asset) and P (liability/equity)


def signed_balance(code, kind, debit, credit):
    """Balance in the account's natural direction: positive means 'normal'."""
    if natural_side(code, kind) == 'debit':
        return (debit or 0) - (credit or 0)
    return (credit or 0) - (debit or 0)


# ========== Posting ==========

def _next_entry_no(conn):
    row = conn.execute("SELECT COALESCE(MAX(entry_no), 0) + 1 AS n FROM journal_entries").fetchone()
    return row['n']


def _clean_lines(lines):
    """Normalize caller lines and drop zero rows.

    A line is {account_id, debit|credit, plus optional analytics}. Rows that
    round to nothing are dropped rather than rejected: a zero-VAT invoice
    legitimately produces a 0.00 VAT line, and refusing it would make every
    caller pre-filter.
    """
    cleaned = []
    for ln in lines or []:
        debit = round(float(ln.get('debit') or 0), 2)
        credit = round(float(ln.get('credit') or 0), 2)
        if debit < 0 or credit < 0:
            raise PostingError('ledger_negative_amount', f'{debit}/{credit}')
        if debit and credit:
            raise PostingError('ledger_both_sides')
        if debit == 0 and credit == 0:
            continue
        if not ln.get('account_id'):
            raise PostingError('ledger_no_account')
        cleaned.append({
            'account_id': int(ln['account_id']),
            'debit': debit, 'credit': credit,
            'currency': ln.get('currency'),
            'amount_cur': ln.get('amount_cur'),
            'exchange_rate': ln.get('exchange_rate'),
            'counterparty_id': ln.get('counterparty_id'),
            'project_id': ln.get('project_id'),
            'phase_id': ln.get('phase_id'),
            'staff_id': ln.get('staff_id'),
            'description': ln.get('description'),
        })
    return cleaned


def post_entry(conn, date, lines, memo=None, document_id=None,
               reversal_of_id=None, allow_closed=False):
    """Write one balanced journal entry. Does NOT commit — the caller owns the
    transaction boundary, so a document and its posting land together or not at all.

    Returns the new entry id.
    Raises PostingError for: empty entry, unbalanced entry, closed period.
    """
    if not date:
        raise PostingError('ledger_no_date')
    period = period_of(date)

    if not allow_closed:
        status = get_period_status(period)
        if status in ('soft_closed', 'hard_closed'):
            raise PostingError('ledger_period_closed', period)

    cleaned = _clean_lines(lines)
    if not cleaned:
        raise PostingError('ledger_empty_entry')

    total_debit = round(sum(l['debit'] for l in cleaned), 2)
    total_credit = round(sum(l['credit'] for l in cleaned), 2)
    if abs(total_debit - total_credit) > BALANCE_EPSILON:
        raise PostingError(
            'ledger_unbalanced',
            f'debit {total_debit:.2f} != credit {total_credit:.2f}')

    ensure_fiscal_period(conn, period)
    entry_no = _next_entry_no(conn)
    cur = conn.execute(
        "INSERT INTO journal_entries"
        " (entry_no, date, period, document_id, memo, reversal_of_id, created_by)"
        " VALUES (?,?,?,?,?,?,?)",
        (entry_no, date, period, document_id, memo, reversal_of_id, _current_username()))
    entry_id = cur.lastrowid

    for i, ln in enumerate(cleaned, start=1):
        conn.execute(
            "INSERT INTO journal_lines"
            " (entry_id, line_no, account_id, debit, credit, currency, amount_cur,"
            "  exchange_rate, counterparty_id, project_id, phase_id, staff_id, description)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (entry_id, i, ln['account_id'], ln['debit'], ln['credit'],
             ln['currency'], ln['amount_cur'], ln['exchange_rate'],
             ln['counterparty_id'], ln['project_id'], ln['phase_id'],
             ln['staff_id'], ln['description']))

    _write_audit(conn, 'post', 'journal_entries', entry_id,
                 context=f'entry #{entry_no} {date} {memo or ""} '
                         f'({total_debit:.0f} UZS, {len(cleaned)} lines)')
    return entry_id


def reverse_entry(conn, entry_id, date=None, memo=None, allow_closed=False):
    """Post the mirror image of an entry (storno). Does not commit.

    Debits become credits and vice versa, analytics are carried over, so the
    two entries net to exactly zero on every account — the invariant behind
    "void is a perfect mirror".
    """
    head = conn.execute("SELECT * FROM journal_entries WHERE id=?", (entry_id,)).fetchone()
    if not head:
        raise PostingError('ledger_entry_missing', str(entry_id))
    existing = conn.execute(
        "SELECT id FROM journal_entries WHERE reversal_of_id=?", (entry_id,)).fetchone()
    if existing:
        raise PostingError('ledger_already_reversed', str(entry_id))

    rows = conn.execute(
        "SELECT * FROM journal_lines WHERE entry_id=? ORDER BY line_no", (entry_id,)
    ).fetchall()
    mirrored = [{
        'account_id': r['account_id'],
        'debit': r['credit'], 'credit': r['debit'],
        'currency': r['currency'],
        'amount_cur': -(r['amount_cur'] or 0) if r['amount_cur'] else None,
        'exchange_rate': r['exchange_rate'],
        'counterparty_id': r['counterparty_id'], 'project_id': r['project_id'],
        'phase_id': r['phase_id'], 'staff_id': r['staff_id'],
        'description': r['description'],
    } for r in rows]

    return post_entry(
        conn, date or head['date'], mirrored,
        memo=memo or f"Storno #{head['entry_no']}",
        document_id=head['document_id'], reversal_of_id=entry_id,
        allow_closed=allow_closed)


# ========== Balances & reports ==========

def _filters(project_id=None, counterparty_id=None, staff_id=None, phase_id=None):
    """Build the analytic WHERE fragment shared by the balance queries."""
    clauses, params = [], []
    for col, val in (('project_id', project_id), ('counterparty_id', counterparty_id),
                     ('staff_id', staff_id), ('phase_id', phase_id)):
        if val is not None:
            clauses.append(f" AND jl.{col} = ?")
            params.append(val)
    return ''.join(clauses), params


def account_turnover(account_code=None, account_id=None, date_from=None, date_to=None,
                     project_id=None, counterparty_id=None, staff_id=None,
                     phase_id=None, conn=None):
    """{'debit', 'credit', 'balance'} for one account over a date window.

    balance is signed in the account's natural direction (see natural_side).
    """
    own = conn is None
    conn = conn or get_db()
    try:
        if account_id is None:
            acc = get_account_by_code(account_code, conn)
            if not acc:
                return {'debit': 0.0, 'credit': 0.0, 'balance': 0.0}
            account_id = acc['id']
            code, kind = acc['code'], acc['kind']
        else:
            row = conn.execute("SELECT code, kind FROM accounts WHERE id=?",
                               (account_id,)).fetchone()
            code, kind = (row['code'], row['kind']) if row else ('', 'A')

        sql = ("SELECT COALESCE(SUM(jl.debit),0) AS d, COALESCE(SUM(jl.credit),0) AS c"
               " FROM journal_lines jl JOIN journal_entries je ON je.id = jl.entry_id"
               " WHERE jl.account_id = ?")
        params = [account_id]
        if date_from:
            sql += " AND je.date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND je.date <= ?"
            params.append(date_to)
        extra, extra_params = _filters(project_id, counterparty_id, staff_id, phase_id)
        sql += extra
        params += extra_params

        row = conn.execute(sql, params).fetchone()
        d, c = row['d'] or 0.0, row['c'] or 0.0
        return {'debit': d, 'credit': c, 'balance': signed_balance(code, kind, d, c)}
    finally:
        if own:
            conn.close()


def account_balance(account_code=None, account_id=None, as_of=None, **kw):
    """Signed balance of one account up to and including as_of."""
    return account_turnover(account_code=account_code, account_id=account_id,
                            date_to=as_of, **kw)['balance']


def balances_by_purpose(purpose, as_of=None, **kw):
    return account_balance(account_id=account_id_for(purpose), as_of=as_of, **kw)


def get_trial_balance(date_from=None, date_to=None, include_zero=False):
    """Оборотно-сальдовая ведомость: opening / turnover / closing per account.

    Returns {'rows': [...], 'totals': {...}, 'is_balanced': bool}. Debit and
    credit totals must be equal in all three blocks — that equality is the
    rendered proof the books are intact.
    """
    conn = get_db()
    try:
        accounts = conn.execute(
            "SELECT id, code, name_ru, name_uz, kind FROM accounts ORDER BY code").fetchall()

        opening = {}
        if date_from:
            for r in conn.execute(
                "SELECT jl.account_id AS aid, COALESCE(SUM(jl.debit),0) AS d,"
                " COALESCE(SUM(jl.credit),0) AS c"
                " FROM journal_lines jl JOIN journal_entries je ON je.id = jl.entry_id"
                " WHERE je.date < ? GROUP BY jl.account_id", (date_from,)
            ):
                opening[r['aid']] = (r['d'], r['c'])

        turn_sql = ("SELECT jl.account_id AS aid, COALESCE(SUM(jl.debit),0) AS d,"
                    " COALESCE(SUM(jl.credit),0) AS c"
                    " FROM journal_lines jl JOIN journal_entries je ON je.id = jl.entry_id"
                    " WHERE 1=1")
        params = []
        if date_from:
            turn_sql += " AND je.date >= ?"
            params.append(date_from)
        if date_to:
            turn_sql += " AND je.date <= ?"
            params.append(date_to)
        turn_sql += " GROUP BY jl.account_id"
        turnover = {r['aid']: (r['d'], r['c']) for r in conn.execute(turn_sql, params)}
    finally:
        conn.close()

    rows = []
    tot = {'open_d': 0.0, 'open_c': 0.0, 'turn_d': 0.0, 'turn_c': 0.0,
           'close_d': 0.0, 'close_c': 0.0}
    for a in accounts:
        od, oc = opening.get(a['id'], (0.0, 0.0))
        td, tc = turnover.get(a['id'], (0.0, 0.0))
        if not include_zero and not (od or oc or td or tc):
            continue
        cd, cc = od + td, oc + tc
        # An account is shown on the side its net balance actually sits on, so
        # the two closing columns add up like a real trial balance.
        open_bal = signed_balance(a['code'], a['kind'], od, oc)
        close_bal = signed_balance(a['code'], a['kind'], cd, cc)
        debit_side = natural_side(a['code'], a['kind']) == 'debit'

        def split(bal):
            if debit_side:
                return (bal, 0.0) if bal >= 0 else (0.0, -bal)
            return (0.0, bal) if bal >= 0 else (-bal, 0.0)

        o_d, o_c = split(open_bal)
        c_d, c_c = split(close_bal)
        rows.append({
            'account_id': a['id'], 'code': a['code'],
            'name_ru': a['name_ru'], 'name_uz': a['name_uz'], 'kind': a['kind'],
            'open_debit': o_d, 'open_credit': o_c,
            'turn_debit': td, 'turn_credit': tc,
            'close_debit': c_d, 'close_credit': c_c,
            'balance': close_bal,
        })
        tot['open_d'] += o_d
        tot['open_c'] += o_c
        tot['turn_d'] += td
        tot['turn_c'] += tc
        tot['close_d'] += c_d
        tot['close_c'] += c_c

    is_balanced = (abs(tot['turn_d'] - tot['turn_c']) <= BALANCE_EPSILON
                   and abs(tot['close_d'] - tot['close_c']) <= BALANCE_EPSILON)
    return {'rows': rows, 'totals': tot, 'is_balanced': is_balanced}


def get_journal(date_from=None, date_to=None, account_id=None, document_id=None,
                project_id=None, counterparty_id=None, limit=200, offset=0):
    """Journal entries with their lines, newest first."""
    conn = get_db()
    try:
        sql = ("SELECT DISTINCT je.* FROM journal_entries je"
               " JOIN journal_lines jl ON jl.entry_id = je.id WHERE 1=1")
        params = []
        if date_from:
            sql += " AND je.date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND je.date <= ?"
            params.append(date_to)
        if account_id:
            sql += " AND jl.account_id = ?"
            params.append(account_id)
        if document_id:
            sql += " AND je.document_id = ?"
            params.append(document_id)
        if project_id:
            sql += " AND jl.project_id = ?"
            params.append(project_id)
        if counterparty_id:
            sql += " AND jl.counterparty_id = ?"
            params.append(counterparty_id)
        sql += " ORDER BY je.date DESC, je.entry_no DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        heads = [dict(r) for r in conn.execute(sql, params)]
        if not heads:
            return []

        ids = tuple(h['id'] for h in heads)
        ph = ','.join('?' * len(ids))
        lines = conn.execute(
            f"SELECT jl.*, a.code AS account_code, a.name_ru AS account_name,"
            f" a.name_uz AS account_name_uz,"
            f" c.name AS counterparty_name, p.name AS project_name, s.name AS staff_name"
            f" FROM journal_lines jl"
            f" JOIN accounts a ON a.id = jl.account_id"
            f" LEFT JOIN counterparties c ON c.id = jl.counterparty_id"
            f" LEFT JOIN projects p ON p.id = jl.project_id"
            f" LEFT JOIN staff s ON s.id = jl.staff_id"
            f" WHERE jl.entry_id IN ({ph}) ORDER BY jl.entry_id, jl.line_no", ids
        ).fetchall()
        docs = conn.execute(
            f"SELECT id, doc_type, number, status FROM documents"
            f" WHERE id IN (SELECT document_id FROM journal_entries WHERE id IN ({ph}))",
            ids).fetchall()
    finally:
        conn.close()

    by_entry = {}
    for l in lines:
        by_entry.setdefault(l['entry_id'], []).append(dict(l))
    doc_by_id = {d['id']: dict(d) for d in docs}
    for h in heads:
        h['lines'] = by_entry.get(h['id'], [])
        h['total'] = sum(l['debit'] for l in h['lines'])
        h['document'] = doc_by_id.get(h['document_id'])
    return heads


def get_account_ledger(account_id, date_from=None, date_to=None):
    """Running-balance statement for one account (карточка счёта)."""
    conn = get_db()
    try:
        acc = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        if not acc:
            return None
        acc = dict(acc)
        opening = 0.0
        if date_from:
            row = conn.execute(
                "SELECT COALESCE(SUM(jl.debit),0) AS d, COALESCE(SUM(jl.credit),0) AS c"
                " FROM journal_lines jl JOIN journal_entries je ON je.id = jl.entry_id"
                " WHERE jl.account_id=? AND je.date < ?", (account_id, date_from)).fetchone()
            opening = signed_balance(acc['code'], acc['kind'], row['d'], row['c'])

        sql = ("SELECT je.id AS entry_id, je.entry_no, je.date, je.memo, je.document_id,"
               " jl.debit, jl.credit, jl.description,"
               " c.name AS counterparty_name, p.name AS project_name, s.name AS staff_name"
               " FROM journal_lines jl JOIN journal_entries je ON je.id = jl.entry_id"
               " LEFT JOIN counterparties c ON c.id = jl.counterparty_id"
               " LEFT JOIN projects p ON p.id = jl.project_id"
               " LEFT JOIN staff s ON s.id = jl.staff_id"
               " WHERE jl.account_id = ?")
        params = [account_id]
        if date_from:
            sql += " AND je.date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND je.date <= ?"
            params.append(date_to)
        sql += " ORDER BY je.date, je.entry_no"
        rows = [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()

    debit_side = natural_side(acc['code'], acc['kind']) == 'debit'
    running = opening
    for r in rows:
        delta = (r['debit'] - r['credit']) if debit_side else (r['credit'] - r['debit'])
        running += delta
        r['running_balance'] = running
    return {
        'account': acc, 'rows': rows, 'opening': opening, 'closing': running,
        'total_debit': sum(r['debit'] for r in rows),
        'total_credit': sum(r['credit'] for r in rows),
    }


def balances_by_analytic(account_ids, analytic, as_of=None, min_abs=0.01):
    """Balances of one or more accounts grouped by an analytic dimension.

    analytic ∈ 'counterparty_id' | 'project_id' | 'staff_id'. Powers AR/AP by
    counterparty, project profitability and the payroll payable breakdown.
    """
    if not account_ids:
        return []
    if analytic not in ('counterparty_id', 'project_id', 'staff_id', 'phase_id'):
        raise ValueError(f'bad analytic: {analytic}')
    conn = get_db()
    try:
        ph = ','.join('?' * len(account_ids))
        sql = (f"SELECT jl.{analytic} AS key, jl.account_id,"
               f" COALESCE(SUM(jl.debit),0) AS d, COALESCE(SUM(jl.credit),0) AS c"
               f" FROM journal_lines jl JOIN journal_entries je ON je.id = jl.entry_id"
               f" WHERE jl.account_id IN ({ph})")
        params = list(account_ids)
        if as_of:
            sql += " AND je.date <= ?"
            params.append(as_of)
        sql += f" GROUP BY jl.{analytic}, jl.account_id"
        rows = conn.execute(sql, params).fetchall()
        kinds = {r['id']: (r['code'], r['kind']) for r in conn.execute(
            f"SELECT id, code, kind FROM accounts WHERE id IN ({ph})", account_ids)}
    finally:
        conn.close()

    out = {}
    for r in rows:
        code, kind = kinds.get(r['account_id'], ('', 'A'))
        bal = signed_balance(code, kind, r['d'], r['c'])
        bucket = out.setdefault(r['key'], {'key': r['key'], 'balance': 0.0,
                                           'debit': 0.0, 'credit': 0.0})
        bucket['balance'] += bal
        bucket['debit'] += r['d']
        bucket['credit'] += r['c']
    return [v for v in out.values() if abs(v['balance']) >= min_abs]


def monthly_turnover(account_ids, date_from=None, date_to=None):
    """{period 'YYYY-MM' → {'debit', 'credit'}} across the given accounts."""
    if not account_ids:
        return {}
    conn = get_db()
    try:
        ph = ','.join('?' * len(account_ids))
        sql = (f"SELECT je.period AS period, COALESCE(SUM(jl.debit),0) AS d,"
               f" COALESCE(SUM(jl.credit),0) AS c"
               f" FROM journal_lines jl JOIN journal_entries je ON je.id = jl.entry_id"
               f" WHERE jl.account_id IN ({ph})")
        params = list(account_ids)
        if date_from:
            sql += " AND je.date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND je.date <= ?"
            params.append(date_to)
        sql += " GROUP BY je.period ORDER BY je.period"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return {r['period']: {'debit': r['d'], 'credit': r['c']} for r in rows}


def accounts_in_pool(cost_pool, conn=None):
    """Account ids flagged with a given cost_pool — the rate engine's input."""
    own = conn is None
    conn = conn or get_db()
    try:
        rows = conn.execute(
            "SELECT id FROM accounts WHERE cost_pool=? AND is_active=1", (cost_pool,)
        ).fetchall()
        return [r['id'] for r in rows]
    finally:
        if own:
            conn.close()


def verify_all_entries():
    """Data-quality sweep: every entry must balance. Returns offending entries.

    post_entry() makes this impossible through the app; the check exists to
    catch direct database edits and to prove the invariant in tests.
    """
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT je.id, je.entry_no, je.date, je.memo,"
            " COALESCE(SUM(jl.debit),0) AS d, COALESCE(SUM(jl.credit),0) AS c"
            " FROM journal_entries je LEFT JOIN journal_lines jl ON jl.entry_id = je.id"
            " GROUP BY je.id HAVING ABS(d - c) > ?", (BALANCE_EPSILON,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
