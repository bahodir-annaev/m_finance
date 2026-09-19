"""Bank accounts — the firm's own расчётные счета behind 5110 / 5210."""
from flask import Blueprint, request, redirect, flash
from flask_login import login_required

from auth import require_level
from models import (
    list_bank_accounts, save_bank_account, bank_account_balances,
    subdividable_accounts, assign_unassigned_lines, today_str,
)
from utils import render_page, t, parse_int

bp = Blueprint('bank_accounts', __name__)


@bp.route('/bank-accounts')
@login_required
def bank_accounts_page():
    as_of = request.args.get('as_of') or today_str()
    return render_page('bank_accounts', 'bank_accounts.html',
                       groups=bank_account_balances(as_of=as_of),
                       rows=list_bank_accounts(active_only=False),
                       ledgers=subdividable_accounts(),
                       as_of=as_of, title=t('nav_bank_accounts'))


@bp.route('/bank-accounts/save', methods=['POST'])
@login_required
@require_level('manager')
def bank_account_save():
    bid = parse_int(request.form.get('id'))
    fields = {
        'account_id': parse_int(request.form.get('account_id')),
        'name': (request.form.get('name') or '').strip(),
        'account_number': (request.form.get('account_number') or '').strip() or None,
        'bank_name': (request.form.get('bank_name') or '').strip() or None,
        'mfo': (request.form.get('mfo') or '').strip() or None,
        'currency': request.form.get('currency') or 'UZS',
        'is_default': 1 if request.form.get('is_default') else 0,
        'is_active': 1 if request.form.get('is_active') else 0,
        'notes': (request.form.get('notes') or '').strip() or None,
    }
    try:
        save_bank_account(fields, bid)
        flash(f'<div class="alert alert-success">{t("saved")}</div>', 'success')
    except ValueError:
        flash(f'<div class="alert alert-error">{t("required_field")}</div>', 'error')
    return redirect('/bank-accounts')


@bp.route('/bank-accounts/<int:bank_account_id>/assign-unassigned', methods=['POST'])
@login_required
@require_level('admin')
def bank_account_assign(bank_account_id):
    try:
        n = assign_unassigned_lines(bank_account_id)
        flash(f'<div class="alert alert-success">{t("assign_unassigned_done", n)}</div>',
              'success')
    except ValueError:
        flash(f'<div class="alert alert-error">{t("error")}</div>', 'error')
    return redirect('/bank-accounts')
