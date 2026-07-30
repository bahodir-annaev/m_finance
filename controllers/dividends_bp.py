"""Dividends (founder distributions) route."""
from datetime import datetime
from flask import Blueprint, request, abort
from flask_login import login_required
from utils import render_page, t, fmt
from models import get_db, get_all_dividends, get_dividend_summary, add_dividend

bp = Blueprint('dividends', __name__)


@bp.route('/dividends', methods=['GET', 'POST'])
@login_required
def dividends_page():
    msg = ''
    if request.method == 'POST':
        if not _can_edit():
            abort(403)
        msg = _handle_post()

    conn = get_db()
    payment_types = conn.execute(
        "SELECT code, label_uz FROM payment_types WHERE is_active=1 ORDER BY sort_order"
    ).fetchall()
    conn.close()

    return render_page('dividends', 'dividends.html',
        msg=msg,
        today_str=datetime.now().strftime('%Y-%m-%d'),
        dividends=get_all_dividends(),
        summary=get_dividend_summary(),
        payment_types=payment_types,
        can_edit=_can_edit(),
    )


def _can_edit():
    from flask_login import current_user
    return getattr(current_user, 'role', None) in ('manager', 'admin')


def _handle_post():
    today = datetime.now().strftime('%Y-%m-%d')
    amount = float(request.form.get('amount', 0) or 0)
    if amount <= 0:
        return f'<div class="alert alert-warn">{t("div_msg_need_amount")}</div>'

    # A blank "paid" means not paid out yet (status stays pending) — it must
    # never silently mean "paid in full", which would deduct undistributed
    # dividends from the running cash balance.
    paid = float(request.form.get('paid', 0) or 0)
    founder = request.form.get('founder', '').strip()
    add_dividend(
        date=request.form.get('date', today) or today,
        founder=founder,
        amount=amount,
        paid=paid,
        currency=request.form.get('currency', 'UZS'),
        payment_type=request.form.get('payment_type', 'bank'),
        doc_id=request.form.get('doc_id', ''),
        responsible=request.form.get('responsible', ''),
        description=request.form.get('description', ''),
        notes=request.form.get('notes', ''),
    )
    return f'<div class="alert alert-success">{t("div_msg_saved", founder or "-", fmt(amount))}</div>'
