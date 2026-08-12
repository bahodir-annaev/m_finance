"""Project cost calculations, FX gain/loss, earned revenue."""
from .base import (
    get_db, get_setting, get_rate_for_date, get_current_usd_rate,
    is_period_closed, INCOME_TX_SQL,
)
from .staff import calculate_hourly_rate


def get_staff_billable_hours_for_project(project_id):
    conn = get_db()
    row = conn.execute(
        "SELECT COALESCE(SUM(hours), 0) as total FROM project_hours WHERE project_id=?",
        (project_id,)
    ).fetchone()
    conn.close()
    return row['total']


def calculate_fx_gain_loss(project_id):
    conn = get_db()
    current_rate = get_current_usd_rate()
    # Follow-up payment rows are excluded: the invoice already carries the full USD
    # exposure in amount_usd, and a payment row (amount_usd = 0) would fall into the
    # paid/tx_rate branch below and count the same principal a second time.
    rows = conn.execute('''
        SELECT amount, paid, amount_usd, currency, date, exchange_rate
        FROM transactions WHERE project_id=? AND currency='USD' AND direction='external'
          AND parent_tx_id IS NULL
    ''', (project_id,)).fetchall()
    conn.close()
    fx_gain_loss = 0
    for r in rows:
        tx_rate = r['exchange_rate'] if r['exchange_rate'] and r['exchange_rate'] > 0 else get_rate_for_date(r['date'])
        # FX exposure is the USD principal × rate movement. Prefer amount_usd;
        # otherwise derive it from the UZS `paid` at the entry-time rate.
        # (Never treat `paid` itself as USD — it is stored in UZS, and one such
        # row would inflate the FX figure by a factor of the exchange rate.)
        if r['amount_usd'] and r['amount_usd'] > 0:
            usd_amount = r['amount_usd']
        elif r['paid'] and tx_rate:
            usd_amount = r['paid'] / tx_rate
        else:
            continue
        fx_gain_loss += usd_amount * (current_rate - tx_rate)
    return fx_gain_loss


def get_earned_revenue(project_id):
    conn = get_db()
    proj = conn.execute(
        "SELECT contract_amount, estimated_total_hours FROM projects WHERE id=?", (project_id,)
    ).fetchone()
    conn.close()
    if not proj:
        return 0
    contract = proj['contract_amount'] or 0
    est_hours = proj['estimated_total_hours'] or 0
    if est_hours <= 0 or contract <= 0:
        return 0
    actual_hours = get_staff_billable_hours_for_project(project_id)
    completion = min(actual_hours / est_hours, 1.0)
    return contract * completion


def calculate_project_cost(project_id):
    conn = get_db()
    proj = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if not proj:
        conn.close()
        return None

    # Hours grouped by staff — use snapshot amounts for closed periods when available
    hours_data = conn.execute('''
        SELECT ph.staff_id, s.name, ph.period,
               SUM(ph.hours) as total_hours,
               SUM(COALESCE(ph.applied_cost_amount, 0)) as snapped_cost,
               SUM(COALESCE(ph.applied_billing_amount, 0)) as snapped_billing,
               MAX(ph.rate_snapshot_source) as snapshot_source
        FROM project_hours ph
        JOIN staff s ON ph.staff_id = s.id
        WHERE ph.project_id = ?
        GROUP BY ph.staff_id, ph.period
    ''', (project_id,)).fetchall()

    income_row = conn.execute(
        "SELECT COALESCE(SUM(paid), 0) as total FROM transactions"
        f" WHERE project_id=? AND tx_type IN {INCOME_TX_SQL}",
        (project_id,)
    ).fetchone()
    outsourcing_row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) as total FROM transactions"
        " WHERE project_id=? AND tx_type='outsourcing'",
        (project_id,)
    ).fetchone()
    material_row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) as total FROM transactions"
        " WHERE project_id=? AND tx_type='material'",
        (project_id,)
    ).fetchone()
    conn.close()

    total_cost = 0
    total_billing = 0
    total_hours = 0
    staff_summary = {}  # staff_id → {name, hours, cost, billing}

    # Cache period closed status (small number of unique periods, each is a DB call)
    unique_periods = {h['period'] for h in hours_data}
    period_closed_cache = {p: is_period_closed(p) for p in unique_periods}
    # Cache hourly rates per unique staff_id — the rate doesn't vary by period,
    # so calling it once per staff member instead of once per (staff, period) row
    # eliminates the dominant source of redundant DB work on this page.
    rate_cache = {}

    for h in hours_data:
        sid = h['staff_id']
        period_closed = period_closed_cache[h['period']]

        if period_closed and h['snapped_cost'] > 0:
            cost = h['snapped_cost']
            billing = h['snapped_billing']
            cost_rate = cost / h['total_hours'] if h['total_hours'] else 0
            billing_rate = billing / h['total_hours'] if h['total_hours'] else 0
        else:
            if sid not in rate_cache:
                rate_cache[sid] = calculate_hourly_rate(sid)
            rate_info = rate_cache[sid]
            if isinstance(rate_info, dict):
                cost_rate = rate_info['cost_rate']
                billing_rate = rate_info['billing_rate']
            else:
                cost_rate = billing_rate = 0
            cost = h['total_hours'] * cost_rate
            billing = h['total_hours'] * billing_rate

        total_cost += cost
        total_billing += billing
        total_hours += h['total_hours']

        if sid not in staff_summary:
            staff_summary[sid] = {
                'staff_name': h['name'], 'hours': 0,
                'cost_rate': cost_rate, 'billing_rate': billing_rate,
                'cost': 0, 'billing': 0,
            }
        staff_summary[sid]['hours'] += h['total_hours']
        staff_summary[sid]['cost'] += cost
        staff_summary[sid]['billing'] += billing

    risk = proj['risk_coefficient']
    mizan_cost = total_cost
    mizan_price = total_cost * risk
    mizan_billing = total_billing
    usd_rate = get_current_usd_rate()

    total_income = income_row['total']
    total_outsourcing = outsourcing_row['total']
    total_material = material_row['total']
    total_expense = mizan_cost + total_outsourcing + total_material
    profit = total_income - total_expense
    # Net project margin: after ALL project costs (labor + outsourcing + material),
    # not a GAAP gross margin. Named accordingly to avoid confusion.
    net_margin = (profit / total_income * 100) if total_income > 0 else 0

    fx_gain_loss = calculate_fx_gain_loss(project_id)
    earned_revenue = get_earned_revenue(project_id)
    contract_amt = proj['contract_amount'] or 0
    deferred_revenue = contract_amt - earned_revenue if earned_revenue > 0 else 0

    return {
        'project': dict(proj),
        'staff_breakdown': list(staff_summary.values()),
        'total_hours': total_hours,
        'risk_coefficient': risk,
        'mizan_cost': mizan_cost,
        'mizan_price': mizan_price,
        'mizan_billing': mizan_billing,
        'mizan_cost_usd': mizan_cost / usd_rate,
        'mizan_billing_usd': mizan_billing / usd_rate,
        'outsourcing': total_outsourcing,
        'material': total_material,
        'total_expense': total_expense,
        'contract_amount': contract_amt,
        'income': total_income,
        'profit': profit,
        'net_margin': net_margin,
        'margin': net_margin,
        'fx_gain_loss': fx_gain_loss,
        'earned_revenue': earned_revenue,
        'deferred_revenue': deferred_revenue,
    }
