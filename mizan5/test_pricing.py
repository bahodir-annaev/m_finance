# -*- coding: utf-8 -*-
"""Pricing ladder, milestones and budget tests.

The pinned invariant is the ladder's LINEARITY: the sum of per-milestone target
prices must equal the target price of the summed costs, exactly. That identity
is why the per-milestone figures and the project totals can never disagree.

    python test_pricing.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DB_FILE = os.path.join(tempfile.gettempdir(), 'mizan5_test_pricing.db')
os.environ['MIZAN5_DB'] = DB_FILE
os.environ['MIZAN_ADMIN_PASSWORD'] = 'test'
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

from models.base import init_db, get_db                              # noqa: E402
from models.pricing import (                                         # noqa: E402
    calculate_risk_score, price_from_cost, price_project_plan,
    apply_suggested_prices, freeze_project_plan, get_plan_baseline,
    save_project_risk, set_milestone_staff, get_milestone_staff, quote_schedule,
)
from models.milestones import (                                      # noqa: E402
    add_milestone, rollup_milestone_plan, generate_milestone_schedule,
    get_project_milestones, set_milestone_status, delete_milestone,
    allocate_plan_to_months, STANDARD_SCHEDULE,
)
from models.projects import get_budget_overview, project_actuals      # noqa: E402
from models.documents import save_document, post_document            # noqa: E402

PASS, FAIL = 0, 0


def check(label, condition, detail=''):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f'  [ok]   {label}')
    else:
        FAIL += 1
        print(f'  [FAIL] {label}' + (f'  — {detail}' if detail else ''))


def close(a, b, tol=0.01):
    return abs((a or 0) - (b or 0)) <= tol


init_db()
conn = get_db()
CLIENT = conn.execute(
    "INSERT INTO counterparties (name, counterparty_type) VALUES ('Mijoz MChJ','client')"
).lastrowid
PROJECT = conn.execute(
    "INSERT INTO projects (name, counterparty_id, is_billable, estimated_total_hours)"
    " VALUES ('Biznes markaz', ?, 1, 1000)", (CLIENT,)).lastrowid
STAFF = {}
for name, base in (('Aziz', 9000000), ('Bekzod', 5000000)):
    sid = conn.execute(
        "INSERT INTO staff (name, role, department, staff_type) VALUES (?,?,?,'production')",
        (name, 'Arxitektor', 'Arxitektura')).lastrowid
    conn.execute("INSERT INTO salary_history (staff_id, base_salary, premium, start_date)"
                 " VALUES (?,?,0,'2026-01-01')", (sid, base))
    STAFF[name] = sid
conn.commit()
conn.close()

print('\n=== Risk scoring (4-factor matrix) ===')
score, coeff = calculate_risk_score(10, 'regular', 'simple', 'UZS')
check('lowest risk scores 1.0 and prices at 1.00', close(score, 1.0) and close(coeff, 1.0),
      f'{score}/{coeff}')
score, coeff = calculate_risk_score(3, 'government', 'high', 'EUR')
check('highest risk scores 3.0 and prices at 1.30', close(score, 3.0) and close(coeff, 1.3),
      f'{score}/{coeff}')
score, coeff = calculate_risk_score(6, 'new', 'medium', 'USD')
check('mid risk weights the four factors',
      close(score, 2 * 0.30 + 2 * 0.25 + 2 * 0.25 + 2 * 0.20), f'{score}')
check('deadline under 4 months scores high',
      calculate_risk_score(3, 'regular', 'simple', 'UZS')[0] >
      calculate_risk_score(10, 'regular', 'simple', 'UZS')[0])

print('\n=== The price ladder ===')
ladder = price_from_cost(1000000, 200000, 100000, risk_coeff=1.15, target_margin=0.50)
check('minimum = risked labor + pass-through costs',
      close(ladder['minimum'], 1000000 * 1.15 + 300000), str(ladder['minimum']))
check('target grosses the minimum up to the margin',
      close(ladder['target'], ladder['minimum'] / 0.5))
# The margin is achieved against the risk-loaded minimum (the walk-away
# price), not against raw cost. Measured against raw cost the realised margin
# is HIGHER, because the risk premium is pure buffer when the risk does not
# materialise — that gap is the point of pricing on risk.
check('target achieves the margin over the risk-loaded minimum',
      close((ladder['target'] - ladder['minimum']) / ladder['target'] * 100, 50, 0.001),
      f"{(ladder['target'] - ladder['minimum']) / ladder['target'] * 100:.2f}%")
check('realised margin over raw cost exceeds the target by the risk premium',
      (ladder['target'] - 1300000) / ladder['target'] * 100 > 50,
      f"{(ladder['target'] - 1300000) / ladder['target'] * 100:.1f}%")
check('premium is target + 20%', close(ladder['premium'], ladder['target'] * 1.2))
check('risk is never applied to cost, only to price',
      close(price_from_cost(1000000, 0, 0, 1.0, 0.5)['minimum'], 1000000))

print('\n=== PINNED INVARIANT: the ladder is linear ===')
parts = [(400000, 50000, 10000), (350000, 0, 25000), (250000, 120000, 0)]
per_row = sum(price_from_cost(*p, risk_coeff=1.21, target_margin=0.45)['target']
              for p in parts)
combined = price_from_cost(sum(p[0] for p in parts), sum(p[1] for p in parts),
                           sum(p[2] for p in parts),
                           risk_coeff=1.21, target_margin=0.45)['target']
check('sum(per-milestone target) == target(sum of costs)', close(per_row, combined, 0.001),
      f'{per_row:.4f} vs {combined:.4f}')
per_min = sum(price_from_cost(*p, risk_coeff=1.21, target_margin=0.45)['minimum']
              for p in parts)
comb_min = price_from_cost(sum(p[0] for p in parts), sum(p[1] for p in parts),
                           sum(p[2] for p in parts),
                           risk_coeff=1.21, target_margin=0.45)['minimum']
check('the same identity holds for the minimum price', close(per_min, comb_min, 0.001))

print('\n=== Milestones drive the plan ===')
save_project_risk(PROJECT, 6, 'new', 'medium', 'UZS')
m1 = add_milestone(PROJECT, 'Eskiz loyiha', work_type='eskiz',
                   start_date='2026-03-01', end_date='2026-04-30',
                   planned_outsourcing=200000)
m2 = add_milestone(PROJECT, 'Arxitektura yechimlari', work_type='ar',
                   start_date='2026-05-01', end_date='2026-08-31',
                   planned_material=150000)
set_milestone_staff(m1, [{'staff_id': STAFF['Aziz'], 'est_hours': 100}])
set_milestone_staff(m2, [{'staff_id': STAFF['Aziz'], 'est_hours': 150},
                         {'staff_id': STAFF['Bekzod'], 'est_hours': 200}])

roll = rollup_milestone_plan(PROJECT)
check('roll-up counts both milestones', roll['count'] == 2)
check('planned hours come from the employee rows',
      close(roll['planned_hours'], 450), str(roll['planned_hours']))
check('planned cost is derived, not typed', roll['planned_cost'] > 0)
check('milestone staff rows snapshot the rate',
      all(r['cost_rate'] > 0 for r in get_milestone_staff(m1)))

plan = price_project_plan(PROJECT)
check('the plan prices every milestone', len(plan['rows']) == 2)
check('project total equals the sum of milestone suggestions',
      close(sum(r['suggested'] for r in plan['rows']), plan['target_contract'], 0.01),
      f"{sum(r['suggested'] for r in plan['rows']):.2f} vs {plan['target_contract']:.2f}")
check('cumulative expense accumulates down the rows',
      close(plan['rows'][-1]['cum_expense'], plan['total_expense']))
check('outsourcing and material are passed through',
      close(plan['total_outsourcing'], 200000) and close(plan['total_material'], 150000))

print('\n=== Suggested prices and overrides ===')
changed = apply_suggested_prices(PROJECT)
check('suggested prices applied to both rows', changed == 2, str(changed))
plan = price_project_plan(PROJECT)
check('no row reads as overridden after applying',
      not plan['has_override'], str([r['is_overridden'] for r in plan['rows']]))
check('plan revenue now matches the target',
      close(plan['plan_revenue_total'], plan['target_contract'], 1.0))

conn = get_db()
conn.execute("UPDATE project_phases SET planned_revenue=? WHERE id=?", (99000000, m1))
conn.commit()
conn.close()
plan = price_project_plan(PROJECT)
check('a hand-typed price is flagged as an override', plan['has_override'])
changed = apply_suggested_prices(PROJECT)
check('applying prices leaves an override alone', changed == 0, str(changed))
changed = apply_suggested_prices(PROJECT, force=True)
check('force overwrites the override', changed == 1, str(changed))

print('\n=== Freeze and drift ===')
baseline = get_plan_baseline(PROJECT)
check('an unfrozen project reports no baseline', not baseline['is_frozen'])
result, err = freeze_project_plan(PROJECT)
check('freezing writes the baseline', err is None and result['count'] == 2)
baseline = get_plan_baseline(PROJECT)
check('baseline is frozen and not stale', baseline['is_frozen'] and not baseline['is_stale'])
check('frozen cost is risk-exclusive labor',
      close(baseline['planned_cost'], roll['planned_cost']))

set_milestone_staff(m1, [{'staff_id': STAFF['Aziz'], 'est_hours': 300}])
baseline = get_plan_baseline(PROJECT)
check('editing the live plan marks the baseline stale', baseline['is_stale'],
      f"d_expense={baseline['d_expense']:.0f}")
freeze_project_plan(PROJECT)
check('re-freezing clears the drift', not get_plan_baseline(PROJECT)['is_stale'])

conn = get_db()
empty_project = conn.execute(
    "INSERT INTO projects (name, is_billable) VALUES ('Reja yoq loyiha', 1)").lastrowid
conn.commit()
conn.close()
_, err = freeze_project_plan(empty_project)
check('freezing a project with no milestones is refused', err == 'empty', str(err))

print('\n=== Cancelled milestones leave the plan ===')
before = price_project_plan(PROJECT)['total_expense']
set_milestone_status(m2, 'cancelled')
after = price_project_plan(PROJECT)
check('a cancelled milestone is excluded from the price',
      after['total_expense'] < before and after['milestone_count'] == 1)
check('roll-up excludes it too', rollup_milestone_plan(PROJECT)['count'] == 1)
set_milestone_status(m2, 'planned')

print('\n=== Schedule generation ===')
conn = get_db()
p2 = conn.execute("INSERT INTO projects (name, is_billable) VALUES ('Turar-joy', 1)").lastrowid
conn.commit()
conn.close()
created, err = generate_milestone_schedule(p2, STANDARD_SCHEDULE)
check('skeleton mode creates the standard six stages', created == 6 and err is None)
skeleton = get_project_milestones(p2)
check('skeleton carries no plan figures',
      all((m['planned_cost'] or 0) == 0 for m in skeleton))

created, err = generate_milestone_schedule(p2, STANDARD_SCHEDULE, overwrite=False)
check('generating over an existing schedule is refused', err == 'exists')

conn = get_db()
p3 = conn.execute("INSERT INTO projects (name, is_billable) VALUES ('Maktab', 1)").lastrowid
conn.commit()
conn.close()
created, err = generate_milestone_schedule(
    p3, STANDARD_SCHEDULE,
    totals={'income': 100000000, 'hours': 1000, 'labor_cost': 40000000,
            'outsourcing': 5000000, 'material': 2000000})
check('percentage mode splits the totals', created == 6 and err is None)
roll3 = rollup_milestone_plan(p3)
check('split income sums exactly to the total',
      close(roll3['planned_revenue'], 100000000, 0.01), str(roll3['planned_revenue']))
check('split hours sum exactly to the total', close(roll3['planned_hours'], 1000, 0.01))
check('split labor cost sums exactly', close(roll3['planned_cost'], 40000000, 0.01))
check('outsourcing follows the income share, not the last row',
      close(roll3['planned_outsourcing'], 5000000, 0.01))

bad_pct = [dict(r, income_pct=50) for r in STANDARD_SCHEDULE]
conn = get_db()
p4 = conn.execute("INSERT INTO projects (name) VALUES ('Pct test')").lastrowid
conn.commit()
conn.close()
_, err = generate_milestone_schedule(p4, bad_pct, totals={'income': 100})
check('percentages that do not total 100 are refused', err == 'pct', str(err))

print('\n=== Day-weighted month allocation ===')
alloc = allocate_plan_to_months({'start_date': '2026-03-01', 'end_date': '2026-04-30',
                                 'planned_revenue': 6000000, 'planned_cost': 3000000,
                                 'planned_hours': 200})
check('allocation covers both months', sorted(alloc) == ['2026-03', '2026-04'])
check('allocated income sums exactly to the plan',
      close(sum(v['income'] for v in alloc.values()), 6000000, 0.001))
check('allocated hours sum exactly to the plan',
      close(sum(v['hours'] for v in alloc.values()), 200, 0.001))
check('an undated milestone allocates nothing',
      allocate_plan_to_months({'planned_revenue': 100}) == {})

print('\n=== Budget: plan vs actual from the ledger ===')
inv = save_document(
    {'doc_type': 'sales_invoice', 'date': '2026-05-10', 'counterparty_id': CLIENT,
     'project_id': PROJECT, 'description': 'Bosqich 1'},
    lines=[{'description': 'Loyiha ishlari', 'amount': 50000000, 'vat_rate': 0,
            'vat_amount': 0, 'project_id': PROJECT, 'phase_id': m1}])
post_document(inv)
actuals = project_actuals(PROJECT)
check('invoiced revenue reaches the project', close(actuals['invoiced'], 50000000),
      str(actuals['invoiced']))
check('nothing received until a payment is allocated', close(actuals['received'], 0))
check('outstanding equals invoiced', close(actuals['outstanding'], 50000000))

pay = save_document({'doc_type': 'cash_in', 'date': '2026-05-25',
                     'counterparty_id': CLIENT, 'total': 20000000},
                    allocations=[{'invoice_doc_id': inv, 'amount': 20000000}])
post_document(pay)
actuals = project_actuals(PROJECT)
check('allocated cash counts as received', close(actuals['received'], 20000000))

overview = get_budget_overview()
row = next(r for r in overview['rows'] if r['id'] == PROJECT)
check('a frozen project appears with its plan', row['has_plan'])
check('unplanned projects are excluded from plan totals',
      overview['unplanned_count'] >= 1
      and close(overview['totals']['p_cost'],
                sum(r['p_cost'] for r in overview['rows'] if r['has_plan'])))
noplan = next(r for r in overview['rows'] if not r['has_plan'])
check('an unplanned project reports noplan, not on-budget',
      noplan['status'] == 'noplan')

print('\n=== Scratch quote uses the same kernel ===')
quote = quote_schedule(
    [{'name': 'Bosqich A', 'staff': [{'staff_id': STAFF['Aziz'], 'hours': 100}],
      'outsourcing': 100000},
     {'name': 'Bosqich B', 'staff': [{'staff_id': STAFF['Bekzod'], 'hours': 50}]}],
    risk_kwargs={'deadline_months': 6, 'client_type': 'new',
                 'complexity': 'medium', 'currency': 'UZS'})
check('the quote prices every row', len(quote['rows']) == 2)
check('quote totals equal the sum of its rows',
      close(sum(r['suggested'] for r in quote['rows']), quote['target_contract'], 0.01))
check('quote hours come from the employee rows', close(quote['total_hours'], 150))

print(f'\n{"=" * 46}\n  passed: {PASS}   failed: {FAIL}\n{"=" * 46}')
sys.exit(1 if FAIL else 0)
