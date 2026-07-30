"""Milestones (project_phases) and the periodic plan-vs-actual roll-up.

Actuals semantics mirror calculate_project_cost() exactly so the milestone
pages, /budget and the dashboard never disagree:
  income  = SUM(paid)   for income tx_types (INCOME_TX_TYPES — cash received)
  direct  = SUM(amount) for tx_type outsourcing/material (committed)
  labor   = applied_cost_amount snapshots for closed periods,
            hours × live cost_rate otherwise (no risk coefficient on fact).
The plan side is on the same basis: planned_cost is the risk-EXCLUSIVE labor
cost (same as projects.planned_cost written by the pricing freeze); the risk
uplift lives only in planned_revenue.
"""
from calendar import monthrange
from datetime import datetime, date

from .base import get_db, is_period_closed, _write_audit, INCOME_TX_SQL
from .staff import calculate_hourly_rate

MILESTONE_STATUSES = ('planned', 'in_progress', 'done', 'cancelled')

# Cumulative status bands (continuity with the /budget page's ±5/−10 rules)
_DRIFT_ON = -5      # profit drift ≥ −5 % of planned income → on course
_DRIFT_EDGE = -15   # −15…−5 → borderline; below → off course
_COLLECT_ON = 0.95
_COLLECT_EDGE = 0.85
_BEHIND_MARGIN = 15  # completion % more than 15 pts under schedule → behind


def _parse_date(s):
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def _month_first_day(period):
    """'YYYY-MM' → 'YYYY-MM-01', or None if malformed."""
    try:
        y, m = int(period[:4]), int(period[5:7])
        return date(y, m, 1).strftime('%Y-%m-%d')
    except (TypeError, ValueError):
        return None


def _month_last_day(period):
    try:
        y, m = int(period[:4]), int(period[5:7])
        return date(y, m, monthrange(y, m)[1]).strftime('%Y-%m-%d')
    except (TypeError, ValueError):
        return None


def _normalize_status(s):
    if s == 'active':  # legacy value from the dormant v5.3 schema
        return 'in_progress'
    return s if s in MILESTONE_STATUSES else 'planned'


