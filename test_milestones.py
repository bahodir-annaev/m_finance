"""Standalone tests for the milestones / periodic plan feature.

No server needed:  python test_milestones.py

Runs against a throwaway temp database (models.base.DB_PATH is patched before
anything touches the DB), so the real mizan_finance.db is never modified.
"""
import os
import sys
import tempfile

import models.base as base

_TMP_DIR = tempfile.mkdtemp(prefix='mizan_ms_test_')
base.DB_PATH = os.path.join(_TMP_DIR, 'test_mizan.db')
# The admin user is seeded on first init_db with this password (random otherwise),
# so the Flask smoke test below can log in.
os.environ['MIZAN_ADMIN_PASSWORD'] = 'mizan2024'

from models import (  # noqa: E402 — must come after the DB_PATH patch
    init_db, get_db, calculate_project_cost,
    add_milestone, delete_milestone, set_milestone_status,
    allocate_plan_to_months, get_project_monthly_actuals, get_milestone_actuals,
    get_project_monthly_rollup, get_project_milestones,
    generate_milestones_from_pricing, assign_hours_to_milestone,
    suggest_phase_for_period, get_plan_overview, get_projects_on_course_summary,
    set_milestone_staff, get_milestone_staff, get_production_staff_rates,
    calculate_hourly_rate,
)

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('  OK   ' if cond else '  FAIL ') + name + ('' if cond else f'   {detail}'))


print('== Schema / migration ==')
init_db()
init_db()  # must be idempotent (migrations re-run silently)
check('init_db is idempotent', True)

conn = get_db()
ph_cols = [r[1] for r in conn.execute("PRAGMA table_info(project_phases)")]
check('project_phases extended',
      all(c in ph_cols for c in ('work_type', 'planned_outsourcing', 'planned_material',
                                 'completed_date', 'notes', 'updated_at')), str(ph_cols))
tx_cols = [r[1] for r in conn.execute("PRAGMA table_info(transactions)")]
check('transactions.phase_id exists', 'phase_id' in tx_cols)
wt_count = conn.execute("SELECT COUNT(*) FROM work_types").fetchone()[0]
check('work_types seeded', wt_count >= 8, f'got {wt_count}')

# Seed one production staff + salary + project + hours + transactions
conn.execute("INSERT INTO staff (name, role, department, staff_type, is_active)"
             " VALUES ('Test Prod', 'Architect', 'AR', 'production', 1)")
sid = conn.execute("SELECT id FROM staff WHERE name='Test Prod'").fetchone()['id']
conn.execute("INSERT INTO salary_history (staff_id, base_salary, premium, start_date)"
             " VALUES (?, 10000000, 0, '2026-01-01')", (sid,))
conn.execute("INSERT INTO projects (name, contract_amount, risk_coefficient)"
             " VALUES ('MS Test', 100000000, 1.15)")
pid = conn.execute("SELECT id FROM projects WHERE name='MS Test'").fetchone()['id']
conn.execute("INSERT INTO project_hours (project_id, staff_id, hours, period)"
             " VALUES (?, ?, 100, '2026-05')", (pid, sid))
conn.execute("INSERT INTO project_hours (project_id, staff_id, hours, period)"
             " VALUES (?, ?, 50, '2026-06')", (pid, sid))
conn.execute("INSERT INTO transactions (direction, tx_type, date, project_id, amount, paid)"
             " VALUES ('external', 'tushum', '2026-05-10', ?, 50000000, 30000000)", (pid,))
conn.execute("INSERT INTO transactions (direction, tx_type, date, project_id, amount, paid)"
             " VALUES ('external', 'outsourcing', '2026-06-05', ?, 8000000, 8000000)", (pid,))
conn.commit()
conn.close()

print('== Day-weighted allocation ==')
m = {'start_date': '2026-05-15', 'end_date': '2026-07-14', 'planned_revenue': 100,
     'planned_cost': 60, 'planned_outsourcing': 30, 'planned_material': 10,
     'planned_hours': 300}
alloc = allocate_plan_to_months(m)
check('covers exactly the overlapped months', sorted(alloc) == ['2026-05', '2026-06', '2026-07'])
check('income sums exactly', abs(sum(v['income'] for v in alloc.values()) - 100) < 1e-9)
check('expense sums exactly (cost+outs+mat)', abs(sum(v['expense'] for v in alloc.values()) - 100) < 1e-9)
check('hours sum exactly', abs(sum(v['hours'] for v in alloc.values()) - 300) < 1e-9)
check('weighted by days (full month > partial)', alloc['2026-06']['income'] > alloc['2026-05']['income'])
check('undated milestone allocates nothing',
      allocate_plan_to_months({'start_date': None, 'end_date': None, 'planned_revenue': 5}) == {})

