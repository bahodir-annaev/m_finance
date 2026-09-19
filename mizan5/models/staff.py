"""The man-hour cost engine — the reason this application exists.

The formula chain is carried over from v4 unchanged:

    available_hours = (365 - 104 weekends - holidays - leave) / 12 * 8   ~151 h/mo
    total_monthly   = gross + tax + social + admin_share
                    + personal_equipment + personal_licenses
                    + general_equipment_share + overhead_share
    cost_rate       = total_monthly / available_hours
    billing_rate    = cost_rate * markup

Three things changed in v5, all checked against A/E industry practice
(AIA/PSMJ benchmarking, FAR Part 31 / AASHTO indirect-cost audit guides):

1. The overhead pool is read from the LEDGER — actual posted costs on accounts
   flagged cost_pool='indirect' — instead of a hand-maintained table. The
   static overhead_budget table remains as the fallback for an empty ledger.

2. The allocation base defaults to DIRECT LABOR COST rather than hours. That is
   the standard base: a senior hour consumes more overhead than a junior hour,
   which an hours base cannot express. Setting allocation_base_labor_cost=0
   restores the v4 hours base exactly — test_rates.py pins that parity.

3. Three benchmark figures the formula always implied but never showed:
   overhead rate (% of direct labor), utilization (%), and the equivalent net
   multiplier on raw labor. None of them change the arithmetic.
"""
from datetime import datetime, timedelta

from .base import (
    get_db, get_setting, get_current_usd_rate, now_ts, period_end, today_str,
)
from .ledger import accounts_in_pool


# 365.25 / 12 — the overhead window is measured in days, not calendar months,
# so that a monthly recurring cost is caught exactly once per elapsed month
# regardless of which day of the month it is posted on.
DAYS_PER_MONTH = 30.44


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


# ========== Calendar ==========

def get_available_hours():
    """Net available (chargeable) hours per month, on an annual basis.

    365 days less 104 weekend days, public holidays and average leave, spread
    over 12 months at 8 h/day — about 151 h/month or ~1,816 h/year, inside the
    1,760–1,880 band the A/E industry treats as net available hours.
    """
    holidays = _num(get_setting('holidays_per_year', 14))
    leave_days = _num(get_setting('avg_leave_days', 20))
    yearly_work_days = 365 - 104 - holidays
    net_work_days = yearly_work_days - leave_days
    monthly_work_days = net_work_days / 12
    return {
        'yearly_work_days': yearly_work_days,
        'net_work_days': net_work_days,
        'available_days': round(monthly_work_days, 1),
        'available_hours': round(monthly_work_days * 8, 1),
        'annual_hours': round(net_work_days * 8, 1),
    }


def available_hours_value():
    holidays = _num(get_setting('holidays_per_year', 14))
    leave_days = _num(get_setting('avg_leave_days', 20))
    return ((365 - 104 - holidays - leave_days) / 12) * 8


# ========== Salary ==========

def get_current_salary(staff_id, conn=None):
    """The open salary row (end_date IS NULL) for one employee."""
    own = conn is None
    conn = conn or get_db()
    try:
        row = conn.execute(
            "SELECT base_salary, premium FROM salary_history"
            " WHERE staff_id=? AND end_date IS NULL"
            " ORDER BY start_date DESC LIMIT 1", (staff_id,)).fetchone()
        return dict(row) if row else None
    finally:
        if own:
            conn.close()


def salary_at(staff_id, date_str, conn=None):
    """The salary in force on a given date — used by the payroll run."""
    own = conn is None
    conn = conn or get_db()
    try:
        row = conn.execute(
            "SELECT base_salary, premium FROM salary_history"
            " WHERE staff_id=? AND start_date <= ?"
            "   AND (end_date IS NULL OR end_date >= ?)"
            " ORDER BY start_date DESC LIMIT 1",
            (staff_id, date_str, date_str)).fetchone()
        return dict(row) if row else None
    finally:
        if own:
            conn.close()


def employer_burden(gross, tax_rate=None, social_rate=None):
    """Gross plus the employer-side statutory cost — the 'fringe' of a wrap rate."""
    tax_rate = _num(get_setting('tax_rate', 0.12)) if tax_rate is None else tax_rate
    social_rate = _num(get_setting('social_rate', 0.12)) if social_rate is None else social_rate
    gross = _num(gross)
    return {'gross': gross, 'tax': gross * tax_rate, 'social': gross * social_rate,
            'total': gross * (1 + tax_rate + social_rate)}


# ========== Hours ==========