def get_work_types(active_only=True):
    conn = get_db()
    where = " WHERE is_active=1" if active_only else ""
    rows = conn.execute(
        f"SELECT * FROM work_types{where} ORDER BY sort_order, code"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ========== Actuals aggregators ==========

def _labor_cost(rows, rate_cache, period_closed_cache):
    """Sum labor cost over (period, staff) grouped rows using the same
    closed-period/snapshot rule as calculate_project_cost."""
    total_cost = 0.0
    total_hours = 0.0
    for r in rows:
        p = r['period']
        if p not in period_closed_cache:
            period_closed_cache[p] = is_period_closed(p)
        if period_closed_cache[p] and (r['snapped_cost'] or 0) > 0:
            cost = r['snapped_cost']
        else:
            sid = r['staff_id']
            if sid not in rate_cache:
                info = calculate_hourly_rate(sid)
                rate_cache[sid] = info['cost_rate'] if isinstance(info, dict) else 0
            cost = (r['hours'] or 0) * rate_cache[sid]
        total_cost += cost
        total_hours += r['hours'] or 0
    return total_cost, total_hours


def _empty_bucket():
    return {'income': 0.0, 'labor': 0.0, 'direct': 0.0, 'expense': 0.0, 'hours': 0.0}


def get_project_monthly_actuals(project_id):
    """{period 'YYYY-MM' → {income, labor, direct, expense, hours}} for one project."""
    conn = get_db()
    money = conn.execute(f'''
        SELECT strftime('%Y-%m', date) AS period,
               SUM(CASE WHEN tx_type IN {INCOME_TX_SQL} THEN COALESCE(paid,0) ELSE 0 END) AS income,
               SUM(CASE WHEN tx_type IN ('outsourcing','material') THEN COALESCE(amount,0) ELSE 0 END) AS direct
        FROM transactions WHERE project_id=?
        GROUP BY period
    ''', (project_id,)).fetchall()
    labor_rows = conn.execute('''
        SELECT period, staff_id, SUM(hours) AS hours,
               SUM(COALESCE(applied_cost_amount,0)) AS snapped_cost
        FROM project_hours WHERE project_id=?
        GROUP BY period, staff_id
    ''', (project_id,)).fetchall()
    conn.close()

    out = {}
    for r in money:
        if not r['period']:
            continue
        b = out.setdefault(r['period'], _empty_bucket())
        b['income'] += r['income'] or 0
        b['direct'] += r['direct'] or 0

    rate_cache, closed_cache = {}, {}
    for r in labor_rows:
        cost, hours = _labor_cost([r], rate_cache, closed_cache)
        b = out.setdefault(r['period'], _empty_bucket())
        b['labor'] += cost
        b['hours'] += hours

    for b in out.values():
        b['expense'] = b['labor'] + b['direct']
    return out


def get_milestone_actuals(project_id):
    """{phase_id → bucket} with explicit attribution; key None = unassigned."""
    conn = get_db()
    money = conn.execute(f'''
        SELECT phase_id,
               SUM(CASE WHEN tx_type IN {INCOME_TX_SQL} THEN COALESCE(paid,0) ELSE 0 END) AS income,
               SUM(CASE WHEN tx_type IN ('outsourcing','material') THEN COALESCE(amount,0) ELSE 0 END) AS direct
        FROM transactions WHERE project_id=?
        GROUP BY phase_id
    ''', (project_id,)).fetchall()
    labor_rows = conn.execute('''
        SELECT phase_id, period, staff_id, SUM(hours) AS hours,
               SUM(COALESCE(applied_cost_amount,0)) AS snapped_cost
        FROM project_hours WHERE project_id=?
        GROUP BY phase_id, period, staff_id
    ''', (project_id,)).fetchall()
    conn.close()

    out = {}
    for r in money:
        b = out.setdefault(r['phase_id'], _empty_bucket())
        b['income'] += r['income'] or 0
        b['direct'] += r['direct'] or 0

    rate_cache, closed_cache = {}, {}
    for r in labor_rows:
        cost, hours = _labor_cost([r], rate_cache, closed_cache)
        b = out.setdefault(r['phase_id'], _empty_bucket())
        b['labor'] += cost
        b['hours'] += hours

    for b in out.values():
        b['expense'] = b['labor'] + b['direct']
    return out


# ========== Plan allocation ==========

def _plan_expense_total(m):
    return ((m.get('planned_cost') or 0)
            + (m.get('planned_outsourcing') or 0)
            + (m.get('planned_material') or 0))


def allocate_plan_to_months(m):
    """Day-weighted spread of one milestone's plan over the calendar months its
    [start_date, end_date] covers. The last month absorbs the rounding
    remainder so the allocation sums exactly to the milestone totals.
    Returns {} for undated milestones."""
    d0 = _parse_date(m.get('start_date'))
    d1 = _parse_date(m.get('end_date'))
    if not d0 or not d1:
        return {}
    if d1 < d0:
        d0, d1 = d1, d0
    total_days = (d1 - d0).days + 1
    income_total = m.get('planned_revenue') or 0
    expense_total = _plan_expense_total(m)
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
            inc, exp, hrs = income_total - acc_i, expense_total - acc_e, hours_total - acc_h
        else:
            share = days / total_days
            inc, exp, hrs = income_total * share, expense_total * share, hours_total * share
            acc_i += inc
            acc_e += exp
            acc_h += hrs
        out[period] = {'income': inc, 'expense': exp, 'hours': hrs}
    return out


# ========== Milestone listing ==========

def get_project_milestones(project_id):
    """Milestones enriched with actuals, derived lateness/behind flags,
    earned value, CPI/SPI and timeline strip percentages."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM project_phases WHERE project_id=?"
        " ORDER BY sort_order, COALESCE(start_date,''), id",
        (project_id,)
    ).fetchall()
    conn.close()
    actuals = get_milestone_actuals(project_id)
    today = date.today()

    result = []
    for r in rows:
        m = dict(r)
        m['status'] = _normalize_status(m.get('status'))
        m['staff'] = get_milestone_staff(m['id'])
        completion = min(max(m.get('completion_percent') or 0, 0), 100)
        if m['status'] == 'done':
            completion = 100
        m['completion'] = completion

        start, end = _parse_date(m.get('start_date')), _parse_date(m.get('end_date'))
        m['is_late'] = bool(end and end < today and m['status'] not in ('done', 'cancelled'))
        expected = None
        if start and end and end >= start:
            total = (end - start).days + 1
            expected = min(max(((today - start).days + 1) / total, 0), 1) * 100
        m['expected_completion'] = expected
        m['is_behind'] = bool(
            expected is not None and m['status'] not in ('done', 'cancelled')
            and completion < expected - _BEHIND_MARGIN
        )

        m['plan_expense'] = _plan_expense_total(m)
        a = actuals.get(m['id'], _empty_bucket())
        m['fact'] = a
        ev = (m.get('planned_revenue') or 0) * completion / 100
        m['earned_value'] = ev
        pv = (m.get('planned_revenue') or 0) * expected / 100 if expected is not None else None
        m['cpi'] = ev / a['expense'] if a['expense'] > 0 else None
        m['spi'] = ev / pv if pv else None
        m['hours_completion'] = (
            min(a['hours'] / m['planned_hours'], 1) * 100
            if (m.get('planned_hours') or 0) > 0 else None
        )
        m['cost_d_pct'] = ((a['expense'] - m['plan_expense']) / max(m['plan_expense'], 1)) * 100
        result.append(m)

    # Timeline strip: left/width % across the span of all dated milestones
    dated = [(m, _parse_date(m['start_date']), _parse_date(m['end_date']))
             for m in result if _parse_date(m.get('start_date')) and _parse_date(m.get('end_date'))]
    if dated:
        span_start = min(s for _, s, _ in dated)
        span_end = max(e for _, _, e in dated)
        span_days = max((span_end - span_start).days + 1, 1)
        for m, s, e in dated:
            m['tl_left'] = round((s - span_start).days / span_days * 100, 2)
            m['tl_width'] = max(round(((e - s).days + 1) / span_days * 100, 2), 1.5)
    return result


def get_unassigned_actuals(project_id):
    """Actuals on this project not yet attributed to any milestone."""
    conn = get_db()
    txs = conn.execute(f'''
        SELECT id, date, tx_type, description, amount, paid
        FROM transactions
        WHERE project_id=? AND phase_id IS NULL
          AND (tx_type IN {INCOME_TX_SQL} OR tx_type IN ('outsourcing','material'))
        ORDER BY date DESC LIMIT 100
    ''', (project_id,)).fetchall()
    hours = conn.execute('''
        SELECT period, SUM(hours) AS hours
        FROM project_hours WHERE project_id=? AND phase_id IS NULL
        GROUP BY period ORDER BY period DESC
    ''', (project_id,)).fetchall()
    conn.close()
    return {'transactions': [dict(r) for r in txs],
            'hour_periods': [dict(r) for r in hours]}


def suggest_phase_for_period(project_id, period, milestones=None):
    """Milestone whose date range overlaps the given month most (or None).
    Used to pre-select in the assign UI — never written automatically."""
    if milestones is None:
        conn = get_db()
        milestones = [dict(r) for r in conn.execute(
            "SELECT id, start_date, end_date FROM project_phases WHERE project_id=?",
            (project_id,)).fetchall()]
        conn.close()
    m_start = _parse_date(_month_first_day(period))
    m_end = _parse_date(_month_last_day(period))
    if not m_start:
        return None
    best, best_overlap = None, 0
    for m in milestones:
        d0, d1 = _parse_date(m.get('start_date')), _parse_date(m.get('end_date'))
        if not d0 or not d1:
            continue
        overlap = (min(d1, m_end) - max(d0, m_start)).days + 1
        if overlap > best_overlap:
            best, best_overlap = m['id'], overlap
    return best


# ========== Milestone staff plan (per-employee hour estimates) ==========

def get_milestone_staff(phase_id):
    """Employee plan rows for one milestone, with per-row cost/billing built
    from the rates snapshotted at save time."""
    conn = get_db()
    rows = conn.execute('''
        SELECT ms.staff_id, s.name, s.role, ms.est_hours, ms.cost_rate, ms.billing_rate
        FROM milestone_staff ms JOIN staff s ON s.id = ms.staff_id
        WHERE ms.phase_id=? ORDER BY s.name''', (phase_id,)).fetchall()
    conn.close()
    return [{
        'staff_id': r['staff_id'], 'name': r['name'], 'role': r['role'],
        'est_hours': r['est_hours'] or 0,
        'cost_rate': r['cost_rate'] or 0, 'billing_rate': r['billing_rate'] or 0,
        'cost': (r['est_hours'] or 0) * (r['cost_rate'] or 0),
        'billing': (r['est_hours'] or 0) * (r['billing_rate'] or 0),
    } for r in rows]


def set_milestone_staff(phase_id, staff_rows):
    """Replace the milestone's employee plan rows and recompute its labor plan.

    staff_rows: list of {staff_id, est_hours}. Rates are snapshotted per staff
    member at save time (frozen-plan pattern) so later salary changes never
    alter a saved plan. project_phases.planned_hours/planned_cost are rewritten
    from the rows — they are derived values while rows exist. Milestones that
    never had rows (e.g. generated from pricing) keep their directly-written
    scalars when saved with an empty list.
    Returns {'planned_hours', 'planned_cost', 'planned_billing'}.
    """
    rate_cache = {}
    cleaned = []
    for r in staff_rows or []:
        try:
            sid = int(r.get('staff_id') or 0)
            hours = float(r.get('est_hours') or 0)
        except (TypeError, ValueError):
            continue
        if sid <= 0 or hours <= 0:
            continue
        if sid not in rate_cache:
            info = calculate_hourly_rate(sid)
            rate_cache[sid] = ((info['cost_rate'], info['billing_rate'])
                               if isinstance(info, dict) else (0, 0))
        cleaned.append((sid, hours) + rate_cache[sid])

    conn = get_db()
    try:
        if cleaned:  # drop rows whose staff_id would violate the FK
            ph = ','.join('?' * len(cleaned))
            valid = {r[0] for r in conn.execute(
                f"SELECT id FROM staff WHERE id IN ({ph})",
                [c[0] for c in cleaned]).fetchall()}
            cleaned = [c for c in cleaned if c[0] in valid]
        total_hours = sum(h for _, h, _, _ in cleaned)
        total_cost = sum(h * cr for _, h, cr, _ in cleaned)
        total_billing = sum(h * br for _, h, _, br in cleaned)
        had_rows = conn.execute(
            "SELECT COUNT(*) FROM milestone_staff WHERE phase_id=?", (phase_id,)
        ).fetchone()[0]
        conn.execute("DELETE FROM milestone_staff WHERE phase_id=?", (phase_id,))
        # Duplicate staff_ids in the input collapse onto one row (last wins)
        # instead of violating UNIQUE(phase_id, staff_id).
        conn.executemany(
            "INSERT OR REPLACE INTO milestone_staff"
            " (phase_id, staff_id, est_hours, cost_rate, billing_rate) VALUES (?,?,?,?,?)",
            [(phase_id, sid, h, cr, br) for sid, h, cr, br in cleaned])
        if cleaned or had_rows:
            conn.execute(
                "UPDATE project_phases SET planned_hours=?, planned_cost=?, updated_at=?"
                " WHERE id=?",
                (total_hours, total_cost,
                 datetime.now().strftime('%Y-%m-%d %H:%M:%S'), phase_id))
            _write_audit(conn, 'update', 'project_phases', phase_id,
                         context=f'staff plan set: {len(cleaned)} rows,'
                                 f' hours={total_hours:g}, cost={total_cost:.0f}')
        conn.commit()
    finally:
        conn.close()
    return {'planned_hours': total_hours, 'planned_cost': total_cost,
            'planned_billing': total_billing}


# ========== Milestone CRUD (field edits go through the generic /api/update) ==========

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
        while conn.execute(
            "SELECT 1 FROM project_phases WHERE project_id=? AND code=?",
            (project_id, code_try)
        ).fetchone():
            n += 1
            code_try = f"{base_code}-{n}"
        cur = conn.execute('''INSERT INTO project_phases
            (project_id, code, name, sort_order, work_type, start_date, end_date,
             planned_hours, planned_cost, planned_outsourcing, planned_material,
             planned_revenue, status, notes, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (project_id, code_try, name, sort_order, work_type,
             start_date or None, end_date or None,
             planned_hours, planned_cost, planned_outsourcing, planned_material,
             planned_revenue, _normalize_status(status), notes,
             datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        mid = cur.lastrowid
        _write_audit(conn, 'create', 'project_phases', mid,
                     context=f'milestone "{name}" added to project {project_id}')
        conn.commit()
        return mid
    finally:
        conn.close()


def delete_milestone(milestone_id):
    """Refuses while transactions reference the milestone; unassigns hours.
    Returns (ok, attached_tx_count)."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT project_id, name FROM project_phases WHERE id=?", (milestone_id,)
        ).fetchone()
        if not row:
            return False, 0
        used = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE phase_id=?", (milestone_id,)
        ).fetchone()[0]
        if used:
            return False, used
        conn.execute("UPDATE project_hours SET phase_id=NULL WHERE phase_id=?", (milestone_id,))
        conn.execute("DELETE FROM project_phases WHERE id=?", (milestone_id,))
        _write_audit(conn, 'delete', 'project_phases', milestone_id,
                     context=f'milestone "{row["name"]}" deleted (project {row["project_id"]})')
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
            (milestone_id,)
        ).fetchone()
        if not row:
            return False
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if status == 'done':
            conn.execute(
                "UPDATE project_phases SET status=?, completed_date=?,"
                " completion_percent=?, updated_at=? WHERE id=?",
                (status, datetime.now().strftime('%Y-%m-%d'),
                 max(row['completion_percent'] or 0, 100), now, milestone_id))
        else:
            conn.execute(
                "UPDATE project_phases SET status=?, completed_date=NULL, updated_at=?"
                " WHERE id=?",
                (status, now, milestone_id))
        _write_audit(conn, 'update', 'project_phases', milestone_id,
                     'status', row['status'], status)
        conn.commit()
        return True
    finally:
        conn.close()


