"""Monthly depreciation of the equipment register.

Posts `Dr 9420.1 / Cr 0200` for one period, so the P&L shows a depreciation
expense and the balance sheet shows accumulated depreciation. Until this
existed, both accounts sat at zero forever and
`staff.depreciation_reconciliation()` had nothing to reconcile.

THE RULE (shared, see `posting.assert_not_pooled`): the depreciation expense
account must never carry `cost_pool='indirect'`. The equipment register already
charges these assets to staff rates through `personal_eq` / `general_eq` in
`calculate_hourly_rate()`; if the expense also joined the overhead pool,
`ledger_overhead_monthly()` would pick up the debit while the 0200 credit
(cost_pool=None) would not offset it, and every asset would be counted twice.
Measured before the split: one 15,000,000 laptop moved an employee's cost rate
from 73,744 to 93,018. `post_period_depreciation()` refuses to post if that
flag is ever set. The same guard now protects admin salary (9420.2) and
licenses (9420.3), which are register-modeled for exactly the same reason.

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

import re

from .base import (
    get_db, today_str, period_end, period_of, asset_class_map,
    default_lifespan_for, _write_audit,
)
from .ledger import (
    PostingError, account_id_for, get_account_by_code, post_entry, reverse_entry,
)
from .posting import assert_not_pooled

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
    return f'{period}-01', period_end(period)


# ── Asset classification ────────────────────────────────────────────────────
# Ordered rules, first match wins. Names in the register are a mix of Russian,
# Uzbek and English, and a machine's name often mentions its peripherals
# ("Ноутбук … BenQ GW2780 2x"), so the machine patterns are matched BEFORE the
# accessory ones — otherwise a laptop would be classified by its monitor.
#
# This is a suggestion engine, not an authority: classify_register() reports
# every assignment for review and anything it cannot place becomes 'other'
# rather than being guessed at.
CLASSIFY_RULES = [
    # Whole machines first — these tokens lead the name.
    ('computer', r'ноутбук|noutbuk|laptop|моноблок|monoblok|компьютер|komp[yu]?ter'
                 r'|\bпк\b|\bpc\b|imac|macbook|систем\w*\s*блок|sistem\s*blok'),
    # Then displays, peripherals and print/scan — data-processing equipment,
    # which shares the computer class (and account 0150) with the machines.
    ('computer', r'монитор|monitor|benq|dell\s*u\d|redmi\s*a\d|\bups\b|ion\s*v-'
                 r'|avt\d|ks\d{3,}|\ba-?1500\b|logitech|мышь|клав|mouse|keyboard'
                 r'|sichqon|klaviatura|принтер|printer|мфу|\bmfu\b|сканер|skaner'
                 r'|canon|epson'),
    # ИНВ-М is the register's own furniture inventory prefix (М = мебель) and is
    # the single most reliable signal in the data.
    ('furniture', r'инв-м|стол|кресло|стул|тумб|диван|шкаф|полк|мебел'
                  r'|stol|stul|shkaf|divan|kreslo|tumba|javon|mebel|sofa'
                  r'|\bdesk\b|\bchair\b|cabinet'),
    # 0140 is "мебель и офисное оборудование" — office equipment lives here too.
    ('furniture', r'телевизор|televizor|\btv\b|проектор|proyektor|projector'),
    ('vehicle', r'автомобил|avtomobil|транспорт|\bcobalt\b|\bdamas\b|\blabo\b'),
    ('machinery', r'кондиционер|kondi[ts]+ioner|станок|dastgoh|генератор|generator'),
]

DEFAULT_ASSET_CLASS = 'other'


def classify_asset(name):
    """Best-guess asset class from an asset's name, or 'other'.

    Returns (class_code, matched_rule_index or None) so a caller can tell a
    confident match from a fallback.
    """
    text = (name or '').lower()
    for i, (code, pattern) in enumerate(CLASSIFY_RULES):
        if re.search(pattern, text):
            return code, i
    return DEFAULT_ASSET_CLASS, None


def classify_register(apply=False, only_unclassified=True, conn=None):
    """Suggest an asset class for every row in the register.

    Returns a full report — one entry per asset with its current class, the
    suggested one, and whether the suggestion was a keyword match or the
    'other' fallback. Nothing is written unless apply=True.

    only_unclassified guards the common case: re-running this must not undo
    a class someone corrected by hand. Pass False to re-suggest everything.
    """
    own = conn is None
    conn = conn or get_db()
    try:
        rows = conn.execute(
            "SELECT id, name, asset_class, price, quantity FROM equipment"
            " ORDER BY id").fetchall()
        report, changed = [], 0
        for r in rows:
            current = r['asset_class'] or DEFAULT_ASSET_CLASS
            suggested, rule = classify_asset(r['name'])
            skip = only_unclassified and current not in (None, '', DEFAULT_ASSET_CLASS)
            will_change = (not skip) and suggested != current
            report.append({
                'equipment_id': r['id'], 'name': r['name'],
                'value': _num(r['price']) * (r['quantity'] or 1),
                'current': current, 'suggested': suggested,
                'matched': rule is not None,
                'changed': will_change and apply, 'skipped': skip,
            })
            if will_change and apply:
                conn.execute("UPDATE equipment SET asset_class=?, updated_at=?"
                             " WHERE id=?", (suggested, today_str(), r['id']))
                changed += 1
        if apply:
            conn.commit()
        summary = {}
        for e in report:
            key = e['suggested'] if not e['skipped'] else e['current']
            s = summary.setdefault(key, {'asset_class': key, 'n': 0, 'value': 0.0,
                                         'fallback': 0})
            s['n'] += 1
            s['value'] += e['value']
            if not e['matched']:
                s['fallback'] += 1
        return {'rows': report, 'changed': changed, 'applied': apply,
                'summary': sorted(summary.values(), key=lambda s: -s['value']),
                'unmatched': [e for e in report if not e['matched']]}
    finally:
        if own:
            conn.close()


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

    The per-row lifespan_months is authoritative, never the class default. A
    row whose life differs from its class default is reported in 'off_default'
    so the divergence is visible — editing a class default must not silently
    re-depreciate assets already on the books, but nor should the difference
    go unnoticed.
    """
    own = conn is None
    conn = conn or get_db()
    try:
        rows = conn.execute(
            "SELECT e.*, s.name AS staff_name FROM equipment e"
            " LEFT JOIN staff s ON s.id = e.staff_id"
            " WHERE e.is_active = 1 ORDER BY e.asset_class, e.kind, e.name").fetchall()
        classes = asset_class_map(conn)
    finally:
        if own:
            conn.close()

    start, end = period_bounds(period)
    out, skipped, off_default = [], [], []
    register_cost = 0.0

    for r in rows:
        cost = _num(r['price']) * (r['quantity'] or 1)
        register_cost += cost
        life = int(r['lifespan_months'] or 0)
        cls = r['asset_class'] or 'other'
        cls_row = classes.get(cls, {})
        cls_default = int(cls_row.get('default_lifespan_months') or 0)
        if life > 0 and cls_default > 0 and life != cls_default:
            off_default.append({'equipment_id': r['id'], 'name': r['name'],
                                'asset_class': cls, 'lifespan_months': life,
                                'class_default': cls_default})
        base = {'equipment_id': r['id'], 'name': r['name'], 'kind': r['kind'],
                'asset_class': cls,
                'accum_account': cls_row.get('accum_account'),
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

    by_class = {}
    for r in out:
        c = by_class.setdefault(r['asset_class'],
                                {'asset_class': r['asset_class'],
                                 'accum_account': r['accum_account'],
                                 'charge': 0.0, 'cost': 0.0, 'n': 0})
        c['charge'] += r['charge']
        c['cost'] += r['cost']
        c['n'] += 1
    for c in by_class.values():
        c['charge'] = round(c['charge'], 2)

    return {'period': period, 'start': start, 'end': end,
            'rows': out, 'skipped': skipped, 'off_default': off_default,
            'by_class': sorted(by_class.values(), key=lambda c: c['asset_class']),
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
    account's cost_pool in the UI and quietly double every rate. The check now
    lives in posting.assert_not_pooled() so payroll and licenses share it.
    """
    return assert_not_pooled(EXPENSE_PURPOSE, conn)


def _accum_account_id(accum_code, conn):
    """The class's accumulated-depreciation account, falling back to 0200.

    A class with no accum_account configured, or one naming an account that
    does not exist, must still post — landing on the 0200 parent is wrong in
    presentation but not in arithmetic, whereas refusing would block the close.
    """
    if accum_code:
        row = get_account_by_code(accum_code, conn)
        if row:
            return row['id']
    return account_id_for(ACCUM_PURPOSE, conn)


def _build_lines(schedule, conn):
    """Debit per employee (personal) plus one general line; credit per CLASS.

    Two different groupings on the two sides of the same entry, on purpose:

    - The DEBIT side groups by who bears the cost (staff / general), because
      that is the dimension the rate engine allocates on.
    - The CREDIT side groups by asset class, because each class has its own
      contra account (0250 computers, 0240 furniture, 0230 machinery …). One
      lump on 0200 cannot tell you what is worn out.

    Personal lines carry staff_id so a future ledger-driven rate engine can
    attribute the charge per person. Per-asset lines would bloat the entry —
    the asset-level detail is reproducible from depreciation_schedule().
    """
    expense_id = _expense_account(conn)

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

    total = round(sum(l['debit'] for l in lines), 2)
    if total <= 0:
        return lines, total

    # The credits must sum to the ROUNDED debit total, so the entry balances
    # exactly rather than merely within post_entry's 1 UZS tolerance. Rounding
    # each class independently can drift by a tiyin per class, so the largest
    # class absorbs the remainder.
    credits = []
    for c in schedule['by_class']:
        amount = round(c['charge'], 2)
        if amount <= 0:
            continue
        credits.append({'account_id': _accum_account_id(c['accum_account'], conn),
                        'credit': amount, 'asset_class': c['asset_class'],
                        'description': f"Amortizatsiya {schedule['period']}"
                                       f" — {c['asset_class']}"})
    if not credits:
        credits = [{'account_id': _accum_account_id(None, conn), 'credit': total,
                    'description': f"Amortizatsiya {schedule['period']}"}]
    drift = round(total - sum(c['credit'] for c in credits), 2)
    if drift:
        biggest = max(credits, key=lambda c: c['credit'])
        biggest['credit'] = round(biggest['credit'] + drift, 2)
    for c in credits:
        c.pop('asset_class', None)
    lines.extend(credits)
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

    # accum_code is the FALLBACK (0200); accum_codes is what the entry will
    # actually credit, one per asset class present in the schedule. Reporting
    # only the fallback would tell the periods page the wrong accounts.
    accum_codes = sorted({c['accum_account'] or (accum_row['code'] if accum_row else '0200')
                          for c in schedule['by_class']})
    return {'period': period, 'schedule': schedule, 'lines': lines,
            'total': total, 'error': error,
            'already_posted': entry_id is not None, 'entry_id': entry_id,
            'expense_code': expense_row['code'] if expense_row else None,
            'accum_code': accum_row['code'] if accum_row else None,
            'accum_codes': accum_codes}


def post_period_depreciation(period, memo=None):
    """Post one period's depreciation. Owns its transaction.

    Returns (entry_id, total), or (None, 0.0) when there is nothing to
    depreciate OR the period already carries a live depreciation entry —
    running twice is a no-op, never a double charge.

    Raises PostingError('account_in_pool') if the expense account
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
