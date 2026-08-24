"""Dashboard — cash, receivables, profitability and capacity at a glance."""
from flask import Blueprint
from flask_login import login_required

from models import get_dashboard, get_trial_balance
from utils import render_page, t

bp = Blueprint('dashboard', __name__)


@bp.route('/')
@login_required
def dashboard():
    data = get_dashboard()
    # The trial balance is shown as a health check: if it ever stops balancing,
    # the dashboard says so rather than quietly reporting wrong totals.
    data['books_balanced'] = get_trial_balance()['is_balanced']
    return render_page('dashboard', 'dashboard.html', d=data, title=t('nav_dashboard'))
