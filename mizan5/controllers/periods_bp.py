"""Fiscal periods — closing, reopening, FX revaluation and depreciation."""
from flask import Blueprint, request, redirect, flash
from flask_login import login_required

from auth import require_level
from models import (
    list_periods, close_period, reopen_period, PostingError, get_pnl,
    fx_position, post_fx_revaluation, period_bounds, today_str, period_of,
    depreciation_preview, post_period_depreciation,
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
    # The depreciation card previews one period at a time; the selector posts
    # back as a GET so the chosen period survives a refresh.
    dep_period = request.args.get('dep_period') or period_of(today_str())
    return render_page('periods', 'periods.html', periods=periods,
                       fx=fx_position(), today=today_str(),
                       depreciation=depreciation_preview(dep_period),
                       dep_period=dep_period, title=t('nav_periods'))


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


@bp.route('/periods/depreciate', methods=['POST'])
@login_required
@require_level('admin')
def period_depreciate():
    """Post one period's depreciation on demand.

    Closing a period does this automatically; this route exists for posting a
    month without closing it yet, and for re-posting after a reopen.
    """
    period = request.form.get('period') or period_of(today_str())
    try:
        entry_id, total = post_period_depreciation(period)
        if entry_id:
            flash(f'<div class="alert alert-success">{t("depreciation_posted_ok")} — '
                  f'{total:,.0f}</div>', 'success')
        else:
            flash(f'<div class="alert alert-info">{t("depreciation_nothing")}</div>',
                  'info')
    except PostingError as e:
        flash(f'<div class="alert alert-error">{t(e.key)} {e.detail}</div>', 'error')
    return redirect(f'/periods?dep_period={period}')


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
