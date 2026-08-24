"""Staff register, salary history, timesheets, equipment and licenses."""
from flask import Blueprint, request, redirect, flash
from flask_login import login_required

from auth import require_level
from models import (
    get_db, now_ts, get_lookup, get_production_staff_rates, list_projects,
    latest_period, calculate_hourly_rate, rate_context,
)
from utils import render_page, t, parse_float, parse_int

bp = Blueprint('staff', __name__)


@bp.route('/staff')
@login_required
def staff_page():
    conn = get_db()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT s.*, sh.base_salary, sh.premium, sh.start_date AS salary_from,"
            " c.name AS counterparty_name FROM staff s"
            " LEFT JOIN salary_history sh ON sh.staff_id = s.id AND sh.end_date IS NULL"
            " LEFT JOIN counterparties c ON c.id = s.counterparty_id"
            " ORDER BY s.is_active DESC, s.staff_type, s.name")]
        salary_history = [dict(r) for r in conn.execute(
            "SELECT sh.*, s.name AS staff_name FROM salary_history sh"
            " JOIN staff s ON s.id = sh.staff_id"
            " ORDER BY sh.start_date DESC, s.name LIMIT 100")]
    finally:
        conn.close()

    ctx = rate_context()
    for r in rows:
        info = calculate_hourly_rate(r['id'], context=ctx) if r['staff_type'] == 'production' else None
        r['cost_rate'] = info['cost_rate'] if info else 0
        r['billing_rate'] = info['billing_rate'] if info else 0
    return render_page('staff', 'staff.html', rows=rows, salary_history=salary_history,
                       departments=get_lookup('departments'),
                       roles=get_lookup('staff_roles'), title=t('staff_plural'))


@bp.route('/staff/save', methods=['POST'])
@login_required
@require_level('manager')
def staff_save():
    sid = parse_int(request.form.get('id'))
    fields = {
        'name': (request.form.get('name') or '').strip(),
        'full_name': (request.form.get('full_name') or '').strip() or None,
        'role': (request.form.get('role') or '').strip(),
        'department': (request.form.get('department') or '').strip(),
        'staff_type': request.form.get('staff_type') or 'production',
        'hire_date': request.form.get('hire_date') or None,
        'termination_date': request.form.get('termination_date') or None,
        'is_active': 1 if request.form.get('is_active') else 0,
    }
    if not fields['name'] or not fields['role'] or not fields['department']:
        flash(f'<div class="alert alert-error">{t("required_field")}</div>', 'error')
        return redirect('/staff')

    conn = get_db()
    try:
        if sid:
            sets = ', '.join(f'{k}=?' for k in fields)
            conn.execute(f"UPDATE staff SET {sets}, updated_at=? WHERE id=?",
                         list(fields.values()) + [now_ts(), sid])
        else:
            cols = ','.join(fields)
            ph = ','.join('?' * len(fields))
            cur = conn.execute(f"INSERT INTO staff ({cols}) VALUES ({ph})",
                               list(fields.values()))
            sid = cur.lastrowid
            prefix = 'MZ' if fields['staff_type'] == 'production' else 'MA'
            conn.execute("UPDATE staff SET staff_code=? WHERE id=?",
                         (f'{prefix}-{sid:03d}', sid))
        conn.commit()
        flash(f'<div class="alert alert-success">{t("saved")}</div>', 'success')
    except Exception:
        flash(f'<div class="alert alert-error">{t("error")}</div>', 'error')
    finally:
        conn.close()
    return redirect('/staff')


@bp.route('/staff/salary', methods=['POST'])
@login_required
@require_level('manager')
def salary_save():
    """Add a salary row and close the previous one the day before it starts.

    Salary is a timeline, not a field: closing the old row keeps history intact
    so a payroll run for an earlier month still finds the rate that applied then.
    """
    staff_id = parse_int(request.form.get('staff_id'))
    start_date = request.form.get('start_date')
    base = parse_float(request.form.get('base_salary'))
    premium = parse_float(request.form.get('premium'))
    if not staff_id or not start_date:
        flash(f'<div class="alert alert-error">{t("required_field")}</div>', 'error')
        return redirect('/staff')

    conn = get_db()
    try:
        conn.execute(
            "UPDATE salary_history SET end_date = date(?, '-1 day'), updated_at=?"
            " WHERE staff_id=? AND end_date IS NULL AND start_date < ?",
            (start_date, now_ts(), staff_id, start_date))
        conn.execute(
            "INSERT OR REPLACE INTO salary_history"
            " (staff_id, base_salary, premium, start_date, updated_at)"
            " VALUES (?,?,?,?,?)", (staff_id, base, premium, start_date, now_ts()))
        conn.commit()
        flash(f'<div class="alert alert-success">{t("saved")}</div>', 'success')
    finally:
        conn.close()
    return redirect('/staff')


