"""Journal, trial balance and the account card."""
from flask import Blueprint, request, abort
from flask_login import login_required

from models import (
    get_journal, get_trial_balance, get_account_ledger, get_accounts,
    get_db, today_str,
)
from utils import render_page, t

bp = Blueprint('journal', __name__)


@bp.route('/journal')
@login_required
def journal():
    filters = {
        'date_from': request.args.get('date_from') or None,
        'date_to': request.args.get('date_to') or None,
        'account_id': request.args.get('account_id', type=int),
        'project_id': request.args.get('project_id', type=int),
        'counterparty_id': request.args.get('counterparty_id', type=int),
    }
    entries = get_journal(limit=150, **filters)
    return render_page('journal', 'journal.html', entries=entries, filters=filters,
                       accounts=get_accounts(), title=t('nav_journal'))


@bp.route('/trial-balance')
@login_required
def trial_balance():
    date_from = request.args.get('date_from') or None
    date_to = request.args.get('date_to') or today_str()
    include_zero = bool(request.args.get('include_zero'))
    tb = get_trial_balance(date_from, date_to, include_zero)
    return render_page('trial_balance', 'trial_balance.html', tb=tb,
                       date_from=date_from, date_to=date_to,
                       include_zero=include_zero, title=t('nav_trial_balance'))


@bp.route('/accounts/<int:account_id>/card')
@login_required
def account_card(account_id):
    data = get_account_ledger(account_id,
                              request.args.get('date_from') or None,
                              request.args.get('date_to') or None)
    if not data:
        abort(404)
    return render_page('accounts', 'account_card.html', data=data,
                       date_from=request.args.get('date_from') or '',
                       date_to=request.args.get('date_to') or '',
                       title=t('account_card'))
