"""Financial reports — P&L, balance sheet, cash flow, VAT and aging."""
from flask import Blueprint, request
from flask_login import login_required

from models import (
    get_pnl, get_balance_sheet, get_cash_flow, get_aging, aging_by_counterparty,
    get_vat_report, today_str, cash_by_account,
)
from utils import render_page, t

bp = Blueprint('reports', __name__)


def _range():
    """Default reporting window: year to date."""
    today = today_str()
    return (request.args.get('date_from') or f'{today[:4]}-01-01',
            request.args.get('date_to') or today)


@bp.route('/reports/pnl')
@login_required
def pnl():
    date_from, date_to = _range()
    return render_page('pnl', 'pnl.html', pnl=get_pnl(date_from, date_to),
                       date_from=date_from, date_to=date_to, title=t('nav_pnl'))


@bp.route('/reports/balance-sheet')
@login_required
def balance_sheet():
    as_of = request.args.get('as_of') or today_str()
    return render_page('balance_sheet', 'balance_sheet.html',
                       bs=get_balance_sheet(as_of), as_of=as_of,
                       title=t('nav_balance_sheet'))


@bp.route('/reports/cashflow')
@login_required
def cashflow():
    date_from, date_to = _range()
    return render_page('cashflow', 'cashflow.html',
                       cf=get_cash_flow(date_from, date_to),
                       accounts=cash_by_account(date_to),
                       date_from=date_from, date_to=date_to, title=t('nav_cashflow'))


@bp.route('/reports/vat')
@login_required
def vat():
    date_from, date_to = _range()
    return render_page('vat', 'vat.html', vat=get_vat_report(date_from, date_to),
                       date_from=date_from, date_to=date_to, title=t('nav_vat'))


@bp.route('/reports/aging')
@login_required
def aging():
    as_of = request.args.get('as_of') or today_str()
    kind = request.args.get('kind') or 'ar'
    if kind not in ('ar', 'ap'):
        kind = 'ar'
    return render_page('aging', 'aging.html', aging=get_aging(kind, as_of),
                       by_counterparty=aging_by_counterparty(kind, as_of),
                       kind=kind, as_of=as_of, title=t('nav_aging'))
