"""Dashboard route."""
from flask import Blueprint
from flask_login import login_required
from utils import render_page
from models import get_dashboard_data, get_loan_summary, get_projects_on_course_summary

bp = Blueprint('dashboard', __name__)


@bp.route('/')
@login_required
def dashboard():
    data = get_dashboard_data()
    loan_data = get_loan_summary()
    oc = get_projects_on_course_summary()
    return render_page('dashboard', 'dashboard.html',
        data=data,
        loan_data=loan_data,
        oc=oc,
        oc_class='stat-red' if oc['off'] > 0 else ('stat-green' if oc['tracked'] > 0 else ''),
        profit_class='stat-green' if data['total_profit'] >= 0 else 'stat-red',
        burn_class='stat-red' if data.get('runway_months', 999) < 6 else '',
        cap_class='stat-green' if data.get('capacity_pct', 0) < 85 else 'stat-red',
        ar_class='stat-red' if data.get('ar_overdue', 0) > 0 else '',
        fx_class='stat-green' if data.get('total_fx_gain_loss', 0) >= 0 else 'stat-red',
    )
