# -*- coding: utf-8 -*-
"""Overhead pool tests — THE NO-DOUBLE-COUNT RULE, generalized.

An account may not carry cost_pool='indirect' if the same cost is already
modeled by a register that calculate_hourly_rate() reads. test_depreciation.py
pins that rule for equipment (9420.1). This file pins it for the other two
registers the rate engine reads — admin salary (9420.2) and licenses (9420.3) —
plus the pool's own arithmetic: what may enter it, what its shares must sum to,
and what happens when it cannot answer.

The headline assertion, as in test_depreciation.py, is that posting must not
move a cost rate. Measured on the real migrated register before the split,
pooling admin salary added 183M UZS/month to the pool and +13% to every
production cost rate — and to every price quoted from one.

    python test_overhead.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DB_FILE = os.path.join(tempfile.gettempdir(), 'mizan5_test_overhead.db')
os.environ['MIZAN5_DB'] = DB_FILE
os.environ['MIZAN_ADMIN_PASSWORD'] = 'test'
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

from datetime import datetime, timedelta                                 # noqa: E402

from models.base import (                                                # noqa: E402
    init_db, get_db, set_setting, today_str, REGISTER_MODELED_CODES,
)
from models.staff import DAYS_PER_MONTH                                  # noqa: E402
from models.ledger import (                                              # noqa: E402
    PostingError, account_id_for, accounts_in_pool, get_account_by_code,
    get_trial_balance, pnl_section, verify_all_entries,
)
from models.documents import save_document, post_document, void_document  # noqa: E402
from models.payroll import build_payroll_rows, save_payroll               # noqa: E402
from models import staff as rates                                        # noqa: E402

PASS, FAIL = 0, 0


def check(label, condition, detail=''):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f'  [ok]   {label}')
    else:
        FAIL += 1
        print(f'  [FAIL] {label}' + (f'  — {detail}' if detail else ''))


def close_to(a, b, tol=0.01):
    return abs((a or 0) - (b or 0)) <= tol


def d(months_ago):
    """A date exactly `months_ago` pool-months back from today, as YYYY-MM-DD.

    Dates here are RELATIVE to the wall clock on purpose. The overhead window
    is a trailing window, so a fixture pinned to fixed dates silently falls out
    of it once enough real time passes and the assertions then fail for a
    reason that looks nothing like the cause.

    The offset is exact — no snapping to a tidy day-of-month. Snapping shifts a
    date by up to a month depending on when the suite happens to run, which is
    the same wall-clock fragility in a smaller form.
    """
    t = datetime.strptime(today_str(), '%Y-%m-%d') - timedelta(days=months_ago * DAYS_PER_MONTH)
    return t.strftime('%Y-%m-%d')


def rates_by_name():
    ov = rates.get_rates_overview()
    return {r['name']: r for r in ov['rows'] if r.get('has_salary')}, ov


# ── Fixture ─────────────────────────────────────────────────────────────────
init_db()
set_setting('overhead_from_ledger', 1)
set_setting('allocation_base_labor_cost', 1)

conn = get_db()
STAFF = {}
for name, stype, base in (('Aziz', 'production', 9000000),
                          ('Bekzod', 'production', 5000000),
                          ('Dilnoza', 'production', 14000000),
                          ('Nodira', 'admin', 7000000),
                          ('Sardor', 'admin', 6000000)):
    sid = conn.execute(
        "INSERT INTO staff (name, role, department, staff_type, hire_date)"
        " VALUES (?,?,?,?,?)",
        (name, 'Xodim', 'Boshqaruv', stype, '2024-01-01')).lastrowid
    conn.execute("INSERT INTO salary_history (staff_id, base_salary, premium, start_date)"
                 " VALUES (?,?,0,'2024-01-01')", (sid, base))
    STAFF[name] = sid

# A license on the register — the rate engine charges annual_cost/12 from here.
conn.execute("INSERT INTO licenses (name, staff_id, annual_cost, is_active)"
             " VALUES ('Revit', ?, 24000000, 1)", (STAFF['Aziz'],))

VENDOR = conn.execute(
    "INSERT INTO counterparties (name, counterparty_type) VALUES ('Ijarachi','vendor')"
).lastrowid
CLIENT = conn.execute(
    "INSERT INTO counterparties (name, counterparty_type) VALUES ('Buyurtmachi','client')"
).lastrowid
conn.commit()
conn.close()


def rent_invoice(month_ago, amount=20000000, purpose='admin_expense'):
    doc = save_document(
        {'doc_type': 'purchase_invoice', 'date': d(month_ago),
         'counterparty_id': VENDOR, 'description': 'Xarajat'},
        lines=[{'description': 'Xarajat', 'amount': amount, 'vat_rate': 0,
                'account_id': account_id_for(purpose)}])
    return post_document(doc)


print('\n=== The register-modeled accounts are seeded outside the pool ===')
for code in REGISTER_MODELED_CODES:
    acc = get_account_by_code(code)
    check(f'{code} is seeded', acc is not None)
    check(f'{code} is an expense in the P&L', pnl_section(code) == 'expense')
    check(f'THE CRITICAL FLAG: {code} is not in the indirect pool',
          acc['id'] not in accounts_in_pool('indirect'),
          f"cost_pool={acc['cost_pool']}")

conn = get_db()
pool = {conn.execute("SELECT code FROM accounts WHERE id=?", (a,)).fetchone()['code']
        for a in accounts_in_pool('indirect')}
conn.close()
check('the indirect pool is still exactly 9410/9420/9430',
      pool == {'9410', '9420', '9430'}, str(sorted(pool)))
check('unattributed income has its own account outside the pool',
      get_account_by_code('9390')['id'] not in accounts_in_pool('indirect'))

print('\n=== The pool reads posted indirect cost ===')
# Deliberately all older than one month, so the staleness check further down
# can force a fallback with a 1-month window no matter when the suite runs.
rent_invoice(4)
rent_invoice(3)
rent_invoice(2)
detail = rates.ledger_overhead_monthly()
check('the pool totals the posted invoices', close_to(detail['total'], 60000000),
      str(detail['total']))
check('the divisor never exceeds the window',
      detail['elapsed_months'] <= detail['window_months'])
# The invariant, not a magic range: with less history than the window, the
# divisor is exactly the span the data covers. This is what the old
# round()-then-walk-back arithmetic could not guarantee.
span = ((datetime.strptime(detail['as_of'], '%Y-%m-%d')
         - datetime.strptime(detail['earliest'], '%Y-%m-%d')).days + 1) / DAYS_PER_MONTH
check('the divisor is exactly the span the data covers',
      close_to(detail['elapsed_months'], span, 1e-9),
      f"{detail['elapsed_months']} vs {span}")
check('the cutoff is not earlier than the first posting',
      detail['cutoff'] <= detail['earliest'],
      f"cutoff={detail['cutoff']} earliest={detail['earliest']}")
check('the breakdown names the account',
      any(b['code'] == '9420' for b in detail['breakdown']))
resolved = rates.overhead_monthly()
check('the ledger is the source when postings exist',
      resolved['source'] == 'ledger' and resolved['reason'] == 'ledger')

print('\n=== No double count: ADMIN SALARY ===')
before, ov_before = rates_by_name()
check('a rate exists for every production employee', len(before) == 3, str(len(before)))
admin_share_before = before['Aziz']['admin_share']
check('admin cost is already in the rate before any payroll is posted',
      admin_share_before > 0, str(admin_share_before))

payroll_rows = build_payroll_rows('2026-06')
check('payroll proposes a row per employee with a salary',
      len(payroll_rows) == 5, str(len(payroll_rows)))
pay_doc = save_payroll('2026-06', payroll_rows)
post_document(pay_doc)

conn = get_db()
admin_acc = account_id_for('admin_salary_expense')
posted_admin = conn.execute(
    "SELECT COALESCE(SUM(debit),0) AS d FROM journal_lines WHERE account_id=?",
    (admin_acc,)).fetchone()['d']
conn.close()
check('the admin payroll leg landed on 9420.2',
      close_to(posted_admin, (7000000 + 6000000) * 1.12), str(posted_admin))
check('9420.2 is absent from the overhead pool breakdown',
      not any(b['code'].startswith('9420.2')
              for b in rates.ledger_overhead_monthly()['breakdown']))

after, ov_after = rates_by_name()
check('THE HEADLINE: posting payroll does not move a single cost rate',
      all(close_to(before[n]['cost_rate'], after[n]['cost_rate']) for n in before),
      str({n: (round(before[n]['cost_rate']), round(after[n]['cost_rate']))
           for n in before}))
# admin_share comes from the register and never moves; the double count would
# arrive through overhead_share, so that is the one worth asserting on.
check('admin cost is still counted exactly once — via the register',
      close_to(after['Aziz']['admin_share'], admin_share_before))
check('...and not a second time through the overhead pool',
      close_to(after['Aziz']['overhead_share'], before['Aziz']['overhead_share']),
      f"{before['Aziz']['overhead_share']:.0f} -> {after['Aziz']['overhead_share']:.0f}")
check('the indirect pool total is unchanged',
      close_to(ov_before['indirect_pool_total'], ov_after['indirect_pool_total']))

# Negative control: prove the guard fires rather than merely trusting the flag.
conn = get_db()
conn.execute("UPDATE accounts SET cost_pool='indirect' WHERE code='9420.2'")
conn.commit()
conn.close()
try:
    post_document(save_payroll('2026-07', build_payroll_rows('2026-07')))
    check('a pooled admin salary account is refused', False, 'no exception raised')
except PostingError as e:
    check('a pooled admin salary account is refused', e.key == 'account_in_pool', e.key)
conn = get_db()
conn.execute("UPDATE accounts SET cost_pool='excluded' WHERE code='9420.2'")
conn.commit()
conn.close()

print('\n=== No double count: LICENSES ===')
before, _ = rates_by_name()
lic_before = before['Aziz']['personal_lic']
check('the license register charges annual_cost / 12',
      close_to(lic_before, 24000000 / 12), str(lic_before))
rent_invoice(1, amount=24000000, purpose='license_expense')
after, _ = rates_by_name()
check('a license invoice does not move a cost rate',
      all(close_to(before[n]['cost_rate'], after[n]['cost_rate']) for n in before))
check('9420.3 is absent from the overhead pool breakdown',
      not any(b['code'].startswith('9420.3')
              for b in rates.ledger_overhead_monthly()['breakdown']))
check('the license is still charged once, from the register',
      close_to(after['Aziz']['personal_lic'], lic_before))

print('\n=== Income may not net against the pool ===')
pool_before = rates.ledger_overhead_monthly()['total']
receipt = save_document(
    {'doc_type': 'cash_in', 'date': d(1), 'total': 50000000,
     'description': 'Nomalum tushum'}, lines=[])
post_document(receipt)
pool_after = rates.ledger_overhead_monthly()
check('an unattributed receipt does not reduce the pool',
      close_to(pool_before, pool_after['total']),
      f'{pool_before} -> {pool_after["total"]}')
check('the pool cannot go negative from income', pool_after['total'] > 0)
conn = get_db()
income_acc = get_account_by_code('9390')['id']
credited = conn.execute(
    "SELECT COALESCE(SUM(credit),0) AS c FROM journal_lines WHERE account_id=?",
    (income_acc,)).fetchone()['c']
conn.close()
check('it landed on 9390 instead', close_to(credited, 50000000), str(credited))

print('\n=== Allocation shares always sum to 1 ===')
conn = get_db()
proj = conn.execute(
    "INSERT INTO projects (name, is_billable) VALUES ('Loyiha', 1)").lastrowid
for name in ('Aziz', 'Bekzod', 'Dilnoza'):
    conn.execute("INSERT INTO project_hours (project_id, staff_id, hours, period)"
                 " VALUES (?,?,?,?)", (proj, STAFF[name], 100, '2026-06'))
# A departed employee with hours in the same period. Their hours must not sit
# in the denominator: they receive no share, so the pool they attract would be
# charged to nobody and the firm would never recover it in a rate.
gone = conn.execute(
    "INSERT INTO staff (name, role, department, staff_type, is_active)"
    " VALUES ('Ketgan','Xodim','Arxitektura','production',0)").lastrowid
conn.execute("INSERT INTO project_hours (project_id, staff_id, hours, period)"
             " VALUES (?,?,?,?)", (proj, gone, 400, '2026-06'))
conn.commit()
conn.close()

for base in ('labor_cost', 'hours'):
    set_setting('allocation_base_labor_cost', 1 if base == 'labor_cost' else 0)
    shares = rates.compute_shares()['shares']
    check(f'{base} base: shares sum to exactly 1',
          close_to(sum(shares.values()), 1.0, 1e-9), str(sum(shares.values())))
    check(f'{base} base: an inactive employee gets no share', gone not in shares)
set_setting('allocation_base_labor_cost', 1)

print('\n=== The firm benchmark equals the rows it is shown beside ===')
rows, ov = rates_by_name()
check('the indirect pool is the sum of the rows',
      close_to(ov['indirect_pool_total'], sum(r['indirect_total'] for r in rows.values())))
check('the firm overhead rate is that pool over direct labor',
      close_to(ov['firm_overhead_rate_pct'],
               ov['indirect_pool_total'] / ov['direct_labor_total'] * 100, 0.001))
weighted = (sum(r['overhead_rate_pct'] * r['direct_labor_cost'] for r in rows.values())
            / sum(r['direct_labor_cost'] for r in rows.values()))
check('and equals the labor-weighted average of the per-row rates',
      close_to(ov['firm_overhead_rate_pct'], weighted, 0.001),
      f"{ov['firm_overhead_rate_pct']:.3f} vs {weighted:.3f}")

print('\n=== The fallback says why ===')
set_setting('overhead_from_ledger', 0)
res = rates.overhead_monthly()
check('turning the ledger off reports "disabled"',
      res['source'] == 'budget' and res['reason'] == 'disabled', res['reason'])
set_setting('overhead_from_ledger', 1)

# Every pooled posting above is at least two months old, so a one-month window
# excludes all of them regardless of the day the suite runs.
set_setting('overhead_window_months', 1)
res = rates.overhead_monthly()
check('a window with no postings in it falls back as "stale", not silently',
      res['source'] == 'budget' and res['reason'] == 'ledger_stale', res['reason'])
check('the fallback still returns the budget table figure',
      close_to(res['monthly'], rates.overhead_budget_monthly()))
set_setting('overhead_window_months', 12)
check('widening the window restores the ledger source',
      rates.overhead_monthly()['reason'] == 'ledger')

print('\n=== A back-dated snapshot costs the period, not today ===')
conn = get_db()
# Aziz got a raise after the period being closed. The snapshot must not use it.
conn.execute("UPDATE salary_history SET end_date='2026-06-30' WHERE staff_id=?",
             (STAFF['Aziz'],))
conn.execute("INSERT INTO salary_history (staff_id, base_salary, premium, start_date)"
             " VALUES (?,?,0,'2026-07-01')", (STAFF['Aziz'], 30000000))
conn.commit()
conn.close()

rates.snapshot_period_allocations('2026-06')
snap = {r['name']: r for r in rates.get_period_snapshot('2026-06')}
check('a snapshot row per production employee', len(snap) == 3, str(len(snap)))
check('the snapshot uses the salary in force during the period',
      close_to(snap['Aziz']['gross_salary'], 9000000),
      str(snap['Aziz']['gross_salary']))
check("today's raise is visible on the live page, not in the snapshot",
      close_to(rates_by_name()[0]['Aziz']['gross'], 30000000))
check('the snapshot records which base and source it used',
      snap['Aziz']['allocation_base'] == 'labor_cost'
      and snap['Aziz']['overhead_source'] in ('ledger', 'budget'))

print('\n=== The ledger is still sound ===')
check('every entry balances', not verify_all_entries())
check('the trial balance holds', get_trial_balance()['is_balanced'])
void_document(receipt)
check('void leaves the pool where it was',
      close_to(rates.ledger_overhead_monthly()['total'], pool_before))
check('the trial balance still holds after the void',
      get_trial_balance()['is_balanced'])

print(f'\n{"=" * 46}\n  passed: {PASS}   failed: {FAIL}\n{"=" * 46}')
sys.exit(1 if FAIL else 0)