def assign_hours_to_milestone(project_id, period, phase_id):
    """Attach a whole NIZAM period's hours to a milestone (phase_id=None unassigns).
    Returns rows updated, or -1 if the phase belongs to another project."""
    conn = get_db()
    try:
        if phase_id:
            ok = conn.execute(
                "SELECT 1 FROM project_phases WHERE id=? AND project_id=?",
                (phase_id, project_id)
            ).fetchone()
            if not ok:
                return -1
        cur = conn.execute(
            "UPDATE project_hours SET phase_id=? WHERE project_id=? AND period=?",
            (phase_id or None, project_id, period))
        _write_audit(conn, 'update', 'project_hours', None, 'phase_id', None, phase_id,
                     context=f'project {project_id} period {period} hours → milestone {phase_id}')
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# ========== Monthly roll-up & on-course verdict ==========

def _light_from_drift(drift):
    if drift is None:
        return 'none'
    if drift >= _DRIFT_ON:
        return 'on'
    if drift >= _DRIFT_EDGE:
        return 'edge'
    return 'off'


def _light_from_ratio(ratio):
    if ratio is None:
        return 'none'
    if ratio >= _COLLECT_ON:
        return 'on'
    if ratio >= _COLLECT_EDGE:
        return 'edge'
    return 'off'


