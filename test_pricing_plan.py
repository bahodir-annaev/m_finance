"""Standalone tests for the unified Plan & Price feature.

No server needed:  python test_pricing_plan.py

Runs against a throwaway temp database (models.base.DB_PATH is patched before
anything touches the DB), so the real mizan_finance.db is never modified.
"""
import os
import tempfile

import models.base as base

_TMP_DIR = tempfile.mkdtemp(prefix='mizan_pp_test_')
base.DB_PATH = os.path.join(_TMP_DIR, 'test_mizan.db')
# The admin user is seeded on first init_db with this password (random otherwise),
# so the Flask smoke test below can log in.
os.environ['MIZAN_ADMIN_PASSWORD'] = 'mizan2024'

from models import (  # noqa: E402 — must come after the DB_PATH patch
    init_db, get_db, price_from_cost, pricing_estimate, calculate_risk_score,
    price_project_plan, get_project_risk, save_project_risk,
    apply_suggested_prices, freeze_project_plan, get_plan_baseline,
    generate_milestone_schedule, generate_milestones_from_pricing,
    rollup_milestone_plan, quote_schedule, save_quote_to_project,
    add_milestone, set_milestone_staff, get_milestone_staff,
    get_project_milestones, get_target_margin, STANDARD_SCHEDULE,
)

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('  OK   ' if cond else '  FAIL ') + name + ('' if cond else f'   {detail}'))


def close(a, b, tol=1e-6):
    return abs((a or 0) - (b or 0)) < tol


init_db()

# ── Seed: two production staff on different salaries, one project ──
conn = get_db()
conn.execute("INSERT INTO staff (name, role, department, staff_type, is_active)"
             " VALUES ('PP Arch', 'Architect', 'AR', 'production', 1)")
conn.execute("INSERT INTO staff (name, role, department, staff_type, is_active)"
             " VALUES ('PP Vis', 'Visualizer', 'AR', 'production', 1)")
sid_a = conn.execute("SELECT id FROM staff WHERE name='PP Arch'").fetchone()['id']
sid_v = conn.execute("SELECT id FROM staff WHERE name='PP Vis'").fetchone()['id']
conn.execute("INSERT INTO salary_history (staff_id, base_salary, premium, start_date)"
             " VALUES (?, 12000000, 0, '2026-01-01')", (sid_a,))
conn.execute("INSERT INTO salary_history (staff_id, base_salary, premium, start_date)"
             " VALUES (?, 7000000, 0, '2026-01-01')", (sid_v,))
conn.execute("INSERT INTO projects (name, contract_amount, risk_coefficient) "
             "VALUES ('PP Test', 0, 1.15)")
pid = conn.execute("SELECT id FROM projects WHERE name='PP Test'").fetchone()['id']
conn.execute("INSERT INTO projects (name, contract_amount) VALUES ('PP Empty', 0)")
pid_empty = conn.execute("SELECT id FROM projects WHERE name='PP Empty'").fetchone()['id']
conn.commit()
conn.close()

print('== Schema ==')
conn = get_db()
pcols = [r[1] for r in conn.execute("PRAGMA table_info(projects)")]
conn.close()
check('projects has the risk_* inputs',
      all(c in pcols for c in ('risk_deadline_months', 'risk_client_type',
                               'risk_complexity', 'risk_currency')), str(pcols))

print('== price_from_cost: the single ladder ==')
L = price_from_cost(100_000_000, 20_000_000, 5_000_000, 1.15, 0.50)
check('risked cost = labor x risk', close(L['risked_cost'], 115_000_000))
check('minimum = risked + outsourcing + material', close(L['minimum'], 140_000_000))
check('target grosses up to the margin', close(L['target'], 280_000_000))
check('target really yields the margin',
      close((L['target'] - L['minimum']) / L['target'], 0.50, 1e-9))
check('premium = target + 20%', close(L['premium'], 336_000_000))
check('margin >= 1 degrades to x2', close(price_from_cost(100, 0, 0, 1.0, 1.5)['target'], 200))
check('zero cost prices at zero', close(price_from_cost(0, 0, 0, 1.15, 0.5)['target'], 0))
check('None inputs are treated as zero', close(price_from_cost(None, None, None)['minimum'], 0))

print('== Linearity: intermediate figures cannot disagree with the total ==')
parts = [(60_000_000, 10_000_000, 2_000_000),
         (40_000_000, 10_000_000, 3_000_000),
         (25_000_000, 0, 1_500_000)]
