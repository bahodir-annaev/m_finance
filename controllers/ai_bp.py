"""Local AI agent — chat page + JSON endpoints.

Exposes financial data to the model, so access is gated to manager/admin
(same level as the accounting/database area).
"""
from flask import Blueprint, request, jsonify
from auth import require_role
from utils import render_page, get_lang
from models import ai_agent

bp = Blueprint('ai', __name__)


@bp.route('/ai')
@require_role('manager', 'admin')
def ai_page():
    ok, detail = ai_agent.is_available()
    return render_page('ai', 'ai.html',
        ai_ok=ok, ai_detail=detail,
        ai_model=ai_agent.AI_MODEL, ai_url=ai_agent.AI_URL,
    )


@bp.route('/ai/chat', methods=['POST'])
@require_role('manager', 'admin')
def ai_chat():
    if request.is_json:
        question = (request.json.get('question') or '').strip()
    else:
        question = request.form.get('question', '').strip()
    if not question:
        return jsonify({'error': 'empty'}), 400
    try:
        answer = ai_agent.ask(question, lang=get_lang())
        return jsonify({'answer': answer})
    except ai_agent.AIUnavailable as e:
        return jsonify({'error': str(e)}), 503


@bp.route('/ai/summary', methods=['POST'])
@require_role('manager', 'admin')
def ai_summary():
    try:
        return jsonify({'answer': ai_agent.generate_cfo_summary(lang=get_lang())})
    except ai_agent.AIUnavailable as e:
        return jsonify({'error': str(e)}), 503


@bp.route('/ai/anomalies', methods=['POST'])
@require_role('manager', 'admin')
def ai_anomalies():
    try:
        return jsonify({'answer': ai_agent.detect_anomalies(lang=get_lang())})
    except ai_agent.AIUnavailable as e:
        return jsonify({'error': str(e)}), 503


@bp.route('/ai/categorize', methods=['POST'])
@require_role('manager', 'admin')
def ai_categorize():
    desc = request.form.get('description', '').strip()
    direction = request.form.get('direction', 'internal')
    amount = request.form.get('amount')
    if not desc:
        return jsonify({'error': 'empty'}), 400
    try:
        return jsonify(ai_agent.categorize_transaction(desc, amount, direction))
    except ai_agent.AIUnavailable as e:
        return jsonify({'error': str(e)}), 503
