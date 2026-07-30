"""Staff, hourly rates, and KPI routes."""
from flask import Blueprint
from flask_login import login_required, current_user
from utils import render_page
from models import get_db, get_setting, calculate_hourly_rate, get_staff_kpi

bp = Blueprint('staff', __name__)


def _can_edit():
    return current_user.is_authenticated and current_user.role in ('manager', 'admin')


@bp.route('/staff')
@login_required
def staff_page():
    conn = get_db()
    staff = conn.execute('''
        SELECT s.*, sh.base_salary, sh.premium
        FROM staff s
        LEFT JOIN salary_history sh ON s.id = sh.staff_id AND sh.end_date IS NULL
        ORDER BY s.staff_type, s.department, s.name
    ''').fetchall()
    departments = conn.execute(
        "SELECT label_uz FROM departments WHERE is_active=1 ORDER BY sort_order, label_uz"
    ).fetchall()
    staff_roles = conn.execute(
        "SELECT label_uz FROM staff_roles WHERE is_active=1 ORDER BY sort_order, label_uz"
    ).fetchall()
    conn.close()
    tax = get_setting('tax_rate')
    social = get_setting('social_rate')

    rows = []
    for s in staff:
        base = s['base_salary'] or 0
        prem = s['premium'] or 0
        gross = base + prem
        rows.append({
            'id': s['id'], 'name': s['name'], 'full_name': s['full_name'] or '',
            'role': s['role'], 'department': s['department'],
            'staff_type': s['staff_type'], 'is_active': s['is_active'],
            'base': base, 'prem': prem,
            'tax_amt': gross * tax,
            'social_amt': gross * social,
            'total': gross + gross * tax + gross * social,
        })

    return render_page('staff', 'staff.html',
        rows=rows,
        depts=sorted(set(s['department'] for s in staff)),
        types=sorted(set(s['staff_type'] for s in staff)),
        departments=departments,
        staff_roles=staff_roles,
        can_edit=_can_edit(),
    )


@bp.route('/hourly')
@login_required
def hourly_page():
    conn = get_db()
    staff = conn.execute(
        "SELECT id, name, role, department FROM staff WHERE staff_type='production' AND is_active=1 ORDER BY department, name"
    ).fetchall()
    conn.close()

    rows = []
    for s in staff:
        r = calculate_hourly_rate(s['id'])
        if not isinstance(r, dict):
            continue
        rows.append({
            'name': s['name'], 'role': s['role'], 'department': s['department'],
            'salary_prem': r['base_salary'] + r['premium'],
            'tax_social': r['tax'] + r['social'],
            'admin_share': r['admin_share'],
            'tech_lic': r['personal_eq'] + r['personal_lic'],
            'general_eq': r['general_eq'],
            'overhead_share': r['overhead_share'],
            'total_monthly': r['total_monthly'],
            'cost_rate': r['cost_rate'],
            'billing_rate': r['billing_rate'],
            'billing_usd': r['billing_usd'],
        })

    return render_page('hourly', 'hourly.html',
        rows=rows,
        depts=sorted(set(s['department'] for s in staff)),
        eff_hrs=get_setting('effective_hours') or 132,
        mult=get_setting('billing_multiplier') or 2.0,
    )


@bp.route('/kpi')
@login_required
def kpi_page():
    return render_page('kpi', 'kpi.html', kpi=get_staff_kpi())
