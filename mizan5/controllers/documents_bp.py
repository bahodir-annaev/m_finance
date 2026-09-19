"""Document list views and the data-entry modals behind them.

One route family serves every document type: the list page renders the right
modal for its type, and saving always goes through models.documents so the
lifecycle rules (draft-only edits, allocation limits, balanced postings) are
enforced in one place rather than per form.
"""
from flask import Blueprint, request, redirect, flash, abort
from flask_login import login_required

from auth import require_level
from models import (
    DocumentError, PostingError, save_document, post_document, void_document,
    delete_draft, get_document, list_documents, count_documents, open_invoices,
    get_accounts, get_lookup, get_db, today_str, default_vat_rate, list_projects,
    get_project_milestones, DIRECTIONS, list_bank_accounts, DOCUMENT_SORTS,
    sync_loan_status,
)
from utils import render_page, t, parse_float, parse_int, form_rows

bp = Blueprint('documents', __name__)

# Which modal template and page title each type uses.
DOC_PAGES = {
    'sales_invoice': ('nav_sales_invoices', 'doc_sales_invoice'),
    'purchase_invoice': ('nav_purchase_invoices', 'doc_purchase_invoice'),
    'cash_in': ('nav_cash_in', 'doc_cash_in'),
    'cash_out': ('nav_cash_out', 'doc_cash_out'),
    'manual': ('doc_manual', 'doc_manual'),
    'opening': ('nav_opening', 'doc_opening'),
    'dividend': ('doc_dividend', 'doc_dividend'),
}

PER_PAGE_CHOICES = (25, 50, 100, 200)

INVOICE_LINE_FIELDS = ('description', 'quantity', 'unit_price', 'amount',
                       'vat_rate', 'vat_amount', 'account_id', 'project_id', 'phase_id')
CASH_LINE_FIELDS = ('description', 'amount', 'account_id', 'project_id')
JOURNAL_LINE_FIELDS = ('description', 'account_id', 'debit', 'credit',
                       'counterparty_id', 'project_id')


def _flash_error(err_key, detail=''):
    label = t(err_key)
    flash(f'<div class="alert alert-error">{label}'
          f'{" — " + detail if detail else ""}</div>', 'error')


def _collect_lines(doc_type):
    """Pull the modal's line grid out of the form, per document type."""
    if doc_type in ('sales_invoice', 'purchase_invoice'):
        rows = form_rows(request.form, 'line', INVOICE_LINE_FIELDS)
        return [{
            'description': r['description'],
            'quantity': parse_float(r['quantity'], 1),
            'unit_price': parse_float(r['unit_price']),
            'amount': parse_float(r['amount']),
            'vat_rate': parse_float(r['vat_rate']),
            'vat_amount': parse_float(r['vat_amount']),
            'account_id': parse_int(r['account_id']),
            'project_id': parse_int(r['project_id']),
            'phase_id': parse_int(r['phase_id']),
        } for r in rows if parse_float(r['amount']) or r['description'].strip()]

    if doc_type in ('cash_in', 'cash_out'):
        rows = form_rows(request.form, 'line', CASH_LINE_FIELDS)
        return [{
            'description': r['description'],
            'amount': parse_float(r['amount']),
            'account_id': parse_int(r['account_id']),
            'project_id': parse_int(r['project_id']),
        } for r in rows if parse_float(r['amount']) and parse_int(r['account_id'])]

    rows = form_rows(request.form, 'line', JOURNAL_LINE_FIELDS)
    return [{
        'description': r['description'],
        'account_id': parse_int(r['account_id']),
        'debit': parse_float(r['debit']),
        'credit': parse_float(r['credit']),
        'counterparty_id': parse_int(r['counterparty_id']),
        'project_id': parse_int(r['project_id']),
    } for r in rows if parse_int(r['account_id'])
        and (parse_float(r['debit']) or parse_float(r['credit']))]


