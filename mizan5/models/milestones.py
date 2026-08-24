"""Milestones (project_phases) — the schedule that is also the plan.

Actuals come from two places and it matters which:

  income  — revenue POSTED to 9030 and tagged to the milestone (accrual, from
            the sales invoice), not cash received. v4 used cash; the ledger
            makes recognised revenue available, which is the correct basis for
            comparing against a plan.
  direct  — subcontract and material cost posted to 2010 with a project tag.
            Payroll also debits 2010 but carries no project, so it never leaks
            into a milestone's direct cost.
  labor   — timesheet hours x cost rate (snapshotted rate for closed periods),
            exactly as v4 computed it.

The plan side is risk-EXCLUSIVE labor cost, matching the fact side.
"""
from calendar import monthrange
from datetime import date, datetime

from .base import get_db, now_ts, is_period_closed, _write_audit
from .ledger import account_id_for
from .staff import calculate_hourly_rate, rate_context

MILESTONE_STATUSES = ('planned', 'in_progress', 'done', 'cancelled')

_BEHIND_MARGIN = 15   # completion this far under schedule (points) reads as behind


def _parse_date(s):
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def month_first_day(period):
    try:
        y, m = int(period[:4]), int(period[5:7])
        return date(y, m, 1).strftime('%Y-%m-%d')
    except (TypeError, ValueError):
        return None


def month_last_day(period):
    try:
        y, m = int(period[:4]), int(period[5:7])
        return date(y, m, monthrange(y, m)[1]).strftime('%Y-%m-%d')
    except (TypeError, ValueError):
        return None


def _normalize_status(s):
    return s if s in MILESTONE_STATUSES else 'planned'


def _empty_bucket():
    return {'income': 0.0, 'labor': 0.0, 'direct': 0.0, 'expense': 0.0, 'hours': 0.0}


# ========== Actuals ==========

def _labor_from_hours(rows, ctx, closed_cache):
    """Sum labor cost over (period, staff) rows.

    A closed period uses the cost snapshotted onto the timesheet row, so
    history never moves when today's salaries change.
    """
    total_cost = total_hours = 0.0
    for r in rows:
        period = r['period']
        if period not in closed_cache:
            closed_cache[period] = is_period_closed(period)
        if closed_cache[period] and (r['snapped_cost'] or 0) > 0:
            cost = r['snapped_cost']
        else:
            info = calculate_hourly_rate(r['staff_id'], context=ctx)
            cost = (r['hours'] or 0) * (info['cost_rate'] if info else 0.0)
        total_cost += cost
        total_hours += r['hours'] or 0
    return total_cost, total_hours


def get_milestone_actuals(project_id, conn=None, ctx=None):
    """{phase_id -> bucket}; the None key holds anything not yet attributed."""
    own = conn is None
    conn = conn or get_db()
    try:
        revenue_id = account_id_for('revenue', conn)
        production_id = account_id_for('production_cost', conn)

        money = conn.execute(
            "SELECT jl.phase_id AS pid,"
            " SUM(CASE WHEN jl.account_id = ? THEN jl.credit - jl.debit ELSE 0 END) AS income,"
            " SUM(CASE WHEN jl.account_id = ? THEN jl.debit - jl.credit ELSE 0 END) AS direct"
            " FROM journal_lines jl JOIN journal_entries je ON je.id = jl.entry_id"
            " WHERE jl.project_id = ? GROUP BY jl.phase_id",
            (revenue_id, production_id, project_id)).fetchall()

        labor_rows = conn.execute(
            "SELECT phase_id, period, staff_id, SUM(hours) AS hours,"
            " SUM(COALESCE(applied_cost_amount,0)) AS snapped_cost"
            " FROM project_hours WHERE project_id=?"
            " GROUP BY phase_id, period, staff_id", (project_id,)).fetchall()
    finally:
        if own:
            conn.close()

    out = {}
    for r in money:
        b = out.setdefault(r['pid'], _empty_bucket())
        b['income'] += r['income'] or 0
        b['direct'] += r['direct'] or 0

    ctx = ctx or rate_context()
    closed_cache = {}
    for r in labor_rows:
        cost, hours = _labor_from_hours([r], ctx, closed_cache)
        b = out.setdefault(r['phase_id'], _empty_bucket())
        b['labor'] += cost
        b['hours'] += hours

    for b in out.values():
        b['expense'] = b['labor'] + b['direct']
    return out


