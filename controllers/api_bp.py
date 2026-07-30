"""Language switching + generic CRUD JSON API."""
from flask import Blueprint, request, jsonify, redirect, make_response
from flask_login import login_required
from auth import require_role
from models import get_record, update_record, delete_record, update_transaction

bp = Blueprint('api', __name__)

SUPPORTED_LANGS = ['uz', 'en', 'ru']


@bp.route('/set-lang/<lang>')
def set_lang(lang):
    if lang not in SUPPORTED_LANGS:
        lang = 'uz'
    referer = request.headers.get('Referer', '/')
    resp = make_response(redirect(referer))
    resp.set_cookie('mizan_lang', lang, max_age=365 * 24 * 3600)
    return resp


@bp.route('/api/get/<table>/<int:record_id>')
@login_required
def api_get_record(table, record_id):
    record = get_record(table, record_id)
    if record is None:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(record)


@bp.route('/api/update/<table>/<int:record_id>', methods=['POST'])
@require_role('manager', 'admin')
def api_update_record(table, record_id):
    data = request.form.to_dict()
    for key in ['amount', 'paid', 'base_salary', 'premium', 'price', 'annual_cost',
                'monthly_amount', 'rate', 'value', 'lifespan_months', 'quantity']:
        if key in data:
            try:
                data[key] = float(data[key])
            except (ValueError, TypeError):
                pass
    # FK columns: blank string means "unlink" (NULL), otherwise coerce to int
    for key in ['project_id', 'counterparty_id', 'responsible_id', 'category_id', 'phase_id']:
        if key in data:
            val = str(data[key]).strip()
            data[key] = int(val) if val.isdigit() else None
    if table == 'transactions':
        # transactions carry derived columns (status, amount_usd, exchange_rate)
        # that must be recomputed from the new amount/paid values
        success = update_transaction(record_id, data)
    else:
        success = update_record(table, record_id, data)
    if success:
        return jsonify({'status': 'ok'})
    return jsonify({'error': 'Update failed'}), 400


@bp.route('/api/delete/<table>/<int:record_id>', methods=['POST'])
@require_role('manager', 'admin')
def api_delete_record(table, record_id):
    success = delete_record(table, record_id)
    if success:
        return jsonify({'status': 'ok'})
    return jsonify({'error': 'Delete failed'}), 400