def _collect_allocations():
    rows = form_rows(request.form, 'alloc', ('invoice_doc_id', 'amount'))
    return [{'invoice_doc_id': parse_int(r['invoice_doc_id']),
             'amount': parse_float(r['amount'])}
            for r in rows
            if parse_int(r['invoice_doc_id']) and parse_float(r['amount']) > 0]


@bp.route('/documents/<doc_type>')
@login_required
def document_list(doc_type):
    if doc_type not in DOC_PAGES:
        abort(404)
    title_key, modal_key = DOC_PAGES[doc_type]
    filters = {
        'date_from': request.args.get('date_from') or None,
        'date_to': request.args.get('date_to') or None,
        'status': request.args.get('status') or None,
        'search': request.args.get('q') or None,
        'counterparty_id': request.args.get('counterparty_id', type=int),
        'project_id': request.args.get('project_id', type=int),
        'responsible_id': request.args.get('responsible_id', type=int),
        'direction': request.args.get('direction') or None,
    }
    # Paging and sorting. The sort key is whitelisted in the model; per-page
    # is whitelisted here so a crafted URL cannot ask for a million rows.
    sort = request.args.get('sort') or 'date'
    if sort not in DOCUMENT_SORTS:
        sort = 'date'
    sort_dir = 'asc' if request.args.get('dir') == 'asc' else 'desc'
    per_page = request.args.get('per_page', type=int) or 50
    if per_page not in PER_PAGE_CHOICES:
        per_page = 50
    total_count = count_documents(doc_type=doc_type, **filters)
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = min(max(1, request.args.get('page', type=int) or 1), total_pages)
    rows = list_documents(doc_type=doc_type, limit=per_page, offset=(page - 1) * per_page,
                          sort=sort, sort_dir=sort_dir, **filters)

    conn = get_db()
    counterparties = [dict(r) for r in conn.execute(
        "SELECT id, name, inn FROM counterparties WHERE is_active=1 ORDER BY name")]
    staff = [dict(r) for r in conn.execute(
        "SELECT id, name FROM staff WHERE is_active=1 ORDER BY name")]
    conn.close()

    # The query string without page/sort, so the pager and the header links can
    # append their own without losing the filter.
    base_args = {k: v for k, v in request.args.items()
                 if k not in ('page', 'sort', 'dir') and v}
    context = {
        'doc_type': doc_type, 'rows': rows, 'filters': filters,
        'title': t(title_key), 'modal_title': t(modal_key),
        'counterparties': counterparties, 'staff': staff,
        'directions': DIRECTIONS,
        'projects': list_projects(sort='name'),
        'accounts': get_accounts(),
        'payment_types': get_lookup('payment_types'),
        'bank_accounts': list_bank_accounts(),
        'today': today_str(),
        'vat_rate': default_vat_rate(),
        'totals': {
            'total': sum(r['total'] for r in rows),
            'settled': sum(r.get('settled') or 0 for r in rows),
            'outstanding': sum(r.get('outstanding') or 0 for r in rows),
        },
        'sort': sort, 'sort_dir': sort_dir, 'cur_page': page, 'per_page': per_page,
        'total_pages': total_pages, 'total_count': total_count,
        'per_page_choices': PER_PAGE_CHOICES, 'base_args': base_args,
    }
    if doc_type in ('cash_in', 'cash_out'):
        context['open_invoices'] = open_invoices(
            None, 'sales_invoice' if doc_type == 'cash_in' else 'purchase_invoice')
    return render_page(doc_type, 'documents.html', **context)


