"""Pricing engine and risk scoring — ported from v4, arithmetic unchanged.

One price ladder, price_from_cost(), serves both a single milestone and a whole
project. It is linear in its cost inputs, so

    sum(per-milestone target) == target(sum labor, sum outsourcing, sum material)

exactly — which is why the per-milestone figures on the project card can never
disagree with the project totals. test_pricing.py pins that identity.

The risk-exclusive invariant holds throughout: planned_cost — on both projects
and project_phases — is labor only. Risk and target margin live in the price,
never in the cost, so plan and actual always compare like with like.
"""
from .base import get_db, get_setting, get_current_usd_rate, now_ts, _write_audit
from .staff import calculate_hourly_rate, rate_context

# A stored price this close to the computed suggestion counts as "auto".
_OVERRIDE_TOL_PCT = 0.005
_OVERRIDE_TOL_ABS = 1.0

RISK_CLIENT_TYPES = ('regular', 'new', 'government')
RISK_COMPLEXITIES = ('simple', 'medium', 'high')


def calculate_risk_score(deadline_months=None, client_type='new',
                         complexity='medium', currency='UZS'):
    """Four weighted factors → (score, coefficient).

    deadline 30% | client type 25% | complexity 25% | currency 20%
    Each factor scores 1 (low), 2 (medium) or 3 (high); the coefficient adds
    15% of the distance above 1, so a perfectly safe job prices at 1.00 and the
    riskiest at 1.30.
    """
    if deadline_months is None or deadline_months > 8:
        d = 1
    elif deadline_months >= 4:
        d = 2
    else:
        d = 3

    cl = {'regular': 1, 'new': 2, 'government': 3}.get(client_type, 2)
    cx = {'simple': 1, 'medium': 2, 'high': 3}.get(complexity, 2)
    cu = {'UZS': 1, 'USD': 2}.get(currency, 3)

    weighted = d * 0.30 + cl * 0.25 + cx * 0.25 + cu * 0.20
    risk_coeff = 1 + (weighted - 1) * 0.15
    return round(weighted, 2), round(risk_coeff, 3)


def price_from_cost(labor_cost, outsourcing=0, material=0, risk_coeff=1.15,
                    target_margin=0.50):
    """The single price ladder — one milestone or a whole project.

    minimum = risk-loaded labor + pass-through outsourcing/material (walk-away)
    target  = minimum grossed up so target_margin is genuinely achieved
    premium = target + 20%
    """
    labor_cost = labor_cost or 0
    outsourcing = outsourcing or 0
    material = material or 0
    risked = labor_cost * risk_coeff
    minimum = risked + outsourcing + material
    target = minimum / (1 - target_margin) if target_margin < 1 else minimum * 2
    return {'risked_cost': risked, 'minimum': minimum,
            'target': target, 'premium': target * 1.2}


def get_target_margin():
    """Target margin from settings; a stored 0 also means 'use the default'."""
    return get_setting('target_margin') or 0.50


def _is_overridden(price, suggested):
    """True only when someone deliberately typed a price other than the suggestion.

    An unset price (0) means 'not priced yet', not an override — otherwise
    every freshly created milestone would be skipped by apply_suggested_prices().
    """
    if not price or price <= 0:
        return False
    return abs(price - suggested) > max(_OVERRIDE_TOL_ABS, suggested * _OVERRIDE_TOL_PCT)


# ========== Project risk parameters ==========

def get_project_risk(project_id, conn=None):
    """The four stored risk inputs plus the derived score and coefficient."""
    own = conn is None
    conn = conn or get_db()
    try:
        row = conn.execute(
            "SELECT risk_deadline_months, risk_client_type, risk_complexity,"
            " risk_currency, risk_coefficient, risk_score, currency"
            " FROM projects WHERE id=?", (project_id,)).fetchone()
    finally:
        if own:
            conn.close()
    if not row:
        return None

    kwargs = {
        'deadline_months': (row['risk_deadline_months']
                            if row['risk_deadline_months'] is not None else 6),
        'client_type': row['risk_client_type'] or 'new',
        'complexity': row['risk_complexity'] or 'medium',
        'currency': row['risk_currency'] or row['currency'] or 'UZS',
    }
    score, coeff = calculate_risk_score(**kwargs)
    kwargs['risk_score'] = score
    kwargs['risk_coeff'] = coeff
    dm = kwargs['deadline_months']
    kwargs['deadline_bucket'] = 3 if dm < 4 else (6 if dm <= 8 else 10)
    return kwargs


