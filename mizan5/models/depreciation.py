"""Monthly depreciation of the equipment register.

Posts `Dr 9420.1 / Cr 0200` for one period, so the P&L shows a depreciation
expense and the balance sheet shows accumulated depreciation. Until this
existed, both accounts sat at zero forever and
`staff.depreciation_reconciliation()` had nothing to reconcile.

THE ONE RULE: the depreciation expense account must never carry
`cost_pool='indirect'`. The equipment register already charges these assets to
staff rates through `personal_eq` / `general_eq` in `calculate_hourly_rate()`;
if the expense also joined the overhead pool, `ledger_overhead_monthly()` would
pick up the debit while the 0200 credit (cost_pool=None) would not offset it,
and every asset would be counted twice. Measured before the split: one
15,000,000 laptop moved an employee's cost rate from 73,744 to 93,018.
`post_period_depreciation()` refuses to post if that flag is ever set.

The register stays the rate engine's source. This module exists for the
financial statements only.

Deliberate divergence from `staff.NOT_EXPIRED`: that predicate asks "is this
asset still alive TODAY" (wall clock), which is right for the rate engine and
is what the golden v4-parity test pins. A posting must instead ask "was it
alive during THAT period". The two are different on purpose — do not unify them.

Out of scope: asset disposal. Setting `is_active=0` mid-life simply stops the
charge and leaves accumulated < cost on the books. Real disposal would be
`Dr 0200 / Dr 9431.1 loss / Cr 0150` and belongs in its own routine.
"""
from calendar import monthrange

from .base import get_db, today_str, period_of, _write_audit
from .ledger import (
    PostingError, account_id_for, post_entry, reverse_entry,
)

# Memo prefix is the idempotency token — the same mechanism reopen_period()
# uses to find a closing entry. The storno memo deliberately does NOT start
# with the prefix, so a reversal can never be mistaken for a live posting.
DEPRECIATION_MEMO_PREFIX = 'Amortizatsiya'
DEPRECIATION_STORNO_MEMO = 'Storno: amortizatsiya'

EXPENSE_PURPOSE = 'depreciation_expense'
ACCUM_PURPOSE = 'equipment_depreciation'


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def period_bounds(period):
    """'YYYY-MM' → ('YYYY-MM-01', 'YYYY-MM-<last>')."""
    y, m = int(period[:4]), int(period[5:7])
    return f'{y:04d}-{m:02d}-01', f'{y:04d}-{m:02d}-{monthrange(y, m)[1]:02d}'


def _month_index(purchase_date, period):
    """1 in the asset's purchase month, 2 the next month, <= 0 before purchase.

    Depreciation starts in the month of purchase, which is what the register
    implicitly assumes — keeping register and ledger reconcilable.
    """
    py, pm = int(purchase_date[:4]), int(purchase_date[5:7])
    y, m = int(period[:4]), int(period[5:7])
    return (y * 12 + m) - (py * 12 + pm) + 1


def _accumulated(cost, lifespan_months, n):
    """Cumulative straight-line depreciation after n months, capped at cost.

    The per-period charge is the difference of two of these, never
    `round(cost / lifespan, 2)`: 15,000,000 / 36 rounds to 416,666.67, and
    36 of those overshoot cost by 12 tiyin. Taking cumulative differences makes
    "accumulated equals cost exactly in the final month" true by construction.
    """
    lifespan_months = int(lifespan_months)
    n = max(0, min(int(n), lifespan_months))
    if n >= lifespan_months:
        return round(cost, 2)
    return round(cost * n / lifespan_months, 2)