sum_target = sum(price_from_cost(*p, 1.23, 0.4)['target'] for p in parts)
whole = price_from_cost(sum(p[0] for p in parts), sum(p[1] for p in parts),
                        sum(p[2] for p in parts), 1.23, 0.4)
check('sum(per-milestone target) == target(sum of costs)',
      close(sum_target, whole['target'], 1e-6), f'{sum_target} vs {whole["target"]}')
sum_min = sum(price_from_cost(*p, 1.23, 0.4)['minimum'] for p in parts)
check('the same holds for minimum', close(sum_min, whole['minimum'], 1e-6))

print('== calculate_risk_score unchanged ==')
# Level 2 on all four factors means USD — UZS is the level-1 currency.
check('all factors at level 2 -> 2.0 / 1.15',
      calculate_risk_score(6, 'new', 'medium', 'USD') == (2.0, 1.15))
check('medium everything in UZS -> 1.8 / 1.12',
      calculate_risk_score(6, 'new', 'medium', 'UZS') == (1.8, 1.12))
check('lowest risk is 1.0 / 1.0', calculate_risk_score(12, 'regular', 'simple', 'UZS') == (1.0, 1.0))
check('highest risk is 3.0 / 1.3', calculate_risk_score(1, 'government', 'high', 'EUR') == (3.0, 1.3))

print('== pricing_estimate still agrees with the kernel ==')
est = pricing_estimate([{'staff_id': sid_a, 'planned_hours': 100},
                        {'staff_id': sid_v, 'planned_hours': 50}],
                       {'deadline_months': 6}, outsourcing=5_000_000, material=1_000_000)
man = price_from_cost(est['total_cost'], 5_000_000, 1_000_000,
                      est['risk_coeff'], est['target_margin'])
check('estimate minimum matches the kernel', close(est['minimum_contract'], man['minimum']))
check('estimate target matches the kernel', close(est['target_contract'], man['target']))
check('estimate premium matches the kernel', close(est['premium_contract'], man['premium']))
check('mizan_cost is the risk-loaded labor', close(est['mizan_cost'], man['risked_cost']))
check('both staff rolled up', len(est['staff_details']) == 2 and est['total_hours'] == 150)
check('unknown staff are skipped, not fatal',
      pricing_estimate([{'staff_id': 999999, 'planned_hours': 10}])['total_hours'] == 0)

print('== save_project_risk round-trips ==')
saved = save_project_risk(pid, deadline_months=3, client_type='government',
                          complexity='high', currency='USD')
r = get_project_risk(pid)
check('inputs persisted',
      r['deadline_months'] == 3 and r['client_type'] == 'government'
      and r['complexity'] == 'high' and r['currency'] == 'USD', str(r))
check('risk_score is written (was a dead column)', (r['risk_score'] or 0) > 0, str(r.get('risk_score')))
check('coefficient matches the score', close(r['risk_coeff'], saved['risk_coeff']))
check('deadline bucket maps to the UI option', r['deadline_bucket'] == 3)
conn = get_db()
stored = conn.execute("SELECT risk_score, risk_coefficient FROM projects WHERE id=?", (pid,)).fetchone()
conn.close()
check('score/coefficient land in the DB',
      close(stored['risk_score'], r['risk_score']) and close(stored['risk_coefficient'], r['risk_coeff']))
check('bad input falls back rather than raising',
      save_project_risk(pid, deadline_months='abc', client_type='???',
                        complexity='???', currency='')['client_type'] == 'new')
check('unknown project returns None', save_project_risk(999999) is None)
save_project_risk(pid, deadline_months=6, client_type='new', complexity='medium', currency='UZS')

print('== Skeleton generation (totals=None) ==')
created, err = generate_milestone_schedule(pid, [dict(x) for x in STANDARD_SCHEDULE], totals=None)
check('skeleton needs no percentages', err is None and created == 6, f'{created}/{err}')
ms = get_project_milestones(pid)
check('skeleton rows carry name + work type',
      all(m['name'] and m['work_type'] for m in ms))
check('skeleton plan figures start at zero',
      all((m['planned_cost'] or 0) == 0 and (m['planned_revenue'] or 0) == 0 for m in ms))
check('a second run refuses without overwrite',
      generate_milestone_schedule(pid, [dict(x) for x in STANDARD_SCHEDULE])[1] == 'exists')
check('empty rows are rejected', generate_milestone_schedule(pid, [], totals=None)[1] == 'empty')