def get_project_monthly_rollup(project_id, milestones=None):
    """Monthly PLAN (day-weighted from milestones) vs FAKT (date/period-based —
    independent of tagging), with cumulative to-date figures and the on-course
    verdict. 'To date' = months strictly before the current month; the current
    month is displayed but never judged (income lands late in a month)."""
    if milestones is None:
        conn = get_db()
        milestones = [dict(r) for r in conn.execute(
            "SELECT * FROM project_phases WHERE project_id=?", (project_id,)).fetchall()]
        conn.close()

    today = date.today()
    plan = {}
    undated = []
    any_late = any_behind = False
    plan_total_income = plan_total_expense = 0.0
    for m in milestones:
        st = _normalize_status(m.get('status'))
        d0, d1 = _parse_date(m.get('start_date')), _parse_date(m.get('end_date'))
        if d1 and d1 < today and st not in ('done', 'cancelled'):
            any_late = True
        if d0 and d1 and d1 >= d0 and st not in ('done', 'cancelled'):
            total = (d1 - d0).days + 1
            expected = min(max(((today - d0).days + 1) / total, 0), 1) * 100
            comp = 100 if st == 'done' else min(max(m.get('completion_percent') or 0, 0), 100)
            if comp < expected - _BEHIND_MARGIN:
                any_behind = True
        alloc = allocate_plan_to_months(m)
        if not alloc and ((m.get('planned_revenue') or 0) or _plan_expense_total(m)):
            undated.append(m.get('name'))
        for p, v in alloc.items():
            b = plan.setdefault(p, {'income': 0.0, 'expense': 0.0, 'hours': 0.0})
            b['income'] += v['income']
            b['expense'] += v['expense']
            b['hours'] += v['hours']
        plan_total_income += m.get('planned_revenue') or 0
        plan_total_expense += _plan_expense_total(m)

    actual = get_project_monthly_actuals(project_id)
    current = datetime.now().strftime('%Y-%m')
    months = sorted(set(plan) | set(actual))

    rows = []
    run_plan_profit = run_fact_profit = 0.0
    td = {'plan_income': 0.0, 'plan_expense': 0.0, 'fact_income': 0.0, 'fact_expense': 0.0}
    fact_total_income = fact_total_expense = 0.0
    for p in months:
        pl = plan.get(p, {'income': 0.0, 'expense': 0.0, 'hours': 0.0})
        fa = actual.get(p, _empty_bucket())
        plan_profit = pl['income'] - pl['expense']
        fact_profit = fa['income'] - fa['expense']
        run_plan_profit += plan_profit
        run_fact_profit += fact_profit
        fact_total_income += fa['income']
        fact_total_expense += fa['expense']
        if p < current:
            td['plan_income'] += pl['income']
            td['plan_expense'] += pl['expense']
            td['fact_income'] += fa['income']
            td['fact_expense'] += fa['expense']
        rows.append({
            'period': p,
            'plan_income': pl['income'], 'plan_expense': pl['expense'],
            'plan_hours': pl['hours'], 'plan_profit': plan_profit,
            'fact_income': fa['income'], 'fact_expense': fa['expense'],
            'fact_hours': fa['hours'], 'fact_profit': fact_profit,
            'd_profit': fact_profit - plan_profit,
            'cum_plan_profit': run_plan_profit, 'cum_fact_profit': run_fact_profit,
            'is_current': p == current, 'is_future': p > current,
            'unplanned': p not in plan and p in actual,
        })

    td_plan_profit = td['plan_income'] - td['plan_expense']
    td_fact_profit = td['fact_income'] - td['fact_expense']
    has_plan_td = td['plan_income'] > 0 or td['plan_expense'] > 0

    drift = ((td_fact_profit - td_plan_profit) / max(td['plan_income'], 1) * 100
             if has_plan_td else None)
    ratio = td['fact_income'] / td['plan_income'] if td['plan_income'] > 0 else None
    expense_d = ((td['fact_expense'] - td['plan_expense']) / max(td['plan_expense'], 1) * 100
                 if td['plan_expense'] > 0 else None)

    money = _light_from_drift(drift)
    collections = _light_from_ratio(ratio)
    if milestones:
        schedule = 'off' if any_late else ('edge' if any_behind else 'on')
    else:
        schedule = 'none'
    if money == 'off' or schedule == 'off':
        headline = 'off'
    elif money == 'edge' or schedule == 'edge':
        headline = 'edge'
    elif money == 'none' and schedule == 'none':
        headline = 'none'
    else:
        headline = 'on'

    if expense_d is None:
        expense_badge = 'none'
    elif expense_d > 5:
        expense_badge = 'over'
    elif expense_d < -10:
        expense_badge = 'under'
    else:
        expense_badge = 'border'

    return {
        'months': rows,
        'to_date': {
            'plan_income': td['plan_income'], 'plan_expense': td['plan_expense'],
            'plan_profit': td_plan_profit,
            'fact_income': td['fact_income'], 'fact_expense': td['fact_expense'],
            'fact_profit': td_fact_profit,
            'profit_drift': drift, 'income_ratio': ratio, 'expense_d_pct': expense_d,
        },
        'totals': {
            'plan_income': plan_total_income, 'plan_expense': plan_total_expense,
            'plan_profit': plan_total_income - plan_total_expense,
            'fact_income': fact_total_income, 'fact_expense': fact_total_expense,
            'fact_profit': fact_total_income - fact_total_expense,
        },
        'status': {'money': money, 'schedule': schedule,
                   'collections': collections, 'expense': expense_badge,
                   'headline': headline},
        'undated': undated,
        'current_month': current,
        'any_late': any_late,
        'any_behind': any_behind,
    }


