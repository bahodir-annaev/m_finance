# -*- coding: utf-8 -*-
"""Man-hour cost engine tests — the app's primary calculation.

The headline case is a GOLDEN PARITY test: given identical inputs and the
v4-compatible settings (hours allocation base, overhead from the static table),
the v5 engine must reproduce v4's rates to the cent. The v4 engine runs in a
subprocess because both codebases have a package called `models`.

    python test_rates.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
V4_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

TMP = tempfile.gettempdir()
DB_FILE = os.path.join(TMP, 'mizan5_test_rates.db')
os.environ['MIZAN5_DB'] = DB_FILE
os.environ['MIZAN_ADMIN_PASSWORD'] = 'test'
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

from models.base import init_db, get_db, set_setting                # noqa: E402
from models import staff as rates                                   # noqa: E402
from models.documents import save_document, post_document           # noqa: E402
from models.ledger import account_id_for                            # noqa: E402
from models.payroll import build_payroll_rows, save_payroll, payroll_totals  # noqa: E402

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


# ── Shared fixture — deliberately uneven salaries and hours so the two
#    allocation bases produce visibly different answers ─────────────────────
FIXTURE = {
    'salary_start': '2026-01-01',
    'settings': {
        'tax_rate': 0.12, 'social_rate': 0.12, 'billing_multiplier': 2.0,
        'holidays_per_year': 14, 'avg_leave_days': 20,
    },
    'staff': [
        {'name': 'Aziz', 'role': 'Arxitektor', 'department': 'Arxitektura',
         'staff_type': 'production', 'base_salary': 8000000, 'premium': 1000000},
        {'name': 'Bekzod', 'role': 'BIM muhandis', 'department': 'Arxitektura',
         'staff_type': 'production', 'base_salary': 5000000, 'premium': 0},
        {'name': 'Dilnoza', 'role': 'Loyiha rahbari', 'department': 'Boshqaruv',
         'staff_type': 'production', 'base_salary': 12000000, 'premium': 2000000},
        {'name': 'Nodira', 'role': 'Buxgalter', 'department': 'Moliya',
         'staff_type': 'admin', 'base_salary': 7000000, 'premium': 500000},
    ],
    'personal_equipment': [
        {'name': 'Laptop', 'staff': 'Aziz', 'price': 15000000,
         'lifespan_months': 36, 'purchase_date': '2025-06-01'},
        {'name': 'Workstation', 'staff': 'Dilnoza', 'price': 24000000,
         'lifespan_months': 36, 'purchase_date': '2025-01-15'},
    ],
    'general_equipment': [
        {'name': 'Server', 'quantity': 1, 'price': 30000000,
         'lifespan_months': 60, 'purchase_date': '2025-03-01'},
        {'name': 'Printer', 'quantity': 2, 'price': 6000000,
         'lifespan_months': 36, 'purchase_date': '2025-09-01'},
    ],
    'licenses': [
        {'name': 'Revit', 'staff': 'Aziz', 'annual_cost': 12000000},
        {'name': '3ds Max', 'staff': 'Dilnoza', 'annual_cost': 9600000},
    ],
    'overhead': [
        {'name': 'Ofis ijarasi', 'monthly_amount': 25000000},
        {'name': 'Kommunal', 'monthly_amount': 5000000},
    ],
    'projects': [{'name': 'Turar-joy majmuasi', 'is_billable': 1}],
    'hours': [
        {'project': 'Turar-joy majmuasi', 'staff': 'Aziz', 'hours': 120, 'period': '2026-06'},
        {'project': 'Turar-joy majmuasi', 'staff': 'Bekzod', 'hours': 100, 'period': '2026-06'},
        {'project': 'Turar-joy majmuasi', 'staff': 'Dilnoza', 'hours': 80, 'period': '2026-06'},
    ],
}


def build_v5_fixture():
    init_db()
    conn = get_db()
    conn.execute("DELETE FROM overhead_budget")     # drop the install seed
    for key, value in FIXTURE['settings'].items():
        conn.execute("UPDATE settings SET value=? WHERE key=?", (value, key))
    # v4-compatible mode for the parity comparison
    conn.execute("UPDATE settings SET value=0 WHERE key='allocation_base_labor_cost'")
    conn.execute("UPDATE settings SET value=0 WHERE key='overhead_from_ledger'")

    ids = {}
    for s in FIXTURE['staff']:
        cur = conn.execute(
            "INSERT INTO staff (name, role, department, staff_type, is_active)"
            " VALUES (?,?,?,?,1)",
            (s['name'], s['role'], s['department'], s['staff_type']))
        ids[s['name']] = cur.lastrowid
        conn.execute(
            "INSERT INTO salary_history (staff_id, base_salary, premium, start_date)"
            " VALUES (?,?,?,?)",
            (cur.lastrowid, s['base_salary'], s['premium'], FIXTURE['salary_start']))

    for e in FIXTURE['personal_equipment']:
        conn.execute(
            "INSERT INTO equipment (name, kind, staff_id, quantity, price,"
            " lifespan_months, purchase_date, is_active) VALUES (?,'personal',?,1,?,?,?,1)",
            (e['name'], ids[e['staff']], e['price'], e['lifespan_months'],
             e.get('purchase_date')))
    for e in FIXTURE['general_equipment']:
        conn.execute(
            "INSERT INTO equipment (name, kind, quantity, price, lifespan_months,"
            " purchase_date, is_active) VALUES (?,'general',?,?,?,?,1)",
            (e['name'], e['quantity'], e['price'], e['lifespan_months'],
             e.get('purchase_date')))
    for l in FIXTURE['licenses']:
        conn.execute(
            "INSERT INTO licenses (name, staff_id, annual_cost, is_active)"
            " VALUES (?,?,?,1)", (l['name'], ids[l['staff']], l['annual_cost']))
    for o in FIXTURE['overhead']:
        conn.execute(
            "INSERT INTO overhead_budget (name, monthly_amount, is_active) VALUES (?,?,1)",
            (o['name'], o['monthly_amount']))

    projects = {}
    for p in FIXTURE['projects']:
        cur = conn.execute("INSERT INTO projects (name, is_billable) VALUES (?,?)",
                           (p['name'], p['is_billable']))
        projects[p['name']] = cur.lastrowid
    for h in FIXTURE['hours']:
        conn.execute(
            "INSERT INTO project_hours (project_id, staff_id, hours, period, source)"
            " VALUES (?,?,?,?,'manual')",
            (projects[h['project']], ids[h['staff']], h['hours'], h['period']))
    conn.commit()
    conn.close()
    return ids, projects


STAFF_IDS, PROJECT_IDS = build_v5_fixture()

print('\n=== Available hours (industry: 1760–1880 net annual) ===')
ah = rates.get_available_hours()
check('monthly available hours ~151', close(ah['available_hours'], 151.3, 0.5),
      str(ah['available_hours']))
check('annual hours inside the industry band',
      1760 <= ah['annual_hours'] <= 1880, str(ah['annual_hours']))

print('\n=== GOLDEN PARITY: v5 (hours base) reproduces v4 ===')
fixture_file = os.path.join(TMP, 'mizan5_rate_fixture.json')
with open(fixture_file, 'w', encoding='utf-8') as f:
    json.dump(FIXTURE, f)
probe = subprocess.run(
    [sys.executable, os.path.join(HERE, '_v4_rate_probe.py'), fixture_file,
     V4_ROOT, os.path.join(TMP, 'mizan4_probe.db')],
    capture_output=True, text=True, cwd=HERE)
if probe.returncode != 0:
    check('v4 probe ran', False, probe.stderr.strip()[-400:])
    v4_rates = {}
else:
    v4_rates = json.loads(probe.stdout.strip().splitlines()[-1])
    check('v4 probe ran', True)

ctx = rates.rate_context()
for name in ('Aziz', 'Bekzod', 'Dilnoza'):
    if name not in v4_rates:
        continue
    v4 = v4_rates[name]
    v5 = rates.calculate_hourly_rate(STAFF_IDS[name], context=ctx)
    for field, v5_key in (('tax', 'tax'), ('social', 'social'),
                          ('admin_share', 'admin_share'),
                          ('personal_eq', 'personal_eq'),
                          ('personal_lic', 'personal_lic'),
                          ('general_eq', 'general_eq'),
                          ('overhead_share', 'overhead_share'),
                          ('total_monthly', 'total_monthly')):
        check(f'{name}: {field} matches v4', close(v4[field], v5[v5_key]),
              f"v4={v4[field]:.4f} v5={v5[v5_key]:.4f}")
    check(f'{name}: COST RATE matches v4', close(v4['cost_rate'], v5['cost_rate']),
          f"v4={v4['cost_rate']:.4f} v5={v5['cost_rate']:.4f}")
    check(f'{name}: BILLING RATE matches v4',
          close(v4['billing_rate'], v5['billing_rate']),
          f"v4={v4['billing_rate']:.4f} v5={v5['billing_rate']:.4f}")

print('\n=== Formula internals ===')
aziz = rates.calculate_hourly_rate(STAFF_IDS['Aziz'], context=ctx)
check('gross is base + premium', close(aziz['gross'], 9000000))
check('tax is 12% of gross', close(aziz['tax'], 1080000))
check('social is 12% of gross', close(aziz['social'], 1080000))
check('personal equipment is price / lifespan',
      close(aziz['personal_eq'], 15000000 / 36))
check('personal licence is annual / 12', close(aziz['personal_lic'], 1000000))
check('billing rate is cost rate x markup',
      close(aziz['billing_rate'], aziz['cost_rate'] * 2.0))
check('cost rate is total monthly / available hours',
      close(aziz['cost_rate'], aziz['total_monthly'] / aziz['available_hours']))
check('rate returns None when no salary exists',
      rates.calculate_hourly_rate(99999, context=ctx) is None)

print('\n=== Hours base: shares follow billable hours ===')
shares = rates.compute_shares()
check('allocation base reads as hours', shares['base'] == 'hours')
check('Aziz share = 120/300', close(shares['shares'][STAFF_IDS['Aziz']], 0.4, 0.0001))
check('Bekzod share = 100/300',
      close(shares['shares'][STAFF_IDS['Bekzod']], 1 / 3, 0.0001))
check('shares sum to 1', close(sum(shares['shares'].values()), 1.0, 0.0001))

print('\n=== Labor-cost base (v5 default, industry standard) ===')
set_setting('allocation_base_labor_cost', 1)
lc = rates.compute_shares()
check('allocation base reads as labor_cost', lc['base'] == 'labor_cost')
total_gross = 9000000 + 5000000 + 14000000
check('Dilnoza carries the largest share (highest paid)',
      close(lc['shares'][STAFF_IDS['Dilnoza']], 14000000 / total_gross, 0.0001),
      str(lc['shares'][STAFF_IDS['Dilnoza']]))
check('shares still sum to 1', close(sum(lc['shares'].values()), 1.0, 0.0001))
check('the two bases genuinely differ',
      not close(lc['shares'][STAFF_IDS['Dilnoza']],
                shares['shares'][STAFF_IDS['Dilnoza']], 0.01))

ctx_lc = rates.rate_context()
bek_hours = rates.calculate_hourly_rate(STAFF_IDS['Bekzod'], context=ctx)
bek_labor = rates.calculate_hourly_rate(STAFF_IDS['Bekzod'], context=ctx_lc)
check('a cheaper employee absorbs less overhead on the labor-cost base',
      bek_labor['overhead_share'] < bek_hours['overhead_share'],
      f"{bek_labor['overhead_share']:.0f} vs {bek_hours['overhead_share']:.0f}")

print('\n=== Benchmark figures ===')
ov = rates.get_rates_overview()
check('firm overhead rate is reported', ov['firm_overhead_rate_pct'] > 0,
      f"{ov['firm_overhead_rate_pct']:.1f}%")
check('overhead rate is indirect pool / direct labor',
      close(ov['firm_overhead_rate_pct'],
            ov['indirect_pool_total'] / ov['direct_labor_total'] * 100, 0.001))
check('utilization is reported per employee',
      all('utilization_pct' in r for r in ov['rows'] if r.get('has_salary')))
check('Aziz utilization = 120 / available hours',
      close(ov['rows'][0]['utilization_pct'] if ov['rows'][0]['name'] == 'Aziz' else -1,
            120 / ov['available_hours'] * 100, 0.01))
check('net multiplier is reported and sensible',
      all(1.5 < r['net_multiplier'] < 8 for r in ov['rows'] if r.get('has_salary')),
      str([round(r.get('net_multiplier', 0), 2) for r in ov['rows']]))
check('net multiplier is billing rate over raw labor rate',
      close(aziz['net_multiplier'], aziz['billing_rate'] / aziz['raw_labor_rate'], 0.0001))

print('\n=== Overhead source: ledger vs budget table ===')
set_setting('overhead_from_ledger', 0)
budget_ov = rates.overhead_monthly()
check('budget source totals the overhead table',
      close(budget_ov['monthly'], 30000000) and budget_ov['source'] == 'budget',
      str(budget_ov['monthly']))

# Post a real office-rent expense; with the ledger source it must replace the table.
conn = get_db()
vendor = conn.execute(
    "INSERT INTO counterparties (name, counterparty_type) VALUES ('Ijarachi MChJ','vendor')"
).lastrowid
conn.commit()
conn.close()
for month in ('2026-04', '2026-05', '2026-06'):
    doc = save_document(
        {'doc_type': 'purchase_invoice', 'date': f'{month}-05',
         'counterparty_id': vendor, 'description': 'Ofis ijarasi'},
        lines=[{'description': 'Ijara', 'amount': 20000000, 'vat_rate': 0,
                'account_id': account_id_for('admin_expense')}])
    post_document(doc)

set_setting('overhead_from_ledger', 1)
ledger_ov = rates.overhead_monthly()
check('ledger source is used when postings exist', ledger_ov['source'] == 'ledger')
check('ledger pool averages actual posted cost over elapsed months',
      ledger_ov['monthly'] > 0, str(ledger_ov['monthly']))
detail = ledger_ov['detail']
check('pool total is the sum of indirect postings',
      close(detail['total'], 60000000), str(detail['total']))
check('pool divides by elapsed months, not the full window',
      detail['elapsed_months'] <= detail['window_months'])
check('pool breakdown names the accounts',
      any(b['code'] == '9420' for b in detail['breakdown']))

set_setting('overhead_from_ledger', 0)

print('\n=== Payroll feeds the same numbers ===')
rows = build_payroll_rows('2026-06')
check('payroll proposes a row per employee with a salary', len(rows) == 4, str(len(rows)))
totals = payroll_totals(rows)
check('payroll gross matches the salary table',
      close(totals['gross'], 9000000 + 5000000 + 14000000 + 7500000),
      str(totals['gross']))
check('PIT is withheld at the configured rate',
      close(totals['pit'], totals['gross'] * 0.12))
check('employer cost is gross plus social',
      close(totals['employer_cost'], totals['gross'] * 1.12))

pay_doc = save_payroll('2026-06', rows)
post_document(pay_doc)
from models.payroll import payroll_liabilities                       # noqa: E402
liab = payroll_liabilities()
check('net pay is owed to staff after accrual',
      close(liab['net_pay'], totals['gross'] - totals['pit']), str(liab['net_pay']))
check('PIT is owed to the budget', close(liab['pit'], totals['pit']))
check('social is owed to the fund', close(liab['social'], totals['social']))

from models.ledger import get_trial_balance, verify_all_entries      # noqa: E402
check('payroll accrual balances', not verify_all_entries())
check('trial balance holds after payroll', get_trial_balance()['is_balanced'])

print('\n=== Snapshot freezes the period ===')
written = rates.snapshot_period_allocations('2026-06')
check('a snapshot row per production employee', written == 3, str(written))
snap = rates.get_period_snapshot('2026-06')
check('snapshot stores the cost rate',
      all(r['cost_rate'] > 0 for r in snap))
check('snapshot records which base was used',
      all(r['allocation_base'] in ('hours', 'labor_cost') for r in snap))
check('snapshot records the benchmark figures',
      all(r['overhead_rate_pct'] > 0 for r in snap))

rates.snapshot_hours_rates('2026-06')
conn = get_db()
snapped = conn.execute(
    "SELECT COUNT(*) AS n FROM project_hours WHERE applied_cost_amount > 0").fetchone()['n']
conn.close()
check('timesheet rows carry their applied cost', snapped == 3, str(snapped))

print(f'\n{"=" * 46}\n  passed: {PASS}   failed: {FAIL}\n{"=" * 46}')
sys.exit(1 if FAIL else 0)
