"""Dashboard aggregations, burn rate, capacity, AR aging."""
from datetime import datetime, date
from .base import get_db, get_setting, get_current_usd_rate, INCOME_TX_SQL
from .staff import (
    count_staff_by_type, get_available_hours,
    get_admin_total_cost, get_total_overhead, calculate_hourly_rate,
)
from .equipment import get_general_equipment_monthly
from .projects import calculate_project_cost
from .transactions import get_cash_flow_by_month


def get_burn_rate_and_runway():
    from .staff import get_indirect_pool_monthly

    conn = get_db()
    sal_rows = conn.execute('''
        SELECT SUM(sh.base_salary + sh.premium) as total
        FROM staff s
        JOIN salary_history sh ON s.id = sh.staff_id
        WHERE s.is_active = 1 AND sh.end_date IS NULL
    ''').fetchone()
    conn.close()

    tax_rate = get_setting('tax_rate') or 0.12
    social_rate = get_setting('social_rate') or 0.12
    indirect_pool_enabled = (get_setting('indirect_pool_enabled') or 0)
    monthly_salary = sal_rows['total'] if sal_rows and sal_rows['total'] else 0
    monthly_salary_full = monthly_salary * (1 + tax_rate + social_rate)
    monthly_overhead = get_indirect_pool_monthly() if indirect_pool_enabled else get_total_overhead()
    monthly_equipment = get_general_equipment_monthly()
    # Burn rate is cash leaving the business, so it excludes the non-cash
    # general-equipment depreciation. Total operating cost keeps the full figure.
    burn_rate = monthly_salary_full + monthly_overhead
    total_operating_cost = burn_rate + monthly_equipment

    cf = get_cash_flow_by_month()
    running_balance = cf[-1]['running_balance'] if cf else 0
    runway = running_balance / burn_rate if burn_rate > 0 else 999

    return {
        'burn_rate': burn_rate,
        'total_operating_cost': total_operating_cost,
        'salary_component': monthly_salary_full,
        'overhead_component': monthly_overhead,
        'equipment_component': monthly_equipment,
        'runway_months': round(runway, 1),
        'running_balance': running_balance,
    }


def get_capacity_data():
    prod_count = count_staff_by_type('production')
    available = get_available_hours()['available_hours']
    kpi_months = get_setting('kpi_months') or 43
    total_capacity = prod_count * available * kpi_months

    conn = get_db()
    booked_row = conn.execute('''
        SELECT COALESCE(SUM(ph.hours), 0) as total
        FROM project_hours ph
        JOIN projects p ON ph.project_id = p.id
        WHERE p.is_billable = 1 AND p.status = 'active'
    ''').fetchone()
    conn.close()
    booked = booked_row['total']

    return {
        'total_capacity': total_capacity,
        'booked_capacity': booked,
        'available_capacity': total_capacity - booked,
        'capacity_pct': (booked / total_capacity * 100) if total_capacity > 0 else 0,
    }


def get_ar_aging():
    conn = get_db()
    rows = conn.execute(f'''
        SELECT date, amount, paid, project_id, description
        FROM transactions
        WHERE tx_type IN {INCOME_TX_SQL} AND direction = 'external' AND paid < amount
    ''').fetchall()
    conn.close()

    today = date.today()
    buckets = {'0_30': 0, '31_60': 0, '61_90': 0, '90_plus': 0}
    total_outstanding = 0
    overdue_items = []

    for r in rows:
        outstanding = r['amount'] - r['paid']
        total_outstanding += outstanding
        try:
            tx_date = datetime.strptime(r['date'], '%Y-%m-%d').date()
        except (ValueError, TypeError):
            tx_date = today
        days = (today - tx_date).days

        if days <= 30:
            buckets['0_30'] += outstanding
        elif days <= 60:
            buckets['31_60'] += outstanding
        elif days <= 90:
            buckets['61_90'] += outstanding
        else:
            buckets['90_plus'] += outstanding
            overdue_items.append({
                'desc': r['description'] or f"Project #{r['project_id']}",
                'outstanding': outstanding,
                'days': days,
            })

    return {
        'total_outstanding': total_outstanding,
        'overdue_total': buckets['61_90'] + buckets['90_plus'],
        'buckets': buckets,
        'overdue_items': overdue_items[:10],
    }


