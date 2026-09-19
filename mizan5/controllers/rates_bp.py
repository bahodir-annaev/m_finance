"""The rates page — the app's headline output.

Shows each production employee's fully burdened cost per hour together with the
three benchmark figures (overhead rate, utilization, net multiplier) so the
firm can sanity-check itself against A/E industry norms.
"""
from flask import Blueprint, request, redirect, flash
from flask_login import login_required

from auth import require_level
from models import (
    get_rates_overview, get_available_hours, set_setting,
    snapshot_period_allocations, get_period_snapshot, latest_period,
    depreciation_reconciliation, ledger_overhead_monthly, overhead_budget_monthly,
    overhead_monthly,
)
from utils import render_page, t

bp = Blueprint('rates', __name__)

# A/E industry reference bands, shown next to the firm's own figures.
BENCHMARKS = {
    'overhead_rate': (150, 180),      # indirect cost as % of direct labor
    'utilization': (75, 85),          # chargeable share of available hours
    'net_multiplier': (2.75, 3.25),   # billing rate over raw labor rate
}


@bp.route('/rates')
@login_required
def rates_page():
    period = request.args.get('period') or None
    overview = get_rates_overview(period)
    return render_page('rates', 'rates.html',
                       ov=overview, hours=get_available_hours(),
                       benchmarks=BENCHMARKS,
                       snapshot=get_period_snapshot(overview['period'])
                                if overview['period'] else [],
                       latest=latest_period(),
                       depreciation=depreciation_reconciliation(),
                       title=t('rates_title'))


@bp.route('/rates/settings', methods=['POST'])
@login_required
@require_level('admin')
def rates_settings():
    """The two switches that change how the rate is built."""
    set_setting('allocation_base_labor_cost',
                1 if request.form.get('allocation_base') == 'labor_cost' else 0)
    set_setting('overhead_from_ledger',
                1 if request.form.get('overhead_source') == 'ledger' else 0)
    for key in ('billing_multiplier', 'holidays_per_year', 'avg_leave_days',
                'overhead_window_months'):
        value = request.form.get(key)
        if value:
            try:
                set_setting(key, float(value))
            except ValueError:
                pass
    flash(f'<div class="alert alert-success">{t("saved")}</div>', 'success')
    return redirect('/rates')


@bp.route('/rates/snapshot', methods=['POST'])
@login_required
@require_level('manager')
def rates_snapshot():
    period = request.form.get('period') or latest_period()
    if period:
        written = snapshot_period_allocations(period)
        flash(f'<div class="alert alert-success">{t("saved")} — {written}</div>', 'success')
    return redirect('/rates')


@bp.route('/rates/overhead')
@login_required
def overhead_reconciliation():
    """Ledger pool against the static budget table, side by side.

    `reason` is what the rate engine actually did, not what the setting asks
    for: asking for the ledger and silently getting the budget table is the
    failure mode this page exists to make visible.
    """
    resolved = overhead_monthly()
    return render_page('rates', 'overhead.html',
                       ledger=ledger_overhead_monthly(),
                       budget_total=overhead_budget_monthly(),
                       active=resolved['source'],
                       reason=resolved['reason'],
                       title=t('nav_overhead'))