def save_project_risk(project_id, deadline_months=None, client_type='new',
                      complexity='medium', currency='UZS'):
    """Persist the four risk inputs plus both outputs of calculate_risk_score."""
    try:
        deadline_months = int(deadline_months) if deadline_months not in (None, '') else None
    except (TypeError, ValueError):
        deadline_months = None
    client_type = client_type if client_type in RISK_CLIENT_TYPES else 'new'
    complexity = complexity if complexity in RISK_COMPLEXITIES else 'medium'
    currency = (currency or 'UZS').strip() or 'UZS'

    score, coeff = calculate_risk_score(deadline_months, client_type, complexity, currency)
    conn = get_db()
    try:
        if not conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
            return None
        conn.execute(
            "UPDATE projects SET risk_deadline_months=?, risk_client_type=?,"
            " risk_complexity=?, risk_currency=?, risk_coefficient=?, risk_score=?,"
            " updated_at=? WHERE id=?",
            (deadline_months, client_type, complexity, currency, coeff, score,
             now_ts(), project_id))
        _write_audit(conn, 'update', 'projects', project_id, 'risk_coefficient',
                     None, coeff,
                     context=f'risk set: {deadline_months}mo/{client_type}/'
                             f'{complexity}/{currency} -> score {score}')
        conn.commit()
    finally:
        conn.close()
    return {'deadline_months': deadline_months, 'client_type': client_type,
            'complexity': complexity, 'currency': currency,
            'risk_score': score, 'risk_coeff': coeff}


# ========== Milestone staff plan ==========

def get_milestone_staff(phase_id, conn=None):
    """Employee plan rows for one milestone, costed at their snapshotted rates."""
    own = conn is None
    conn = conn or get_db()
    try:
        rows = conn.execute(
            "SELECT ms.staff_id, s.name, s.role, ms.est_hours, ms.cost_rate,"
            " ms.billing_rate FROM milestone_staff ms"
            " JOIN staff s ON s.id = ms.staff_id"
            " WHERE ms.phase_id=? ORDER BY s.name", (phase_id,)).fetchall()
    finally:
        if own:
            conn.close()
    return [{'staff_id': r['staff_id'], 'name': r['name'], 'role': r['role'],
             'est_hours': r['est_hours'] or 0,
             'cost_rate': r['cost_rate'] or 0, 'billing_rate': r['billing_rate'] or 0,
             'cost': (r['est_hours'] or 0) * (r['cost_rate'] or 0),
             'billing': (r['est_hours'] or 0) * (r['billing_rate'] or 0)}
            for r in rows]


def set_milestone_staff(phase_id, staff_rows):
    """Replace a milestone's employee plan and recompute its labor plan.

    Rates are snapshotted per employee at save time (the frozen-plan pattern),
    so a later salary change never silently rewrites a saved plan.
    project_phases.planned_hours/planned_cost are derived from these rows.
    """
    ctx = rate_context()
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
            info = calculate_hourly_rate(sid, context=ctx)
            rate_cache[sid] = ((info['cost_rate'], info['billing_rate'])
                               if info else (0.0, 0.0))
        cleaned.append((sid, hours) + rate_cache[sid])

    conn = get_db()
    try:
        if cleaned:   # drop rows whose staff_id would violate the FK
            ph = ','.join('?' * len(cleaned))
            valid = {r['id'] for r in conn.execute(
                f"SELECT id FROM staff WHERE id IN ({ph})", [c[0] for c in cleaned])}
            cleaned = [c for c in cleaned if c[0] in valid]
        total_hours = sum(h for _, h, _, _ in cleaned)
        total_cost = sum(h * cr for _, h, cr, _ in cleaned)
        total_billing = sum(h * br for _, h, _, br in cleaned)
        had_rows = conn.execute(
            "SELECT COUNT(*) AS n FROM milestone_staff WHERE phase_id=?",
            (phase_id,)).fetchone()['n']
        conn.execute("DELETE FROM milestone_staff WHERE phase_id=?", (phase_id,))
        conn.executemany(
            "INSERT OR REPLACE INTO milestone_staff"
            " (phase_id, staff_id, est_hours, cost_rate, billing_rate) VALUES (?,?,?,?,?)",
            [(phase_id, sid, h, cr, br) for sid, h, cr, br in cleaned])
        if cleaned or had_rows:
            conn.execute(
                "UPDATE project_phases SET planned_hours=?, planned_cost=?, updated_at=?"
                " WHERE id=?", (total_hours, total_cost, now_ts(), phase_id))
            _write_audit(conn, 'update', 'project_phases', phase_id,
                         context=f'staff plan set: {len(cleaned)} rows,'
                                 f' hours={total_hours:g}, cost={total_cost:.0f}')
        conn.commit()
    finally:
        conn.close()
    return {'planned_hours': total_hours, 'planned_cost': total_cost,
            'planned_billing': total_billing}