def get_project_monthly_actuals(project_id, conn=None, ctx=None):
    """{period -> bucket} for one project, by posting date and timesheet period."""
    own = conn is None
    conn = conn or get_db()
    try:
        revenue_id = account_id_for('revenue', conn)
        production_id = account_id_for('production_cost', conn)
        money = conn.execute(
            "SELECT je.period AS period,"
            " SUM(CASE WHEN jl.account_id = ? THEN jl.credit - jl.debit ELSE 0 END) AS income,"
            " SUM(CASE WHEN jl.account_id = ? THEN jl.debit - jl.credit ELSE 0 END) AS direct"
            " FROM journal_lines jl JOIN journal_entries je ON je.id = jl.entry_id"
            " WHERE jl.project_id = ? GROUP BY je.period",
            (revenue_id, production_id, project_id)).fetchall()
        labor_rows = conn.execute(
            "SELECT period, staff_id, SUM(hours) AS hours,"
            " SUM(COALESCE(applied_cost_amount,0)) AS snapped_cost"
            " FROM project_hours WHERE project_id=? GROUP BY period, staff_id",
            (project_id,)).fetchall()
    finally:
        if own:
            conn.close()

    out = {}
    for r in money:
        if not r['period']:
            continue
        b = out.setdefault(r['period'], _empty_bucket())
        b['income'] += r['income'] or 0
        b['direct'] += r['direct'] or 0

    ctx = ctx or rate_context()
    closed_cache = {}
    for r in labor_rows:
        cost, hours = _labor_from_hours([r], ctx, closed_cache)
        b = out.setdefault(r['period'], _empty_bucket())
        b['labor'] += cost
        b['hours'] += hours

    for b in out.values():
        b['expense'] = b['labor'] + b['direct']
    return out


# ========== Listing ==========

def get_project_milestones(project_id, ctx=None):
    """Milestones enriched with actuals, lateness, earned value and CPI/SPI."""
    from .pricing import get_milestone_staff

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM project_phases WHERE project_id=?"
            " ORDER BY sort_order, COALESCE(start_date,''), id", (project_id,)).fetchall()
        ctx = ctx or rate_context(conn)
        actuals = get_milestone_actuals(project_id, conn, ctx)
        staff_by_phase = {r['id']: get_milestone_staff(r['id'], conn) for r in rows}
    finally:
        conn.close()

    today = date.today()
    result = []
    for r in rows:
        m = dict(r)
        m['status'] = _normalize_status(m.get('status'))
        m['staff'] = staff_by_phase.get(m['id'], [])
        completion = min(max(m.get('completion_percent') or 0, 0), 100)
        if m['status'] == 'done':
            completion = 100
        m['completion'] = completion

        start, end = _parse_date(m.get('start_date')), _parse_date(m.get('end_date'))
        m['is_late'] = bool(end and end < today
                            and m['status'] not in ('done', 'cancelled'))
        expected = None
        if start and end and end >= start:
            total = (end - start).days + 1
            expected = min(max(((today - start).days + 1) / total, 0), 1) * 100
        m['expected_completion'] = expected
        m['is_behind'] = bool(expected is not None
                              and m['status'] not in ('done', 'cancelled')
                              and completion < expected - _BEHIND_MARGIN)

        m['plan_expense'] = plan_expense_total(m)
        a = actuals.get(m['id'], _empty_bucket())
        m['fact'] = a
        ev = (m.get('planned_revenue') or 0) * completion / 100
        m['earned_value'] = ev
        pv = ((m.get('planned_revenue') or 0) * expected / 100
              if expected is not None else None)
        m['cpi'] = ev / a['expense'] if a['expense'] > 0 else None
        m['spi'] = ev / pv if pv else None
        m['hours_completion'] = (min(a['hours'] / m['planned_hours'], 1) * 100
                                 if (m.get('planned_hours') or 0) > 0 else None)
        m['cost_d_pct'] = ((a['expense'] - m['plan_expense'])
                           / max(m['plan_expense'], 1)) * 100
        result.append(m)

    # Timeline strip: left/width percentages across the dated span
    dated = [(m, _parse_date(m['start_date']), _parse_date(m['end_date']))
             for m in result
             if _parse_date(m.get('start_date')) and _parse_date(m.get('end_date'))]
    if dated:
        span_start = min(s for _, s, _ in dated)
        span_end = max(e for _, _, e in dated)
        span_days = max((span_end - span_start).days + 1, 1)
        for m, s, e in dated:
            m['tl_left'] = round((s - span_start).days / span_days * 100, 2)
            m['tl_width'] = max(round(((e - s).days + 1) / span_days * 100, 2), 1.5)
    return result


