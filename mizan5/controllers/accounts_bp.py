"""Chart of accounts — the seeded subset, plus the account_map wiring."""
from flask import Blueprint, request, redirect, flash
from flask_login import login_required

from auth import require_level
from models import get_accounts, account_map_all, get_db, now_ts, account_balance
from utils import render_page, t, parse_int

bp = Blueprint('accounts', __name__)

COST_POOLS = ('', 'direct_labor', 'indirect', 'excluded')


@bp.route('/accounts')
@login_required
def accounts_page():
    accounts = get_accounts(active_only=False)
    for a in accounts:
        a['balance'] = account_balance(account_id=a['id'])
    return render_page('accounts', 'accounts.html', accounts=accounts,
                       account_map=account_map_all(), cost_pools=COST_POOLS,
                       title=t('nav_accounts'))


@bp.route('/accounts/save', methods=['POST'])
@login_required
@require_level('admin')
def account_save():
    """Create or edit an account. Codes come from CHART_OF_ACCOUNTS.md; the
    editable parts are the cost-pool flag and whether it is active."""
    account_id = parse_int(request.form.get('id'))
    code = (request.form.get('code') or '').strip()
    conn = get_db()
    try:
        if account_id:
            conn.execute(
                "UPDATE accounts SET name_ru=?, name_uz=?, kind=?, subconto=?,"
                " cost_pool=?, is_active=?, updated_at=? WHERE id=?",
                (request.form.get('name_ru'), request.form.get('name_uz'),
                 request.form.get('kind') or 'A',
                 request.form.get('subconto') or None,
                 request.form.get('cost_pool') or None,
                 1 if request.form.get('is_active') else 0, now_ts(), account_id))
        elif code:
            conn.execute(
                "INSERT OR IGNORE INTO accounts (code, name_ru, name_uz, kind,"
                " subconto, cost_pool, sort_order) VALUES (?,?,?,?,?,?,?)",
                (code, request.form.get('name_ru') or code,
                 request.form.get('name_uz'), request.form.get('kind') or 'A',
                 request.form.get('subconto') or None,
                 request.form.get('cost_pool') or None,
                 parse_int(request.form.get('sort_order'), 500)))
        conn.commit()
        flash(f'<div class="alert alert-success">{t("saved")}</div>', 'success')
    finally:
        conn.close()
    return redirect('/accounts')


@bp.route('/accounts/map', methods=['POST'])
@login_required
@require_level('admin')
def account_map_save():
    """Re-point a posting purpose at a different account."""
    conn = get_db()
    try:
        for key, value in request.form.items():
            if not key.startswith('purpose_') or not value:
                continue
            conn.execute("UPDATE account_map SET account_id=? WHERE purpose=?",
                         (int(value), key[len('purpose_'):]))
        conn.commit()
        flash(f'<div class="alert alert-success">{t("saved")}</div>', 'success')
    finally:
        conn.close()
    return redirect('/accounts')