def latest_period(conn=None):
    """Most recently imported timesheet period, or None."""
    own = conn is None
    conn = conn or get_db()
    try:
        row = conn.execute(
            "SELECT period FROM project_hours ORDER BY imported_at DESC, period DESC LIMIT 1"
        ).fetchone()
        return row['period'] if row else None
    finally:
        if own:
            conn.close()


def billable_hours_by_staff(period=None, conn=None):
    """{staff_id: hours} on billable projects, for one period or all time.

    is_active is filtered here to match _production_staff(). Without it a
    departed employee's hours sit in the allocation denominator while they
    receive no share, so that slice of the overhead pool is charged to nobody
    and the firm never recovers it — shares would sum to less than 1.
    """
    own = conn is None
    conn = conn or get_db()
    try:
        sql = ("SELECT ph.staff_id AS sid, COALESCE(SUM(ph.hours),0) AS h"
               " FROM project_hours ph"
               " JOIN projects p ON p.id = ph.project_id"
               " JOIN staff s ON s.id = ph.staff_id"
               " WHERE p.is_billable = 1 AND s.staff_type = 'production'"
               "   AND s.is_active = 1")
        params = []
        if period:
            sql += " AND ph.period = ?"
            params.append(period)
        sql += " GROUP BY ph.staff_id"
        return {r['sid']: r['h'] for r in conn.execute(sql, params)}
    finally:
        if own:
            conn.close()


# ========== Equipment, licenses, overhead ==========

# Straight-line depreciation runs only inside the asset's lifespan. Without
# this guard a fully-depreciated laptop keeps inflating its owner's monthly
# cost — and every rate quoted from it — until someone deactivates the row.
#
# The date is a bound parameter so a period snapshot can ask "was this asset
# alive at the END OF THAT PERIOD" instead of "is it alive today" — otherwise
# closing March in August costs March at August's register. It still defaults
# to today, which is what the golden v4-parity test pins.
#
# Not the same question as depreciation._month_index, which asks whether the
# asset was alive *during* a period in order to post a charge. Do not unify.
NOT_EXPIRED = ("(purchase_date IS NULL OR purchase_date = '' OR"
               " date(purchase_date, '+' || lifespan_months || ' months') > date(?))")


def _as_of(as_of=None):
    return as_of or today_str()


def personal_equipment_monthly(staff_id, conn=None, as_of=None):
    own = conn is None
    conn = conn or get_db()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(price * quantity / NULLIF(lifespan_months,0)),0) AS m"
            f" FROM equipment WHERE kind='personal' AND staff_id=? AND is_active=1"
            f" AND {NOT_EXPIRED}", (staff_id, _as_of(as_of))).fetchone()
        return _num(row['m'])
    finally:
        if own:
            conn.close()


def personal_licenses_monthly(staff_id, conn=None):
    own = conn is None
    conn = conn or get_db()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(annual_cost),0)/12.0 AS m FROM licenses"
            " WHERE staff_id=? AND is_active=1", (staff_id,)).fetchone()
        return _num(row['m'])
    finally:
        if own:
            conn.close()


def general_equipment_monthly(conn=None, as_of=None):
    own = conn is None
    conn = conn or get_db()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(price * quantity / NULLIF(lifespan_months,0)),0) AS m"
            f" FROM equipment WHERE kind='general' AND is_active=1"
            f" AND {NOT_EXPIRED}", (_as_of(as_of),)).fetchone()
        return _num(row['m'])
    finally:
        if own:
            conn.close()


def overhead_budget_monthly(conn=None):
    """The hand-maintained overhead table — the fallback source."""
    own = conn is None
    conn = conn or get_db()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(monthly_amount),0) AS m FROM overhead_budget"
            " WHERE is_active=1").fetchone()
        return _num(row['m'])
    finally:
        if own:
            conn.close()