# ========== Project plan pricing ==========

def price_project_plan(project_id, risk_kwargs=None, milestones=None):
    """Price a project from its milestone schedule.

    Each milestone contributes its own labor cost, outsourcing and material.
    Rows carry running cumulative expense and price — the intermediate figures
    — and the totals are the same ladder applied to the summed costs.
    """
    from .milestones import get_project_milestones

    risk = get_project_risk(project_id)
    if risk is None:
        return None
    if risk_kwargs:
        score, coeff = calculate_risk_score(**risk_kwargs)
        risk = dict(risk, **risk_kwargs)
        risk['risk_score'], risk['risk_coeff'] = score, coeff
    risk_coeff = risk['risk_coeff']
    target_margin = get_target_margin()
    usd_rate = get_current_usd_rate()

    if milestones is None:
        milestones = get_project_milestones(project_id)

    rows = []
    cum_expense = cum_price = 0.0
    t_hours = t_labor = t_out = t_mat = t_price = 0.0
    for m in milestones:
        # Cancelled milestones are not part of the plan, for the same reason
        # rollup_milestone_plan() excludes them: the card totals and the frozen
        # baseline must always agree.
        if m.get('status') == 'cancelled':
            continue
        labor = m.get('planned_cost') or 0
        out = m.get('planned_outsourcing') or 0
        mat = m.get('planned_material') or 0
        expense = labor + out + mat
        ladder = price_from_cost(labor, out, mat, risk_coeff, target_margin)
        price = m.get('planned_revenue') or 0
        cum_expense += expense
        cum_price += price
        t_hours += m.get('planned_hours') or 0
        t_labor += labor
        t_out += out
        t_mat += mat
        t_price += price
        rows.append({
            'id': m['id'], 'name': m.get('name'),
            'hours': m.get('planned_hours') or 0,
            'labor': labor, 'outsourcing': out, 'material': mat, 'expense': expense,
            'minimum': ladder['minimum'], 'suggested': ladder['target'],
            'premium': ladder['premium'], 'price': price,
            'is_overridden': _is_overridden(price, ladder['target']),
            'margin_pct': ((price - expense) / price * 100) if price > 0 else 0,
            'cum_expense': cum_expense, 'cum_price': cum_price,
        })

    totals = price_from_cost(t_labor, t_out, t_mat, risk_coeff, target_margin)
    t_expense = t_labor + t_out + t_mat
    return {
        'rows': rows, 'risk': risk, 'risk_coeff': risk_coeff,
        'target_margin': target_margin, 'usd_rate': usd_rate,
        'milestone_count': len(rows),
        'total_hours': t_hours, 'total_labor': t_labor,
        'total_outsourcing': t_out, 'total_material': t_mat,
        'total_expense': t_expense,
        'mizan_cost': totals['risked_cost'],
        'minimum_contract': totals['minimum'],
        'target_contract': totals['target'],
        'premium_contract': totals['premium'],
        'minimum_usd': totals['minimum'] / usd_rate if usd_rate else 0,
        'target_usd': totals['target'] / usd_rate if usd_rate else 0,
        'premium_usd': totals['premium'] / usd_rate if usd_rate else 0,
        'plan_revenue_total': t_price,
        'revenue_gap': t_price - totals['target'],
        'plan_margin_pct': ((t_price - t_expense) / t_price * 100) if t_price > 0 else 0,
        'has_override': any(r['is_overridden'] for r in rows),
    }


