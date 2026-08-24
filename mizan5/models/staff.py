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

from .base import get_db, get_setting, get_current_usd_rate, now_ts, today_str
from .ledger import accounts_in_pool


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
    """{staff_id: hours} on billable projects, for one period or all time."""
    own = conn is None
    conn = conn or get_db()
    try:
        sql = ("SELECT ph.staff_id AS sid, COALESCE(SUM(ph.hours),0) AS h"
               " FROM project_hours ph"
               " JOIN projects p ON p.id = ph.project_id"
               " JOIN staff s ON s.id = ph.staff_id"
               " WHERE p.is_billable = 1 AND s.staff_type = 'production'")
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
NOT_EXPIRED = ("(purchase_date IS NULL OR purchase_date = '' OR"
               " date(purchase_date, '+' || lifespan_months || ' months') > date('now'))")


def personal_equipment_monthly(staff_id, conn=None):
    own = conn is None
    conn = conn or get_db()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(price * quantity / NULLIF(lifespan_months,0)),0) AS m"
            f" FROM equipment WHERE kind='personal' AND staff_id=? AND is_active=1"
            f" AND {NOT_EXPIRED}", (staff_id,)).fetchone()
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


def general_equipment_monthly(conn=None):
    own = conn is None
    conn = conn or get_db()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(price * quantity / NULLIF(lifespan_months,0)),0) AS m"
            f" FROM equipment WHERE kind='general' AND is_active=1"
            f" AND {NOT_EXPIRED}").fetchone()
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

    Returns {'monthly', 'total', 'elapsed_months', 'window_months', 'breakdown'}.
    """
    own = conn is None
    conn = conn or get_db()
    try:
        window = int(_num(get_setting('overhead_window_months', 12)) or 12)
        account_ids = accounts_in_pool('indirect', conn)
        empty = {'monthly': 0.0, 'total': 0.0, 'elapsed_months': 0,
                 'window_months': window, 'breakdown': []}
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
        elapsed = max(1, min(window, round((end - earliest).days / 30.44) or 1))
        cutoff = (end - timedelta(days=elapsed * 30.44)).strftime('%Y-%m-%d')

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
                'breakdown': breakdown}
    finally:
        if own:
            conn.close()


def overhead_monthly(conn=None, as_of=None):
    """The overhead pool actually used by the rate engine, plus its provenance."""
    use_ledger = _num(get_setting('overhead_from_ledger', 1)) >= 1
    if use_ledger:
        pool = ledger_overhead_monthly(as_of=as_of, conn=conn)
        if pool['monthly'] > 0:
            return {'monthly': pool['monthly'], 'source': 'ledger', 'detail': pool}
    budget = overhead_budget_monthly(conn)
    return {'monthly': budget, 'source': 'budget', 'detail': None}


def admin_total_cost(conn=None):
    """Fully burdened monthly cost of every active admin employee."""
    own = conn is None
    conn = conn or get_db()
    try:
        rows = conn.execute(
            "SELECT sh.base_salary, sh.premium FROM staff s"
            " JOIN salary_history sh ON sh.staff_id = s.id"
            " WHERE s.staff_type='admin' AND s.is_active=1 AND sh.end_date IS NULL"
        ).fetchall()
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


def compute_shares(conn=None, period=None):
    """Each production employee's share of the indirect pools.

    labor_cost base (default, industry standard) — share of total direct labor
    cost, so an expensive hour carries proportionally more overhead.

    hours base (v4-compatible) — share of billable hours in the most recent
    period, falling back to all-time hours and then to an equal head-count
    split, exactly as v4 did.

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
            sal = get_current_salary(s['id'], conn) or {}
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
    indirect_total = admin_share + general_eq_share + overhead_share
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


def rate_context(conn=None, period=None):
    """Everything the rate formula needs that is shared across employees.

    Resolving these once turns the rates page from O(n) pool queries per
    employee into one pass.
    """
    own = conn is None
    conn = conn or get_db()
    try:
        share_info = compute_shares(conn, period)
        overhead = overhead_monthly(conn)
        staff = share_info['staff']
        salaries = {}
        personal_eq = {}
        personal_lic = {}
        for s in staff:
            sal = get_current_salary(s['id'], conn)
            if sal:
                salaries[s['id']] = sal
            personal_eq[s['id']] = personal_equipment_monthly(s['id'], conn)
            personal_lic[s['id']] = personal_licenses_monthly(s['id'], conn)
        return {
            'staff': staff,
            'salaries': salaries,
            'shares': share_info['shares'],
            'hours': share_info['hours'],
            'labor_cost': share_info['labor_cost'],
            'total_labor_cost': share_info['total_labor_cost'],
            'total_hours': share_info['total_hours'],
            'period': share_info['period'],
            'allocation_base': share_info['base'],
            'admin_total': admin_total_cost(conn),
            'general_equipment': general_equipment_monthly(conn),
            'overhead_monthly': overhead['monthly'],
            'overhead_source': overhead['source'],
            'overhead_detail': overhead['detail'],
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
        indirect_pool = (ctx['admin_total'] + ctx['general_equipment']
                         + ctx['overhead_monthly']
                         + sum(r['personal_eq'] + r['personal_lic'] for r in priced))
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


# ========== Snapshots ==========

def snapshot_period_allocations(period):
    """Freeze every production employee's cost build-up for a period.

    Rates drift as salaries and overhead change; a closed period must keep the
    rates it was actually costed at, which is what makes historical project
    costs reproducible.
    """
    conn = get_db()
    try:
        ctx = rate_context(conn, period)
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
    """
    from .ledger import account_balance
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT kind, COALESCE(SUM(price * quantity),0) AS cost,"
            " COALESCE(SUM(CASE WHEN " + NOT_EXPIRED + " THEN"
            "   price * quantity / NULLIF(lifespan_months,0) ELSE 0 END),0) AS monthly,"
            " COUNT(*) AS n FROM equipment WHERE is_active=1 GROUP BY kind").fetchall()
    finally:
        conn.close()
    register = {r['kind']: dict(r) for r in rows}
    return {
        'register': register,
        'register_cost': sum(_num(r['cost']) for r in rows),
        'register_monthly': sum(_num(r['monthly']) for r in rows),
        'ledger_asset': account_balance('0150', as_of=as_of),
        'ledger_accumulated': account_balance('0200', as_of=as_of),
    }