def ledger_overhead_monthly(as_of=None, conn=None):
    """Monthly indirect cost from actually posted expenses.

    Sums net debits on every account flagged cost_pool='indirect' over a
    trailing window and divides by the months that have actually elapsed, so a
    firm with only four months of history is not divided by twelve.

    The window and the divisor are derived from ONE span, so they can never
    disagree. Deriving them separately (round() the divisor, then walk the
    cutoff back by that many months) puts the cutoff before the first posting
    whenever the rounding goes up — dividing real data by more months than
    produced it, ~25% low at the 1.5-month mark — and drops the oldest
    postings out of both the total and the breakdown when it goes down.

    Returns {'monthly', 'total', 'elapsed_months', 'window_months', 'breakdown',
             'earliest', 'cutoff', 'is_stale'}.
    """
    own = conn is None
    conn = conn or get_db()
    try:
        window = int(_num(get_setting('overhead_window_months', 12)) or 12)
        account_ids = accounts_in_pool('indirect', conn)
        empty = {'monthly': 0.0, 'total': 0.0, 'elapsed_months': 0,
                 'window_months': window, 'breakdown': [],
                 'earliest': None, 'cutoff': None, 'as_of': as_of or today_str(),
                 'is_stale': False}
        if not account_ids:
            return empty

        as_of = as_of or today_str()
        ph = ','.join('?' * len(account_ids))
        first = conn.execute(
            f"SELECT MIN(je.date) AS d FROM journal_lines jl"
            f" JOIN journal_entries je ON je.id = jl.entry_id"
            f" WHERE jl.account_id IN ({ph}) AND je.date <= ?",
            list(account_ids) + [as_of]).fetchone()
        if not first or not first['d']:
            return empty

        earliest = datetime.strptime(first['d'], '%Y-%m-%d')
        end = datetime.strptime(as_of, '%Y-%m-%d')
        # One span drives both bounds: at most `window` months, and never more
        # history than actually exists. +1 makes a same-day first posting a
        # full day of data rather than zero.
        span_days = min(window * DAYS_PER_MONTH, (end - earliest).days + 1)
        elapsed = max(1.0, span_days / DAYS_PER_MONTH)
        cutoff = (end - timedelta(days=span_days)).strftime('%Y-%m-%d')

        rows = conn.execute(
            f"SELECT a.code, a.name_ru, a.name_uz,"
            f" COALESCE(SUM(jl.debit),0) - COALESCE(SUM(jl.credit),0) AS net"
            f" FROM journal_lines jl"
            f" JOIN journal_entries je ON je.id = jl.entry_id"
            f" JOIN accounts a ON a.id = jl.account_id"
            f" WHERE jl.account_id IN ({ph}) AND je.date >= ? AND je.date <= ?"
            f" GROUP BY a.id ORDER BY net DESC",
            list(account_ids) + [cutoff, as_of]).fetchall()

        breakdown = [dict(r) for r in rows]
        total = sum(_num(r['net']) for r in breakdown)
        return {'monthly': total / elapsed, 'total': total,
                'elapsed_months': elapsed, 'window_months': window,
                'breakdown': breakdown, 'earliest': first['d'], 'cutoff': cutoff,
                'as_of': as_of,
                # Postings exist, but every one of them predates the window.
                'is_stale': not breakdown}
    finally:
        if own:
            conn.close()


# Why the ledger pool was not used, when it was not used. Rates built on the
# static table are built on OVERHEAD_SEED until someone edits it, so a silent
# fallback means confident, fabricated numbers — the UI shows this reason.
OVERHEAD_REASONS = ('ledger', 'disabled', 'ledger_empty', 'ledger_stale',
                    'pool_negative')


def overhead_monthly(conn=None, as_of=None):
    """The overhead pool actually used by the rate engine, plus its provenance.

    Returns {'monthly', 'source': 'ledger'|'budget', 'reason', 'detail'}.
    """
    if _num(get_setting('overhead_from_ledger', 1)) < 1:
        return {'monthly': overhead_budget_monthly(conn), 'source': 'budget',
                'reason': 'disabled', 'detail': None}

    pool = ledger_overhead_monthly(as_of=as_of, conn=conn)
    if pool['monthly'] > 0:
        return {'monthly': pool['monthly'], 'source': 'ledger',
                'reason': 'ledger', 'detail': pool}
    if pool['monthly'] < 0:
        # Credits outweigh debits on the pool accounts. Almost always income
        # misfiled onto 9430 — a negative overhead pool is never a real answer.
        reason = 'pool_negative'
    elif pool['is_stale']:
        reason = 'ledger_stale'
    else:
        reason = 'ledger_empty'
    return {'monthly': overhead_budget_monthly(conn), 'source': 'budget',
            'reason': reason, 'detail': pool}


