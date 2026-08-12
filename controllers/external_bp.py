"""External transactions route."""
from flask import Blueprint, request
from flask_login import login_required
from utils import render_page
from models import get_db
from models.base import (
    INCOME_TX_SQL, INCOME_TX_TYPES, CHILD_PAID_JOIN, SETTLED_EXPR,
)

bp = Blueprint('external', __name__)
_VALID_PER_PAGE = {25, 50, 100}
# Whitelist of sortable columns -> SQL expression (prevents SQL injection).
_SORT_COLS = {
    'date': 't.date',
    'project': 'p.name',
    'type': 't.tx_type',
    'paid': 't.paid',
}


@bp.route('/external')
@login_required
def external_page():
    conn = get_db()

    search   = request.args.get('search', '').strip()
    project  = request.args.get('project', '').strip()
    tx_type  = request.args.get('tx_type', '').strip()
    status   = request.args.get('status', '').strip()
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

    conds  = ["t.direction='external'"]
    params = []
    if search:
        conds.append(
            "(ulower(t.description) LIKE ? OR ulower(t.client) LIKE ?"
            " OR ulower(t.paid_to) LIKE ? OR ulower(t.notes) LIKE ?"
            " OR ulower(p.name) LIKE ?)"
        )
        params += [f'%{search.lower()}%'] * 5
    if project:
        conds.append("p.name = ?")
        params.append(project)
    if tx_type:
        conds.append("t.tx_type = ?")
        params.append(tx_type)
    if status:
        conds.append("t.status = ?")
        params.append(status)
    if date_from:
        conds.append("t.date >= ?")
        params.append(date_from)
    if date_to:
        conds.append("t.date <= ?")
        params.append(date_to)

    where = 'WHERE ' + ' AND '.join(conds)

    total_count = conn.execute(
        f"SELECT COUNT(*) FROM transactions t"
        f" LEFT JOIN projects p ON t.project_id = p.id {where}",
        params
    ).fetchone()[0]
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    cur_page = min(cur_page, total_pages)
    offset   = (cur_page - 1) * per_page

    order_by = f"ORDER BY {_SORT_COLS[sort]} {direction.upper()}, t.id DESC"
    txs = conn.execute(
        f"SELECT t.*, p.name as project_name,"
        f" {SETTLED_EXPR} AS settled, t.amount - {SETTLED_EXPR} AS outstanding,"
        f" par.doc_id AS parent_doc_id"
        f" FROM transactions t"
        f" LEFT JOIN projects p ON t.project_id = p.id"
        f" {CHILD_PAID_JOIN}"
        f" LEFT JOIN transactions par ON par.id = t.parent_tx_id"
        f" {where} {order_by} LIMIT ? OFFSET ?",
        params + [per_page, offset]
    ).fetchall()

    # Receivable/payable measure against the settled total (invoice `paid` plus its
    # follow-up payment rows), so an invoice collected in instalments clears. The
    # payment rows are excluded as parents — they carry amount=0 and no balance.
    agg = conn.execute(
        f"SELECT"
        f"  COALESCE(SUM(CASE WHEN t.tx_type IN {INCOME_TX_SQL} THEN t.paid ELSE 0 END),0),"
        f"  COALESCE(SUM(CASE WHEN t.tx_type NOT IN {INCOME_TX_SQL} THEN t.amount ELSE 0 END),0),"
        f"  COALESCE(SUM(CASE WHEN t.tx_type IN {INCOME_TX_SQL} AND t.parent_tx_id IS NULL"
        f"       AND {SETTLED_EXPR} < t.amount THEN t.amount - {SETTLED_EXPR} ELSE 0 END),0),"
        f"  COALESCE(SUM(CASE WHEN t.tx_type NOT IN {INCOME_TX_SQL} AND t.parent_tx_id IS NULL"
        f"       AND {SETTLED_EXPR} < t.amount THEN t.amount - {SETTLED_EXPR} ELSE 0 END),0)"
        f" FROM transactions t LEFT JOIN projects p ON t.project_id = p.id"
        f" {CHILD_PAID_JOIN} {where}",
        params
    ).fetchone()
    total_income     = agg[0]
    total_expense    = agg[1]
    total_receivable = agg[2]   # income invoiced but not collected (AR)
    total_payable    = agg[3]   # expense committed but not paid (AP)

    proj_filter_opts = [r[0] for r in conn.execute(
        "SELECT DISTINCT p.name FROM transactions t"
        " LEFT JOIN projects p ON t.project_id = p.id"
        " WHERE t.direction='external' AND p.name IS NOT NULL ORDER BY p.name"
    ).fetchall()]
    type_filter_opts = [r[0] for r in conn.execute(
        "SELECT DISTINCT tx_type FROM transactions"
        " WHERE direction='external' AND tx_type IS NOT NULL ORDER BY tx_type"
    ).fetchall()]
    status_filter_opts = [r[0] for r in conn.execute(
        "SELECT DISTINCT status FROM transactions"
        " WHERE direction='external' AND status IS NOT NULL ORDER BY status"
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

    return render_page('external', 'external.html',
        txs=txs,
        total_income=total_income,
        total_expense=total_expense,
        total_receivable=total_receivable,
        total_payable=total_payable,
        income_tx_types=INCOME_TX_TYPES,
        proj_filter_opts=proj_filter_opts,
        type_filter_opts=type_filter_opts,
        status_filter_opts=status_filter_opts,
        m_tx_types=m_tx_types,
        m_payment_types=m_payment_types,
        m_projects=m_projects,
        m_phases=m_phases,
        unresolved_ids=unresolved_ids,
        f_search=search,
        f_project=project,
        f_tx_type=tx_type,
        f_status=status,
        f_date_from=date_from,
        f_date_to=date_to,
        f_sort=sort,
        f_dir=direction,
        cur_page=cur_page,
        per_page=per_page,
        total_pages=total_pages,
        total_count=total_count,
    )