def depreciation_schedule(period, conn=None):
    """Per-asset straight-line charge for one period — as-of aware and capped.

    Every asset that cannot be depreciated is reported in 'skipped' with a
    reason rather than dropped silently. The 'no_purchase_date' bucket matters:
    the rate engine charges those assets forever (staff.NOT_EXPIRED treats a
    NULL date as never expiring), so they are a permanent, explainable gap
    between register and ledger.
    """
    own = conn is None
    conn = conn or get_db()
    try:
        rows = conn.execute(
            "SELECT e.*, s.name AS staff_name FROM equipment e"
            " LEFT JOIN staff s ON s.id = e.staff_id"
            " WHERE e.is_active = 1 ORDER BY e.kind, e.name").fetchall()
    finally:
        if own:
            conn.close()

    start, end = period_bounds(period)
    out, skipped = [], []
    register_cost = 0.0

    for r in rows:
        cost = _num(r['price']) * (r['quantity'] or 1)
        register_cost += cost
        life = int(r['lifespan_months'] or 0)
        base = {'equipment_id': r['id'], 'name': r['name'], 'kind': r['kind'],
                'staff_id': r['staff_id'], 'staff_name': r['staff_name'],
                'cost': cost}

        if cost <= 0:
            skipped.append({**base, 'reason': 'zero_cost'})
            continue
        if life <= 0:
            skipped.append({**base, 'reason': 'no_lifespan'})
            continue
        if not r['purchase_date']:
            skipped.append({**base, 'reason': 'no_purchase_date'})
            continue
        try:
            n = _month_index(r['purchase_date'], period)
        except (TypeError, ValueError, IndexError):
            # The periods page calls this on every GET; a bad date from an
            # import must not take the page down.
            skipped.append({**base, 'reason': 'bad_purchase_date'})
            continue

        if n <= 0:
            skipped.append({**base, 'reason': 'not_yet_purchased'})
            continue
        if n > life:
            skipped.append({**base, 'reason': 'fully_depreciated'})
            continue

        before = _accumulated(cost, life, n - 1)
        after = _accumulated(cost, life, n)
        out.append({
            **base,
            'quantity': r['quantity'] or 1, 'price': _num(r['price']),
            'lifespan_months': life, 'purchase_date': r['purchase_date'],
            'month_index': n, 'monthly': round(cost / life, 2),
            'charge': round(after - before, 2),
            'accumulated_before': before, 'accumulated_after': after,
            'book_value': round(cost - after, 2),
            'is_final_month': n == life,
        })

    return {'period': period, 'start': start, 'end': end,
            'rows': out, 'skipped': skipped,
            'total': round(sum(r['charge'] for r in out), 2),
            'register_cost': register_cost,
            'accumulated_after': round(sum(r['accumulated_after'] for r in out), 2)}


def depreciation_posted_entry(period, conn=None):
    """The live (un-reversed) depreciation entry id for a period, or None.

    Mirrors reopen_period()'s lookup: an entry that has been storno'd no longer
    counts, so reopening a period and re-closing it recomputes the charge from
    a corrected register instead of silently keeping the stale one.
    """
    own = conn is None
    conn = conn or get_db()
    try:
        row = conn.execute(
            "SELECT id FROM journal_entries"
            " WHERE period = ? AND memo LIKE ?"
            "   AND reversal_of_id IS NULL"
            "   AND id NOT IN (SELECT reversal_of_id FROM journal_entries"
            "                   WHERE reversal_of_id IS NOT NULL)"
            " ORDER BY id DESC LIMIT 1",
            (period, DEPRECIATION_MEMO_PREFIX + '%')).fetchone()
        return row['id'] if row else None
    finally:
        if own:
            conn.close()


def _expense_account(conn):
    """Resolve the depreciation expense account, refusing a pooled one.

    This guard is the whole feature. Without it a future admin could flip the
    account's cost_pool in the UI and quietly double every rate.
    """
    account_id = account_id_for(EXPENSE_PURPOSE, conn)
    row = conn.execute("SELECT code, cost_pool FROM accounts WHERE id=?",
                       (account_id,)).fetchone()
    if row and row['cost_pool'] == 'indirect':
        raise PostingError('depreciation_account_in_pool', row['code'])
    return account_id


def _build_lines(schedule, conn):
    """Debit per employee (personal assets) plus one general line; credit 0200.

    Personal lines carry staff_id so a future ledger-driven rate engine can
    attribute the charge per person. Per-asset lines would bloat the entry —
    the asset-level detail is reproducible from depreciation_schedule().
    """
    expense_id = _expense_account(conn)
    accum_id = account_id_for(ACCUM_PURPOSE, conn)

    by_staff, general = {}, 0.0
    names = {}
    for r in schedule['rows']:
        if r['kind'] == 'personal' and r['staff_id']:
            by_staff[r['staff_id']] = by_staff.get(r['staff_id'], 0.0) + r['charge']
            names[r['staff_id']] = r['staff_name']
        else:
            general += r['charge']

    lines = []
    for staff_id, amount in sorted(by_staff.items()):
        amount = round(amount, 2)
        if amount <= 0:
            continue
        lines.append({'account_id': expense_id, 'debit': amount,
                      'staff_id': staff_id,
                      'description': f"Amortizatsiya — {names.get(staff_id) or ''}".strip()})
    general = round(general, 2)
    if general > 0:
        lines.append({'account_id': expense_id, 'debit': general,
                      'description': 'Amortizatsiya — umumiy jihozlar'})

    # Credit the sum of the ROUNDED debits, so the entry balances exactly
    # rather than merely within post_entry's 1 UZS tolerance.
    total = round(sum(l['debit'] for l in lines), 2)
    if total > 0:
        lines.append({'account_id': accum_id, 'credit': total,
                      'description': f"Amortizatsiya {schedule['period']}"})
    return lines, total


