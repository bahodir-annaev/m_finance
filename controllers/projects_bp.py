"""Projects and the portfolio budget/plan page."""
from flask import Blueprint
from flask_login import login_required
from utils import render_page
from models import get_db, get_plan_overview

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
    """Portfolio plan vs fact under both lenses — time-phased "to date" and
    whole-project baseline. Merged from the former /plan page, which now
    redirects here. All figures come from get_plan_overview()."""
    ov = get_plan_overview()
    return render_page('budget', 'budget.html',
        rows=ov['rows'],
        counts=ov['counts'],
        totals=ov['totals'],
        whole_totals=ov['whole_totals'],
        whole_d_total=ov['whole_d_total'],
        whole_d_pct=ov['whole_d_pct'],
        whole_d_css=ov['whole_d_css'],
    )