def admin_total_cost(conn=None, as_of=None):
    """Fully burdened monthly cost of every active admin employee.

    This is the ONLY place admin salary enters a man-hour rate. The ledger
    posts it to 9420.2, which is deliberately outside the overhead pool, so
    that `ledger_overhead_monthly()` cannot charge it a second time.
    """
    own = conn is None
    conn = conn or get_db()
    try:
        if as_of:
            rows = conn.execute(
                "SELECT sh.base_salary, sh.premium FROM staff s"
                " JOIN salary_history sh ON sh.staff_id = s.id"
                " WHERE s.staff_type='admin' AND s.is_active=1"
                "   AND sh.start_date <= ?"
                "   AND (sh.end_date IS NULL OR sh.end_date >= ?)", (as_of, as_of)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT sh.base_salary, sh.premium FROM staff s"
                " JOIN salary_history sh ON sh.staff_id = s.id"
                " WHERE s.staff_type='admin' AND s.is_active=1"
                "   AND sh.end_date IS NULL").fetchall()
        tax = _num(get_setting('tax_rate', 0.12))
        social = _num(get_setting('social_rate', 0.12))
        return sum(_num(r['base_salary']) + _num(r['premium']) for r in rows) * (
            1 + tax + social)
    finally:
        if own:
            conn.close()


# ========== Allocation base ==========

def allocation_base_name():
    return 'labor_cost' if _num(get_setting('allocation_base_labor_cost', 1)) >= 1 else 'hours'


def _production_staff(conn):
    return [dict(r) for r in conn.execute(
        "SELECT id, name, role, department, staff_code, staff_type FROM staff"
        " WHERE staff_type='production' AND is_active=1 ORDER BY name")]


def compute_shares(conn=None, period=None, as_of=None):
    """Each production employee's share of the indirect pools.

    labor_cost base (default, industry standard) — share of total direct labor
    cost, so an expensive hour carries proportionally more overhead.

    hours base (v4-compatible) — share of billable hours in the most recent
    period, falling back to all-time hours and then to an equal head-count
    split, exactly as v4 did.

    Either way the shares sum to 1: whatever is not allocated here is overhead
    the firm never recovers in a rate.

    Returns {'shares': {staff_id: ratio}, 'base', 'period', 'hours',
             'labor_cost', 'total_labor_cost', 'total_hours'}.
    """
    own = conn is None
    conn = conn or get_db()
    try:
        staff = _production_staff(conn)
        base = allocation_base_name()
        period = period or latest_period(conn)

        labor_cost = {}
        for s in staff:
            sal = (salary_at(s['id'], as_of, conn) if as_of
                   else get_current_salary(s['id'], conn)) or {}
            gross = _num(sal.get('base_salary')) + _num(sal.get('premium'))
            labor_cost[s['id']] = employer_burden(gross)['total']
        total_labor = sum(labor_cost.values())

        period_hours = billable_hours_by_staff(period, conn) if period else {}
        total_period_hours = sum(period_hours.values())
        if total_period_hours <= 0:
            period_hours = billable_hours_by_staff(None, conn)
            total_period_hours = sum(period_hours.values())
        hours = {s['id']: _num(period_hours.get(s['id'])) for s in staff}

        shares = {}
        if base == 'labor_cost' and total_labor > 0:
            for s in staff:
                shares[s['id']] = labor_cost[s['id']] / total_labor
        elif total_period_hours > 0:
            for s in staff:
                shares[s['id']] = hours[s['id']] / total_period_hours
        else:
            n = max(len(staff), 1)
            for s in staff:
                shares[s['id']] = 1.0 / n

        return {'shares': shares, 'base': base, 'period': period,
                'hours': hours, 'labor_cost': labor_cost,
                'total_labor_cost': total_labor, 'total_hours': total_period_hours,
                'staff': staff}
    finally:
        if own:
            conn.close()


# ========== The rate ==========

