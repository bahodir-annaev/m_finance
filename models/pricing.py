"""Pricing engine and risk scoring."""
from .base import get_db, get_setting, get_current_usd_rate
from .staff import calculate_hourly_rate


def calculate_risk_score(deadline_months=None, client_type='new', complexity='medium', currency='UZS'):
    if deadline_months is None or deadline_months > 8:
        d = 1
    elif deadline_months >= 4:
        d = 2
    else:
        d = 3

    cl = {'regular': 1, 'new': 2, 'government': 3}.get(client_type, 2)
    cx = {'simple': 1, 'medium': 2, 'high': 3}.get(complexity, 2)
    cu = {'UZS': 1, 'USD': 2}.get(currency, 3)

    weighted = d * 0.30 + cl * 0.25 + cx * 0.25 + cu * 0.20
    risk_coeff = 1 + (weighted - 1) * 0.15
    return round(weighted, 2), round(risk_coeff, 3)


def pricing_estimate(staff_hours_list, risk_kwargs=None, outsourcing=0, material=0):
    if risk_kwargs is None:
        risk_kwargs = {}

    _, risk_coeff = calculate_risk_score(**risk_kwargs)
    target_margin = get_setting('target_margin') or 0.50
    usd_rate = get_current_usd_rate()

    staff_details = []
    total_cost = 0
    total_billing = 0
    total_hours = 0

    for item in staff_hours_list:
        sid = item['staff_id']
        hrs = item['planned_hours']
        rate_info = calculate_hourly_rate(sid)
        if not isinstance(rate_info, dict):
            continue

        cost = hrs * rate_info['cost_rate']
        billing = hrs * rate_info['billing_rate']
        total_cost += cost
        total_billing += billing
        total_hours += hrs

        conn = get_db()
        s = conn.execute("SELECT name, role FROM staff WHERE id=?", (sid,)).fetchone()
        conn.close()

        staff_details.append({
            'name': s['name'] if s else f'ID:{sid}',
            'role': s['role'] if s else '',
            'hours': hrs,
            'cost_rate': rate_info['cost_rate'],
            'billing_rate': rate_info['billing_rate'],
            'cost': cost,
            'billing': billing,
        })

    mizan_cost = total_cost * risk_coeff
    minimum_contract = mizan_cost + outsourcing + material
    target_contract = minimum_contract / (1 - target_margin) if target_margin < 1 else minimum_contract * 2
    premium_contract = target_contract * 1.2

    return {
        'staff_details': staff_details,
        'total_hours': total_hours,
        'total_cost': total_cost,
        'total_billing': total_billing,
        'risk_coeff': risk_coeff,
        'mizan_cost': mizan_cost,
        'outsourcing': outsourcing,
        'material': material,
        'minimum_contract': minimum_contract,
        'target_contract': target_contract,
        'premium_contract': premium_contract,
        'minimum_usd': minimum_contract / usd_rate,
        'target_usd': target_contract / usd_rate,
        'premium_usd': premium_contract / usd_rate,
        'target_margin': target_margin,
    }
