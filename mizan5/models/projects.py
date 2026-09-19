"""Projects — profitability, budget plan-vs-fact, and the portfolio view.

Money comes from the ledger, labor from timesheets priced at the cost rate.
The split matters: the ledger knows what was invoiced and what was bought for a
project, but it cannot know how many of an architect's hours went to which
project — that is what the timesheet is for.

Risk coefficient is never applied to cost. It only raises a quoted price.
"""
from .base import get_db, now_ts, is_period_closed, _write_audit, get_current_usd_rate
from .ledger import account_id_for
from .staff import calculate_hourly_rate, rate_context

# Whole-project expense variance bands (carried over from the v4 budget page).
_BUDGET_OVER = 5      # more than +5% over the baseline reads as over budget
_BUDGET_UNDER = -10   # more than 10% under reads as comfortably inside


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def budget_verdict(d_pct):
    """(status_key, css_class) for whole-project expense variance."""
    if d_pct > _BUDGET_OVER:
        return 'over', 'loss'
    if d_pct < _BUDGET_UNDER:
        return 'under', 'profit'
    return 'border', ''


# ========== CRUD ==========

PROJECT_SORTS = {
    'hours': 'total_hours DESC, p.name',
    'name': 'p.name',
    'status': 'p.status, p.name',
    'contract': 'p.contract_amount DESC, p.name',
    'start': "COALESCE(p.start_date,'') DESC, p.name",
}


def list_projects(status=None, billable_only=False, search=None, sort='hours'):
    """Project register rows with the counts the list page shows: lifetime
    hours, distinct people, milestones done/total and how many are late."""
    conn = get_db()
    try:
        sql = ("SELECT p.*, c.name AS client_name, s.name AS responsible_name,"
               " COALESCE(h.total_hours, 0) AS total_hours,"
               " COALESCE(h.staff_count, 0) AS staff_count,"
               " (SELECT COUNT(*) FROM project_phases pp WHERE pp.project_id = p.id) AS ms_total,"
               " (SELECT COUNT(*) FROM project_phases pp WHERE pp.project_id = p.id"
               "    AND pp.status='done') AS ms_done,"
               " (SELECT COUNT(*) FROM project_phases pp WHERE pp.project_id = p.id"
               "    AND pp.end_date IS NOT NULL AND pp.end_date < date('now')"
               "    AND pp.status NOT IN ('done','cancelled')) AS ms_late"
               " FROM projects p"
               " LEFT JOIN counterparties c ON c.id = p.counterparty_id"
               " LEFT JOIN staff s ON s.id = p.responsible_id"
               " LEFT JOIN (SELECT project_id, SUM(hours) AS total_hours,"
               "                   COUNT(DISTINCT staff_id) AS staff_count"
               "            FROM project_hours GROUP BY project_id) h"
               "   ON h.project_id = p.id WHERE 1=1")
        params = []
        if status:
            sql += " AND p.status = ?"
            params.append(status)
        if billable_only:
            sql += " AND p.is_billable = 1"
        if search:
            sql += " AND (ulower(p.name) LIKE ulower(?) OR ulower(c.name) LIKE ulower(?))"
            params += [f'%{search}%', f'%{search}%']
        sql += " ORDER BY " + PROJECT_SORTS.get(sort, PROJECT_SORTS['hours'])
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def get_project(project_id, conn=None):
    own = conn is None
    conn = conn or get_db()
    try:
        row = conn.execute(
            "SELECT p.*, c.name AS client_name, s.name AS responsible_name"
            " FROM projects p"
            " LEFT JOIN counterparties c ON c.id = p.counterparty_id"
            " LEFT JOIN staff s ON s.id = p.responsible_id WHERE p.id=?",
            (project_id,)).fetchone()
        return dict(row) if row else None
    finally:
        if own:
            conn.close()


def create_project(data):
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO projects (name, code, counterparty_id, responsible_id,"
            " contract_amount, currency, start_date, end_date, status, is_billable,"
            " estimated_total_hours, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ((data.get('name') or '').strip(), (data.get('code') or '').strip() or None,
             data.get('counterparty_id') or None, data.get('responsible_id') or None,
             _num(data.get('contract_amount')), data.get('currency') or 'UZS',
             data.get('start_date') or None, data.get('end_date') or None,
             data.get('status') or 'active',
             1 if str(data.get('is_billable', '1')) in ('1', 'on', 'true') else 0,
             _num(data.get('estimated_total_hours')),
             (data.get('notes') or '').strip() or None))
        pid = cur.lastrowid
        _write_audit(conn, 'create', 'projects', pid,
                     context=f"project '{data.get('name')}' created")
        conn.commit()
        return pid
    finally:
        conn.close()


