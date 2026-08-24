"""Generic record update/delete used by inline edits, plus small JSON lookups.

Only tables in models.base._ALLOWED_TABLES are reachable here — the ledger and
document tables are deliberately excluded so nothing can bypass post_entry()
or the document lifecycle.
"""
from flask import Blueprint, request, jsonify, redirect
from flask_login import login_required

from auth import require_level
from models import (
    update_record, delete_record, get_db, open_invoices, invoice_outstanding,
)

bp = Blueprint('api', __name__)


@bp.route('/api/update/<table>/<int:record_id>', methods=['POST'])
@login_required
@require_level('manager')
def api_update(table, record_id):
    data = {k: v for k, v in request.form.items() if k not in ('id', 'next')}
    ok = update_record(table, record_id, data)
    nxt = request.form.get('next') or request.referrer or '/'
    if request.headers.get('X-Requested-With') == 'fetch':
        return jsonify({'ok': ok})
    return redirect(nxt)


@bp.route('/api/delete/<table>/<int:record_id>', methods=['POST'])
@login_required
@require_level('manager')
def api_delete(table, record_id):
    ok = delete_record(table, record_id)
    nxt = request.form.get('next') or request.referrer or '/'
    if request.headers.get('X-Requested-With') == 'fetch':
        return jsonify({'ok': ok})
    return redirect(nxt)


@bp.route('/api/open-invoices')
@login_required
def api_open_invoices():
    """Open invoices for the payment allocation grid."""
    counterparty_id = request.args.get('counterparty_id', type=int)
    doc_type = request.args.get('doc_type', 'sales_invoice')
    include = request.args.get('include', type=int)
    rows = open_invoices(counterparty_id, doc_type, include_doc_id=include)
    return jsonify([{'id': r['id'], 'number': r['number'], 'date': r['date'],
                     'due_date': r['due_date'], 'total': r['total'],
                     'outstanding': r['outstanding'],
                     'description': r['description'] or ''} for r in rows])


@bp.route('/api/staff-rates')
@login_required
def api_staff_rates():
    """Production staff with current rates — feeds the milestone planner."""
    from models import get_production_staff_rates
    return jsonify([{'id': r['id'], 'name': r['name'], 'role': r['role'],
                     'cost_rate': r.get('cost_rate', 0),
                     'billing_rate': r.get('billing_rate', 0)}
                    for r in get_production_staff_rates()])


@bp.route('/api/invoice/<int:doc_id>/outstanding')
@login_required
def api_invoice_outstanding(doc_id):
    return jsonify({'outstanding': invoice_outstanding(doc_id)})