def calculate_hourly_rate(staff_id, conn=None, context=None):
    """Full cost build-up and hourly rates for one employee.

    context is an optional pre-computed dict from rate_context() — pass it when
    looping over many employees so the shared pools are resolved once instead
    of once per person.

    Returns None when the employee has no salary on record; there is no
    meaningful rate without one, and inventing zero would quietly understate
    every project it touches.
    """
    own_ctx = context is None
    ctx = context or rate_context(conn=conn)

    sal = ctx['salaries'].get(staff_id)
    if sal is None:
        return None

    gross = _num(sal.get('base_salary')) + _num(sal.get('premium'))
    burden = employer_burden(gross, ctx['tax_rate'], ctx['social_rate'])
    share = ctx['shares'].get(staff_id, 0.0)

    admin_share = ctx['admin_total'] * share
    general_eq_share = ctx['general_equipment'] * share
    overhead_share = ctx['overhead_monthly'] * share
    personal_eq = ctx['personal_equipment'].get(staff_id, 0.0)
    personal_lic = ctx['personal_licenses'].get(staff_id, 0.0)

    total_monthly = (burden['total'] + admin_share + personal_eq + personal_lic
                     + general_eq_share + overhead_share)

    available = ctx['available_hours']
    cost_rate = total_monthly / available if available else 0.0
    billing_rate = cost_rate * ctx['markup']

    # Benchmark figures — reporting only, they never feed the arithmetic above.
    raw_labor_rate = gross / available if available else 0.0
    # Everything in the build-up that is not this person's own direct labor.
    # Personal equipment and licenses belong here: they are indirect cost by
    # the A/E definition, assigned rather than allocated. Excluding them would
    # make overhead_rate_pct disagree with the firm-level figure computed in
    # get_rates_overview(), while both are read against the same 150–180% band.
    indirect_total = (admin_share + general_eq_share + overhead_share
                      + personal_eq + personal_lic)
    result = {
        'staff_id': staff_id,
        'base_salary': _num(sal.get('base_salary')),
        'premium': _num(sal.get('premium')),
        'gross': gross,
        'tax': burden['tax'],
        'social': burden['social'],
        'direct_labor_cost': burden['total'],
        'admin_share': admin_share,
        'personal_eq': personal_eq,
        'personal_lic': personal_lic,
        'general_eq': general_eq_share,
        'overhead_share': overhead_share,
        'indirect_total': indirect_total,
        'total_monthly': total_monthly,
        'available_hours': available,
        'markup': ctx['markup'],
        'cost_rate': cost_rate,
        'billing_rate': billing_rate,
        'share_ratio': share,
        'allocation_base': ctx['allocation_base'],
        'overhead_source': ctx['overhead_source'],
        'billable_hours': ctx['hours'].get(staff_id, 0.0),
        'utilization_pct': (ctx['hours'].get(staff_id, 0.0) / available * 100
                            if available else 0.0),
        'overhead_rate_pct': (indirect_total / burden['total'] * 100
                              if burden['total'] else 0.0),
        'net_multiplier': billing_rate / raw_labor_rate if raw_labor_rate else 0.0,
        'raw_labor_rate': raw_labor_rate,
        'cost_usd': cost_rate / ctx['usd_rate'] if ctx['usd_rate'] else 0.0,
        'billing_usd': billing_rate / ctx['usd_rate'] if ctx['usd_rate'] else 0.0,
    }
    if own_ctx:
        result['context'] = None
    return result


def rate_context(conn=None, period=None, as_of=None):
    """Everything the rate formula needs that is shared across employees.

    Resolving these once turns the rates page from O(n) pool queries per
    employee into one pass.

    as_of costs the firm as it stood on a given date rather than today —
    salaries in force then, the overhead window ending then, assets alive then.
    It defaults to None (= today), which is what /rates shows and what the
    golden v4-parity test pins; only snapshot_period_allocations() sets it, so
    that closing a back-dated period freezes that period's cost rather than
    whatever the register happens to say on the day someone runs the close.
    """
    own = conn is None
    conn = conn or get_db()
    try:
        share_info = compute_shares(conn, period, as_of)
        overhead = overhead_monthly(conn, as_of=as_of)
        staff = share_info['staff']
        salaries = {}
        personal_eq = {}
        personal_lic = {}
        for s in staff:
            sal = (salary_at(s['id'], as_of, conn) if as_of
                   else get_current_salary(s['id'], conn))
            if sal:
                salaries[s['id']] = sal
            personal_eq[s['id']] = personal_equipment_monthly(s['id'], conn, as_of)
            personal_lic[s['id']] = personal_licenses_monthly(s['id'], conn)
        # Every closed period, resolved once: the actuals loops ask "is this
        # month closed?" per timesheet row, and opening a connection each
        # time was most of the budget page's render time.
        closed_periods = frozenset(r['code'] for r in conn.execute(
            "SELECT code FROM fiscal_periods WHERE status IN ('soft_closed','hard_closed')"))
        return {
            'staff': staff,
            'salaries': salaries,
            'closed_periods': closed_periods,
            'shares': share_info['shares'],
            'hours': share_info['hours'],
            'labor_cost': share_info['labor_cost'],
            'total_labor_cost': share_info['total_labor_cost'],
            'total_hours': share_info['total_hours'],
            'period': share_info['period'],
            'allocation_base': share_info['base'],
            'admin_total': admin_total_cost(conn, as_of),
            'general_equipment': general_equipment_monthly(conn, as_of),
            'overhead_monthly': overhead['monthly'],
            'overhead_source': overhead['source'],
            'overhead_reason': overhead['reason'],
            'overhead_detail': overhead['detail'],
            'as_of': as_of,
            'personal_equipment': personal_eq,
            'personal_licenses': personal_lic,
            'available_hours': available_hours_value(),
            'markup': _num(get_setting('billing_multiplier', 2.0)) or 2.0,
            'tax_rate': _num(get_setting('tax_rate', 0.12)),
            'social_rate': _num(get_setting('social_rate', 0.12)),
            'usd_rate': get_current_usd_rate(),
        }
    finally:
        if own:
            conn.close()


