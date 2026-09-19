"""Staff register, salary history, timesheets (+ NIZAM import), KPI, equipment."""
import os
from datetime import date

from flask import Blueprint, request, redirect, flash, current_app
from flask_login import login_required
from werkzeug.utils import secure_filename

from auth import require_level
from models import (
    get_db, now_ts, get_lookup, get_production_staff_rates, list_projects,
    latest_period, calculate_hourly_rate, rate_context,
    default_lifespan_for, classify_register, import_nizam_file, get_staff_kpi,
    get_current_usd_rate,
)
from utils import render_page, t, parse_float, parse_int

bp = Blueprint('staff', __name__)


@bp.route('/staff')
@login_required
def staff_page():
    f_dept = request.args.get('department') or ''
    f_type = request.args.get('staff_type') or ''
    f_active = request.args.get('active') or ''
    f_search = request.args.get('q') or ''
    conn = get_db()
    try:
        sql = ("SELECT s.*, sh.base_salary, sh.premium, sh.start_date AS salary_from,"
               " c.name AS counterparty_name FROM staff s"
               " LEFT JOIN salary_history sh ON sh.staff_id = s.id AND sh.end_date IS NULL"
               " LEFT JOIN counterparties c ON c.id = s.counterparty_id WHERE 1=1")
        params = []
        if f_dept:
            sql += " AND s.department = ?"
            params.append(f_dept)
        if f_type in ('production', 'admin'):
            sql += " AND s.staff_type = ?"
            params.append(f_type)
        if f_active in ('1', '0'):
            sql += " AND s.is_active = ?"
            params.append(int(f_active))
        if f_search:
            sql += (" AND (ulower(s.name) LIKE ulower(?) OR ulower(s.full_name) LIKE ulower(?)"
                    " OR ulower(s.role) LIKE ulower(?))")
            params += [f'%{f_search}%'] * 3
        sql += " ORDER BY s.is_active DESC, s.staff_type, s.name"
        rows = [dict(r) for r in conn.execute(sql, params)]
        all_depts = [r[0] for r in conn.execute(
            "SELECT DISTINCT department FROM staff ORDER BY department")]
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
                       departments=get_lookup('departments'), all_depts=all_depts,
                       roles=get_lookup('staff_roles'),
                       f_dept=f_dept, f_type=f_type, f_active=f_active, f_search=f_search,
                       title=t('staff_plural'))


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
                       period=period, staff=staff, projects=list_projects(sort='name'),
                       total_hours=sum(r['hours'] or 0 for r in rows),
                       import_periods=_recent_months(18),
                       import_result=None,
                       title=t('nav_hours'))


def _recent_months(n):
    """The last n months as YYYY-MM, newest first — the import selector."""
    today = date.today()
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        out.append(f'{y:04d}-{m:02d}')
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return out


@bp.route('/staff/hours/import', methods=['POST'])
@login_required
@require_level('manager')
def hours_import():
    """Upload one month of the NIZAM CRM timesheet export."""
    f = request.files.get('file')
    period = (request.form.get('period') or '').strip()
    if not f or not f.filename or len(period) != 7:
        flash(f'<div class="alert alert-error">{t("required_field")}</div>', 'error')
        return redirect('/staff/hours')
    folder = current_app.config['UPLOAD_FOLDER']
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, secure_filename(f.filename) or 'nizam.xlsx')
    f.save(path)
    try:
        result = import_nizam_file(path, period,
                                   create_projects=bool(request.form.get('create_projects')))
    except Exception as e:   # a broken workbook must not 500 the page
        flash(f'<div class="alert alert-error">{t("import_failed")} — {e}</div>', 'error')
        return redirect('/staff/hours')
    parts = [t('import_done', result['hour_entries'], result['projects'],
               result['employees_mapped'], len(result['skipped']))]
    if result['projects_created']:
        parts.append(t('import_projects_created', result['projects_created']))
    flash(f'<div class="alert alert-success">{" ".join(parts)}</div>', 'success')
    if result['unmatched_names']:
        flash(f'<div class="alert alert-warn"><b>{t("import_unmatched", len(result["unmatched_names"]))}:</b> '
              f'{", ".join(result["unmatched_names"])}<br>{t("import_unmatched_hint")}</div>', 'warn')
    if result['unresolved_projects']:
        flash(f'<div class="alert alert-warn"><b>{t("import_unresolved_projects", len(result["unresolved_projects"]))}:</b> '
              f'{", ".join(result["unresolved_projects"])}</div>', 'warn')
    if result['skipped']:
        items = ', '.join(f"{sk['row']}: {sk['raw'] or '—'} ({t('skip_' + sk['reason'])})"
                          for sk in result['skipped'][:40])
        flash(f'<div class="alert alert-info">{t("import_skipped_rows", len(result["skipped"]))}: {items}</div>', 'info')
    return redirect(f'/staff/hours?period={period}')


@bp.route('/kpi')
@login_required
def kpi_page():
    kpi = get_staff_kpi()
    return render_page('kpi', 'kpi.html', kpi=kpi, rows=kpi['rows'],
                       usd_rate=get_current_usd_rate(), title=t('nav_kpi'))