def calculate_fx_gain_loss(project_id, conn=None):
    """Unrealised FX exposure on a project's foreign-currency invoices.

    For every posted invoice in a currency other than UZS the exposure is the
    document's own currency amount times the move from the rate it was booked
    at to the current rate. Sales invoices gain when the som weakens; purchase
    invoices lose. Payments are not counted — the invoice already carries the
    whole exposure, so adding its settlements would count the principal twice.
    """
    own = conn is None
    conn = conn or get_db()
    try:
        rows = conn.execute(
            "SELECT doc_type, total_cur, exchange_rate, total FROM documents"
            " WHERE project_id=? AND status='posted' AND currency != 'UZS'"
            "   AND doc_type IN ('sales_invoice','purchase_invoice')",
            (project_id,)).fetchall()
    finally:
        if own:
            conn.close()
    current = get_current_usd_rate()
    total = 0.0
    for r in rows:
        rate = _num(r['exchange_rate'])
        amount_cur = _num(r['total_cur'])
        if amount_cur <= 0 and rate > 0:
            amount_cur = _num(r['total']) / rate
        if amount_cur <= 0 or rate <= 0:
            continue
        move = amount_cur * (current - rate)
        total += move if r['doc_type'] == 'sales_invoice' else -move
    return total


# ========== Actuals ==========

def project_actuals(project_id, conn=None, ctx=None):
    """What actually happened on a project.

    invoiced  — revenue recognised on 9030 for this project (accrual)
    received  — cash allocated against this project's invoices
    direct    — subcontract/material posted to 2010 with this project's tag
    labor     — timesheet hours x cost rate, snapshotted for closed periods
    """
    own = conn is None
    conn = conn or get_db()
    try:
        revenue_id = account_id_for('revenue', conn)
        production_id = account_id_for('production_cost', conn)
        row = conn.execute(
            "SELECT"
            " SUM(CASE WHEN jl.account_id = ? THEN jl.credit - jl.debit ELSE 0 END) AS invoiced,"
            " SUM(CASE WHEN jl.account_id = ? THEN jl.debit - jl.credit ELSE 0 END) AS direct"
            " FROM journal_lines jl WHERE jl.project_id = ?",
            (revenue_id, production_id, project_id)).fetchone()

        received = conn.execute(
            "SELECT COALESCE(SUM(pa.amount),0) AS r FROM payment_allocations pa"
            " JOIN documents inv ON inv.id = pa.invoice_doc_id"
            " JOIN documents pay ON pay.id = pa.payment_doc_id"
            " WHERE inv.project_id = ? AND inv.doc_type='sales_invoice'"
            "   AND pay.status='posted'", (project_id,)).fetchone()['r']

        hour_rows = conn.execute(
            "SELECT period, staff_id, SUM(hours) AS hours,"
            " SUM(COALESCE(applied_cost_amount,0)) AS snapped_cost,"
            " SUM(COALESCE(applied_billing_amount,0)) AS snapped_billing"
            " FROM project_hours WHERE project_id=? GROUP BY period, staff_id",
            (project_id,)).fetchall()
        ctx = ctx or rate_context(conn)
    finally:
        if own:
            conn.close()

    labor = billing = hours = 0.0
    closed_cache = {}
    closed_set = ctx.get('closed_periods')
    for r in hour_rows:
        period = r['period']
        if period not in closed_cache:
            closed_cache[period] = (period in closed_set if closed_set is not None
                                    else is_period_closed(period))
        info = calculate_hourly_rate(r['staff_id'], context=ctx)
        cost_rate = info['cost_rate'] if info else 0.0
        billing_rate = info['billing_rate'] if info else 0.0
        if closed_cache[period] and _num(r['snapped_cost']) > 0:
            labor += _num(r['snapped_cost'])
            billing += _num(r['snapped_billing'])
        else:
            labor += _num(r['hours']) * cost_rate
            billing += _num(r['hours']) * billing_rate
        hours += _num(r['hours'])

    invoiced = _num(row['invoiced'])
    direct = _num(row['direct'])
    total_cost = labor + direct
    return {
        'invoiced': invoiced, 'received': _num(received),
        'outstanding': invoiced - _num(received),
        'direct': direct, 'labor': labor, 'hours': hours,
        'billing_value': billing, 'total_cost': total_cost,
        'profit': invoiced - total_cost,
        'margin_pct': ((invoiced - total_cost) / invoiced * 100) if invoiced > 0 else 0.0,
    }