def depreciation_preview(period=None, conn=None):
    """Dry run — the schedule plus the exact lines that WOULD be posted.

    Writes nothing. Shares _build_lines() with the poster so the preview can
    never drift from what actually posts.
    """
    period = period or period_of(today_str())
    own = conn is None
    conn = conn or get_db()
    try:
        schedule = depreciation_schedule(period, conn)
        try:
            lines, total = _build_lines(schedule, conn)
            error = None
        except PostingError as e:
            # A misconfigured account must not break the page it is shown on.
            lines, total, error = [], 0.0, e.key
        entry_id = depreciation_posted_entry(period, conn)
        expense_row = conn.execute(
            "SELECT a.code FROM account_map m JOIN accounts a ON a.id = m.account_id"
            " WHERE m.purpose=?", (EXPENSE_PURPOSE,)).fetchone()
        accum_row = conn.execute(
            "SELECT a.code FROM account_map m JOIN accounts a ON a.id = m.account_id"
            " WHERE m.purpose=?", (ACCUM_PURPOSE,)).fetchone()
    finally:
        if own:
            conn.close()

    return {'period': period, 'schedule': schedule, 'lines': lines,
            'total': total, 'error': error,
            'already_posted': entry_id is not None, 'entry_id': entry_id,
            'expense_code': expense_row['code'] if expense_row else None,
            'accum_code': accum_row['code'] if accum_row else None}


def post_period_depreciation(period, memo=None):
    """Post one period's depreciation. Owns its transaction.

    Returns (entry_id, total), or (None, 0.0) when there is nothing to
    depreciate OR the period already carries a live depreciation entry —
    running twice is a no-op, never a double charge.

    Raises PostingError('depreciation_account_in_pool') if the expense account
    has been flagged indirect, and 'ledger_period_closed' (via post_entry) for
    a closed period.
    """
    conn = get_db()
    try:
        if depreciation_posted_entry(period, conn) is not None:
            return None, 0.0

        schedule = depreciation_schedule(period, conn)
        lines, total = _build_lines(schedule, conn)
        if not lines or total <= 0:
            return None, 0.0

        _, end = period_bounds(period)
        entry_id = post_entry(
            conn, end, lines,
            memo=memo or f'{DEPRECIATION_MEMO_PREFIX} {period}')
        _write_audit(conn, 'post', 'journal_entries', entry_id,
                     context=f'depreciation {period}: {total:.0f} UZS,'
                             f" {len(schedule['rows'])} assets")
        conn.commit()
        return entry_id, total
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reverse_period_depreciation(period, conn=None, memo=None, allow_closed=True):
    """Storno a period's depreciation entry.

    Returns the reversal entry id, or None when the period has no live
    depreciation entry — that quiet None is what lets reopen_period() call this
    unconditionally on installs that own no equipment.

    Takes an optional conn because reopen_period() reverses inside its own
    transaction, alongside the closing-entry reversal.
    """
    own = conn is None
    conn = conn or get_db()
    try:
        entry_id = depreciation_posted_entry(period, conn)
        if entry_id is None:
            return None
        reversal_id = reverse_entry(
            conn, entry_id,
            memo=memo or f'{DEPRECIATION_STORNO_MEMO} {period}',
            allow_closed=allow_closed)
        _write_audit(conn, 'void', 'journal_entries', entry_id,
                     context=f'depreciation {period} reversed')
        if own:
            conn.commit()
        return reversal_id
    except Exception:
        if own:
            conn.rollback()
        raise
    finally:
        if own:
            conn.close()