def plan_expense_total(m):
    return ((m.get('planned_cost') or 0) + (m.get('planned_outsourcing') or 0)
            + (m.get('planned_material') or 0))


def rollup_milestone_plan(project_id):
    """The live sum of a project's milestone plans. No writes.

    This is what freeze_project_plan() copies into projects.planned_*, and what
    the card compares against that baseline to detect drift.
    """
    conn = get_db()
    try:
        if not conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
            return None
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(planned_hours),0) AS hours,"
            " COALESCE(SUM(planned_cost),0) AS cost,"
            " COALESCE(SUM(planned_revenue),0) AS revenue,"
            " COALESCE(SUM(planned_outsourcing),0) AS outsourcing,"
            " COALESCE(SUM(planned_material),0) AS material"
            " FROM project_phases WHERE project_id=? AND status != 'cancelled'",
            (project_id,)).fetchone()
    finally:
        conn.close()
    return {'count': row['n'], 'planned_hours': row['hours'],
            'planned_cost': row['cost'], 'planned_revenue': row['revenue'],
            'planned_outsourcing': row['outsourcing'],
            'planned_material': row['material']}


# ========== Plan allocation over months ==========

def allocate_plan_to_months(m):
    """Day-weighted spread of one milestone's plan across the months it covers.

    The last month absorbs the rounding remainder, so the allocation sums
    exactly to the milestone totals. Undated milestones return {}.
    """
    d0, d1 = _parse_date(m.get('start_date')), _parse_date(m.get('end_date'))
    if not d0 or not d1:
        return {}
    if d1 < d0:
        d0, d1 = d1, d0
    total_days = (d1 - d0).days + 1
    income_total = m.get('planned_revenue') or 0
    expense_total = plan_expense_total(m)
    hours_total = m.get('planned_hours') or 0

    segments = []
    cur = date(d0.year, d0.month, 1)
    while cur <= d1:
        month_end = date(cur.year, cur.month, monthrange(cur.year, cur.month)[1])
        overlap = (min(d1, month_end) - max(d0, cur)).days + 1
        segments.append((cur.strftime('%Y-%m'), overlap))
        cur = date(cur.year + cur.month // 12, cur.month % 12 + 1, 1)

    out = {}
    acc_i = acc_e = acc_h = 0.0
    for idx, (period, days) in enumerate(segments):
        if idx == len(segments) - 1:
            inc = income_total - acc_i
            exp = expense_total - acc_e
            hrs = hours_total - acc_h
        else:
            share = days / total_days
            inc, exp, hrs = (income_total * share, expense_total * share,
                             hours_total * share)
            acc_i += inc
            acc_e += exp
            acc_h += hrs
        out[period] = {'income': inc, 'expense': exp, 'hours': hrs}
    return out


# ========== CRUD ==========

def add_milestone(project_id, name, code=None, work_type=None, start_date=None,
                  end_date=None, planned_hours=0, planned_cost=0,
                  planned_outsourcing=0, planned_material=0, planned_revenue=0,
                  sort_order=100, status='planned', notes=None):
    name = (name or '').strip()
    if not name:
        return None
    conn = get_db()
    try:
        base_code = (code or '').strip() or (work_type or 'ms')
        code_try, n = base_code, 1
        while conn.execute("SELECT 1 FROM project_phases WHERE project_id=? AND code=?",
                           (project_id, code_try)).fetchone():
            n += 1
            code_try = f'{base_code}-{n}'
        cur = conn.execute(
            "INSERT INTO project_phases (project_id, code, name, sort_order, work_type,"
            " start_date, end_date, planned_hours, planned_cost, planned_outsourcing,"
            " planned_material, planned_revenue, status, notes, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, code_try, name, sort_order, work_type,
             start_date or None, end_date or None, planned_hours, planned_cost,
             planned_outsourcing, planned_material, planned_revenue,
             _normalize_status(status), notes, now_ts()))
        mid = cur.lastrowid
        _write_audit(conn, 'create', 'project_phases', mid,
                     context=f'milestone "{name}" added to project {project_id}')
        conn.commit()
        return mid
    finally:
        conn.close()