def calculate_project_cost(project_id, ctx=None):
    """Full profitability picture for one project, including earned revenue."""
    conn = get_db()
    try:
        project = get_project(project_id, conn)
        if not project:
            return None
        ctx = ctx or rate_context(conn)
        actuals = project_actuals(project_id, conn, ctx)

        staff_rows = conn.execute(
            "SELECT ph.staff_id, s.name, s.role, SUM(ph.hours) AS hours"
            " FROM project_hours ph JOIN staff s ON s.id = ph.staff_id"
            " WHERE ph.project_id=? GROUP BY ph.staff_id ORDER BY hours DESC",
            (project_id,)).fetchall()
    finally:
        conn.close()

    breakdown = []
    for r in staff_rows:
        info = calculate_hourly_rate(r['staff_id'], context=ctx)
        cost_rate = info['cost_rate'] if info else 0.0
        billing_rate = info['billing_rate'] if info else 0.0
        breakdown.append({
            'staff_id': r['staff_id'], 'name': r['name'], 'role': r['role'],
            'hours': _num(r['hours']), 'cost_rate': cost_rate,
            'billing_rate': billing_rate,
            'cost': _num(r['hours']) * cost_rate,
            'billing': _num(r['hours']) * billing_rate,
        })

    # Earned revenue: contract value times completion, capped at 100%.
    estimated = _num(project.get('estimated_total_hours'))
    completion = min(actuals['hours'] / estimated, 1.0) if estimated > 0 else 0.0
    earned = _num(project.get('contract_amount')) * completion

    return {**project, **actuals, 'staff_breakdown': breakdown,
            'fx_gain_loss': calculate_fx_gain_loss(project_id),
            'completion_ratio': completion, 'earned_revenue': earned,
            'risk_price': actuals['labor'] * _num(project.get('risk_coefficient') or 1)}


# ========== Budget: plan vs fact ==========