def get_production_staff_rates(conn=None):
    """Every active production employee with their current rates."""
    own = conn is None
    conn = conn or get_db()
    try:
        ctx = rate_context(conn)
        out = []
        for s in ctx['staff']:
            info = calculate_hourly_rate(s['id'], context=ctx)
            row = {'id': s['id'], 'name': s['name'], 'role': s['role'],
                   'department': s['department'], 'staff_code': s['staff_code'],
                   'cost_rate': 0.0, 'billing_rate': 0.0, 'has_salary': info is not None}
            if info:
                row.update(info)
            out.append(row)
        return out
    finally:
        if own:
            conn.close()


def get_rates_overview(period=None):
    """The rates page: per-employee build-up plus firm-level benchmarks.

    The firm overhead rate is the whole indirect pool over the whole direct
    labor base — the number A/E firms benchmark at roughly 150–180%.
    """
    conn = get_db()
    try:
        ctx = rate_context(conn, period)
        rows = []
        for s in ctx['staff']:
            info = calculate_hourly_rate(s['id'], context=ctx)
            if info is None:
                rows.append({'id': s['id'], 'name': s['name'], 'role': s['role'],
                             'department': s['department'],
                             'staff_code': s['staff_code'], 'has_salary': False})
                continue
            rows.append({**info, 'id': s['id'], 'name': s['name'], 'role': s['role'],
                         'department': s['department'],
                         'staff_code': s['staff_code'], 'has_salary': True})

        priced = [r for r in rows if r.get('has_salary')]
        direct_labor = sum(r['direct_labor_cost'] for r in priced)
        # Summed from the rows, not rebuilt from ctx: the rows are what the
        # page shows, and this must be their total or the firm benchmark and
        # the per-row percentages would be read off two different definitions.
        indirect_pool = sum(r['indirect_total'] for r in priced)
        total_cost = sum(r['total_monthly'] for r in priced)
        total_hours = sum(r['billable_hours'] for r in priced)
        capacity = ctx['available_hours'] * len(priced)
        avg_cost = (total_cost / (capacity or 1)) if priced else 0.0
        return {
            'rows': rows,
            'context': ctx,
            'period': ctx['period'],
            'allocation_base': ctx['allocation_base'],
            'overhead_source': ctx['overhead_source'],
            'overhead_reason': ctx['overhead_reason'],
            'overhead_detail': ctx['overhead_detail'],
            'available_hours': ctx['available_hours'],
            'markup': ctx['markup'],
            'staff_count': len(rows),
            'priced_count': len(priced),
            'direct_labor_total': direct_labor,
            'indirect_pool_total': indirect_pool,
            'admin_total': ctx['admin_total'],
            'overhead_monthly': ctx['overhead_monthly'],
            'general_equipment': ctx['general_equipment'],
            'total_monthly_cost': total_cost,
            # The headline benchmark: indirect cost as a % of direct labor
            'firm_overhead_rate_pct': (indirect_pool / direct_labor * 100
                                       if direct_labor else 0.0),
            'firm_utilization_pct': (total_hours / capacity * 100) if capacity else 0.0,
            'avg_cost_rate': avg_cost,
            'avg_billing_rate': avg_cost * ctx['markup'],
            'billable_hours_total': total_hours,
            'capacity_hours': capacity,
            'usd_rate': ctx['usd_rate'],
        }
    finally:
        conn.close()