def delete_milestone(milestone_id):
    """Refuse while documents reference the milestone; unassign its hours.

    Returns (ok, attached_document_count).
    """
    conn = get_db()
    try:
        row = conn.execute("SELECT project_id, name FROM project_phases WHERE id=?",
                           (milestone_id,)).fetchone()
        if not row:
            return False, 0
        used = conn.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE phase_id=?"
            " UNION ALL SELECT COUNT(*) FROM document_lines WHERE phase_id=?",
            (milestone_id, milestone_id)).fetchall()
        attached = sum(r['n'] for r in used)
        if attached:
            return False, attached
        conn.execute("UPDATE project_hours SET phase_id=NULL WHERE phase_id=?",
                     (milestone_id,))
        conn.execute("DELETE FROM project_phases WHERE id=?", (milestone_id,))
        _write_audit(conn, 'delete', 'project_phases', milestone_id,
                     context=f'milestone "{row["name"]}" deleted')
        conn.commit()
        return True, 0
    finally:
        conn.close()


def set_milestone_status(milestone_id, status):
    status = _normalize_status(status)
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT status, completion_percent FROM project_phases WHERE id=?",
            (milestone_id,)).fetchone()
        if not row:
            return False
        if status == 'done':
            conn.execute(
                "UPDATE project_phases SET status=?, completed_date=?,"
                " completion_percent=?, updated_at=? WHERE id=?",
                (status, now_ts()[:10], max(row['completion_percent'] or 0, 100),
                 now_ts(), milestone_id))
        else:
            conn.execute(
                "UPDATE project_phases SET status=?, completed_date=NULL, updated_at=?"
                " WHERE id=?", (status, now_ts(), milestone_id))
        _write_audit(conn, 'update', 'project_phases', milestone_id, 'status',
                     row['status'], status)
        conn.commit()
        return True
    finally:
        conn.close()


def assign_hours_to_milestone(project_id, period, phase_id):
    """Attach a whole timesheet period to a milestone (None unassigns).

    Returns rows updated, or -1 when the phase belongs to another project.
    """
    conn = get_db()
    try:
        if phase_id:
            ok = conn.execute("SELECT 1 FROM project_phases WHERE id=? AND project_id=?",
                              (phase_id, project_id)).fetchone()
            if not ok:
                return -1
        cur = conn.execute(
            "UPDATE project_hours SET phase_id=? WHERE project_id=? AND period=?",
            (phase_id or None, project_id, period))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# ========== Schedule generation ==========

# The six standard design-office stages, used by "Add standard schedule".
STANDARD_SCHEDULE = [
    {'name': 'Eskiz loyiha', 'work_type': 'eskiz', 'income_pct': 15, 'hours_pct': 15},
    {'name': 'Arxitektura yechimlari', 'work_type': 'ar', 'income_pct': 30, 'hours_pct': 35},
    {'name': 'Konstruktiv yechimlar', 'work_type': 'kj', 'income_pct': 25, 'hours_pct': 30},
    {'name': 'Muhandislik tarmoqlari', 'work_type': 'im', 'income_pct': 15, 'hours_pct': 10},
    {'name': 'Smeta hujjatlari', 'work_type': 'smeta', 'income_pct': 10, 'hours_pct': 5},
    {'name': 'Avtorlik nazorati', 'work_type': 'nazorat', 'income_pct': 5, 'hours_pct': 5},
]


