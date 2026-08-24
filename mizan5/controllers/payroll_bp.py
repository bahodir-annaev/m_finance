"""Payroll runs — the зарплатная ведомость and its remittances."""
from datetime import datetime

from flask import Blueprint, request, redirect, flash
from flask_login import login_required

from auth import require_level
from models import (
    build_payroll_rows, payroll_totals, save_payroll, get_payroll_for_period,
    list_payroll_periods, payroll_liabilities, build_remittance_lines,
    post_document, DocumentError, PostingError, get_document, today_str,
    get_lookup,
)
from utils import render_page, t, parse_float, parse_int, form_rows

bp = Blueprint('payroll', __name__)


@bp.route('/payroll')
@login_required
def payroll_page():
    period = request.args.get('period') or today_str()[:7]
    existing = get_payroll_for_period(period)
    if existing:
        rows = [{'staff_id': l['staff_id'], 'staff_name': l['staff_name'],
                 'gross': l['gross'], 'pit': l['pit'], 'social': l['social'],
                 'net': (l['gross'] or 0) - (l['pit'] or 0),
                 'description': l['description']} for l in existing['lines']]
    else:
        rows = build_payroll_rows(period)
    return render_page('payroll', 'payroll.html', period=period, rows=rows,
                       totals=payroll_totals(rows), doc=existing,
                       history=list_payroll_periods(),
                       liabilities=payroll_liabilities(),
                       remittance=build_remittance_lines(period),
                       payment_types=get_lookup('payment_types'),
                       today=today_str(), title=t('nav_payroll'))


@bp.route('/payroll/save', methods=['POST'])
@login_required
@require_level('manager')
def payroll_save():
    period = request.form.get('period')
    rows = form_rows(request.form, 'row', ('staff_id', 'gross', 'pit', 'social'))
    payload = [{'staff_id': parse_int(r['staff_id']),
                'gross': parse_float(r['gross']),
                'pit': parse_float(r['pit']),
                'social': parse_float(r['social'])} for r in rows
               if parse_int(r['staff_id']) and parse_float(r['gross']) > 0]
    if not payload:
        flash(f'<div class="alert alert-error">{t("doc_no_lines")}</div>', 'error')
        return redirect(f'/payroll?period={period}')

    existing = get_payroll_for_period(period)
    doc_id = existing['id'] if existing and existing['status'] == 'draft' else None
    try:
        new_id = save_payroll(period, payload, doc_id=doc_id)
        if request.form.get('post_now'):
            post_document(new_id)
            flash(f'<div class="alert alert-success">{t("doc_posted_ok")}</div>', 'success')
        else:
            flash(f'<div class="alert alert-success">{t("saved")}</div>', 'success')
    except (DocumentError, PostingError) as e:
        flash(f'<div class="alert alert-error">{t(e.key)} {e.detail}</div>', 'error')
    return redirect(f'/payroll?period={period}')


@bp.route('/payroll/remit', methods=['POST'])
@login_required
@require_level('manager')
def payroll_remit():
    """Pay out a posted payroll: one cash_out clearing 6710, 6420.1 and 6520."""
    from models import save_document

    period = request.form.get('period')
    lines = build_remittance_lines(period)
    lines = [l for l in lines if l['amount'] > 0]
    if not lines:
        flash(f'<div class="alert alert-warn">{t("doc_not_posted")}</div>', 'warn')
        return redirect(f'/payroll?period={period}')

    total = sum(l['amount'] for l in lines)
    try:
        doc_id = save_document(
            {'doc_type': 'cash_out', 'date': request.form.get('date') or today_str(),
             'payment_method': request.form.get('payment_method') or 'bank',
             'total': total, 'description': f'Ish haqi to\'lovi {period}',
             'cash_purpose': 'payroll'},
            lines=lines)
        post_document(doc_id)
        flash(f'<div class="alert alert-success">{t("doc_posted_ok")}</div>', 'success')
    except (DocumentError, PostingError) as e:
        flash(f'<div class="alert alert-error">{t(e.key)} {e.detail}</div>', 'error')
    return redirect(f'/payroll?period={period}')