def get_staff_kpi(ctx=None):
    """Per-employee productivity over the KPI window (v4's /kpi page).

    utilisation = lifetime billable hours / (kpi_months × available hours);
    cost value = hours × cost rate; revenue value = hours × billing rate. The
    rating is the v4 five-band scale on utilisation. Reporting only — nothing
    here feeds a rate.
    """
    conn = get_db()
    try:
        ctx = ctx or rate_context(conn)
        hours = {r['staff_id']: r for r in conn.execute(
            "SELECT staff_id, SUM(hours) AS total, COUNT(DISTINCT project_id) AS projects"
            " FROM project_hours WHERE hours > 0 GROUP BY staff_id")}
    finally:
        conn.close()

    kpi_months = _num(get_setting('kpi_months', 43)) or 43
    available = ctx['available_hours']
    usd_rate = ctx['usd_rate'] or 1
    window = kpi_months * available

    out = []
    for s in ctx['staff']:
        info = calculate_hourly_rate(s['id'], context=ctx)
        cost_rate = info['cost_rate'] if info else 0.0
        billing_rate = info['billing_rate'] if info else 0.0
        h = hours.get(s['id'])
        total_hours = _num(h['total']) if h else 0.0
        project_count = int(h['projects']) if h else 0
        utilization = total_hours / window if window > 0 else 0.0
        revenue_value = total_hours * billing_rate
        cost_value = total_hours * cost_rate
        if utilization >= 0.85:
            rating = 5
        elif utilization >= 0.70:
            rating = 4
        elif utilization >= 0.55:
            rating = 3
        elif utilization >= 0.35:
            rating = 2
        else:
            rating = 1
        out.append({
            'id': s['id'], 'staff_code': s['staff_code'] or f"MZ-{s['id']:03d}",
            'name': s['name'], 'role': s['role'], 'department': s['department'],
            'has_salary': info is not None,
            'projects': project_count, 'hours': total_hours,
            'avg_hours': total_hours / project_count if project_count else 0.0,
            'cost_rate': cost_rate, 'billing_rate': billing_rate,
            'cost_value': cost_value, 'revenue_value': revenue_value,
            'net_margin': revenue_value - cost_value,
            'cost_usd': cost_value / usd_rate, 'revenue_usd': revenue_value / usd_rate,
            'utilization': utilization, 'utilization_pct': utilization * 100,
            'profit_per_hour': (billing_rate - cost_rate) if cost_rate > 0 else 0.0,
            'rating': rating,
        })
    out.sort(key=lambda x: x['hours'], reverse=True)
    return {'rows': out, 'kpi_months': kpi_months, 'available_hours': available,
            'window_hours': window,
            'total_hours': sum(r['hours'] for r in out),
            'total_cost_value': sum(r['cost_value'] for r in out),
            'total_revenue_value': sum(r['revenue_value'] for r in out)}


# ========== Snapshots ==========

def snapshot_period_allocations(period):
    """Freeze every production employee's cost build-up for a period.

    Rates drift as salaries and overhead change; a closed period must keep the
    rates it was actually costed at, which is what makes historical project
    costs reproducible.

    Costed AS OF the last day of the period, not as of today. Closing March in
    August must freeze March's salaries and March's overhead window; without
    the as_of the snapshot would stamp August's figures onto March and the
    "history never moves" guarantee would only hold for periods closed on time.
    """
    conn = get_db()
    try:
        ctx = rate_context(conn, period, as_of=period_end(period))
        stamp = now_ts()
        written = 0
        for s in ctx['staff']:
            info = calculate_hourly_rate(s['id'], context=ctx)
            if info is None:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO period_allocations"
                " (period, staff_id, billable_hours, hours_share, labor_cost_share,"
                "  admin_share_amount, overhead_share_amount,"
                "  general_equipment_share_amount, personal_equipment_amount,"
                "  personal_licenses_amount, gross_salary, tax_amount, social_amount,"
                "  total_monthly_cost, cost_rate, billing_rate, available_hours,"
                "  overhead_rate_pct, utilization_pct, net_multiplier,"
                "  allocation_base, overhead_source, snapshotted_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (period, s['id'], info['billable_hours'],
                 info['share_ratio'] if ctx['allocation_base'] == 'hours' else 0.0,
                 info['share_ratio'] if ctx['allocation_base'] == 'labor_cost' else 0.0,
                 info['admin_share'], info['overhead_share'], info['general_eq'],
                 info['personal_eq'], info['personal_lic'], info['gross'],
                 info['tax'], info['social'], info['total_monthly'],
                 info['cost_rate'], info['billing_rate'], info['available_hours'],
                 info['overhead_rate_pct'], info['utilization_pct'],
                 info['net_multiplier'], ctx['allocation_base'],
                 ctx['overhead_source'], stamp))
            written += 1
        conn.commit()
        return written
    finally:
        conn.close()