print('== Bottom-up: staff hours drive planned_cost ==')
m_ar = [m for m in ms if m['work_type'] == 'ar'][0]
m_esk = [m for m in ms if m['work_type'] == 'eskiz'][0]
tot_ar = set_milestone_staff(m_ar['id'], [{'staff_id': sid_a, 'est_hours': 120},
                                          {'staff_id': sid_v, 'est_hours': 40}])
tot_esk = set_milestone_staff(m_esk['id'], [{'staff_id': sid_a, 'est_hours': 60}])
check('hours sum from the employee rows', close(tot_ar['planned_hours'], 160))
check('each milestone keeps its own staff mix',
      len(get_milestone_staff(m_ar['id'])) == 2 and len(get_milestone_staff(m_esk['id'])) == 1)
check('cost follows the real mix, not a percentage',
      tot_ar['planned_cost'] > tot_esk['planned_cost'] > 0)

print('== price_project_plan: intermediate + total ==')
conn = get_db()
conn.execute("UPDATE project_phases SET planned_outsourcing=3000000 WHERE id=?", (m_ar['id'],))
conn.execute("UPDATE project_phases SET planned_material=500000 WHERE id=?", (m_esk['id'],))
conn.commit()
conn.close()
plan = price_project_plan(pid)
check('one row per milestone', len(plan['rows']) == 6)
rows = plan['rows']
check('row expense = labor + outsourcing + material',
      all(close(r['expense'], r['labor'] + r['outsourcing'] + r['material']) for r in rows))
check('cumulative expense is monotonic',
      all(rows[i]['cum_expense'] >= rows[i - 1]['cum_expense'] for i in range(1, len(rows))))
check('last cumulative expense == project total expense',
      close(rows[-1]['cum_expense'], plan['total_expense']))
check('sum of row expense == total expense',
      close(sum(r['expense'] for r in rows), plan['total_expense']))
check('sum of suggested prices == project target',
      close(sum(r['suggested'] for r in rows), plan['target_contract'], 1e-6),
      f"{sum(r['suggested'] for r in rows)} vs {plan['target_contract']}")
check('totals use the same ladder',
      close(plan['target_contract'],
            price_from_cost(plan['total_labor'], plan['total_outsourcing'],
                            plan['total_material'], plan['risk_coeff'],
                            plan['target_margin'])['target']))
check('outsourcing/material are not risk-loaded',
      close(plan['minimum_contract'],
            plan['mizan_cost'] + plan['total_outsourcing'] + plan['total_material']))
check('unknown project returns None', price_project_plan(999999) is None)
check('a project with no milestones prices at zero',
      close(price_project_plan(pid_empty)['target_contract'], 0))

print('== Cancelled milestones are excluded from plan and price alike ==')
conn = get_db()
conn.execute("UPDATE project_phases SET status='cancelled' WHERE id=?", (m_esk['id'],))
conn.commit()
conn.close()
plan_c = price_project_plan(pid)
roll_c = rollup_milestone_plan(pid)
check('cancelled row dropped from the priced rows',
      m_esk['id'] not in [r['id'] for r in plan_c['rows']])
check('card labor total matches the freeze rollup',
      close(plan_c['total_labor'], roll_c['planned_cost'], 1e-6),
      f"{plan_c['total_labor']} vs {roll_c['planned_cost']}")
check('cancelled cost is really excluded', plan_c['total_labor'] < plan['total_labor'])
conn = get_db()
conn.execute("UPDATE project_phases SET status='planned' WHERE id=?", (m_esk['id'],))
conn.commit()
conn.close()
check('restoring the milestone restores the total',
      close(price_project_plan(pid)['total_labor'], plan['total_labor'], 1e-6))

print('== Risk override reprices without saving ==')
hi = price_project_plan(pid, risk_kwargs={'deadline_months': 1, 'client_type': 'government',
                                          'complexity': 'high', 'currency': 'EUR'})
check('higher risk raises the price', hi['target_contract'] > plan['target_contract'])
check('preview does not persist',
      close(get_project_risk(pid)['risk_coeff'], calculate_risk_score(6, 'new', 'medium', 'UZS')[1]))
check('labor cost is untouched by risk', close(hi['total_labor'], plan['total_labor']))

