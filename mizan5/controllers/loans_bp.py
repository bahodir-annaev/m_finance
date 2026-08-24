"""Loans — the register; every movement of money is a document.

The register holds the terms. The balance is not stored: it is the ledger
balance on 6820/7820 (money we borrowed) or 5820 (money we lent), which means
a loan can never disagree with the books.
"""
from flask import Blueprint, request, redirect, flash
from flask_login import login_required

from auth import require_level
from models import (
    get_db, now_ts, today_str, save_document, post_document, account_id_for,
    account_balance, DocumentError, PostingError, get_lookup, list_documents,
)
from utils import render_page, t, parse_float, parse_int

bp = Blueprint('loans', __name__)


@bp.route('/loans')
@login_required
def loans_page():
    conn = get_db()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT l.*, c.name AS counterparty_name FROM loans l"
            " LEFT JOIN counterparties c ON c.id = l.counterparty_id"
            " ORDER BY l.status, l.issue_date DESC")]
        counterparties = [dict(r) for r in conn.execute(
            "SELECT id, name FROM counterparties WHERE is_active=1 ORDER BY name")]
    finally:
        conn.close()

    for r in rows:
        purpose = ('loan_long_in' if r['term'] == 'long' else 'loan_short_in') \
            if r['loan_type'] == 'olgan' else 'loan_issued'
        r['ledger_balance'] = account_balance(
            account_id=account_id_for(purpose), counterparty_id=r['counterparty_id'])
        r['documents'] = list_documents(doc_type='loan', limit=100)

    return render_page('loans', 'loans.html', rows=rows,
                       counterparties=counterparties,
                       payment_types=get_lookup('payment_types'),
                       today=today_str(),
                       borrowed=account_balance('6820') + account_balance('7820'),
                       lent=account_balance('5820'),
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
    conn = get_db()
    try:
        if loan_id:
            sets = ', '.join(f'{k}=?' for k in fields)
            conn.execute(f"UPDATE loans SET {sets}, updated_at=? WHERE id=?",
                         list(fields.values()) + [now_ts(), loan_id])
        else:
            cols = ','.join(fields)
            ph = ','.join('?' * len(fields))
            cur = conn.execute(f"INSERT INTO loans ({cols}) VALUES ({ph})",
                               list(fields.values()))
            loan_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    # Booking the money is optional at creation: a loan agreed today may only
    # be disbursed next week, and the register should not force a fake date.
    if request.form.get('post_now') and fields['total_amount'] > 0:
        try:
            doc_id = save_document({
                'doc_type': 'loan', 'date': fields['issue_date'],
                'counterparty_id': fields['counterparty_id'], 'loan_id': loan_id,
                'currency': fields['currency'],
                'total': fields['total_amount'],
                'payment_method': request.form.get('payment_method') or 'bank',
                'description': fields['description'] or t('doc_loan')})
            post_document(doc_id)
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
    conn = get_db()
    try:
        loan = conn.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone()
    finally:
        conn.close()
    if not loan:
        return redirect('/loans')

    principal = parse_float(request.form.get('principal'))
    interest = parse_float(request.form.get('interest'))
    if principal <= 0 and interest <= 0:
        flash(f'<div class="alert alert-error">{t("required_field")}</div>', 'error')
        return redirect('/loans')

    borrowed = loan['loan_type'] == 'olgan'
    principal_purpose = ('loan_long_in' if loan['term'] == 'long' else 'loan_short_in') \
        if borrowed else 'loan_issued'
    interest_purpose = 'interest_expense' if borrowed else 'interest_income'

    lines = []
    if principal > 0:
        lines.append({'account_id': account_id_for(principal_purpose),
                      'amount': principal, 'counterparty_id': loan['counterparty_id'],
                      'description': f'{t("doc_loan")} — asosiy qarz'})
    if interest > 0:
        lines.append({'account_id': account_id_for(interest_purpose),
                      'amount': interest, 'counterparty_id': loan['counterparty_id'],
                      'description': f'{t("doc_loan")} — foiz'})

    try:
        doc_id = save_document({
            'doc_type': 'cash_out' if borrowed else 'cash_in',
            'date': request.form.get('date') or today_str(),
            'counterparty_id': loan['counterparty_id'], 'loan_id': loan_id,
            'total': principal + interest,
            'payment_method': request.form.get('payment_method') or 'bank',
            'description': f'{t("doc_loan")} #{loan_id}'}, lines=lines)
        post_document(doc_id)
        flash(f'<div class="alert alert-success">{t("doc_posted_ok")}</div>', 'success')
    except (DocumentError, PostingError) as e:
        flash(f'<div class="alert alert-error">{t(e.key)} {e.detail}</div>', 'error')
    return redirect('/loans')
