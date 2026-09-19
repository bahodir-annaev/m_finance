"""Pricing: the project picker and the scratch quote for prospects.

The full Plan & Price workspace lives on the project page — it prices a
project from its milestone schedule. This page is the two things that
workspace cannot be: a way in for an existing project, and a throwaway quote
for a prospect that has no project record yet. The quote runs through the
same quote_schedule() kernel as the project card, so the two cannot drift.
"""
from flask import Blueprint, request, jsonify
from flask_login import login_required

from auth import require_level
from models import (
    list_projects, get_lookup, get_production_staff_rates, get_target_margin,
    quote_schedule, save_quote_to_project, STANDARD_SCHEDULE,
    RISK_CLIENT_TYPES, RISK_COMPLEXITIES,
)
from utils import render_page, t

bp = Blueprint('pricing', __name__)


def _risk_from(src):
    def num(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return 6
    return {
        'deadline_months': num(src.get('deadline_months') or src.get('deadline') or 6),
        'client_type': src.get('client_type') or 'new',
        'complexity': src.get('complexity') or 'medium',
        'currency': src.get('currency') or 'UZS',
    }


def _rows_from_json(payload):
    """Schedule rows from the scratch editor's JSON body."""
    rows = []
    for r in (payload.get('rows') or []):
        if not isinstance(r, dict):
            continue
        rows.append({
            'name': (r.get('name') or '').strip(),
            'work_type': (r.get('work_type') or '').strip(),
            'start': (r.get('start') or '').strip(),
            'end': (r.get('end') or '').strip(),
            'outsourcing': r.get('outsourcing') or 0,
            'material': r.get('material') or 0,
            'price': r.get('price') or 0,
            'staff': [s for s in (r.get('staff') or []) if isinstance(s, dict)],
        })
    return rows


@bp.route('/pricing')
@login_required
def pricing_page():
    projects = list_projects(billable_only=True, sort='name')
    return render_page('pricing', 'pricing.html',
                       projects=projects,
                       work_types=get_lookup('work_types'),
                       staff_rates=[s for s in get_production_staff_rates() if s['has_salary']],
                       target_margin=get_target_margin(),
                       standard_schedule=STANDARD_SCHEDULE,
                       client_types=RISK_CLIENT_TYPES,
                       complexities=RISK_COMPLEXITIES,
                       title=t('nav_pricing'))


@bp.route('/api/pricing/quote', methods=['POST'])
@login_required
def api_quote():
    """Stateless scratch calculation — same kernel as the project card, no writes."""
    payload = request.get_json(silent=True) or {}
    quote = quote_schedule(_rows_from_json(payload), _risk_from(payload.get('risk') or {}))
    return jsonify({'status': 'ok', 'quote': quote})


@bp.route('/api/pricing/quote/save', methods=['POST'])
@login_required
@require_level('manager')
def api_quote_save():
    """Persist a scratch quote into a project as real milestones + staff rows."""
    payload = request.get_json(silent=True) or {}
    try:
        project_id = int(payload.get('project_id') or 0)
    except (TypeError, ValueError):
        project_id = 0
    if not project_id:
        return jsonify({'error': 'project_id is required'}), 400
    created, err = save_quote_to_project(
        project_id, _rows_from_json(payload),
        risk_kwargs=_risk_from(payload.get('risk') or {}),
        overwrite=bool(payload.get('overwrite')))
    if err == 'notfound':
        return jsonify({'error': 'not found'}), 404
    if err:
        return jsonify({'error': err}), 400
    return jsonify({'status': 'ok', 'created': created, 'project_id': project_id})