@bp.route('/staff/equipment', methods=['GET'])
@login_required
def equipment_page():
    f = {'kind': request.args.get('kind') or '',
         'asset_class': request.args.get('asset_class') or '',
         'staff_id': request.args.get('staff_id', type=int),
         'license_type': request.args.get('license_type') or '',
         'q': request.args.get('q') or ''}
    conn = get_db()
    try:
        sql = ("SELECT e.*, s.name AS staff_name,"
               " (e.price * e.quantity / NULLIF(e.lifespan_months,0)) AS monthly"
               " FROM equipment e LEFT JOIN staff s ON s.id = e.staff_id WHERE 1=1")
        params = []
        if f['kind'] in ('personal', 'general'):
            sql += " AND e.kind = ?"
            params.append(f['kind'])
        if f['asset_class']:
            sql += " AND e.asset_class = ?"
            params.append(f['asset_class'])
        if f['staff_id']:
            sql += " AND e.staff_id = ?"
            params.append(f['staff_id'])
        if f['q']:
            sql += " AND ulower(e.name) LIKE ulower(?)"
            params.append(f"%{f['q']}%")
        sql += " ORDER BY e.is_active DESC, e.kind, e.name"
        equipment = [dict(r) for r in conn.execute(sql, params)]
        lsql = ("SELECT l.*, s.name AS staff_name, l.annual_cost/12.0 AS monthly"
                " FROM licenses l LEFT JOIN staff s ON s.id = l.staff_id WHERE 1=1")
        lparams = []
        if f['staff_id']:
            lsql += " AND l.staff_id = ?"
            lparams.append(f['staff_id'])
        if f['license_type']:
            lsql += " AND l.license_type = ?"
            lparams.append(f['license_type'])
        if f['q']:
            lsql += " AND ulower(l.name) LIKE ulower(?)"
            lparams.append(f"%{f['q']}%")
        lsql += " ORDER BY l.is_active DESC, l.name"
        licenses = [dict(r) for r in conn.execute(lsql, lparams)]
        overhead = [dict(r) for r in conn.execute(
            "SELECT * FROM overhead_budget ORDER BY is_active DESC, name")]
        staff = [dict(r) for r in conn.execute(
            "SELECT id, name FROM staff WHERE is_active=1 ORDER BY name")]
        classes = [dict(r) for r in conn.execute(
            "SELECT c.*,"
            " (SELECT COUNT(*) FROM equipment e"
            "   WHERE e.asset_class = c.code AND e.is_active=1) AS n,"
            " (SELECT COALESCE(SUM(e.price * e.quantity),0) FROM equipment e"
            "   WHERE e.asset_class = c.code AND e.is_active=1) AS cost,"
            " (SELECT COUNT(*) FROM equipment e"
            "   WHERE e.asset_class = c.code AND e.is_active=1"
            "     AND e.lifespan_months <> c.default_lifespan_months) AS off_default"
            " FROM asset_classes c ORDER BY c.sort_order, c.code")]
    finally:
        conn.close()
    return render_page('equipment', 'equipment.html', equipment=equipment,
                       licenses=licenses, overhead=overhead, staff=staff,
                       classes=classes, f=f, title=t('nav_equipment'))


@bp.route('/staff/equipment/save', methods=['POST'])
@login_required
@require_level('manager')
def equipment_save():
    kind_table = request.form.get('table') or 'equipment'
    rid = parse_int(request.form.get('id'))
    conn = get_db()
    try:
        if kind_table == 'equipment':
            asset_class = request.form.get('asset_class') or 'computer'
            # An empty lifespan falls back to the CLASS default, not to a
            # hardcoded 36 — that is what makes the class default mean
            # anything. An explicit value always wins and is never overwritten.
            months = parse_int(request.form.get('lifespan_months'), 0) or 0
            fields = {
                'name': (request.form.get('name') or '').strip(),
                'kind': request.form.get('kind') or 'personal',
                'asset_class': asset_class,
                'staff_id': parse_int(request.form.get('staff_id')),
                'quantity': parse_int(request.form.get('quantity'), 1) or 1,
                'price': parse_float(request.form.get('price')),
                'lifespan_months': months or default_lifespan_for(asset_class),
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


@bp.route('/staff/equipment/classes', methods=['POST'])
@login_required
@require_level('admin')
def asset_classes_save():
    """Edit the per-class default useful life.

    This changes the default for assets entered FROM NOW ON. Rows already in
    the register keep the life they were recorded with, because changing it
    would silently re-depreciate assets whose charge is already posted and
    move every rate quoted from them. The page shows how many rows in each
    class sit off the default so the divergence stays visible; re-lifing them
    is a deliberate per-row edit.
    """
    conn = get_db()
    try:
        for key, value in request.form.items():
            if not key.startswith('months_'):
                continue
            months = parse_int(value, 0) or 0
            if months > 0:
                conn.execute("UPDATE asset_classes SET default_lifespan_months=?"
                             " WHERE code=?", (months, key[len('months_'):]))
        conn.commit()
        flash(f'<div class="alert alert-success">{t("saved")}</div>', 'success')
    except Exception:
        flash(f'<div class="alert alert-error">{t("error")}</div>', 'error')
    finally:
        conn.close()
    return redirect('/staff/equipment')


@bp.route('/staff/equipment/reclassify', methods=['POST'])
@login_required
@require_level('admin')
def equipment_reclassify():
    """Re-run the keyword classifier over rows still sitting on 'other'.

    Only touches unclassified rows, so a class someone corrected by hand is
    never overwritten.
    """
    result = classify_register(apply=True, only_unclassified=True)
    flash(f'<div class="alert alert-success">{t("saved")} — '
          f'{result["changed"]}</div>', 'success')
    return redirect('/staff/equipment')