def apply_suggested_prices(project_id, force=False):
    """Set planned_revenue to the ladder's target on each milestone.

    Deliberately overridden rows are left alone unless force is set.
    Returns the number of rows changed, or None for an unknown project.
    """
    plan = price_project_plan(project_id)
    if plan is None:
        return None
    changed = 0
    conn = get_db()
    try:
        for r in plan['rows']:
            if r['is_overridden'] and not force:
                continue
            if abs(r['price'] - r['suggested']) < 0.5:
                continue
            conn.execute(
                "UPDATE project_phases SET planned_revenue=?, updated_at=? WHERE id=?",
                (r['suggested'], now_ts(), r['id']))
            changed += 1
        if changed:
            _write_audit(conn, 'update', 'project_phases', None,
                         context=f'{changed} milestone prices set from the pricing'
                                 f' ladder (project {project_id})')
        conn.commit()
    finally:
        conn.close()
    return changed


def freeze_project_plan(project_id):
    """Copy the live milestone plan into projects.planned_* as the baseline.

    projects.planned_* is the frozen baseline the budget page compares actuals
    against; project_phases.planned_* is the live working plan. Only this
    function moves the baseline, and it stamps plan_frozen_date when it does.

    planned_cost stays risk-EXCLUSIVE (labor only) so plan and fact compare on
    the same basis; the risk uplift lives in planned_revenue.

    Returns (result, error) with error in (None, 'notfound', 'empty').
    """
    from .milestones import rollup_milestone_plan

    roll = rollup_milestone_plan(project_id)
    if roll is None:
        return None, 'notfound'
    if roll['count'] == 0:
        # Never zero out an existing baseline for a project with no milestones.
        return None, 'empty'

    risk = get_project_risk(project_id) or {}
    today = now_ts()[:10]
    conn = get_db()
    try:
        cur = conn.execute(
            "SELECT contract_amount, estimated_total_hours FROM projects WHERE id=?",
            (project_id,)).fetchone()
        # A signed contract amount and an existing hours estimate are never
        # silently replaced by a quote — the quote lives in planned_revenue.
        keep_contract = cur and (cur['contract_amount'] or 0) > 0
        keep_est = cur and (cur['estimated_total_hours'] or 0) > 0
        conn.execute(
            "UPDATE projects SET estimated_total_hours=?, contract_amount=?,"
            " planned_hours=?, planned_cost=?, planned_revenue=?,"
            " planned_outsourcing=?, planned_material=?, plan_frozen_date=?,"
            " updated_at=? WHERE id=?",
            (cur['estimated_total_hours'] if keep_est else roll['planned_hours'],
             cur['contract_amount'] if keep_contract else roll['planned_revenue'],
             roll['planned_hours'], roll['planned_cost'], roll['planned_revenue'],
             roll['planned_outsourcing'], roll['planned_material'], today,
             now_ts(), project_id))
        _write_audit(conn, 'update', 'projects', project_id, 'plan_frozen_date',
                     None, today,
                     context=f"plan frozen from {roll['count']} milestones:"
                             f" hours={roll['planned_hours']:g},"
                             f" cost={roll['planned_cost']:.0f},"
                             f" revenue={roll['planned_revenue']:.0f}")
        conn.commit()
    finally:
        conn.close()
    return dict(roll, plan_frozen_date=today, risk_coeff=risk.get('risk_coeff')), None