print('== Monthly actuals reconcile with calculate_project_cost ==')
pc = calculate_project_cost(pid)
ma = get_project_monthly_actuals(pid)
check('sum(monthly income) == project income',
      abs(sum(b['income'] for b in ma.values()) - pc['income']) < 0.01)
check('sum(monthly expense) == project total_expense',
      abs(sum(b['expense'] for b in ma.values()) - pc['total_expense']) < 0.01)
check('sum(monthly hours) == project hours',
      abs(sum(b['hours'] for b in ma.values()) - pc['total_hours']) < 0.01)

print('== Milestone CRUD + attribution ==')
mid1 = add_milestone(pid, 'Eskiz', work_type='eskiz', start_date='2026-05-01',
                     end_date='2026-05-31', planned_revenue=40000000,
                     planned_cost=20000000, planned_hours=100)
mid2 = add_milestone(pid, 'AR', work_type='ar', start_date='2026-06-01',
                     end_date='2026-07-31', planned_revenue=60000000,
                     planned_cost=30000000, planned_outsourcing=8000000,
                     planned_hours=200)
check('milestones created', bool(mid1 and mid2))
mid_dup = add_milestone(pid, 'Eskiz 2', work_type='eskiz')
check('duplicate code auto-suffixed', mid_dup is not None)

conn = get_db()
conn.execute("UPDATE transactions SET phase_id=? WHERE tx_type='tushum' AND project_id=?", (mid1, pid))
conn.commit()
conn.close()
check('hours period assigned', assign_hours_to_milestone(pid, '2026-05', mid1) == 1)
check('foreign phase rejected', assign_hours_to_milestone(pid, '2026-06', 99999) == -1)

msa = get_milestone_actuals(pid)
check('income attributed to milestone', abs(msa.get(mid1, {}).get('income', 0) - 30000000) < 0.01)
check('unassigned bucket holds untagged outsourcing', abs(msa.get(None, {}).get('direct', 0) - 8000000) < 0.01)
check('suggest picks best-overlap milestone', suggest_phase_for_period(pid, '2026-06') == mid2)

print('== Enrichment, lateness, status workflow ==')
ms = get_project_milestones(pid)
check('enriched fields present', all(('earned_value' in x and 'is_late' in x and 'fact' in x) for x in ms))
check('past-due planned milestone is late', any(x['id'] == mid1 and x['is_late'] for x in ms))
set_milestone_status(mid1, 'done')
m1 = [x for x in get_project_milestones(pid) if x['id'] == mid1][0]
check('done: not late, 100%, stamped',
      (not m1['is_late']) and m1['completion'] == 100 and bool(m1['completed_date']))

print('== Monthly roll-up ==')
ru = get_project_monthly_rollup(pid)
check('rollup fact totals reconcile with project P&L',
      abs(ru['totals']['fact_income'] - pc['income']) < 0.01
      and abs(ru['totals']['fact_expense'] - pc['total_expense']) < 0.01)
check('status lights present', set(ru['status']) >= {'money', 'schedule', 'collections', 'headline'})
check('rollup covers plan+fact months',
      {'2026-05', '2026-06', '2026-07'} <= {r['period'] for r in ru['months']})
check('plan totals equal milestone sums',
      abs(ru['totals']['plan_income'] - 100000000) < 1e-6)

print('== Delete guard ==')
ok, used = delete_milestone(mid1)
check('delete blocked while transactions attached', (not ok) and used == 1)
conn = get_db()
conn.execute("UPDATE transactions SET phase_id=NULL WHERE phase_id=?", (mid1,))
conn.commit()
conn.close()
ok, _ = delete_milestone(mid1)
check('delete succeeds after detach', ok)
conn = get_db()
dangling = conn.execute("SELECT COUNT(*) FROM project_hours WHERE phase_id=?", (mid1,)).fetchone()[0]
conn.close()
check('hour links cleared on delete', dangling == 0)

print('== Milestone staff plan ==')
rates = get_production_staff_rates()
check('production staff rates listed', any(r['id'] == sid and r['cost_rate'] > 0 for r in rates))

ms_id = add_milestone(pid, 'Staff MS', work_type='kj')
totals = set_milestone_staff(ms_id, [{'staff_id': sid, 'est_hours': 80}])
live_rate = calculate_hourly_rate(sid)['cost_rate']
check('totals derived from rows',
      abs(totals['planned_hours'] - 80) < 1e-9
      and abs(totals['planned_cost'] - 80 * live_rate) < 0.01
      and totals['planned_billing'] > totals['planned_cost'])
srows = get_milestone_staff(ms_id)
check('child row written with snapshot rates',
      len(srows) == 1 and srows[0]['staff_id'] == sid
      and abs(srows[0]['cost_rate'] - live_rate) < 0.01
      and srows[0]['billing_rate'] > 0)