def get_plan_overview():
    """One row per billable project that has milestones or any actuals:
    plan-to-date vs fact-to-date, headline status, milestone counts."""
    conn = get_db()
    projects = conn.execute('''
        SELECT p.id, p.name, p.status, p.plan_frozen_date,
               (SELECT COUNT(*) FROM project_phases pp WHERE pp.project_id = p.id) AS ms_total,
               (SELECT COUNT(*) FROM project_phases pp
                 WHERE pp.project_id = p.id AND pp.status = 'done') AS ms_done
        FROM projects p WHERE p.is_billable = 1 ORDER BY p.name
    ''').fetchall()
    conn.close()

    rows = []
    counts = {'on': 0, 'edge': 0, 'off': 0, 'none': 0}
    totals = {'plan_income': 0.0, 'plan_expense': 0.0, 'plan_profit': 0.0,
              'fact_income': 0.0, 'fact_expense': 0.0, 'fact_profit': 0.0}
    for p in projects:
        rollup = get_project_monthly_rollup(p['id'])
        if not rollup['months'] and not p['ms_total']:
            continue  # nothing planned, nothing happened — not worth a row
        headline = rollup['status']['headline']
        counts[headline] += 1
        td = rollup['to_date']
        for k in totals:
            totals[k] += td[k]
        plan_months = [r['period'] for r in rollup['months'] if not r['unplanned']]
        rows.append({
            'id': p['id'], 'name': p['name'], 'project_status': p['status'],
            'plan_frozen_date': p['plan_frozen_date'],
            'ms_total': p['ms_total'], 'ms_done': p['ms_done'],
            'months_planned': len(plan_months),
            'months_range': (f"{plan_months[0]} – {plan_months[-1]}" if plan_months else ''),
            'to_date': td,
            'status': rollup['status'],
            'any_late': rollup['any_late'],
        })
    return {'rows': rows, 'counts': counts, 'totals': totals}