def get_plan_baseline(project_id):
    """The frozen baseline and how far the live milestone plan has drifted."""
    from .milestones import rollup_milestone_plan

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT planned_hours, planned_cost, planned_revenue, planned_outsourcing,"
            " planned_material, plan_frozen_date, contract_amount"
            " FROM projects WHERE id=?", (project_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    b = {k: (row[k] or 0) for k in ('planned_hours', 'planned_cost', 'planned_revenue',
                                    'planned_outsourcing', 'planned_material',
                                    'contract_amount')}
    b['plan_frozen_date'] = row['plan_frozen_date']
    b['is_frozen'] = bool(row['plan_frozen_date'])
    b['expense'] = b['planned_cost'] + b['planned_outsourcing'] + b['planned_material']

    roll = rollup_milestone_plan(project_id) or {}
    live_expense = ((roll.get('planned_cost') or 0)
                    + (roll.get('planned_outsourcing') or 0)
                    + (roll.get('planned_material') or 0))
    b['live_expense'] = live_expense
    b['live_revenue'] = roll.get('planned_revenue') or 0
    b['live_hours'] = roll.get('planned_hours') or 0
    b['d_expense'] = live_expense - b['expense']
    b['d_revenue'] = b['live_revenue'] - b['planned_revenue']
    # Drifted = frozen, has milestones, and the live plan no longer matches it.
    b['is_stale'] = bool(
        b['is_frozen'] and (roll.get('count') or 0) > 0
        and (abs(b['d_expense']) > max(1.0, b['expense'] * 0.005)
             or abs(b['d_revenue']) > max(1.0, b['planned_revenue'] * 0.005)))
    return b


# ========== Scratch quote (no project, no DB writes) ==========

def _row_labor_cost(staff_rows, rate_cache, ctx):
    """Hours x cost_rate for one schedule row, caching rates per employee."""
    hours = cost = billing = 0.0
    for sr in staff_rows or []:
        try:
            sid = int(sr.get('staff_id') or 0)
            hrs = float(sr.get('hours') or sr.get('est_hours') or 0)
        except (TypeError, ValueError):
            continue
        if sid <= 0 or hrs <= 0:
            continue
        if sid not in rate_cache:
            info = calculate_hourly_rate(sid, context=ctx)
            rate_cache[sid] = ((info['cost_rate'], info['billing_rate'])
                               if info else (0.0, 0.0))
        cr, br = rate_cache[sid]
        hours += hrs
        cost += hrs * cr
        billing += hrs * br
    return hours, cost, billing


def quote_schedule(schedule_rows, risk_kwargs=None):
    """Price a milestone schedule that is not (yet) in the database.

    Same kernel and same row shape as price_project_plan(), so the scratch
    quote and the project card cannot drift apart.
    """
    score, risk_coeff = calculate_risk_score(**(risk_kwargs or {}))
    target_margin = get_target_margin()
    usd_rate = get_current_usd_rate()
    ctx = rate_context()

    rate_cache = {}
    rows = []
    cum_expense = cum_price = 0.0
    t_hours = t_labor = t_out = t_mat = t_price = 0.0
    for r in schedule_rows or []:
        hours, labor, _billing = _row_labor_cost(r.get('staff'), rate_cache, ctx)
        try:
            out = float(r.get('outsourcing') or 0)
            mat = float(r.get('material') or 0)
        except (TypeError, ValueError):
            out = mat = 0.0
        expense = labor + out + mat
        ladder = price_from_cost(labor, out, mat, risk_coeff, target_margin)
        try:
            price = float(r.get('price') or 0)
        except (TypeError, ValueError):
            price = 0.0
        if price <= 0:
            price = ladder['target']
        cum_expense += expense
        cum_price += price
        t_hours += hours
        t_labor += labor
        t_out += out
        t_mat += mat
        t_price += price
        rows.append({
            'id': None, 'name': (r.get('name') or '').strip(),
            'hours': hours, 'labor': labor, 'outsourcing': out, 'material': mat,
            'expense': expense, 'minimum': ladder['minimum'],
            'suggested': ladder['target'], 'premium': ladder['premium'], 'price': price,
            'is_overridden': _is_overridden(price, ladder['target']),
            'margin_pct': ((price - expense) / price * 100) if price > 0 else 0,
            'cum_expense': cum_expense, 'cum_price': cum_price,
        })

    totals = price_from_cost(t_labor, t_out, t_mat, risk_coeff, target_margin)
    t_expense = t_labor + t_out + t_mat
    return {
        'rows': rows, 'risk_score': score, 'risk_coeff': risk_coeff,
        'target_margin': target_margin, 'usd_rate': usd_rate,
        'milestone_count': len(rows),
        'total_hours': t_hours, 'total_labor': t_labor,
        'total_outsourcing': t_out, 'total_material': t_mat,
        'total_expense': t_expense,
        'mizan_cost': totals['risked_cost'],
        'minimum_contract': totals['minimum'],
        'target_contract': totals['target'],
        'premium_contract': totals['premium'],
        'minimum_usd': totals['minimum'] / usd_rate if usd_rate else 0,
        'target_usd': totals['target'] / usd_rate if usd_rate else 0,
        'premium_usd': totals['premium'] / usd_rate if usd_rate else 0,
        'plan_revenue_total': t_price,
        'revenue_gap': t_price - totals['target'],
        'plan_margin_pct': ((t_price - t_expense) / t_price * 100) if t_price > 0 else 0,
        'has_override': any(r['is_overridden'] for r in rows),
    }