conn = get_db()
ph = conn.execute("SELECT planned_hours, planned_cost FROM project_phases WHERE id=?",
                  (ms_id,)).fetchone()
conn.close()
check('project_phases recomputed from rows',
      abs(ph['planned_hours'] - 80) < 1e-9
      and abs(ph['planned_cost'] - totals['planned_cost']) < 0.01)

set_milestone_staff(ms_id, [{'staff_id': sid, 'est_hours': 50}])
srows = get_milestone_staff(ms_id)
check('re-save replaces rows (no duplicates)',
      len(srows) == 1 and abs(srows[0]['est_hours'] - 50) < 1e-9)
check('blank/zero/bogus rows skipped',
      set_milestone_staff(ms_id, [{'staff_id': sid, 'est_hours': 50},
                                  {'staff_id': '', 'est_hours': 10},
                                  {'staff_id': sid + 999, 'est_hours': 5},
                                  {'staff_id': 'x', 'est_hours': 'y'}])['planned_hours'] == 50)

# Snapshot stability: raising the salary must not alter the saved plan
saved_cost = get_milestone_staff(ms_id)[0]['cost']
conn = get_db()
conn.execute("UPDATE salary_history SET base_salary=20000000 WHERE staff_id=? AND end_date IS NULL", (sid,))
conn.commit()
conn.close()
check('salary change moves the live rate', calculate_hourly_rate(sid)['cost_rate'] > live_rate)
conn = get_db()
ph = conn.execute("SELECT planned_cost FROM project_phases WHERE id=?", (ms_id,)).fetchone()
conn.close()
check('saved plan unaffected by salary change',
      abs(ph['planned_cost'] - saved_cost) < 0.01
      and abs(get_milestone_staff(ms_id)[0]['cost'] - saved_cost) < 0.01)
conn = get_db()
conn.execute("UPDATE salary_history SET base_salary=10000000 WHERE staff_id=? AND end_date IS NULL", (sid,))
conn.commit()
conn.close()

# Empty staff list keeps scalars on milestones that never had rows
scalar_id = add_milestone(pid, 'Scalar MS', planned_hours=42, planned_cost=7000)
set_milestone_staff(scalar_id, [])
conn = get_db()
ph = conn.execute("SELECT planned_hours, planned_cost FROM project_phases WHERE id=?",
                  (scalar_id,)).fetchone()
conn.close()
check('empty rows keep directly-written scalars',
      ph['planned_hours'] == 42 and ph['planned_cost'] == 7000)
delete_milestone(scalar_id)

# Clearing previously-set rows zeroes the derived plan
set_milestone_staff(ms_id, [])
conn = get_db()
ph = conn.execute("SELECT planned_hours, planned_cost FROM project_phases WHERE id=?",
                  (ms_id,)).fetchone()
conn.close()
check('clearing rows zeroes derived plan', ph['planned_hours'] == 0 and ph['planned_cost'] == 0)

set_milestone_staff(ms_id, [{'staff_id': sid, 'est_hours': 10}])
ms_list = get_project_milestones(pid)
check('listing enriched with staff rows',
      any(x['id'] == ms_id and len(x.get('staff') or []) == 1 for x in ms_list))
ok, _ = delete_milestone(ms_id)
conn = get_db()
orphans = conn.execute("SELECT COUNT(*) FROM milestone_staff WHERE phase_id=?", (ms_id,)).fetchone()[0]
conn.close()
check('delete cascades milestone_staff', ok and orphans == 0)

print('== Generation from pricing ==')
conn = get_db()
conn.execute("INSERT INTO projects (name) VALUES ('Gen Test')")
conn.commit()
gid = conn.execute("SELECT id FROM projects WHERE name='Gen Test'").fetchone()['id']
conn.close()
rows = [
    {'name': 'Eskiz', 'work_type': 'eskiz', 'start': '2026-08', 'end': '2026-09',
     'income_pct': 30, 'hours_pct': 40},
    {'name': 'AR', 'work_type': 'ar', 'start': '2026-09', 'end': '2026-11',
     'income_pct': 70, 'hours_pct': 60},
]
created, err = generate_milestones_from_pricing(
    gid, rows, total_income=90000000, total_hours=333, labor_cost=45000000,
    outsourcing=5000000, material=1000000)
check('two milestones generated', created == 2 and err is None, f'{created} {err}')
conn = get_db()
s = conn.execute(
    "SELECT SUM(planned_revenue) ri, SUM(planned_hours) rh, SUM(planned_cost) rc,"
    " SUM(planned_outsourcing) ro, SUM(planned_material) rm, MIN(start_date) sd, MAX(end_date) ed"
    " FROM project_phases WHERE project_id=?", (gid,)).fetchone()
conn.close()
check('generated totals equal frozen plan exactly',
      abs(s['ri'] - 90000000) < 1e-6 and abs(s['rh'] - 333) < 1e-6
      and abs(s['rc'] - 45000000) < 1e-6 and s['ro'] == 5000000 and s['rm'] == 1000000)