def get_projects_on_course_summary():
    """Dashboard counts derived from the overview."""
    ov = get_plan_overview()
    tracked = ov['counts']['on'] + ov['counts']['edge'] + ov['counts']['off']
    late_projects = sum(1 for r in ov['rows'] if r['any_late'])
    return {
        'on': ov['counts']['on'], 'edge': ov['counts']['edge'],
        'off': ov['counts']['off'], 'none': ov['counts']['none'],
        'tracked': tracked, 'total': len(ov['rows']),
        'late_projects': late_projects,
    }


# ========== Pricing-freeze generation ==========

def generate_milestones_from_pricing(project_id, schedule_rows, total_income,
                                     total_hours, labor_cost, outsourcing=0,
                                     material=0, overwrite=False):
    """Create the milestone schedule frozen from the pricing page.

    schedule_rows: [{name, work_type, start 'YYYY-MM', end 'YYYY-MM',
                     income_pct, hours_pct}, ...]
    planned_cost follows hours share (risk-exclusive labor); outsourcing and
    material land on the last milestone. The last row absorbs all rounding
    remainders so milestone totals equal the frozen plan exactly.
    Returns (created_count, error) where error ∈ (None,'empty','pct','exists','locked').
    """
    rows = [r for r in schedule_rows if (r.get('name') or '').strip()]
    if not rows:
        return 0, 'empty'
    try:
        i_pcts = [float(r.get('income_pct') or 0) for r in rows]
        h_pcts = [float(r.get('hours_pct') or 0) for r in rows]
    except (TypeError, ValueError):
        return 0, 'pct'
    if abs(sum(i_pcts) - 100) > 0.5 or abs(sum(h_pcts) - 100) > 0.5:
        return 0, 'pct'

    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT COUNT(*) FROM project_phases WHERE project_id=?", (project_id,)
        ).fetchone()[0]
        if existing:
            if not overwrite:
                return 0, 'exists'
            used = conn.execute('''
                SELECT COUNT(*) FROM transactions t
                JOIN project_phases pp ON t.phase_id = pp.id
                WHERE pp.project_id=?''', (project_id,)).fetchone()[0]
            if used:
                return 0, 'locked'
            conn.execute(
                "UPDATE project_hours SET phase_id=NULL WHERE project_id=? AND phase_id IS NOT NULL",
                (project_id,))
            conn.execute("DELETE FROM project_phases WHERE project_id=?", (project_id,))
            _write_audit(conn, 'delete', 'project_phases', None,
                         context=f'milestones regenerated from pricing (project {project_id})')

        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        acc_i = acc_h = acc_c = 0.0
        created = 0
        for i, r in enumerate(rows):
            last = i == len(rows) - 1
            if last:
                inc, hrs, cost = total_income - acc_i, total_hours - acc_h, labor_cost - acc_c
            else:
                inc = total_income * i_pcts[i] / 100
                hrs = total_hours * h_pcts[i] / 100
                cost = labor_cost * h_pcts[i] / 100
                acc_i += inc
                acc_h += hrs
                acc_c += cost
            start_m, end_m = (r.get('start') or '').strip(), (r.get('end') or '').strip()
            if start_m and end_m and end_m < start_m:
                start_m, end_m = end_m, start_m
            wt = (r.get('work_type') or '').strip() or None
            conn.execute('''INSERT INTO project_phases
                (project_id, code, name, sort_order, work_type, start_date, end_date,
                 planned_hours, planned_cost, planned_outsourcing, planned_material,
                 planned_revenue, status, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (project_id, f"{wt or 'ms'}-{i + 1}", r['name'].strip(), (i + 1) * 10, wt,
                 _month_first_day(start_m), _month_last_day(end_m or start_m),
                 hrs, cost, outsourcing if last else 0, material if last else 0,
                 inc, 'planned', now))
            created += 1
        _write_audit(conn, 'create', 'project_phases', None,
                     context=f'{created} milestones generated from pricing (project {project_id})')
        conn.commit()
        return created, None
    finally:
        conn.close()
