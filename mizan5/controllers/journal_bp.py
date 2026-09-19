"""Journal, trial balance and the account card."""
from flask import Blueprint, request, abort
from flask_login import login_required

from models import (
    get_journal, get_trial_balance, get_account_ledger, get_accounts,
    get_db, today_str, list_bank_accounts, balances_by_analytic, account_balance,
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
    bank_account_id = request.args.get('bank_account_id', type=int)
    date_to = request.args.get('date_to') or None
    data = get_account_ledger(account_id,
                              request.args.get('date_from') or None,
                              date_to, bank_account_id=bank_account_id)
    if not data:
        abort(404)

    # A subdivided money account also shows its balance per bank account.
    bank_accounts = list_bank_accounts(active_only=False, account_id=account_id)
    bank_balances, unassigned = [], 0.0
    if bank_accounts:
        by_bank = {b['key']: b['balance'] for b in balances_by_analytic(
            [account_id], 'bank_account_id', as_of=date_to, min_abs=0)}
        bank_balances = [dict(b, balance=by_bank.get(b['id'], 0.0)) for b in bank_accounts]
        unassigned = round(account_balance(account_id=account_id, as_of=date_to)
                           - sum(b['balance'] for b in bank_balances), 2)
    return render_page('accounts', 'account_card.html', data=data,
                       date_from=request.args.get('date_from') or '',
                       date_to=request.args.get('date_to') or '',
                       bank_accounts=bank_accounts, bank_account_id=bank_account_id,
                       bank_balances=bank_balances, unassigned=unassigned,
                       title=t('account_card'))
