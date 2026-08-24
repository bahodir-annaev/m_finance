"""Pricing engine and risk scoring.

One price ladder, `price_from_cost()`, serves both a single milestone and a
whole project. It is linear in its cost inputs, so

    Σ(per-milestone target) == target(Σ labor, Σ outsourcing, Σ material)

exactly — which is why the intermediate (per-milestone, cumulative) figures on
the project page can never disagree with the project totals.

The risk-exclusive invariant holds throughout: planned_cost — on both
`projects` and `project_phases` — is labor only. Risk and target margin live
in the price, never in the cost.
"""
from datetime import datetime

from .base import (get_db, get_setting, get_current_usd_rate, _write_audit)
from .staff import calculate_hourly_rate
from .milestones import (get_project_milestones, rollup_milestone_plan,
                         add_milestone, set_milestone_staff,
                         _month_first_day, _month_last_day)

# A stored price this close to the computed suggestion counts as "auto"
_OVERRIDE_TOL_PCT = 0.005
_OVERRIDE_TOL_ABS = 1.0

RISK_CLIENT_TYPES = ('regular', 'new', 'government')
RISK_COMPLEXITIES = ('simple', 'medium', 'high')


def calculate_risk_score(deadline_months=None, client_type='new', complexity='medium', currency='UZS'):
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
    """The single price ladder — used for one milestone and for a whole project.

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
    return {
        'risked_cost': risked,
        'minimum': minimum,
        'target': target,
        'premium': target * 1.2,
    }


def get_target_margin():
    """Target margin from settings; a stored 0 also means "use the default"."""
    return get_setting('target_margin') or 0.50


def pricing_estimate(staff_hours_list, risk_kwargs=None, outsourcing=0, material=0):
    """Ad-hoc quote from a flat list of {staff_id, planned_hours}.

    Kept for the scratch-quote path on /pricing; the project-scoped planner
    uses price_project_plan() instead.
    """
    if risk_kwargs is None:
        risk_kwargs = {}

    _, risk_coeff = calculate_risk_score(**risk_kwargs)
    target_margin = get_target_margin()
    usd_rate = get_current_usd_rate()

    staff_details = []
    total_cost = 0
    total_billing = 0
    total_hours = 0

    # One connection for the whole roll-up instead of one per staff row.
    conn = get_db()
    try:
        for item in staff_hours_list:
            sid = item['staff_id']
            hrs = item['planned_hours']
            rate_info = calculate_hourly_rate(sid)
            if not isinstance(rate_info, dict):
                continue

            cost = hrs * rate_info['cost_rate']
            billing = hrs * rate_info['billing_rate']
            total_cost += cost
            total_billing += billing
            total_hours += hrs

            s = conn.execute("SELECT name, role FROM staff WHERE id=?", (sid,)).fetchone()

            staff_details.append({
                'name': s['name'] if s else f'ID:{sid}',
                'role': s['role'] if s else '',
                'hours': hrs,
                'cost_rate': rate_info['cost_rate'],
                'billing_rate': rate_info['billing_rate'],
                'cost': cost,
                'billing': billing,
            })
    finally:
        conn.close()

    ladder = price_from_cost(total_cost, outsourcing, material, risk_coeff, target_margin)

    return {
        'staff_details': staff_details,
        'total_hours': total_hours,
        'total_cost': total_cost,
        'total_billing': total_billing,
        'risk_coeff': risk_coeff,
        'mizan_cost': ladder['risked_cost'],
        'outsourcing': outsourcing,
        'material': material,
        'minimum_contract': ladder['minimum'],
        'target_contract': ladder['target'],
        'premium_contract': ladder['premium'],
        'minimum_usd': ladder['minimum'] / usd_rate,
        'target_usd': ladder['target'] / usd_rate,
        'premium_usd': ladder['premium'] / usd_rate,
        'target_margin': target_margin,
    }


# ========== Project risk parameters ==========

def get_project_risk(project_id, conn=None):
    """The four stored risk inputs plus the derived score/coefficient.

    Falls back to the pricing-engine defaults for a project that has never been
    priced, so the UI always has something selected.
    """
    own = conn is None
    if own:
        conn = get_db()
    try:
        row = conn.execute(
            "SELECT risk_deadline_months, risk_client_type, risk_complexity,"
            " risk_currency, risk_coefficient, risk_score, currency"
            " FROM projects WHERE id=?", (project_id,)
        ).fetchone()
    finally:
        if own:
            conn.close()
    if not row:
        return None

    kwargs = {
        'deadline_months': row['risk_deadline_months'] if row['risk_deadline_months'] is not None else 6,
        'client_type': row['risk_client_type'] or 'new',
        'complexity': row['risk_complexity'] or 'medium',
        'currency': row['risk_currency'] or row['currency'] or 'UZS',
    }
    score, coeff = calculate_risk_score(**kwargs)
    # A project priced before v6.3 has a stored coefficient but no inputs;
    # keep the stored coefficient authoritative so its plan does not shift.
    if row['risk_deadline_months'] is None and (row['risk_coefficient'] or 0) > 0:
        coeff = row['risk_coefficient']
        score = row['risk_score'] or score
    kwargs['risk_score'] = score
    kwargs['risk_coeff'] = coeff
    # Representative month value for the three-option UI select
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
             datetime.now().strftime('%Y-%m-%d %H:%M:%S'), project_id))
        _write_audit(conn, 'update', 'projects', project_id, 'risk_coefficient',
                     None, coeff,
                     context=f'risk set: {deadline_months}mo/{client_type}/'
                             f'{complexity}/{currency} → score {score}')
        conn.commit()
    finally:
        conn.close()
    return {'deadline_months': deadline_months, 'client_type': client_type,
            'complexity': complexity, 'currency': currency,
            'risk_score': score, 'risk_coeff': coeff, 'is_stored': True}


# ========== Project plan pricing (milestones → intermediate + total) ==========

def _is_overridden(price, suggested):
    """True only when someone deliberately typed a price other than the suggestion.

    An unset price (0) is "not priced yet", not an override — otherwise every
    freshly created milestone would be skipped by apply_suggested_prices().
    """
    if not price or price <= 0:
        return False
    return abs(price - suggested) > max(_OVERRIDE_TOL_ABS, suggested * _OVERRIDE_TOL_PCT)


def price_project_plan(project_id, risk_kwargs=None, milestones=None):
    """Price a project from its milestone schedule.

    Each milestone contributes its own labor cost (derived from milestone_staff
    rows by set_milestone_staff, or its stored scalar for legacy rows), its
    outsourcing and its material. Rows carry running cumulative expense and
    price — the "intermediate" figures — and the totals are the same ladder
    applied to the summed costs.

    risk_kwargs overrides the stored project risk for a live preview.
    """
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
        # Cancelled milestones are not part of the plan — excluded here for the
        # same reason rollup_milestone_plan() excludes them, so the card totals
        # and the frozen baseline always agree.
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
            'id': m['id'],
            'name': m.get('name'),
            'hours': m.get('planned_hours') or 0,
            'labor': labor,
            'outsourcing': out,
            'material': mat,
            'expense': expense,
            'minimum': ladder['minimum'],
            'suggested': ladder['target'],
            'premium': ladder['premium'],
            'price': price,
            'is_overridden': _is_overridden(price, ladder['target']),
            'margin_pct': ((price - expense) / price * 100) if price > 0 else 0,
            'cum_expense': cum_expense,
            'cum_price': cum_price,
        })

    totals = price_from_cost(t_labor, t_out, t_mat, risk_coeff, target_margin)
    t_expense = t_labor + t_out + t_mat
    return {
        'rows': rows,
        'risk': risk,
        'risk_coeff': risk_coeff,
        'target_margin': target_margin,
        'usd_rate': usd_rate,
        'milestone_count': len(rows),
        'total_hours': t_hours,
        'total_labor': t_labor,
        'total_outsourcing': t_out,
        'total_material': t_mat,
        'total_expense': t_expense,
        'mizan_cost': totals['risked_cost'],
        'minimum_contract': totals['minimum'],
        'target_contract': totals['target'],
        'premium_contract': totals['premium'],
        'minimum_usd': totals['minimum'] / usd_rate if usd_rate else 0,
        'target_usd': totals['target'] / usd_rate if usd_rate else 0,
        'premium_usd': totals['premium'] / usd_rate if usd_rate else 0,
        # The plan as actually priced (with any per-row overrides) vs the ladder
        'plan_revenue_total': t_price,
        'revenue_gap': t_price - totals['target'],
        'plan_margin_pct': ((t_price - t_expense) / t_price * 100) if t_price > 0 else 0,
        'has_override': any(r['is_overridden'] for r in rows),
    }


def apply_suggested_prices(project_id, force=False):
    """Set planned_revenue = suggested target price on the project's milestones.

    Rows whose price was deliberately overridden are left alone unless force.
    Returns the number of rows changed, or None for an unknown project.
    """
    plan = price_project_plan(project_id)
    if plan is None:
        return None
    changed = 0
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn = get_db()
    try:
        for r in plan['rows']:
            if r['is_overridden'] and not force:
                continue
            if abs(r['price'] - r['suggested']) < 0.5:
                continue
            conn.execute(
                "UPDATE project_phases SET planned_revenue=?, updated_at=? WHERE id=?",
                (r['suggested'], now, r['id']))
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
    """Write the live milestone plan into projects.planned_* as the baseline.

    projects.planned_* is the frozen baseline /budget compares FACT against;
    project_phases.planned_* is the live working plan. Only this function
    moves the baseline, and it stamps plan_frozen_date when it does.

    planned_cost stays risk-EXCLUSIVE (labor only) so plan and fact compare
    like with like; the risk uplift lives in planned_revenue.

    Returns (result_dict, error) with error ∈ (None, 'notfound', 'empty').
    """
    roll = rollup_milestone_plan(project_id)
    if roll is None:
        return None, 'notfound'
    if roll['count'] == 0:
        # Never zero out an existing baseline for a project with no milestones.
        return None, 'empty'

    risk = get_project_risk(project_id) or {}
    today = datetime.now().strftime('%Y-%m-%d')
    conn = get_db()
    try:
        cur = conn.execute(
            "SELECT contract_amount, estimated_total_hours FROM projects WHERE id=?",
            (project_id,)).fetchone()
        # A signed contract amount and an existing hours estimate are never
        # silently replaced by a quote — the quote lives in planned_revenue.
        keep_contract = cur and (cur['contract_amount'] or 0) > 0
        keep_est = cur and (cur['estimated_total_hours'] or 0) > 0
        conn.execute('''UPDATE projects SET
            estimated_total_hours=?, contract_amount=?,
            planned_hours=?, planned_cost=?, planned_revenue=?,
            planned_outsourcing=?, planned_material=?, plan_frozen_date=?,
            updated_at=? WHERE id=?''',
            (cur['estimated_total_hours'] if keep_est else roll['planned_hours'],
             cur['contract_amount'] if keep_contract else roll['planned_revenue'],
             roll['planned_hours'], roll['planned_cost'], roll['planned_revenue'],
             roll['planned_outsourcing'], roll['planned_material'], today,
             datetime.now().strftime('%Y-%m-%d %H:%M:%S'), project_id))
        _write_audit(conn, 'update', 'projects', project_id, 'plan_frozen_date',
                     None, today,
                     context=f"plan frozen from {roll['count']} milestones:"
                             f" hours={roll['planned_hours']:g},"
                             f" cost={roll['planned_cost']:.0f},"
                             f" revenue={roll['planned_revenue']:.0f}")
        conn.commit()
    finally:
        conn.close()
    return dict(roll, plan_frozen_date=today,
                risk_coeff=risk.get('risk_coeff')), None


# ========== Scratch quote (no project, no DB writes) ==========

def _row_labor_cost(staff_rows, rate_cache):
    """Sum hours × cost_rate for one schedule row, caching rates per staff id."""
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
            info = calculate_hourly_rate(sid)
            rate_cache[sid] = ((info['cost_rate'], info['billing_rate'])
                               if isinstance(info, dict) else (0, 0))
        cr, br = rate_cache[sid]
        hours += hrs
        cost += hrs * cr
        billing += hrs * br
    return hours, cost, billing


def quote_schedule(schedule_rows, risk_kwargs=None):
    """Price a milestone schedule that is not (yet) in the database.

    schedule_rows: [{name, work_type, start, end, outsourcing, material,
                     staff: [{staff_id, hours}]}, ...]

    Same kernel and same row shape as price_project_plan(), so the scratch
    quote on /pricing and the project card cannot drift apart.
    """
    score, risk_coeff = calculate_risk_score(**(risk_kwargs or {}))
    target_margin = get_target_margin()
    usd_rate = get_current_usd_rate()

    rate_cache = {}
    rows = []
    cum_expense = cum_price = 0.0
    t_hours = t_labor = t_out = t_mat = t_price = 0.0
    for r in schedule_rows or []:
        hours, labor, _billing = _row_labor_cost(r.get('staff'), rate_cache)
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
            'id': None,
            'name': (r.get('name') or '').strip(),
            'hours': hours, 'labor': labor, 'outsourcing': out, 'material': mat,
            'expense': expense,
            'minimum': ladder['minimum'], 'suggested': ladder['target'],
            'premium': ladder['premium'], 'price': price,
            'is_overridden': _is_overridden(price, ladder['target']),
            'margin_pct': ((price - expense) / price * 100) if price > 0 else 0,
            'cum_expense': cum_expense, 'cum_price': cum_price,
        })

    totals = price_from_cost(t_labor, t_out, t_mat, risk_coeff, target_margin)
    t_expense = t_labor + t_out + t_mat
    return {
        'rows': rows,
        'risk_score': score, 'risk_coeff': risk_coeff,
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


def save_quote_to_project(project_id, schedule_rows, risk_kwargs=None, overwrite=False):
    """Persist a scratch quote as real milestones, staff rows and all.

    Unlike the old percentage split this keeps the per-milestone employee mix,
    so planned_cost stays derived from milestone_staff.
    Returns (created_count, error), error ∈ (None,'notfound','empty','exists','locked').
    """
    rows = [r for r in (schedule_rows or []) if (r.get('name') or '').strip()]
    if not rows:
        return 0, 'empty'
    conn = get_db()
    try:
        if not conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
            return 0, 'notfound'
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
            conn.commit()
    finally:
        conn.close()

    if risk_kwargs:
        save_project_risk(project_id, **risk_kwargs)

    quote = quote_schedule(rows, risk_kwargs)
    created = 0
    for i, (r, priced) in enumerate(zip(rows, quote['rows'])):
        mid = add_milestone(
            project_id, priced['name'],
            work_type=(r.get('work_type') or '').strip() or None,
            start_date=_month_first_day((r.get('start') or '').strip()),
            end_date=_month_last_day((r.get('end') or '').strip()
                                     or (r.get('start') or '').strip()),
            planned_outsourcing=priced['outsourcing'],
            planned_material=priced['material'],
            planned_revenue=priced['price'],
            sort_order=(i + 1) * 10,
        )
        if not mid:
            continue
        # planned_hours/planned_cost are derived from the employee rows
        set_milestone_staff(mid, [
            {'staff_id': sr.get('staff_id'),
             'est_hours': sr.get('hours') or sr.get('est_hours') or 0}
            for sr in (r.get('staff') or [])
        ])
        created += 1
    return created, None


def get_plan_baseline(project_id):
    """The frozen baseline and how far the live milestone plan has drifted from it."""
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
    # Drifted = frozen, has milestones, and the live plan no longer matches it
    b['is_stale'] = bool(
        b['is_frozen'] and (roll.get('count') or 0) > 0
        and (abs(b['d_expense']) > max(1.0, b['expense'] * 0.005)
             or abs(b['d_revenue']) > max(1.0, b['planned_revenue'] * 0.005))
    )
    return b
