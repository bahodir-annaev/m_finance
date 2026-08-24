"""Projects, the Plan & Price card, milestones and the budget page."""
from flask import Blueprint, request, redirect, flash, abort
from flask_login import login_required

from auth import require_level
from models import (
    list_projects, get_project, create_project, calculate_project_cost,
    get_budget_overview, get_project_milestones, add_milestone, delete_milestone,
    set_milestone_status, set_milestone_staff, generate_milestone_schedule,
    price_project_plan, apply_suggested_prices, freeze_project_plan,
    get_plan_baseline, save_project_risk, get_project_risk,
    get_production_staff_rates, get_lookup, get_db, now_ts, update_record,
    STANDARD_SCHEDULE, RISK_CLIENT_TYPES, RISK_COMPLEXITIES, get_document,
    list_documents,
)
from utils import render_page, t, parse_float, parse_int, form_rows

bp = Blueprint('projects', __name__)


@bp.route('/projects')
@login_required
def projects_page():
    rows = list_projects(status=request.args.get('status') or None,
                         search=request.args.get('q') or None)
    conn = get_db()
    try:
        counterparties = [dict(r) for r in conn.execute(
            "SELECT id, name FROM counterparties WHERE is_active=1 ORDER BY name")]
        staff = [dict(r) for r in conn.execute(
            "SELECT id, name FROM staff WHERE is_active=1 ORDER BY name")]
    finally:
        conn.close()
    return render_page('projects', 'projects.html', rows=rows,
                       counterparties=counterparties, staff=staff,
                       search=request.args.get('q') or '',
                       status=request.args.get('status') or '',
                       title=t('projects'))


@bp.route('/projects/save', methods=['POST'])
@login_required
@require_level('manager')
def project_save():
    pid = parse_int(request.form.get('id'))
    data = {
        'name': (request.form.get('name') or '').strip(),
        'code': request.form.get('code'),
        'counterparty_id': parse_int(request.form.get('counterparty_id')),
        'responsible_id': parse_int(request.form.get('responsible_id')),
        'contract_amount': parse_float(request.form.get('contract_amount')),
        'currency': request.form.get('currency') or 'UZS',
        'start_date': request.form.get('start_date') or None,
        'end_date': request.form.get('end_date') or None,
        'status': request.form.get('status') or 'active',
        'is_billable': 1 if request.form.get('is_billable') else 0,
        'estimated_total_hours': parse_float(request.form.get('estimated_total_hours')),
        'notes': request.form.get('notes'),
    }
    if not data['name']:
        flash(f'<div class="alert alert-error">{t("required_field")}</div>', 'error')
        return redirect('/projects')
    if pid:
        update_record('projects', pid, data)
    else:
        pid = create_project(data)
    flash(f'<div class="alert alert-success">{t("saved")}</div>', 'success')
    return redirect(f'/projects/{pid}')


@bp.route('/projects/<int:project_id>')
@login_required
def project_detail(project_id):
    project = get_project(project_id)
    if not project:
        abort(404)
    plan = price_project_plan(project_id)
    return render_page('projects', 'project_detail.html',
                       project=project,
                       cost=calculate_project_cost(project_id),
                       milestones=get_project_milestones(project_id),
                       plan=plan,
                       baseline=get_plan_baseline(project_id),
                       risk=get_project_risk(project_id),
                       staff_rates=get_production_staff_rates(),
                       work_types=get_lookup('work_types'),
                       client_types=RISK_CLIENT_TYPES,
                       complexities=RISK_COMPLEXITIES,
                       standard_schedule=STANDARD_SCHEDULE,
                       documents=list_documents(project_id=project_id, limit=50),
                       title=project['name'])


@bp.route('/projects/<int:project_id>/risk', methods=['POST'])
@login_required
@require_level('manager')
def project_risk(project_id):
    save_project_risk(project_id,
                      deadline_months=request.form.get('deadline_months'),
                      client_type=request.form.get('client_type'),
                      complexity=request.form.get('complexity'),
                      currency=request.form.get('risk_currency'))
    flash(f'<div class="alert alert-success">{t("saved")}</div>', 'success')
    return redirect(f'/projects/{project_id}')