print('== Suggested prices and per-row override ==')
changed = apply_suggested_prices(pid)
plan2 = price_project_plan(pid)
check('prices applied to every priced row', changed >= 2, f'changed={changed}')
check('no row reads as overridden afterwards', not plan2['has_override'])
check('plan revenue now equals the target',
      close(plan2['plan_revenue_total'], plan2['target_contract'], 1.0))
check('revenue gap closes', abs(plan2['revenue_gap']) < 1.0)
conn = get_db()
conn.execute("UPDATE project_phases SET planned_revenue=planned_revenue+9000000 WHERE id=?",
             (m_ar['id'],))
conn.commit()
conn.close()
plan3 = price_project_plan(pid)
over = [r for r in plan3['rows'] if r['id'] == m_ar['id']][0]
check('an edited price is flagged as overridden', over['is_overridden'])
check('other rows stay auto',
      sum(1 for r in plan3['rows'] if r['is_overridden']) == 1)
check('gap moves by exactly the override delta',
      close(plan3['revenue_gap'] - plan2['revenue_gap'], 9000000, 1.0))
apply_suggested_prices(pid)
check('apply leaves an overridden row alone',
      price_project_plan(pid)['rows'][
          [r['id'] for r in plan3['rows']].index(m_ar['id'])]['is_overridden'])
apply_suggested_prices(pid, force=True)
check('force overwrites the override', not price_project_plan(pid)['has_override'])

print('== Freeze: milestone plan becomes the /budget baseline ==')
roll = rollup_milestone_plan(pid)
result, err = freeze_project_plan(pid)
check('freeze succeeds', err is None and result is not None, str(err))
conn = get_db()
p = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
conn.close()
check('planned_hours == sum of milestone hours', close(p['planned_hours'], roll['planned_hours']))
check('planned_cost == sum of milestone cost', close(p['planned_cost'], roll['planned_cost']))
check('planned_revenue == sum of milestone revenue', close(p['planned_revenue'], roll['planned_revenue']))
check('planned_outsourcing rolls up', close(p['planned_outsourcing'], roll['planned_outsourcing']))
check('planned_material rolls up', close(p['planned_material'], roll['planned_material']))
check('plan_frozen_date is stamped', bool(p['plan_frozen_date']))
check('planned_cost stays risk-EXCLUSIVE',
      close(p['planned_cost'], plan['total_labor']) and p['planned_cost'] < plan['mizan_cost'])
check('freeze on a project with no milestones is refused',
      freeze_project_plan(pid_empty)[1] == 'empty')
check('freeze on an unknown project reports notfound',
      freeze_project_plan(999999)[1] == 'notfound')

print('== Baseline drift detection ==')
b = get_plan_baseline(pid)
check('freshly frozen baseline is not stale', b['is_frozen'] and not b['is_stale'], str(b['d_expense']))
set_milestone_staff(m_esk['id'], [{'staff_id': sid_a, 'est_hours': 200}])
b2 = get_plan_baseline(pid)
check('editing a milestone marks the baseline stale', b2['is_stale'])
check('baseline itself did not move', close(b2['expense'], b['expense']))
check('live expense reflects the edit', b2['live_expense'] > b2['expense'])

print('== A signed contract is never overwritten by a quote ==')
conn = get_db()
conn.execute("UPDATE projects SET contract_amount=777000000 WHERE id=?", (pid,))
conn.commit()
conn.close()
freeze_project_plan(pid)
conn = get_db()
ca = conn.execute("SELECT contract_amount FROM projects WHERE id=?", (pid,)).fetchone()[0]
conn.close()
check('contract_amount survives a re-freeze', close(ca, 777000000))

print('== Percentage generation still sums exactly, spreads direct costs ==')
conn = get_db()
conn.execute("INSERT INTO projects (name) VALUES ('PP Pct')")
pid_pct = conn.execute("SELECT id FROM projects WHERE name='PP Pct'").fetchone()['id']
conn.commit()
conn.close()
created, err = generate_milestones_from_pricing(
    pid_pct, [dict(x) for x in STANDARD_SCHEDULE],
    total_income=200_000_000, total_hours=1000, labor_cost=80_000_000,
    outsourcing=10_000_000, material=4_000_000)
