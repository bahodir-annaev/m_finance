"""Pricing engine route."""
from datetime import datetime
from flask import Blueprint, request, abort
from flask_login import login_required, current_user
from utils import render_page, t
from models import get_db, pricing_estimate, get_work_types, generate_milestones_from_pricing

bp = Blueprint('pricing', __name__)


def _collect_schedule_rows(form):
    """Parse the parallel ms_* form arrays from the schedule editor."""
    names = form.getlist('ms_name')
    wts = form.getlist('ms_work_type')
    starts = form.getlist('ms_start')
    ends = form.getlist('ms_end')
    ipcts = form.getlist('ms_income_pct')
    hpcts = form.getlist('ms_hours_pct')

    def at(lst, i, default=''):
        return lst[i] if i < len(lst) else default

    return [{
        'name': names[i],
        'work_type': at(wts, i),
        'start': at(starts, i),
        'end': at(ends, i),
        'income_pct': at(ipcts, i, 0),
        'hours_pct': at(hpcts, i, 0),
    } for i in range(len(names))]


@bp.route('/pricing', methods=['GET', 'POST'])
@login_required
def pricing_page():
    if request.method == 'POST' and current_user.role == 'viewer':
        abort(403)
    conn = get_db()
    staff_list = conn.execute(
        "SELECT id, name, role FROM staff WHERE staff_type='production' AND is_active=1 ORDER BY name"
    ).fetchall()
    pricing_projects = conn.execute(
        "SELECT name FROM projects WHERE is_billable=1 ORDER BY name"
    ).fetchall()
    conn.close()

    result = None
    gen_msg = None
    if request.method == 'POST':
        staff_hours = [
            {'staff_id': s['id'], 'planned_hours': float(request.form.get(f'hours_{s["id"]}', 0) or 0)}
            for s in staff_list
            if float(request.form.get(f'hours_{s["id"]}', 0) or 0) > 0
        ]
        risk_kwargs = {
            'deadline_months': int(request.form.get('deadline', 6)),
            'client_type': request.form.get('client_type', 'new'),
            'complexity': request.form.get('complexity', 'medium'),
            'currency': request.form.get('currency', 'UZS'),
        }
        outsourcing = float(request.form.get('outsourcing', 0))
        material = float(request.form.get('material', 0))
        save_to_project = request.form.get('save_project', '')

        if staff_hours:
            result = pricing_estimate(staff_hours, risk_kwargs, outsourcing, material)
            if save_to_project:
                conn2 = get_db()
                cur_row = conn2.execute(
                    "SELECT id, contract_amount, estimated_total_hours FROM projects WHERE name=?",
                    (save_to_project,)
                ).fetchone()
                # A signed contract amount (entered on project creation) must never
                # be silently replaced by a quote — the quote lives in planned_revenue.
                # Same for an already-set hours estimate (earned revenue depends on it).
                keep_contract = cur_row and (cur_row['contract_amount'] or 0) > 0
                keep_est = cur_row and (cur_row['estimated_total_hours'] or 0) > 0
                conn2.execute('''UPDATE projects SET
                    estimated_total_hours=?, contract_amount=?, risk_coefficient=?,
                    planned_hours=?, planned_cost=?, planned_revenue=?,
                    planned_outsourcing=?, planned_material=?, plan_frozen_date=?
                    WHERE name=?''',
                    (cur_row['estimated_total_hours'] if keep_est else result['total_hours'],
                     cur_row['contract_amount'] if keep_contract else result['target_contract'],
                     result['risk_coeff'],
                     # planned_cost is the risk-EXCLUSIVE labor cost so that plan vs
                     # fact on /budget and milestone pages compares like with like;
                     # the risk uplift lives only in planned_revenue.
                     result['total_hours'], result['total_cost'], result['target_contract'],
                     outsourcing, material, datetime.now().strftime('%Y-%m-%d'), save_to_project))
                conn2.commit()
                proj_row = cur_row
                conn2.close()

                if request.form.get('gen_milestones') and proj_row:
                    created, err = generate_milestones_from_pricing(
                        proj_row['id'], _collect_schedule_rows(request.form),
                        total_income=result['target_contract'],
                        total_hours=result['total_hours'],
                        labor_cost=result['total_cost'],
                        outsourcing=outsourcing,
                        material=material,
                        overwrite=bool(request.form.get('ms_overwrite')),
                    )
                    if err:
                        gen_msg = ('warn', t('pricing_ms_err_' + err))
                    else:
                        gen_msg = ('success', t('pricing_ms_done', created))

    return render_page('pricing', 'pricing.html',
        staff_list=staff_list,
        pricing_projects=pricing_projects,
        work_types=get_work_types(),
        result=result,
        gen_msg=gen_msg,
    )