def get_budget_overview():
    """One row per billable project, under BOTH portfolio lenses:

      whole   — the frozen projects.planned_* baseline against lifetime
                actuals. "Will we come in over budget?" The ±5/−10 badge.
      to_date — time-phased: day-weighted milestone plan against actuals for
                months strictly before the current one. "Are we where we
                should be by now?" The on/edge/off traffic light.

    They measure different things and are never reconciled into one number.
    Projects with no frozen plan are shown as unplanned and excluded from the
    plan-side totals — the plan is never fabricated from the actuals, which is
    what made every unplanned project look on-budget in earlier versions.
    """
    from .milestones import get_project_monthly_rollup

    conn = get_db()
    try:
        projects = conn.execute(
            "SELECT id, name, status, plan_frozen_date, planned_hours, planned_cost,"
            " planned_revenue, planned_outsourcing, planned_material,"
            " estimated_total_hours,"
            " (SELECT COUNT(*) FROM project_phases pp WHERE pp.project_id = p.id) AS ms_total,"
            " (SELECT COUNT(*) FROM project_phases pp WHERE pp.project_id = p.id"
            "    AND pp.status='done') AS ms_done"
            " FROM projects p WHERE is_billable=1 ORDER BY name"
        ).fetchall()
        ctx = rate_context(conn)
        rollups = {p['id']: get_project_monthly_rollup(p['id'], conn=conn, ctx=ctx)
                   for p in projects}
    finally:
        conn.close()

    rows = []
    totals = {'p_hours': 0.0, 'p_cost': 0.0, 'p_direct': 0.0, 'p_total': 0.0,
              'p_revenue': 0.0, 'f_hours': 0.0, 'f_cost': 0.0, 'f_direct': 0.0,
              'f_total': 0.0, 'f_revenue': 0.0}
    td_totals = {'plan_income': 0.0, 'plan_expense': 0.0, 'plan_profit': 0.0,
                 'fact_income': 0.0, 'fact_expense': 0.0, 'fact_profit': 0.0}
    counts = {'on': 0, 'edge': 0, 'off': 0, 'none': 0}
    planned_fact_total = 0.0

    for p in projects:
        a = project_actuals(p['id'], ctx=ctx)
        rollup = rollups[p['id']]
        p_hours = _num(p['planned_hours'])
        p_cost = _num(p['planned_cost'])
        p_direct = _num(p['planned_outsourcing']) + _num(p['planned_material'])
        p_total = p_cost + p_direct
        p_revenue = _num(p['planned_revenue'])
        has_plan = bool(p['plan_frozen_date']) and (p_hours > 0 or p_cost > 0)

        f_total = a['total_cost']
        if has_plan:
            d_total = f_total - p_total
            d_pct = (d_total / max(p_total, 1)) * 100
            status_key, css = budget_verdict(d_pct)
            planned_fact_total += f_total
            totals['p_hours'] += p_hours
            totals['p_cost'] += p_cost
            totals['p_direct'] += p_direct
            totals['p_total'] += p_total
            totals['p_revenue'] += p_revenue
        else:
            d_total = d_pct = 0.0
            status_key, css = 'noplan', 'noplan'

        totals['f_hours'] += a['hours']
        totals['f_cost'] += a['labor']
        totals['f_direct'] += a['direct']
        totals['f_total'] += f_total
        totals['f_revenue'] += a['invoiced']

        headline = rollup['status']['headline']
        counts[headline] += 1
        td = rollup['to_date']
        for k in td_totals:
            td_totals[k] += td[k]
        plan_months = [r['period'] for r in rollup['months'] if not r['unplanned']]

        rows.append({
            'id': p['id'], 'name': p['name'], 'project_status': p['status'],
            'plan_frozen_date': p['plan_frozen_date'], 'has_plan': has_plan,
            'ms_total': p['ms_total'], 'ms_done': p['ms_done'],
            'p_hours': p_hours, 'p_cost': p_cost, 'p_direct': p_direct,
            'p_total': p_total, 'p_revenue': p_revenue,
            'f_hours': a['hours'], 'f_cost': a['labor'], 'f_direct': a['direct'],
            'f_total': f_total, 'f_revenue': a['invoiced'],
            'd_hours': a['hours'] - p_hours, 'd_cost': a['labor'] - p_cost,
            'd_total': d_total, 'd_pct': d_pct,
            'status': status_key, 'css': css,
            'profit': a['profit'], 'margin_pct': a['margin_pct'],
            # time-phased lens
            'to_date': td, 'light': rollup['status'],
            'any_late': rollup['any_late'],
            'months_planned': len(plan_months),
            'months_range': (f"{plan_months[0]} – {plan_months[-1]}" if plan_months else ''),
        })

    d_total = planned_fact_total - totals['p_total']
    d_pct = (d_total / max(totals['p_total'], 1)) * 100
    return {'rows': rows, 'totals': totals, 'total_d': d_total, 'total_d_pct': d_pct,
            'total_css': 'profit' if d_pct <= 0 else 'loss',
            'planned_count': sum(1 for r in rows if r['has_plan']),
            'unplanned_count': sum(1 for r in rows if not r['has_plan']),
            'to_date_totals': td_totals, 'light_counts': counts}


def get_projects_on_course_summary(overview=None):
    """Dashboard counts for the time-phased lens."""
    ov = overview or get_budget_overview()
    c = ov['light_counts']
    tracked = c['on'] + c['edge'] + c['off']
    return {'on': c['on'], 'edge': c['edge'], 'off': c['off'], 'none': c['none'],
            'tracked': tracked, 'total': len(ov['rows']),
            'late_projects': sum(1 for r in ov['rows'] if r['any_late'])}


def get_portfolio_summary(overview=None):
    """Headline numbers for the dashboard's project tile."""
    ov = overview or get_budget_overview()
    over = sum(1 for r in ov['rows'] if r['status'] == 'over')
    return {
        'total': len(ov['rows']),
        'planned': ov['planned_count'], 'unplanned': ov['unplanned_count'],
        'over_budget': over,
        'invoiced': ov['totals']['f_revenue'],
        'cost': ov['totals']['f_total'],
        'profit': ov['totals']['f_revenue'] - ov['totals']['f_total'],
        'hours': ov['totals']['f_hours'],
    }


def get_top_projects(limit=10, ctx=None):
    """The dashboard's project table: billable projects by lifetime hours, with
    the full profitability picture (cost, billing value, income, FX, margin)."""
    conn = get_db()
    try:
        ids = [r['id'] for r in conn.execute(
            "SELECT p.id, COALESCE(SUM(ph.hours),0) AS h FROM projects p"
            " LEFT JOIN project_hours ph ON ph.project_id = p.id"
            " WHERE p.is_billable=1 GROUP BY p.id ORDER BY h DESC, p.name LIMIT ?",
            (limit,))]
        ctx = ctx or rate_context(conn)
    finally:
        conn.close()
    out = []
    for pid in ids:
        pc = calculate_project_cost(pid, ctx=ctx)
        if pc:
            out.append(pc)
    return out