def generate_milestone_schedule(project_id, schedule_rows, totals=None, overwrite=False):
    """Create a milestone schedule for a project.

    totals=None is skeleton mode: names, work types and dates only, with every
    plan figure zero — the schedule is then filled in bottom-up per milestone.
    Supplying totals splits them by the rows' percentages; outsourcing and
    material follow the income share rather than landing on the last milestone,
    so intermediate expense figures stay meaningful.

    Returns (created_count, error) with error in (None,'empty','pct','exists','locked').
    """
    rows = [r for r in schedule_rows if (r.get('name') or '').strip()]
    if not rows:
        return 0, 'empty'
    i_pcts = h_pcts = [0] * len(rows)
    if totals is not None:
        try:
            i_pcts = [float(r.get('income_pct') or 0) for r in rows]
            h_pcts = [float(r.get('hours_pct') or 0) for r in rows]
        except (TypeError, ValueError):
            return 0, 'pct'
        if abs(sum(i_pcts) - 100) > 0.5 or abs(sum(h_pcts) - 100) > 0.5:
            return 0, 'pct'

    total_income = (totals or {}).get('income', 0) or 0
    total_hours = (totals or {}).get('hours', 0) or 0
    labor_cost = (totals or {}).get('labor_cost', 0) or 0
    outsourcing = (totals or {}).get('outsourcing', 0) or 0
    material = (totals or {}).get('material', 0) or 0

    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT COUNT(*) AS n FROM project_phases WHERE project_id=?",
            (project_id,)).fetchone()['n']
        if existing:
            if not overwrite:
                return 0, 'exists'
            used = conn.execute(
                "SELECT COUNT(*) AS n FROM documents d"
                " JOIN project_phases pp ON d.phase_id = pp.id"
                " WHERE pp.project_id=?", (project_id,)).fetchone()['n']
            if used:
                return 0, 'locked'
            conn.execute("UPDATE project_hours SET phase_id=NULL"
                         " WHERE project_id=? AND phase_id IS NOT NULL", (project_id,))
            conn.execute("DELETE FROM project_phases WHERE project_id=?", (project_id,))

        acc_i = acc_h = acc_c = acc_o = acc_m = 0.0
        created = 0
        for i, r in enumerate(rows):
            last = i == len(rows) - 1
            if totals is None:
                inc = hrs = cost = out = mat = 0
            elif last:
                inc, hrs, cost = (total_income - acc_i, total_hours - acc_h,
                                  labor_cost - acc_c)
                out, mat = outsourcing - acc_o, material - acc_m
            else:
                inc = total_income * i_pcts[i] / 100
                hrs = total_hours * h_pcts[i] / 100
                cost = labor_cost * h_pcts[i] / 100
                out = outsourcing * i_pcts[i] / 100
                mat = material * i_pcts[i] / 100
                acc_i += inc
                acc_h += hrs
                acc_c += cost
                acc_o += out
                acc_m += mat
            start_m = (r.get('start') or '').strip()
            end_m = (r.get('end') or '').strip()
            if start_m and end_m and end_m < start_m:
                start_m, end_m = end_m, start_m
            wt = (r.get('work_type') or '').strip() or None
            conn.execute(
                "INSERT INTO project_phases (project_id, code, name, sort_order,"
                " work_type, start_date, end_date, planned_hours, planned_cost,"
                " planned_outsourcing, planned_material, planned_revenue, status,"
                " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (project_id, f"{wt or 'ms'}-{i + 1}", r['name'].strip(), (i + 1) * 10,
                 wt, month_first_day(start_m), month_last_day(end_m or start_m),
                 hrs, cost, out, mat, inc, 'planned', now_ts()))
            created += 1
        kind = 'skeleton' if totals is None else 'pricing'
        _write_audit(conn, 'create', 'project_phases', None,
                     context=f'{created} milestones generated ({kind})'
                             f' for project {project_id}')
        conn.commit()
        return created, None
    finally:
        conn.close()