def get_dashboard_data():
    conn = get_db()
    projects = conn.execute("SELECT COUNT(*) as cnt FROM projects WHERE is_billable=1").fetchone()['cnt']
    total_hours = conn.execute('''
        SELECT COALESCE(SUM(ph.hours), 0) as total
        FROM project_hours ph JOIN projects p ON ph.project_id = p.id WHERE p.is_billable = 1
    ''').fetchone()['total']

    prod_count = count_staff_by_type('production')
    admin_count = count_staff_by_type('admin')
    hrs = get_available_hours()
    available_hours = hrs['available_hours']
    kpi_months = get_setting('kpi_months') or 43

    staff_list = conn.execute("SELECT id FROM staff WHERE staff_type='production' AND is_active=1").fetchall()
    conn.close()

    cost_rates, billing_rates = [], []
    for s in staff_list:
        r = calculate_hourly_rate(s['id'])
        if isinstance(r, dict) and r['cost_rate'] > 0:
            cost_rates.append(r['cost_rate'])
            billing_rates.append(r['billing_rate'])

    avg_cost_rate = sum(cost_rates) / len(cost_rates) if cost_rates else 0
    avg_billing_rate = sum(billing_rates) / len(billing_rates) if billing_rates else 0
    usd_rate = get_current_usd_rate()
    indirect_pool_enabled = (get_setting('indirect_pool_enabled') or 0)
    from .staff import get_indirect_pool_monthly
    overhead = get_indirect_pool_monthly() if indirect_pool_enabled else get_total_overhead()

    theoretical_available = prod_count * kpi_months * available_hours
    utilization = (total_hours / theoretical_available * 100) if theoretical_available > 0 else 0

    conn2 = get_db()
    all_projects = conn2.execute("SELECT id FROM projects WHERE is_billable=1").fetchall()
    conn2.close()

    total_mizan_cost = 0
    total_mizan_billing = 0
    total_income = 0
    total_fx_gain_loss = 0
    total_earned_revenue = 0
    project_details = []
    for p in all_projects:
        pc = calculate_project_cost(p['id'])
        if pc:
            total_mizan_cost += pc['mizan_cost']
            total_mizan_billing += pc['mizan_billing']
            total_income += pc['income']
            total_fx_gain_loss += pc.get('fx_gain_loss', 0)
            total_earned_revenue += pc.get('earned_revenue', 0)
            project_details.append(pc)

    project_details.sort(key=lambda x: x['total_hours'], reverse=True)

    burn_data = get_burn_rate_and_runway()
    capacity_data = get_capacity_data()
    ar_data = get_ar_aging()

    monthly_fixed = get_admin_total_cost() + overhead
    avg_margin_pct = (total_income - total_mizan_cost) / total_income if total_income > 0 else 0.5
    breakeven_revenue = monthly_fixed / max(avg_margin_pct, 0.01)

    gross_profit = total_income - total_mizan_cost
    gross_margin_pct = (gross_profit / total_income * 100) if total_income > 0 else 0

    revenue_per_employee = total_income / max(prod_count, 1)
    profit_per_hour = gross_profit / max(total_hours, 1)

    return {
        'total_projects': projects, 'total_hours': total_hours,
        'production_staff': prod_count, 'admin_staff': admin_count,
        'avg_cost_rate': avg_cost_rate, 'avg_billing_rate': avg_billing_rate,
        'avg_hourly_rate': avg_cost_rate,
        'avg_hourly_usd': avg_cost_rate / usd_rate if usd_rate else 0,
        'avg_billing_usd': avg_billing_rate / usd_rate if usd_rate else 0,
        'total_mizan_cost': total_mizan_cost, 'total_mizan_billing': total_mizan_billing,
        'total_mizan_usd': total_mizan_cost / usd_rate if usd_rate else 0,
        'total_income': total_income, 'total_profit': gross_profit,
        'gross_margin_pct': gross_margin_pct, 'net_margin_pct': gross_margin_pct,
        'monthly_overhead': overhead, 'utilization_pct': utilization,
        'available_hours': available_hours, 'top_projects': project_details[:10],
        'usd_rate': usd_rate, 'total_fx_gain_loss': total_fx_gain_loss,
        'total_earned_revenue': total_earned_revenue,
        'burn_rate': burn_data['burn_rate'], 'runway_months': burn_data['runway_months'],
        'running_balance': burn_data['running_balance'],
        'capacity_total': capacity_data['total_capacity'],
        'capacity_booked': capacity_data['booked_capacity'],
        'capacity_available': capacity_data['available_capacity'],
        'capacity_pct': capacity_data['capacity_pct'],
        'ar_total': ar_data['total_outstanding'], 'ar_overdue': ar_data['overdue_total'],
        'breakeven_revenue': breakeven_revenue,
        'revenue_per_employee': revenue_per_employee, 'profit_per_hour': profit_per_hour,
    }