def snapshot_hours_rates(period, source='period_close'):
    """Write applied_* cost columns onto the period's timesheet rows."""
    conn = get_db()
    try:
        ctx = rate_context(conn, period)
        rows = conn.execute(
            "SELECT id, staff_id, hours FROM project_hours WHERE period=?",
            (period,)).fetchall()
        stamp = now_ts()
        cache = {}
        written = 0
        for r in rows:
            sid = r['staff_id']
            if sid not in cache:
                info = calculate_hourly_rate(sid, context=ctx)
                cache[sid] = (info['cost_rate'], info['billing_rate']) if info else (0, 0)
            cost_rate, billing_rate = cache[sid]
            conn.execute(
                "UPDATE project_hours SET applied_cost_rate=?, applied_billing_rate=?,"
                " applied_cost_amount=?, applied_billing_amount=?,"
                " rate_snapshot_at=?, rate_snapshot_source=? WHERE id=?",
                (cost_rate, billing_rate, _num(r['hours']) * cost_rate,
                 _num(r['hours']) * billing_rate, stamp, source, r['id']))
            written += 1
        conn.commit()
        return written
    finally:
        conn.close()


def get_period_snapshot(period):
    """The frozen allocations for a period, joined to staff names."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT pa.*, s.name, s.role, s.staff_code FROM period_allocations pa"
            " JOIN staff s ON s.id = pa.staff_id WHERE pa.period=? ORDER BY s.name",
            (period,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ========== Depreciation reconciliation ==========

def depreciation_reconciliation(as_of=None):
    """Equipment register vs the ledger's accumulated depreciation.

    The register drives the rate engine; the ledger is what the accounts show.
    They should agree, and a visible gap is the point of this report.

    `uncapitalized` is the headline gap: nothing posts to 0150 yet, so an asset
    bought through a purchase invoice is expensed to 9420 — the overhead pool —
    while the register depreciates the same asset into the same rates. That is
    the double count 9420.1 exists to prevent, arriving through the other door.
    Until capitalization exists the number is a warning, not a reconciliation.
    """
    from .base import asset_class_map
    from .ledger import account_balance
    conn = get_db()
    try:
        monthly = ("COALESCE(SUM(CASE WHEN " + NOT_EXPIRED + " THEN"
                   "   price * quantity / NULLIF(lifespan_months,0) ELSE 0 END),0)")
        rows = conn.execute(
            "SELECT kind, COALESCE(SUM(price * quantity),0) AS cost,"
            f" {monthly} AS monthly,"
            " COUNT(*) AS n FROM equipment WHERE is_active=1 GROUP BY kind",
            (_as_of(as_of),)).fetchall()
        cls_rows = conn.execute(
            "SELECT asset_class, COALESCE(SUM(price * quantity),0) AS cost,"
            f" {monthly} AS monthly,"
            " COUNT(*) AS n FROM equipment WHERE is_active=1"
            " GROUP BY asset_class ORDER BY asset_class", (_as_of(as_of),)).fetchall()
        classes = asset_class_map(conn)
    finally:
        conn.close()

    # Per class, the register against its OWN pair of accounts. Summing 0150
    # alone would call every desk uncapitalized once furniture books to 0140.
    by_class = []
    for r in cls_rows:
        c = classes.get(r['asset_class'], {})
        cost = _num(r['cost'])
        asset_bal = account_balance(c['asset_account'], as_of=as_of) if c.get(
            'asset_account') else 0.0
        by_class.append({
            'asset_class': r['asset_class'], 'n': r['n'], 'cost': cost,
            'monthly': _num(r['monthly']),
            'asset_account': c.get('asset_account'),
            'accum_account': c.get('accum_account'),
            'ledger_asset': asset_bal,
            'ledger_accumulated': account_balance(c['accum_account'], as_of=as_of)
                                  if c.get('accum_account') else 0.0,
            'uncapitalized': cost - asset_bal,
        })

    register = {r['kind']: dict(r) for r in rows}
    register_cost = sum(_num(r['cost']) for r in rows)
    ledger_asset = sum(c['ledger_asset'] for c in by_class)
    # 0200 is still summed in: it is the fallback for a class with no accum
    # account configured, so a charge can legitimately land there.
    ledger_accum = (sum(c['ledger_accumulated'] for c in by_class)
                    + account_balance('0200', as_of=as_of))
    return {
        'register': register,
        'by_class': by_class,
        'register_cost': register_cost,
        'register_monthly': sum(_num(r['monthly']) for r in rows),
        'ledger_asset': ledger_asset,
        'ledger_accumulated': ledger_accum,
        'uncapitalized': register_cost - ledger_asset,
    }