check('month strings became date bounds', s['sd'] == '2026-08-01' and s['ed'] == '2026-11-30')
c2, e2 = generate_milestones_from_pricing(gid, rows, 1, 1, 1)
check('regen without overwrite blocked', c2 == 0 and e2 == 'exists')
c3, e3 = generate_milestones_from_pricing(gid, rows, 90000000, 333, 45000000, overwrite=True)
check('regen with overwrite works', c3 == 2 and e3 is None)
c4, e4 = generate_milestones_from_pricing(
    gid, [{'name': 'x', 'income_pct': 50, 'hours_pct': 100}], 1, 1, 1, overwrite=True)
check('percent validation rejects bad sums', c4 == 0 and e4 == 'pct')

print('== Overview + dashboard summary ==')
ov = get_plan_overview()
check('overview lists the project', any(r['id'] == pid for r in ov['rows']))
oc = get_projects_on_course_summary()
check('summary counts present', all(k in oc for k in ('on', 'edge', 'off', 'tracked', 'late_projects')))

print('== NIZAM upsert preserves attribution ==')
conn = get_db()
conn.execute("UPDATE project_hours SET phase_id=? WHERE project_id=? AND period='2026-06'", (mid2, pid))
conn.commit()
conn.execute("""INSERT INTO project_hours (project_id, staff_id, hours, period)
                VALUES (?, ?, 75, '2026-06')
                ON CONFLICT(project_id, staff_id, period)
                DO UPDATE SET hours=excluded.hours""", (pid, sid))
conn.commit()
row = conn.execute("SELECT hours, phase_id FROM project_hours WHERE project_id=? AND period='2026-06'",
                   (pid,)).fetchone()
conn.close()
check('re-import updates hours but keeps phase_id', row['hours'] == 75 and row['phase_id'] == mid2)

print('== Flask smoke test (in-process client) ==')
from app import create_app  # noqa: E402
flask_app = create_app()
flask_app.config['TESTING'] = True
client = flask_app.test_client()
r = client.post('/login', data={'username': 'admin', 'password': 'mizan2024'},
                follow_redirects=True)
check('login as seeded admin', r.status_code == 200)
for path in ('/plan', f'/projects/{pid}', '/projects', '/pricing', '/external',
             '/internal', '/', '/reference/lookup-tables', '/budget'):
    r = client.get(path)
    check(f'GET {path} -> 200', r.status_code == 200, f'got {r.status_code}')
r = client.get('/projects/999999')
check('GET missing project -> 404', r.status_code == 404)
r = client.post('/api/milestones/add',
                data={'project_id': pid, 'name': 'API bosqich', 'work_type': 'kj',
                      'start_date': '2026-08-01', 'end_date': '2026-08-31',
                      'planned_revenue': '1000', 'planned_cost': '500',
                      'ms_staff_id': [str(sid)], 'ms_staff_hours': ['10']})
check('POST /api/milestones/add -> ok', r.status_code == 200 and r.get_json().get('status') == 'ok')
new_id = r.get_json().get('id')
check('add with staff arrays writes rows',
      len(get_milestone_staff(new_id)) == 1
      and abs(get_milestone_staff(new_id)[0]['est_hours'] - 10) < 1e-9)
r = client.post(f'/api/milestones/{new_id}/update',
                data={'name': 'API bosqich 2', 'status': 'planned',
                      'planned_revenue': '2000', 'sort_order': '100',
                      'ms_staff_id': [str(sid)], 'ms_staff_hours': ['25']})
res = r.get_json()
check('POST /api/milestones/<id>/update -> ok',
      r.status_code == 200 and res.get('status') == 'ok'
      and abs(res.get('planned_hours', 0) - 25) < 1e-9)
check('update replaced staff rows',
      abs(get_milestone_staff(new_id)[0]['est_hours'] - 25) < 1e-9)
r = client.post('/api/milestones/999999/update', data={'name': 'x'})
check('update missing milestone -> 404', r.status_code == 404)
r = client.post(f'/api/milestones/{new_id}/status', data={'status': 'in_progress'})
check('POST status transition -> ok', r.status_code == 200)
r = client.post('/api/update/project_phases/%d' % new_id, data={'completion_percent': '40'})
check('generic update on project_phases -> ok', r.status_code == 200 and r.get_json().get('status') == 'ok')
r = client.post(f'/api/milestones/{new_id}/delete')
check('POST milestone delete -> ok', r.status_code == 200)

print()
print(f'PASSED: {len(PASS)}   FAILED: {len(FAIL)}')
if FAIL:
    print('Failed checks:')
    for f in FAIL:
        print('  -', f)
sys.exit(1 if FAIL else 0)
