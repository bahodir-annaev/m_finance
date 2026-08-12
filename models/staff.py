"""Staff costs, hourly rate calculation, KPI, proportional cost allocation, and snapshots."""
from datetime import datetime
from .base import get_db, get_setting, get_current_usd_rate, is_period_closed
from .equipment import (
    get_personal_equipment_monthly, get_personal_license_monthly,
    get_general_equipment_monthly,
)


def get_available_hours():
    holidays = get_setting('holidays_per_year') or 14
    leave_days = get_setting('avg_leave_days') or 20
    util_rate = get_setting('utilization_rate') or 0.75
    yearly_work_days = 365 - 104 - holidays
    net_work_days = yearly_work_days - leave_days
    monthly_work_days = net_work_days / 12
    available_hours = monthly_work_days * 8
    return {
        'yearly_work_days': yearly_work_days,
        'net_work_days': net_work_days,
        'available_days': round(monthly_work_days, 1),
        'available_hours': round(available_hours, 1),
        'utilization_rate': util_rate,
    }


def count_staff_by_type(staff_type):
    conn = get_db()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM staff WHERE staff_type=? AND is_active=1", (staff_type,)
    ).fetchone()
    conn.close()
    return row['cnt']


def _get_latest_period():
    conn = get_db()
    row = conn.execute(
        "SELECT period FROM project_hours ORDER BY imported_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return row['period'] if row else 'all'


def get_total_billable_hours(period=None):
    conn = get_db()
    if period:
        row = conn.execute('''
            SELECT COALESCE(SUM(ph.hours), 0) as total
            FROM project_hours ph
            JOIN projects p ON ph.project_id = p.id
            JOIN staff s ON ph.staff_id = s.id
            WHERE p.is_billable = 1 AND s.staff_type = 'production' AND ph.period = ?
        ''', (period,)).fetchone()
    else:
        row = conn.execute('''
            SELECT COALESCE(SUM(ph.hours), 0) as total
            FROM project_hours ph
            JOIN projects p ON ph.project_id = p.id
            JOIN staff s ON ph.staff_id = s.id
            WHERE p.is_billable = 1 AND s.staff_type = 'production'
        ''').fetchone()
    conn.close()
    return row['total'] if row['total'] > 0 else 1


def get_staff_billable_hours(staff_id, period=None):
    conn = get_db()
    if period:
        row = conn.execute('''
            SELECT COALESCE(SUM(ph.hours), 0) as total
            FROM project_hours ph
            JOIN projects p ON ph.project_id = p.id
            WHERE ph.staff_id = ? AND p.is_billable = 1 AND ph.period = ?
        ''', (staff_id, period)).fetchone()
    else:
        row = conn.execute('''
            SELECT COALESCE(SUM(ph.hours), 0) as total
            FROM project_hours ph
            JOIN projects p ON ph.project_id = p.id
            WHERE ph.staff_id = ? AND p.is_billable = 1
        ''', (staff_id,)).fetchone()
    conn.close()
    return row['total']


def get_admin_total_cost():
    conn = get_db()
    rows = conn.execute('''
        SELECT sh.base_salary, sh.premium
        FROM staff s
        JOIN salary_history sh ON s.id = sh.staff_id
        WHERE s.staff_type = 'admin' AND s.is_active = 1 AND sh.end_date IS NULL
    ''').fetchall()
    conn.close()
    tax = get_setting('tax_rate')
    social = get_setting('social_rate')
    total = 0
    for r in rows:
        base = r['base_salary'] + r['premium']
        total += base + base * tax + base * social
    return total


def get_total_overhead():
    conn = get_db()
    row = conn.execute("SELECT SUM(monthly_amount) as total FROM overhead WHERE is_active=1").fetchone()
    conn.close()
    return row['total'] or 0


def get_indirect_pool_monthly():
    """Trailing-window average of actual internal cash costs not already
    modeled via salary_history/tax_rate (excludes maosh, soliq).
    Falls back to fewer months if insufficient history exists."""
    from .base import INDIRECT_POOL_EXCLUDED_TX_TYPES
    from datetime import datetime, timedelta

    window_months = get_setting('indirect_pool_window_months') or 12
    conn = get_db()

    min_date = conn.execute(
        "SELECT MIN(date) as d FROM transactions WHERE direction='internal' AND paid > 0"
    ).fetchone()
    conn.close()

    if not min_date or not min_date['d']:
        return 0

    earliest = datetime.strptime(min_date['d'], '%Y-%m-%d')
    today = datetime.now()
    elapsed_months = max(
        1,
        min(window_months, round((today - earliest).days / 30.44))
    )

    excluded_sql = "(" + ",".join(f"'{t}'" for t in INDIRECT_POOL_EXCLUDED_TX_TYPES) + ")"
    cutoff = (today - timedelta(days=elapsed_months * 30.44)).strftime('%Y-%m-%d')

    conn = get_db()
    row = conn.execute(f"""
        SELECT COALESCE(SUM(paid), 0) as total
        FROM transactions
        WHERE direction='internal' AND paid > 0 AND tx_type NOT IN {excluded_sql}
              AND date >= ?
    """, (cutoff,)).fetchone()
    conn.close()

    return (row['total'] or 0) / elapsed_months


def get_indirect_pool_breakdown():
    """Per-tx_type breakdown of indirect pool costs for reconciliation view."""
    from .base import INDIRECT_POOL_EXCLUDED_TX_TYPES
    from datetime import datetime, timedelta

    window_months = get_setting('indirect_pool_window_months') or 12
    conn = get_db()

    min_date = conn.execute(
        "SELECT MIN(date) as d FROM transactions WHERE direction='internal' AND paid > 0"
    ).fetchone()
    conn.close()

    if not min_date or not min_date['d']:
        return []

    earliest = datetime.strptime(min_date['d'], '%Y-%m-%d')
    today = datetime.now()
    elapsed_months = max(
        1,
        min(window_months, round((today - earliest).days / 30.44))
    )

    excluded_sql = "(" + ",".join(f"'{t}'" for t in INDIRECT_POOL_EXCLUDED_TX_TYPES) + ")"
    cutoff = (today - timedelta(days=elapsed_months * 30.44)).strftime('%Y-%m-%d')

    conn = get_db()
    rows = conn.execute(f"""
        SELECT tx_type, COALESCE(SUM(paid), 0) as total, COUNT(*) as count
        FROM transactions
        WHERE direction='internal' AND paid > 0 AND tx_type NOT IN {excluded_sql}
              AND date >= ?
        GROUP BY tx_type ORDER BY total DESC
    """, (cutoff,)).fetchall()
    conn.close()

    return [dict(r) for r in rows]


def get_indirect_pool_info():
    """Returns reconciliation view data: monthly average, breakdown, elapsed window."""
    monthly = get_indirect_pool_monthly()
    breakdown = get_indirect_pool_breakdown()

    # Calculate elapsed_months to match what was used in pool calculation
    from datetime import datetime, timedelta
    from .base import INDIRECT_POOL_EXCLUDED_TX_TYPES

    window_months = get_setting('indirect_pool_window_months') or 12
    conn = get_db()
    min_date = conn.execute(
        "SELECT MIN(date) as d FROM transactions WHERE direction='internal' AND paid > 0"
    ).fetchone()
    conn.close()

    elapsed_months = window_months
    if min_date and min_date['d']:
        earliest = datetime.strptime(min_date['d'], '%Y-%m-%d')
        today = datetime.now()
        elapsed_months = max(
            1,
            min(window_months, round((today - earliest).days / 30.44))
        )

    return {
        'monthly_average': monthly,
        'breakdown': breakdown,
        'elapsed_months': elapsed_months,
        'window_months': window_months,
    }


def _proportional_share(total_cost, staff_id):
    """Distribute total_cost across production staff proportional to current-period hours."""
    period = _get_latest_period()
    total_hours = get_total_billable_hours(period)
    staff_hours = get_staff_billable_hours(staff_id, period)
    if total_hours <= 1:
        total_hours = get_total_billable_hours()
        staff_hours = get_staff_billable_hours(staff_id)
    if total_hours <= 1:
        prod_count = count_staff_by_type('production')
        return total_cost / max(prod_count, 1)
    return total_cost * (staff_hours / total_hours)


def get_admin_share_for_staff(staff_id):
    return _proportional_share(get_admin_total_cost(), staff_id)


def get_overhead_share_for_staff(staff_id):
    return _proportional_share(get_total_overhead(), staff_id)


def get_general_equipment_share_for_staff(staff_id):
    return _proportional_share(get_general_equipment_monthly(), staff_id)


def calculate_hourly_rate(staff_id):
    conn = get_db()
    sal = conn.execute(
        "SELECT base_salary, premium FROM salary_history WHERE staff_id=? AND end_date IS NULL",
        (staff_id,)
    ).fetchone()
    # Load all required settings in one query instead of six separate get_setting() calls
    settings_rows = conn.execute(
        "SELECT key, value FROM settings WHERE key IN (?,?,?,?,?,?,?)",
        ('tax_rate', 'social_rate', 'billing_multiplier', 'holidays_per_year', 'avg_leave_days', 'usd_rate', 'indirect_pool_enabled'),
    ).fetchall()
    conn.close()
    if not sal:
        return 0

    s = {r['key']: r['value'] for r in settings_rows}
    tax_rate = s.get('tax_rate') or 0.12
    social_rate = s.get('social_rate') or 0.12
    markup = s.get('billing_multiplier') or 2.0
    usd_rate = get_current_usd_rate()
    holidays = s.get('holidays_per_year') or 14
    leave_days = s.get('avg_leave_days') or 20
    indirect_pool_enabled = s.get('indirect_pool_enabled') or 0
    available_hours = ((365 - 104 - holidays - leave_days) / 12) * 8

    base = sal['base_salary']
    premium = sal['premium']
    gross = base + premium
    tax = gross * tax_rate
    social = gross * social_rate

    # Compute the proportional share ratio once and reuse it for admin, equipment,
    # and overhead — previously each called _proportional_share() independently,
    # which fetched _get_latest_period() and get_total_billable_hours() three times each.
    period = _get_latest_period()
    total_hours = get_total_billable_hours(period)
    if total_hours <= 1:
        total_hours = get_total_billable_hours()
    staff_hours = get_staff_billable_hours(staff_id, period)
    if total_hours <= 1:
        prod_count = count_staff_by_type('production')
        share_ratio = 1.0 / max(prod_count, 1)
    else:
        share_ratio = staff_hours / total_hours

    admin_share = get_admin_total_cost() * share_ratio
    general_eq_share = get_general_equipment_monthly() * share_ratio
    overhead_total = get_indirect_pool_monthly() if indirect_pool_enabled else get_total_overhead()
    overhead_share = overhead_total * share_ratio

    personal_eq = get_personal_equipment_monthly(staff_id)
    personal_lic = get_personal_license_monthly(staff_id)

    total_monthly = (gross + tax + social + admin_share
                     + personal_eq + personal_lic + general_eq_share + overhead_share)

    cost_rate = total_monthly / available_hours if available_hours else 0
    billing_rate = cost_rate * markup

    return {
        'base_salary': base, 'premium': premium, 'tax': tax, 'social': social,
        'admin_share': admin_share, 'personal_eq': personal_eq, 'personal_lic': personal_lic,
        'general_eq': general_eq_share, 'overhead_share': overhead_share,
        'total_monthly': total_monthly, 'available_hours': available_hours,
        'markup': markup, 'cost_rate': cost_rate, 'billing_rate': billing_rate,
        'hourly_rate': cost_rate,
        'hourly_usd': cost_rate / usd_rate,
        'billing_usd': billing_rate / usd_rate,
    }


def get_production_staff_rates():
    """Active production staff with current cost/billing rates for milestone planning."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, role FROM staff WHERE staff_type='production' AND is_active=1 ORDER BY name"
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        info = calculate_hourly_rate(r['id'])
        out.append({
            'id': r['id'], 'name': r['name'], 'role': r['role'],
            'cost_rate': info['cost_rate'] if isinstance(info, dict) else 0,
            'billing_rate': info['billing_rate'] if isinstance(info, dict) else 0,
        })
    return out


def get_staff_kpi():
    conn = get_db()
    staff = conn.execute(
        "SELECT id, name, role, department, staff_code FROM staff WHERE staff_type='production' AND is_active=1"
    ).fetchall()
    conn.close()

    usd_rate = get_current_usd_rate()
    available_hours = get_available_hours()['available_hours']
    kpi_months = get_setting('kpi_months') or 43

    kpi_list = []
    for s in staff:
        conn2 = get_db()
        hours_row = conn2.execute(
            "SELECT SUM(hours) as total, COUNT(DISTINCT project_id) as projects FROM project_hours WHERE staff_id=? AND hours > 0",
            (s['id'],)
        ).fetchone()
        conn2.close()

        rate_info = calculate_hourly_rate(s['id'])
        cost_rate = rate_info['cost_rate'] if isinstance(rate_info, dict) else 0
        billing_rate = rate_info['billing_rate'] if isinstance(rate_info, dict) else 0
        total_hours = hours_row['total'] or 0
        project_count = hours_row['projects'] or 0

        theoretical_available = kpi_months * available_hours
        utilization = total_hours / theoretical_available if theoretical_available > 0 else 0

        revenue_value = total_hours * billing_rate
        cost_value = total_hours * cost_rate
        net_margin = revenue_value - cost_value
        revenue_per_hour = billing_rate
        profit_per_hour = (billing_rate - cost_rate) if cost_rate > 0 else 0

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

        kpi_list.append({
            'staff_code': s['staff_code'] or f"MZ-{s['id']:03d}",
            'name': s['name'], 'role': s['role'], 'department': s['department'],
            'projects': project_count, 'hours': total_hours,
            'avg_hours': total_hours / project_count if project_count > 0 else 0,
            'cost_rate': cost_rate, 'billing_rate': billing_rate,
            'cost_value': cost_value, 'revenue_value': revenue_value,
            'net_margin': net_margin, 'value_created': cost_value,
            'value_usd': cost_value / usd_rate, 'revenue_usd': revenue_value / usd_rate,
            'utilization': utilization, 'efficiency': utilization, 'hourly_rate': cost_rate,
            'revenue_per_hour': revenue_per_hour, 'profit_per_hour': profit_per_hour,
            'rating': rating,
        })

    kpi_list.sort(key=lambda x: x['hours'], reverse=True)
    return kpi_list


# ── Snapshot helpers (v5.2) ───────────────────────────────────────────────────

def snapshot_period_allocations(period):
    """Freeze cost rates and allocation amounts for all production staff in a period."""
    conn = get_db()
    staff = conn.execute(
        "SELECT id FROM staff WHERE staff_type='production' AND is_active=1"
    ).fetchall()
    conn.close()

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    tax_rate = get_setting('tax_rate') or 0.12
    social_rate = get_setting('social_rate') or 0.12
    available_hours = get_available_hours()['available_hours']
    markup = get_setting('billing_multiplier') or 2.0

    conn = get_db()
    for s in staff:
        sid = s['id']
        rate_info = calculate_hourly_rate(sid)
        if not isinstance(rate_info, dict):
            continue
        bh = get_staff_billable_hours(sid, period)
        th = get_total_billable_hours(period)
        hours_share = (bh / th) if th > 1 else 0
        gross = rate_info['base_salary'] + rate_info['premium']
        conn.execute('''INSERT OR REPLACE INTO period_allocations
            (period, staff_id, billable_hours, hours_share,
             admin_share_amount, overhead_share_amount, general_equipment_share_amount,
             personal_equipment_amount, personal_licenses_amount,
             gross_salary, tax_amount, social_amount, total_monthly_cost,
             cost_rate, billing_rate, available_hours, snapshotted_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (period, sid, bh, hours_share,
             rate_info['admin_share'], rate_info['overhead_share'], rate_info['general_eq'],
             rate_info['personal_eq'], rate_info['personal_lic'],
             gross, rate_info['tax'], rate_info['social'], rate_info['total_monthly'],
             rate_info['cost_rate'], rate_info['billing_rate'], available_hours, now))
    conn.commit()
    conn.close()


def snapshot_hours_rates(period, source='period_close'):
    """Write applied_cost_* snapshot columns onto project_hours rows for a period."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, staff_id, hours FROM project_hours WHERE period=?", (period,)
    ).fetchall()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for row in rows:
        rate_info = calculate_hourly_rate(row['staff_id'])
        if not isinstance(rate_info, dict):
            continue
        cost_rate = rate_info['cost_rate']
        billing_rate = rate_info['billing_rate']
        conn.execute('''UPDATE project_hours SET
            applied_cost_rate=?, applied_billing_rate=?,
            applied_cost_amount=?, applied_billing_amount=?,
            rate_snapshot_at=?, rate_snapshot_source=?
            WHERE id=?''',
            (cost_rate, billing_rate,
             row['hours'] * cost_rate, row['hours'] * billing_rate,
             now, source, row['id']))
    conn.commit()
    conn.close()


def close_fiscal_period(period_code, status='hard_closed'):
    """Run all snapshot hooks and set period status.

    status: 'soft_closed' or 'hard_closed'
    """
    # 1. Snapshot per-staff allocations
    snapshot_period_allocations(period_code)
    # 2. Snapshot rate amounts on project_hours
    snapshot_hours_rates(period_code, source='period_close')
    # 3. Update fiscal_periods status
    conn = get_db()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute(
        "UPDATE fiscal_periods SET status=?, closed_at=? WHERE code=?",
        (status, now, period_code)
    )
    conn.commit()
    conn.close()
