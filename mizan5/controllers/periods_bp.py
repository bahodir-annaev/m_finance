"""Fiscal periods — closing, reopening and the FX revaluation routine."""
from flask import Blueprint, request, redirect, flash
from flask_login import login_required

from auth import require_level
from models import (
    list_periods, close_period, reopen_period, PostingError, get_pnl,
    fx_position, post_fx_revaluation, period_bounds, today_str,
)
from utils import render_page, t

bp = Blueprint('periods', __name__)


@bp.route('/periods')
@login_required
def periods_page():
    periods = list_periods()
    for p in periods:
        pnl = get_pnl(p['start_date'], p['end_date'])
        p['income'] = pnl['total_income']
        p['expense'] = pnl['total_expense']
        p['profit'] = pnl['net_profit']
    return render_page('periods', 'periods.html', periods=periods,
                       fx=fx_position(), today=today_str(), title=t('nav_periods'))


@bp.route('/periods/close', methods=['POST'])
@login_required
@require_level('admin')
def period_close():
    period = request.form.get('period')
    status = request.form.get('status') or 'hard_closed'
    try:
        result, err = close_period(period, status=status)
        if err == 'already_closed':
            flash(f'<div class="alert alert-warn">{t("period_soft_closed")}</div>', 'warn')
        else:
            flash(f'<div class="alert alert-success">{t("period_closed_ok")} — '
                  f'{result["net_profit"]:,.0f}</div>', 'success')
    except PostingError as e:
        flash(f'<div class="alert alert-error">{t(e.key)} {e.detail}</div>', 'error')
    return redirect('/periods')


@bp.route('/periods/reopen', methods=['POST'])
@login_required
@require_level('admin')
def period_reopen():
    reopen_period(request.form.get('period'))
    flash(f'<div class="alert alert-success">{t("period_reopened")}</div>', 'success')
    return redirect('/periods')


@bp.route('/periods/revalue', methods=['POST'])
@login_required
@require_level('admin')
def period_revalue():
    as_of = request.form.get('as_of') or today_str()
    try:
        entry_id, total = post_fx_revaluation(as_of)
        if entry_id:
            flash(f'<div class="alert alert-success">{t("fx_revaluation")}: '
                  f'{total:,.0f}</div>', 'success')
        else:
            flash(f'<div class="alert alert-info">{t("no_records")}</div>', 'info')
    except PostingError as e:
        flash(f'<div class="alert alert-error">{t(e.key)} {e.detail}</div>', 'error')
    return redirect('/periods')