check('wrapper still works for the old call shape', err is None and created == 6, f'{created}/{err}')
r_pct = rollup_milestone_plan(pid_pct)
check('income sums exactly', close(r_pct['planned_revenue'], 200_000_000, 1e-6))
check('hours sum exactly', close(r_pct['planned_hours'], 1000, 1e-6))
check('labor cost sums exactly', close(r_pct['planned_cost'], 80_000_000, 1e-6))
check('outsourcing sums exactly', close(r_pct['planned_outsourcing'], 10_000_000, 1e-6))
check('material sums exactly', close(r_pct['planned_material'], 4_000_000, 1e-6))
conn = get_db()
spread = conn.execute(
    "SELECT COUNT(*) FROM project_phases WHERE project_id=? AND planned_outsourcing > 0",
    (pid_pct,)).fetchone()[0]
conn.close()
check('direct costs spread across milestones, not dumped on the last',
      spread == 6, f'{spread} rows carry outsourcing')
check('percentages that do not total 100 are rejected',
      generate_milestones_from_pricing(pid_pct, [{'name': 'x', 'income_pct': 50, 'hours_pct': 50}],
                                       1, 1, 1, overwrite=True)[1] == 'pct')

print('== Scratch quote (no project, no writes) ==')
q = quote_schedule([
    {'name': 'Eskiz', 'work_type': 'eskiz', 'outsourcing': 0, 'material': 0,
     'staff': [{'staff_id': sid_a, 'hours': 60}]},
    {'name': 'AR', 'work_type': 'ar', 'outsourcing': 3_000_000, 'material': 0,
     'staff': [{'staff_id': sid_a, 'hours': 120}, {'staff_id': sid_v, 'hours': 40}]},
], {'deadline_months': 6, 'client_type': 'new', 'complexity': 'medium', 'currency': 'UZS'})
check('quote returns a row per schedule row', len(q['rows']) == 2)
check('quote hours sum from the staff rows', close(q['total_hours'], 220))
check('quote cumulative expense ends at the total',
      close(q['rows'][-1]['cum_expense'], q['total_expense']))
check('quote per-row prices sum to the target',
      close(sum(r['suggested'] for r in q['rows']), q['target_contract'], 1e-6))
check('quote uses the same ladder as the project pricer',
      close(q['target_contract'],
            price_from_cost(q['total_labor'], q['total_outsourcing'], q['total_material'],
                            q['risk_coeff'], get_target_margin())['target']))
conn = get_db()
before = conn.execute("SELECT COUNT(*) FROM project_phases").fetchone()[0]
conn.close()
quote_schedule([{'name': 'x', 'staff': [{'staff_id': sid_a, 'hours': 5}]}])
conn = get_db()
after = conn.execute("SELECT COUNT(*) FROM project_phases").fetchone()[0]
conn.close()
check('quoting writes nothing to the database', before == after)

print('== Saving a scratch quote keeps the per-milestone staff mix ==')
conn = get_db()
conn.execute("INSERT INTO projects (name) VALUES ('PP Quote')")
pid_q = conn.execute("SELECT id FROM projects WHERE name='PP Quote'").fetchone()['id']
conn.commit()
conn.close()
created, err = save_quote_to_project(pid_q, [
    {'name': 'Eskiz', 'work_type': 'eskiz', 'start': '2026-03', 'end': '2026-04',
     'staff': [{'staff_id': sid_a, 'hours': 60}]},
    {'name': 'AR', 'work_type': 'ar', 'start': '2026-04', 'end': '2026-07', 'outsourcing': 3_000_000,
     'staff': [{'staff_id': sid_a, 'hours': 120}, {'staff_id': sid_v, 'hours': 40}]},
], risk_kwargs={'deadline_months': 6, 'client_type': 'new',
                'complexity': 'medium', 'currency': 'UZS'})
check('quote saved as milestones', err is None and created == 2, f'{created}/{err}')
saved_ms = get_project_milestones(pid_q)
check('employee rows persisted per milestone',
      len(saved_ms[0]['staff']) == 1 and len(saved_ms[1]['staff']) == 2)
check('planned_cost derived from the rows, not a percentage',
      all((m['planned_cost'] or 0) > 0 for m in saved_ms))
check('dates converted from YYYY-MM to first/last day',
      saved_ms[0]['start_date'] == '2026-03-01' and saved_ms[0]['end_date'] == '2026-04-30')
check('risk saved alongside', get_project_risk(pid_q)['deadline_months'] == 6)
check('saved plan matches the quote',
      close(rollup_milestone_plan(pid_q)['planned_cost'],
            quote_schedule([
                {'name': 'Eskiz', 'staff': [{'staff_id': sid_a, 'hours': 60}]},
                {'name': 'AR', 'outsourcing': 3_000_000,
                 'staff': [{'staff_id': sid_a, 'hours': 120}, {'staff_id': sid_v, 'hours': 40}]},
            ])['total_labor'], 1.0))
