"""Milestones (bosqichlar): project detail page, /plan overview, milestone API."""
import re

from flask import Blueprint, request, jsonify, abort
from flask_login import login_required, current_user
from auth import require_role
from utils import render_page
from models import (
    get_db, get_work_types, get_project_milestones, get_project_monthly_rollup,
    get_unassigned_actuals, suggest_phase_for_period, add_milestone,
    delete_milestone, set_milestone_status, assign_hours_to_milestone,
    get_plan_overview, set_milestone_staff, get_production_staff_rates,
    get_record, update_record,
)

bp = Blueprint('milestones', __name__)

_PERIOD_RE = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')


def _can_edit():
    return current_user.is_authenticated and current_user.role in ('manager', 'admin')


@bp.route('/plan')
@login_required
def plan_page():
    ov = get_plan_overview()
    return render_page('plan', 'plan.html',
        rows=ov['rows'],
        counts=ov['counts'],
        totals=ov['totals'],
    )


@bp.route('/projects/<int:project_id>')
@login_required
def project_detail(project_id):
    conn = get_db()
    proj = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    conn.close()
    if not proj:
        abort(404)

    milestones = get_project_milestones(project_id)
    rollup = get_project_monthly_rollup(project_id, milestones=milestones)
    unassigned = get_unassigned_actuals(project_id)
    for hp in unassigned['hour_periods']:
        hp['suggested'] = suggest_phase_for_period(project_id, hp['period'], milestones)
    total_ev = sum(m['earned_value'] for m in milestones)

    # page='projects' keeps the Proektlar nav item highlighted on the detail view
    return render_page('projects', 'project_detail.html',
        proj=dict(proj),
        milestones=milestones,
        rollup=rollup,
        unassigned=unassigned,
        work_types=get_work_types(),
        total_ev=total_ev,
        can_edit=_can_edit(),
        staff_rates=get_production_staff_rates(),
    )


def _num(form, key):
    try:
        return float(form.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _collect_staff_rows(form):
    """Parse the parallel ms_staff_* arrays from the milestone modal.
    Blank/zero rows are dropped by set_milestone_staff."""
    ids = form.getlist('ms_staff_id')
    hrs = form.getlist('ms_staff_hours')
    return [{'staff_id': ids[i], 'est_hours': hrs[i] if i < len(hrs) else 0}
            for i in range(len(ids))]


@bp.route('/api/milestones/add', methods=['POST'])
@require_role('manager', 'admin')
def api_add_milestone():
    f = request.form
    try:
        project_id = int(f.get('project_id') or 0)
    except ValueError:
        project_id = 0
    name = (f.get('name') or '').strip()
    if not project_id or not name:
        return jsonify({'error': 'project_id and name are required'}), 400
    mid = add_milestone(
        project_id, name,
        code=f.get('code'),
        work_type=(f.get('work_type') or '').strip() or None,
        start_date=(f.get('start_date') or '').strip() or None,
        end_date=(f.get('end_date') or '').strip() or None,
        planned_hours=_num(f, 'planned_hours'),
        planned_cost=_num(f, 'planned_cost'),
        planned_outsourcing=_num(f, 'planned_outsourcing'),
        planned_material=_num(f, 'planned_material'),
        planned_revenue=_num(f, 'planned_revenue'),
        sort_order=int(_num(f, 'sort_order') or 100),
        status=f.get('status') or 'planned',
        notes=(f.get('notes') or '').strip() or None,
    )
    if mid:
        set_milestone_staff(mid, _collect_staff_rows(f))
        return jsonify({'status': 'ok', 'id': mid})
    return jsonify({'error': 'insert failed'}), 400


@bp.route('/api/milestones/<int:mid>/update', methods=['POST'])
@require_role('manager', 'admin')
def api_update_milestone(mid):
    """Modal save for an existing milestone: scalar columns + employee plan
    rows in one request. Replaces the generic /api/update/project_phases/<id>
    route, which cannot handle the ms_staff_* child array. planned_hours and
    planned_cost are never taken from the form — set_milestone_staff derives
    them from the rows."""
    if not get_record('project_phases', mid):
        return jsonify({'error': 'not found'}), 404
    f = request.form
    name = (f.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name is required'}), 400
    update_record('project_phases', mid, {
        'name': name,
        'code': (f.get('code') or '').strip() or None,
        'work_type': (f.get('work_type') or '').strip() or None,
        'status': f.get('status') or 'planned',
        'start_date': (f.get('start_date') or '').strip() or None,
        'end_date': (f.get('end_date') or '').strip() or None,
        'planned_revenue': _num(f, 'planned_revenue'),
        'planned_outsourcing': _num(f, 'planned_outsourcing'),
        'planned_material': _num(f, 'planned_material'),
        'completion_percent': _num(f, 'completion_percent'),
        'sort_order': int(_num(f, 'sort_order') or 100),
        'notes': (f.get('notes') or '').strip() or None,
    })
    totals = set_milestone_staff(mid, _collect_staff_rows(f))
    return jsonify({'status': 'ok', 'id': mid, **totals})


@bp.route('/api/milestones/<int:mid>/delete', methods=['POST'])
@require_role('manager', 'admin')
def api_delete_milestone(mid):
    ok, used = delete_milestone(mid)
    if ok:
        return jsonify({'status': 'ok'})
    return jsonify({'error': 'blocked', 'used': used}), 400


@bp.route('/api/milestones/<int:mid>/status', methods=['POST'])
@require_role('manager', 'admin')
def api_milestone_status(mid):
    if set_milestone_status(mid, request.form.get('status') or ''):
        return jsonify({'status': 'ok'})
    return jsonify({'error': 'not found'}), 404


@bp.route('/api/milestones/assign-hours', methods=['POST'])
@require_role('manager', 'admin')
def api_assign_hours():
    try:
        project_id = int(request.form.get('project_id') or 0)
    except ValueError:
        project_id = 0
    period = (request.form.get('period') or '').strip()
    phase_raw = (request.form.get('phase_id') or '').strip()
    phase_id = int(phase_raw) if phase_raw.isdigit() else None
    if not project_id or not _PERIOD_RE.match(period):
        return jsonify({'error': 'bad request'}), 400
    updated = assign_hours_to_milestone(project_id, period, phase_id)
    if updated < 0:
        return jsonify({'error': 'phase belongs to another project'}), 400
    return jsonify({'status': 'ok', 'updated': updated})