@bp.route('/projects/<int:project_id>/milestone', methods=['POST'])
@login_required
@require_level('manager')
def milestone_save(project_id):
    """Create or edit one milestone, including its employee hour rows."""
    phase_id = parse_int(request.form.get('id'))
    name = (request.form.get('name') or '').strip()
    fields = {
        'name': name,
        'work_type': request.form.get('work_type') or None,
        'start_date': request.form.get('start_date') or None,
        'end_date': request.form.get('end_date') or None,
        'planned_outsourcing': parse_float(request.form.get('planned_outsourcing')),
        'planned_material': parse_float(request.form.get('planned_material')),
        'planned_revenue': parse_float(request.form.get('planned_revenue')),
        'completion_percent': parse_float(request.form.get('completion_percent')),
        'status': request.form.get('status') or 'planned',
        'notes': request.form.get('notes') or None,
    }
    if not name:
        flash(f'<div class="alert alert-error">{t("required_field")}</div>', 'error')
        return redirect(f'/projects/{project_id}')

    if phase_id:
        conn = get_db()
        try:
            sets = ', '.join(f'{k}=?' for k in fields)
            conn.execute(f"UPDATE project_phases SET {sets}, updated_at=? WHERE id=?",
                         list(fields.values()) + [now_ts(), phase_id])
            conn.commit()
        finally:
            conn.close()
    else:
        phase_id = add_milestone(project_id, name, **{
            k: v for k, v in fields.items() if k != 'name'})

    staff_rows = form_rows(request.form, 'ms', ('staff_id', 'est_hours'))
    payload = [{'staff_id': parse_int(r['staff_id']),
                'est_hours': parse_float(r['est_hours'])} for r in staff_rows
               if parse_int(r['staff_id']) and parse_float(r['est_hours']) > 0]
    if payload or request.form.get('has_staff_grid'):
        set_milestone_staff(phase_id, payload)

    flash(f'<div class="alert alert-success">{t("saved")}</div>', 'success')
    return redirect(f'/projects/{project_id}')


@bp.route('/projects/<int:project_id>/milestone/<int:phase_id>/delete', methods=['POST'])
@login_required
@require_level('manager')
def milestone_delete(project_id, phase_id):
    ok, used = delete_milestone(phase_id)
    if not ok:
        flash(f'<div class="alert alert-error">{t("error")} — {used}</div>', 'error')
    else:
        flash(f'<div class="alert alert-success">{t("deleted")}</div>', 'success')
    return redirect(f'/projects/{project_id}')


@bp.route('/projects/<int:project_id>/milestone/<int:phase_id>/status', methods=['POST'])
@login_required
@require_level('manager')
def milestone_status(project_id, phase_id):
    set_milestone_status(phase_id, request.form.get('status'))
    return redirect(f'/projects/{project_id}')


@bp.route('/projects/<int:project_id>/schedule', methods=['POST'])
@login_required
@require_level('manager')
def project_schedule(project_id):
    """Add the six standard design stages as an empty skeleton."""
    created, err = generate_milestone_schedule(
        project_id, STANDARD_SCHEDULE,
        overwrite=bool(request.form.get('overwrite')))
    if err:
        flash(f'<div class="alert alert-error">{t("error")} — {err}</div>', 'error')
    else:
        flash(f'<div class="alert alert-success">{created} {t("milestones")}</div>',
              'success')
    return redirect(f'/projects/{project_id}')


@bp.route('/projects/<int:project_id>/prices', methods=['POST'])
@login_required
@require_level('manager')
def project_prices(project_id):
    changed = apply_suggested_prices(project_id, force=bool(request.form.get('force')))
    flash(f'<div class="alert alert-success">{t("saved")} — {changed}</div>', 'success')
    return redirect(f'/projects/{project_id}')


@bp.route('/projects/<int:project_id>/freeze', methods=['POST'])
@login_required
@require_level('manager')
def project_freeze(project_id):
    result, err = freeze_project_plan(project_id)
    if err == 'empty':
        flash(f'<div class="alert alert-warn">{t("no_plan")}</div>', 'warn')
    elif err:
        flash(f'<div class="alert alert-error">{t("error")}</div>', 'error')
    else:
        flash(f'<div class="alert alert-success">{t("plan_frozen")}</div>', 'success')
    return redirect(f'/projects/{project_id}')


@bp.route('/budget')
@login_required
def budget_page():
    return render_page('budget', 'budget.html', ov=get_budget_overview(),
                       title=t('nav_budget'))