@bp.route('/staff/hours', methods=['GET', 'POST'])
@login_required
def hours_page():
    if request.method == 'POST':
        project_id = parse_int(request.form.get('project_id'))
        staff_id = parse_int(request.form.get('staff_id'))
        period = (request.form.get('period') or '').strip()
        hours = parse_float(request.form.get('hours'))
        if project_id and staff_id and period:
            conn = get_db()
            try:
                conn.execute(
                    "INSERT INTO project_hours (project_id, staff_id, phase_id, hours,"
                    " period, source) VALUES (?,?,?,?,?,'manual')"
                    " ON CONFLICT(project_id, staff_id, period)"
                    " DO UPDATE SET hours=excluded.hours, phase_id=excluded.phase_id",
                    (project_id, staff_id, parse_int(request.form.get('phase_id')),
                     hours, period))
                conn.commit()
                flash(f'<div class="alert alert-success">{t("saved")}</div>', 'success')
            finally:
                conn.close()
        return redirect('/staff/hours')

    period = request.args.get('period') or latest_period()
    conn = get_db()
    try:
        sql = ("SELECT ph.*, s.name AS staff_name, p.name AS project_name,"
               " pp.name AS phase_name FROM project_hours ph"
               " JOIN staff s ON s.id = ph.staff_id"
               " JOIN projects p ON p.id = ph.project_id"
               " LEFT JOIN project_phases pp ON pp.id = ph.phase_id")
        params = []
        if period:
            sql += " WHERE ph.period = ?"
            params.append(period)
        sql += " ORDER BY ph.period DESC, p.name, s.name"
        rows = [dict(r) for r in conn.execute(sql, params)]
        periods = [r['period'] for r in conn.execute(
            "SELECT DISTINCT period FROM project_hours ORDER BY period DESC")]
        staff = [dict(r) for r in conn.execute(
            "SELECT id, name FROM staff WHERE is_active=1 ORDER BY name")]
    finally:
        conn.close()
    return render_page('hours', 'hours.html', rows=rows, periods=periods,
                       period=period, staff=staff, projects=list_projects(),
                       total_hours=sum(r['hours'] or 0 for r in rows),
                       title=t('nav_hours'))


@bp.route('/staff/equipment', methods=['GET'])
@login_required
def equipment_page():
    conn = get_db()
    try:
        equipment = [dict(r) for r in conn.execute(
            "SELECT e.*, s.name AS staff_name,"
            " (e.price * e.quantity / NULLIF(e.lifespan_months,0)) AS monthly"
            " FROM equipment e LEFT JOIN staff s ON s.id = e.staff_id"
            " ORDER BY e.is_active DESC, e.kind, e.name")]
        licenses = [dict(r) for r in conn.execute(
            "SELECT l.*, s.name AS staff_name, l.annual_cost/12.0 AS monthly"
            " FROM licenses l LEFT JOIN staff s ON s.id = l.staff_id"
            " ORDER BY l.is_active DESC, l.name")]
        overhead = [dict(r) for r in conn.execute(
            "SELECT * FROM overhead_budget ORDER BY is_active DESC, name")]
        staff = [dict(r) for r in conn.execute(
            "SELECT id, name FROM staff WHERE is_active=1 ORDER BY name")]
    finally:
        conn.close()
    return render_page('equipment', 'equipment.html', equipment=equipment,
                       licenses=licenses, overhead=overhead, staff=staff,
                       title=t('nav_equipment'))


@bp.route('/staff/equipment/save', methods=['POST'])
@login_required
@require_level('manager')
def equipment_save():
    kind_table = request.form.get('table') or 'equipment'
    rid = parse_int(request.form.get('id'))
    conn = get_db()
    try:
        if kind_table == 'equipment':
            fields = {
                'name': (request.form.get('name') or '').strip(),
                'kind': request.form.get('kind') or 'personal',
                'staff_id': parse_int(request.form.get('staff_id')),
                'quantity': parse_int(request.form.get('quantity'), 1) or 1,
                'price': parse_float(request.form.get('price')),
                'lifespan_months': parse_int(request.form.get('lifespan_months'), 36) or 36,
                'purchase_date': request.form.get('purchase_date') or None,
                'is_active': 1 if request.form.get('is_active') else 0,
            }
        elif kind_table == 'licenses':
            fields = {
                'name': (request.form.get('name') or '').strip(),
                'staff_id': parse_int(request.form.get('staff_id')),
                'annual_cost': parse_float(request.form.get('annual_cost')),
                'license_type': request.form.get('license_type') or 'named',
                'is_active': 1 if request.form.get('is_active') else 0,
            }
        else:
            kind_table = 'overhead_budget'
            fields = {
                'name': (request.form.get('name') or '').strip(),
                'monthly_amount': parse_float(request.form.get('monthly_amount')),
                'is_active': 1 if request.form.get('is_active') else 0,
            }
        if rid:
            sets = ', '.join(f'{k}=?' for k in fields)
            conn.execute(f"UPDATE {kind_table} SET {sets}, updated_at=? WHERE id=?",
                         list(fields.values()) + [now_ts(), rid])
        else:
            cols = ','.join(fields)
            ph = ','.join('?' * len(fields))
            conn.execute(f"INSERT INTO {kind_table} ({cols}) VALUES ({ph})",
                         list(fields.values()))
        conn.commit()
        flash(f'<div class="alert alert-success">{t("saved")}</div>', 'success')
    except Exception:
        flash(f'<div class="alert alert-error">{t("error")}</div>', 'error')
    finally:
        conn.close()
    return redirect('/staff/equipment')