check('re-saving refuses without overwrite',
      save_quote_to_project(pid_q, [{'name': 'x', 'staff': []}])[1] == 'exists')
check('unknown project reports notfound',
      save_quote_to_project(999999, [{'name': 'x'}])[1] == 'notfound')

print('== Flask smoke test (in-process client) ==')
from app import create_app  # noqa: E402
flask_app = create_app()
flask_app.config['TESTING'] = True
client = flask_app.test_client()
r = client.post('/login', data={'username': 'admin', 'password': 'mizan2024'},
                follow_redirects=True)
check('login as seeded admin', r.status_code == 200)
for path in ('/pricing', f'/projects/{pid}', f'/projects/{pid_empty}', '/budget'):
    r = client.get(path)
    check(f'GET {path} -> 200', r.status_code == 200, f'got {r.status_code}')
check('GET /plan redirects to the merged /budget',
      client.get('/plan').status_code == 301)

r = client.post(f'/api/projects/{pid}/risk',
                data={'deadline_months': '3', 'client_type': 'government',
                      'complexity': 'high', 'currency': 'USD'})
check('POST /risk -> ok', r.status_code == 200 and r.get_json().get('status') == 'ok')
check('/risk returns the repriced plan', r.get_json().get('plan', {}).get('rows') is not None)
check('/risk persisted', get_project_risk(pid)['client_type'] == 'government')
r = client.post('/api/projects/999999/risk', data={'deadline_months': '6'})
check('POST /risk on a missing project -> 404', r.status_code == 404)

r = client.post(f'/api/projects/{pid}/milestones/apply-prices')
check('POST /apply-prices -> ok', r.status_code == 200 and r.get_json().get('status') == 'ok')
r = client.post(f'/api/projects/{pid}/plan/freeze')
check('POST /plan/freeze -> ok', r.status_code == 200 and r.get_json().get('status') == 'ok')
r = client.post(f'/api/projects/{pid_empty}/plan/freeze')
check('freeze without milestones -> 400 empty',
      r.status_code == 400 and r.get_json().get('error') == 'empty')
r = client.post(f'/api/projects/{pid_empty}/milestones/schedule')
check('POST /milestones/schedule -> ok',
      r.status_code == 200 and r.get_json().get('created') == 6)
r = client.post(f'/api/projects/{pid_empty}/milestones/schedule')
check('schedule twice -> 400 exists',
      r.status_code == 400 and r.get_json().get('error') == 'exists')

r = client.post('/api/pricing/quote', json={
    'rows': [{'name': 'A', 'staff': [{'staff_id': sid_a, 'hours': 10}]}],
    'risk': {'deadline_months': 6}})
check('POST /api/pricing/quote -> ok',
      r.status_code == 200 and r.get_json()['quote']['total_hours'] == 10)
r = client.post('/api/pricing/quote', json={'rows': []})
check('empty quote is not an error', r.status_code == 200)
r = client.post('/api/pricing/quote/save', json={'rows': [{'name': 'A'}]})
check('quote save without project_id -> 400', r.status_code == 400)

print('== Viewer cannot write ==')
conn = get_db()
from werkzeug.security import generate_password_hash  # noqa: E402
conn.execute("INSERT INTO users (username, password_hash, role, is_active)"
             " VALUES ('pp_viewer', ?, 'viewer', 1)",
             (generate_password_hash('viewer123'),))
conn.commit()
conn.close()
vc = flask_app.test_client()
vc.post('/login', data={'username': 'pp_viewer', 'password': 'viewer123'}, follow_redirects=True)
check('viewer can read the project page', vc.get(f'/projects/{pid}').status_code == 200)
for path in (f'/api/projects/{pid}/risk', f'/api/projects/{pid}/plan/freeze',
             f'/api/projects/{pid}/milestones/apply-prices',
             f'/api/projects/{pid}/milestones/schedule'):
    code = vc.post(path).status_code
    check(f'viewer POST {path} blocked', code in (302, 401, 403), f'got {code}')
code = vc.post('/api/pricing/quote', json={'rows': []}).status_code
check('viewer POST /api/pricing/quote blocked', code in (302, 401, 403), f'got {code}')

print()
print(f'PASSED: {len(PASS)}   FAILED: {len(FAIL)}')
raise SystemExit(1 if FAIL else 0)
