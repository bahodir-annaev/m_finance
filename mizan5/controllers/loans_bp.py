"""Loans — the register; every movement of money is a document.

The register holds the terms. The balance is not stored: it is the ledger
balance on 6820/7820 (money we borrowed) or 5820 (money we lent) for this
loan's own documents, which means a loan can never disagree with the books.
A loan closes itself when its principal is repaid (models.loans.sync_loan_status).
"""
from flask import Blueprint, request, redirect, flash
from flask_login import login_required

from auth import require_level
from models import (
    get_db, today_str, list_loans, loan_summary, save_loan, post_loan_issue,
    record_loan_payment, DocumentError, PostingError, get_lookup, list_bank_accounts,
)
from utils import render_page, t, parse_float, parse_int

bp = Blueprint('loans', __name__)


@bp.route('/loans')
@login_required
def loans_page():
    rows = list_loans()
    conn = get_db()
    try:
        counterparties = [dict(r) for r in conn.execute(
            "SELECT id, name FROM counterparties WHERE is_active=1 ORDER BY name")]
    finally:
        conn.close()
    summary = loan_summary(rows)
    return render_page('loans', 'loans.html', rows=rows,
                       taken=[r for r in rows if r['loan_type'] == 'olgan'],
                       given=[r for r in rows if r['loan_type'] == 'bergan'],
                       summary=summary,
                       counterparties=counterparties,
                       payment_types=get_lookup('payment_types'),
                       bank_accounts=list_bank_accounts(),
                       today=today_str(),
                       borrowed=summary['borrowed'], lent=summary['lent'],
                       title=t('nav_loans'))


@bp.route('/loans/save', methods=['POST'])
@login_required
@require_level('manager')
def loan_save():
    loan_id = parse_int(request.form.get('id'))
    fields = {
        'loan_type': request.form.get('loan_type') or 'olgan',
        'counterparty_id': parse_int(request.form.get('counterparty_id')),
        'description': request.form.get('description') or None,
        'total_amount': parse_float(request.form.get('total_amount')),
        'currency': request.form.get('currency') or 'UZS',
        'interest_rate': parse_float(request.form.get('interest_rate')),
        'term': request.form.get('term') or 'short',
        'issue_date': request.form.get('issue_date') or today_str(),
        'due_date': request.form.get('due_date') or None,
        'status': request.form.get('status') or 'ochiq',
        'notes': request.form.get('notes') or None,
    }
    loan_id = save_loan(fields, loan_id)

    # Booking the money is optional at creation: a loan agreed today may only
    # be disbursed next week, and the register should not force a fake date.
    if request.form.get('post_now') and fields['total_amount'] > 0:
        try:
            post_loan_issue({**fields, 'id': loan_id},
                            payment_method=request.form.get('payment_method') or 'bank',
                            bank_account_id=parse_int(request.form.get('bank_account_id')),
                            description=fields['description'] or t('doc_loan'))
            flash(f'<div class="alert alert-success">{t("doc_posted_ok")}</div>', 'success')
        except (DocumentError, PostingError) as e:
            flash(f'<div class="alert alert-error">{t(e.key)} {e.detail}</div>', 'error')
    else:
        flash(f'<div class="alert alert-success">{t("saved")}</div>', 'success')
    return redirect('/loans')


@bp.route('/loans/<int:loan_id>/payment', methods=['POST'])
@login_required
@require_level('manager')
def loan_payment(loan_id):
    """Repayment: principal against the loan account, interest to 9610/9530."""
    principal = parse_float(request.form.get('principal'))
    interest = parse_float(request.form.get('interest'))
    if principal <= 0 and interest <= 0:
        flash(f'<div class="alert alert-error">{t("required_field")}</div>', 'error')
        return redirect('/loans')
    try:
        record_loan_payment(
            loan_id, principal=principal, interest=interest,
            date=request.form.get('date') or today_str(),
            payment_method=request.form.get('payment_method') or 'bank',
            bank_account_id=parse_int(request.form.get('bank_account_id')),
            label=t('doc_loan'))
        flash(f'<div class="alert alert-success">{t("doc_posted_ok")}</div>', 'success')
    except ValueError:
        flash(f'<div class="alert alert-error">{t("error")}</div>', 'error')
    except (DocumentError, PostingError) as e:
        flash(f'<div class="alert alert-error">{t(e.key)} {e.detail}</div>', 'error')
    return redirect('/loans')
