"""Cash flow route."""
from flask import Blueprint
from flask_login import login_required
from utils import render_page
from models import get_cash_flow_by_month, get_payment_summary

bp = Blueprint('cashflow', __name__)


@bp.route('/cashflow')
@login_required
def cashflow_page():
    cf = get_cash_flow_by_month()
    # Headline tiles come from the same series as the monthly table, so the
    # page always reconciles with itself (the payment-type card is a breakdown
    # of transactions + dividends only and is labelled as such).
    tiles = {
        'income': sum(m['income'] for m in cf),
        'expense': sum(m['total_expense'] for m in cf),
        'loan_net': sum(m['loan_flow'] for m in cf),
        'balance': cf[-1]['running_balance'] if cf else 0,
    }
    return render_page('cashflow', 'cashflow.html',
        cf=cf,
        tiles=tiles,
        pay_summary=get_payment_summary(),
    )
