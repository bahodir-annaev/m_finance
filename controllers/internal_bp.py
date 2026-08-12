"""Internal transactions route."""
from flask import Blueprint, request
from flask_login import login_required
from utils import render_page
from models import get_db
from models.base import CHILD_PAID_JOIN, SETTLED_EXPR

bp = Blueprint('internal', __name__)
_VALID_PER_PAGE = {25, 50, 100}
# Whitelist of sortable columns -> SQL expression (prevents SQL injection).
_SORT_COLS = {
    'date': 't.date',
    'description': 't.description',
    'responsible': 't.responsible',
    'paid': 't.paid',
    'category': 't.tx_type',
}


@bp.route('/internal')
@login_required
def internal_page():
    conn = get_db()

    search   = request.args.get('search', '').strip()
    category = request.args.get('category', '').strip()
    resp     = request.args.get('responsible', '').strip()
    date_from = request.args.get('date_from', '').strip()
    date_to   = request.args.get('date_to', '').strip()
    sort      = request.args.get('sort', 'date').strip()
    direction = request.args.get('dir', 'desc').strip().lower()
    if sort not in _SORT_COLS:
        sort = 'date'
    if direction not in ('asc', 'desc'):
        direction = 'desc'
    per_page = int(request.args.get('per_page', 50) or 50)
    if per_page not in _VALID_PER_PAGE:
        per_page = 50
    cur_page = max(1, int(request.args.get('page', 1) or 1))

    # Columns are qualified with `t.` because the list query joins the payment-row
    # sum and a self-join to the parent invoice — both would make bare names ambiguous.
    conds  = ["t.direction='internal'"]
    params = []
    if search:
        conds.append(
            "(ulower(t.description) LIKE ? OR ulower(t.paid_to) LIKE ?"
            " OR ulower(t.responsible) LIKE ? OR ulower(t.notes) LIKE ?)"
        )
        params += [f'%{search.lower()}%'] * 4
    if category:
        conds.append("t.tx_type = ?")
        params.append(category)
    if resp:
        conds.append("t.responsible = ?")
        params.append(resp)
    if date_from:
        conds.append("t.date >= ?")
        params.append(date_from)
    if date_to:
        conds.append("t.date <= ?")
        params.append(date_to)

    where = 'WHERE ' + ' AND '.join(conds)

    total_count = conn.execute(
        f"SELECT COUNT(*) FROM transactions t {where}", params
    ).fetchone()[0]
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    cur_page = min(cur_page, total_pages)
    offset   = (cur_page - 1) * per_page

    order_by = f"ORDER BY {_SORT_COLS[sort]} {direction.upper()}, t.id DESC"
    txs = conn.execute(
        f"SELECT t.*, {SETTLED_EXPR} AS settled, t.amount - {SETTLED_EXPR} AS outstanding,"
        f" par.doc_id AS parent_doc_id"
        f" FROM transactions t"
        f" {CHILD_PAID_JOIN}"
        f" LEFT JOIN transactions par ON par.id = t.parent_tx_id"
        f" {where} {order_by} LIMIT ? OFFSET ?",
        params + [per_page, offset]
    ).fetchall()

    # Payment rows carry amount=0 and their own paid, so these two sums stay correct
    # without a settled join: total_pending is still committed minus cash received.
    agg = conn.execute(
        f"SELECT COALESCE(SUM(t.amount),0), COALESCE(SUM(t.paid),0) FROM transactions t {where}",
        params
    ).fetchone()
    total_amount = agg[0]
    total_paid   = agg[1]

    cats_set = [r[0] for r in conn.execute(
        "SELECT DISTINCT tx_type FROM transactions"
        " WHERE direction='internal' AND tx_type IS NOT NULL ORDER BY tx_type"
    ).fetchall()]
    resp_set = [r[0] for r in conn.execute(
        "SELECT DISTINCT responsible FROM transactions"
        " WHERE direction='internal' AND responsible IS NOT NULL AND responsible != ''"
        " ORDER BY responsible"
    ).fetchall()]

    m_tx_types = conn.execute(
        "SELECT code, label_uz, direction FROM tx_types WHERE is_active=1 ORDER BY sort_order, label_uz"
    ).fetchall()
    m_payment_types = conn.execute(
        "SELECT code, label_uz FROM payment_types WHERE is_active=1 ORDER BY sort_order"
    ).fetchall()
    m_projects = conn.execute("SELECT id, name FROM projects ORDER BY name").fetchall()
    m_phases = conn.execute(
        "SELECT id, project_id, name FROM project_phases ORDER BY project_id, sort_order, id"
    ).fetchall()
    unresolved_ids = {r[0] for r in conn.execute(
        "SELECT DISTINCT transaction_id FROM unresolved_imports WHERE resolved=0"
    ).fetchall()}

    conn.close()

    return render_page('internal', 'internal.html',
        txs=txs,
        total_amount=total_amount,
        total_paid=total_paid,
        total_pending=total_amount - total_paid,
        cats_set=cats_set,
        resp_set=resp_set,
        m_tx_types=m_tx_types,
        m_payment_types=m_payment_types,
        m_projects=m_projects,
        m_phases=m_phases,
        unresolved_ids=unresolved_ids,
        f_search=search,
        f_category=category,
        f_responsible=resp,
        f_date_from=date_from,
        f_date_to=date_to,
        f_sort=sort,
        f_dir=direction,
        cur_page=cur_page,
        per_page=per_page,
        total_pages=total_pages,
        total_count=total_count,
    )
