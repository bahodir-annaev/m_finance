"""Projects and budget routes."""
from flask import Blueprint
from flask_login import login_required
from utils import render_page
from models import get_db, calculate_project_cost

bp = Blueprint('projects', __name__)


@bp.route('/projects')
@login_required
def projects_page():
    conn = get_db()
    projects = conn.execute('''
        SELECT p.*, COALESCE(SUM(ph.hours), 0) as total_hours, COUNT(DISTINCT ph.staff_id) as staff_count,
               (SELECT COUNT(*) FROM project_phases pp WHERE pp.project_id = p.id) as ms_total,
               (SELECT COUNT(*) FROM project_phases pp
                 WHERE pp.project_id = p.id AND pp.status = 'done') as ms_done,
               (SELECT COUNT(*) FROM project_phases pp
                 WHERE pp.project_id = p.id AND pp.end_date IS NOT NULL
                   AND pp.end_date < date('now')
                   AND pp.status NOT IN ('done', 'cancelled')) as ms_late
        FROM projects p
        LEFT JOIN project_hours ph ON p.id = ph.project_id
        WHERE p.is_billable = 1
        GROUP BY p.id ORDER BY total_hours DESC
    ''').fetchall()
    conn.close()
    return render_page('projects', 'projects.html',
        projects=projects,
        statuses=sorted(set(p['status'] for p in projects)),
    )


@bp.route('/budget')
@login_required
def budget_page():
    conn = get_db()
    projects = conn.execute("SELECT id, name FROM projects WHERE is_billable=1").fetchall()
    conn.close()

    rows = []
    totals = {'p_hrs': 0, 'p_cost': 0, 'p_out': 0, 'p_total': 0,
              'f_hrs': 0, 'f_cost': 0, 'f_out': 0, 'f_total': 0}
    # fact totals of *planned* projects only, so the JAMI variance row
    # compares like with like (unplanned projects have no plan side)
    planned_fact_total = 0

    for p in projects:
        pc = calculate_project_cost(p['id'])
        if not pc or pc['total_hours'] == 0:
            continue

        f_hrs = pc['total_hours']
        f_cost = pc['mizan_cost']
        f_out = pc['outsourcing']
        f_total = f_cost + f_out + pc.get('material', 0)
        f_workers = len(pc['staff_breakdown'])

        proj_data = pc['project']
        p_hrs = proj_data.get('planned_hours', 0) or 0
        p_cost = proj_data.get('planned_cost', 0) or 0
        p_out = proj_data.get('planned_outsourcing', 0) or 0
        p_material = proj_data.get('planned_material', 0) or 0
        p_total = p_cost + p_out + p_material
        has_plan = p_hrs > 0 or p_cost > 0

        if has_plan:
            d_hrs = f_hrs - p_hrs
            d_cost = f_cost - p_cost
            d_total = f_total - p_total
            d_pct = (d_total / max(p_total, 1)) * 100

            if d_pct > 5:
                b_status, b_class, pl_class = 'Oshgan', 'loss', 'loss'
            elif d_pct < -10:
                b_status, b_class, pl_class = 'Byudjet ichida', 'profit', 'profit'
            else:
                b_status, b_class, pl_class = 'Chegarada', '', 'profit'
        else:
            # No frozen plan — never fabricate one from the actuals (that made
            # every unplanned project look "on budget"). Show it as unplanned
            # and keep it out of the plan-side totals.
            p_hrs = proj_data.get('estimated_total_hours', 0) or 0
            p_cost = p_out = p_total = 0
            d_hrs = d_cost = d_total = 0
            d_pct = 0
            b_status, b_class, pl_class = "Reja yo'q", '', 'noplan'

        rows.append({
            'project_name': pc['project']['name'],
            'has_plan': has_plan,
            'p_workers': f_workers, 'p_hrs': p_hrs, 'p_cost': p_cost, 'p_out': p_out, 'p_total': p_total,
            'f_workers': f_workers, 'f_hrs': f_hrs, 'f_cost': f_cost, 'f_out': f_out, 'f_total': f_total,
            'd_hrs': d_hrs, 'd_cost': d_cost, 'd_total': d_total, 'd_pct': d_pct,
            'b_status': b_status, 'b_class': b_class, 'pl_class': pl_class,
        })

        for k in ('f_hrs', 'f_cost', 'f_out', 'f_total'):
            totals[k] += locals()[k]
        if has_plan:
            planned_fact_total += f_total
            for k in ('p_hrs', 'p_cost', 'p_out', 'p_total'):
                totals[k] += locals()[k]

    t_d_total = planned_fact_total - totals['p_total']
    t_d_pct = (t_d_total / max(totals['p_total'], 1)) * 100

    return render_page('budget', 'budget.html',
        rows=rows,
        totals=totals,
        t_d_total=t_d_total,
        t_d_pct=t_d_pct,
        total_margin_class='profit' if t_d_pct <= 0 else 'loss',
    )