@bp.route('/documents/<doc_type>/save', methods=['POST'])
@login_required
@require_level('manager')
def document_save(doc_type):
    if doc_type not in DOC_PAGES:
        abort(404)
    doc_id = parse_int(request.form.get('id'))
    data = {
        'doc_type': doc_type,
        'date': request.form.get('date'),
        'counterparty_id': parse_int(request.form.get('counterparty_id')),
        'project_id': parse_int(request.form.get('project_id')),
        'phase_id': parse_int(request.form.get('phase_id')),
        'responsible_id': parse_int(request.form.get('responsible_id')),
        'contract_ref': request.form.get('contract_ref'),
        'external_number': request.form.get('external_number'),
        'currency': request.form.get('currency') or 'UZS',
        'exchange_rate': parse_float(request.form.get('exchange_rate')) or None,
        'total': parse_float(request.form.get('total')),
        'total_cur': parse_float(request.form.get('total_cur')) or None,
        'payment_method': request.form.get('payment_method'),
        'bank_account': request.form.get('bank_account'),
        'bank_account_id': parse_int(request.form.get('bank_account_id')),
        'due_date': request.form.get('due_date'),
        'cash_purpose': request.form.get('cash_purpose'),
        'description': request.form.get('description'),
        'notes': request.form.get('notes'),
    }
    lines = _collect_lines(doc_type)
    allocations = _collect_allocations() if doc_type in ('cash_in', 'cash_out') else None

    try:
        new_id = save_document(data, lines=lines, allocations=allocations, doc_id=doc_id)
        if request.form.get('post_now'):
            post_document(new_id)
            flash(f'<div class="alert alert-success">{t("doc_posted_ok")}</div>', 'success')
        else:
            flash(f'<div class="alert alert-success">{t("saved")}</div>', 'success')
    except (DocumentError, PostingError) as e:
        _flash_error(e.key, e.detail)
    return redirect(f'/documents/{doc_type}')


@bp.route('/documents/<int:doc_id>/post', methods=['POST'])
@login_required
@require_level('manager')
def document_post(doc_id):
    doc = get_document(doc_id)
    if not doc:
        abort(404)
    try:
        post_document(doc_id)
        flash(f'<div class="alert alert-success">{t("doc_posted_ok")}</div>', 'success')
    except (DocumentError, PostingError) as e:
        _flash_error(e.key, e.detail)
    return redirect(request.form.get('next') or f"/documents/{doc['doc_type']}")


@bp.route('/documents/<int:doc_id>/void', methods=['POST'])
@login_required
@require_level('admin')
def document_void(doc_id):
    doc = get_document(doc_id)
    if not doc:
        abort(404)
    try:
        void_document(doc_id, reason=request.form.get('reason'))
        if doc.get('loan_id'):
            sync_loan_status(doc['loan_id'])
        flash(f'<div class="alert alert-success">{t("doc_void_done")}</div>', 'success')
    except (DocumentError, PostingError) as e:
        _flash_error(e.key, e.detail)
    return redirect(request.form.get('next') or f"/documents/{doc['doc_type']}")


@bp.route('/documents/<int:doc_id>/delete', methods=['POST'])
@login_required
@require_level('manager')
def document_delete(doc_id):
    doc = get_document(doc_id)
    if not doc:
        abort(404)
    try:
        delete_draft(doc_id)
        flash(f'<div class="alert alert-success">{t("deleted")}</div>', 'success')
    except DocumentError as e:
        _flash_error(e.key, e.detail)
    return redirect(f"/documents/{doc['doc_type']}")


@bp.route('/documents/<int:doc_id>')
@login_required
def document_detail(doc_id):
    doc = get_document(doc_id)
    if not doc:
        abort(404)
    entry = None
    if doc['entry_id']:
        from models import get_journal
        entries = get_journal(document_id=doc_id, limit=5)
        entry = entries[0] if entries else None
    milestones = get_project_milestones(doc['project_id']) if doc['project_id'] else []
    return render_page(doc['doc_type'], 'document_detail.html',
                       doc=doc, entry=entry, milestones=milestones,
                       title=f"{t(DOC_PAGES.get(doc['doc_type'], ('document',))[0])}"
                             f" {doc['number']}")
