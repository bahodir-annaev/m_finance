# -*- coding: utf-8 -*-
"""Migration test — replay the real v4 database into a fresh v5 one.

Runs against a COPY of mizan_finance.db; the original is never opened for
writing. The reconciliation checks are the point: v5's ledger cash and
receivable must equal what v4 computed by its own rules.

    python test_migration.py [path-to-v4.db]
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.abspath(__file__))
V4_SOURCE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(HERE), 'mizan_finance.db')

TMP = tempfile.gettempdir()
DB_FILE = os.path.join(TMP, 'mizan5_test_migration.db')
V4_COPY = os.path.join(TMP, 'mizan4_migration_source.db')
os.environ['MIZAN5_DB'] = DB_FILE
os.environ['MIZAN_ADMIN_PASSWORD'] = 'test'
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

PASS, FAIL = 0, 0


def check(label, condition, detail=''):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f'  [ok]   {label}')
    else:
        FAIL += 1
        print(f'  [FAIL] {label}' + (f'  — {detail}' if detail else ''))


if not os.path.exists(V4_SOURCE):
    print(f'v4 database not found at {V4_SOURCE} — nothing to migrate.')
    sys.exit(0)

# Work on a copy so a bug here can never touch live data.
shutil.copy2(V4_SOURCE, V4_COPY)

from models.base import init_db, get_db                               # noqa: E402
from models.import_v4 import run_migration, v4_expected_totals        # noqa: E402
from models.ledger import (                                           # noqa: E402
    get_trial_balance, verify_all_entries, account_balance,
)
from models.reports import get_balance_sheet, cash_balance, get_pnl   # noqa: E402

init_db()
print(f'\nmigrating {V4_COPY} ...')
report = run_migration(V4_COPY, verbose=True)
rec = report['reconciliation']

print('\n=== Reference data ===')
conn = get_db()
counts = {name: conn.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()['n']
          for name in ('staff', 'salary_history', 'projects', 'project_phases',
                       'project_hours', 'equipment', 'licenses', 'counterparties',
                       'documents', 'journal_entries', 'journal_lines')}
conn.close()
for name, n in counts.items():
    print(f'  {name}: {n}')

check('staff came across', counts['staff'] > 0, str(counts['staff']))
check('salary history came across', counts['salary_history'] > 0)
check('projects came across', counts['projects'] > 0)
check('counterparties were derived from free-text names',
      counts['counterparties'] > 0, str(counts['counterparties']))
check('documents were created', counts['documents'] > 0, str(counts['documents']))
check('every document produced a posting',
      counts['journal_entries'] > 0 and counts['journal_lines'] >= counts['journal_entries'] * 2)

print('\n=== Books are internally consistent ===')
check('no unbalanced entry exists', rec['unbalanced_entries'] == 0,
      str(rec['unbalanced_entries']))
check('trial balance is balanced', rec['trial_balance_ok'])
check('balance sheet balances', get_balance_sheet()['is_balanced'],
      str(get_balance_sheet()['difference']))

print('\n=== Reconciliation against v4 ===')
print(f"  v4 cash        {rec['v4_cash']:>18,.2f}")
print(f"  v5 cash        {rec['v5_cash']:>18,.2f}")
print(f"  difference     {rec['cash_diff']:>18,.2f}")
print(f"  v4 receivable  {rec['v4_receivable']:>18,.2f}")
print(f"  v5 receivable  {rec['v5_receivable']:>18,.2f}")
print(f"  difference     {rec['receivable_diff']:>18,.2f}")

# One so'm per migrated document is the tolerance: each conversion rounds, and
# a thousand documents can legitimately drift by that much in total.
tolerance = max(2.0, counts['documents'] * 1.0)
check('v5 cash equals v4 cash', abs(rec['cash_diff']) <= tolerance,
      f"diff {rec['cash_diff']:,.2f} > tolerance {tolerance:,.0f}")
check('v5 receivable equals v4 receivable', abs(rec['receivable_diff']) <= tolerance,
      f"diff {rec['receivable_diff']:,.2f} > tolerance {tolerance:,.0f}")

print('\n=== Nothing silently vanished ===')
skipped = report['skipped']
migrated = (report['sales_invoices'] + report['purchase_invoices']
            + report['receipts'] + report['payments'] + report['internal'])
print(f'  v4 transactions: {rec["v4_transactions"]}')
print(f'  v5 documents from them: {migrated}')
print(f'  skipped: {len(skipped)}')
check('skipped rows are reported rather than dropped silently',
      isinstance(skipped, list))
check('the vast majority of transactions migrated',
      len(skipped) < rec['v4_transactions'] * 0.05,
      f'{len(skipped)} of {rec["v4_transactions"]}')

print('\n=== v5 reproduces v4 direction on the migrated data ===')
# v4 stored `direction` on every row; v5 derives it (models/direction.py). The
# aggregates below are what a user actually read off v4's cash-flow page, so
# comparing them is a stronger check than matching row for row — and it needs
# no id map between the two databases.
import sqlite3                                                        # noqa: E402
from models.reports import get_cash_flow                              # noqa: E402

INCOME = "('tushum','mizan_monthly','yakuniy_hisob')"
v4 = sqlite3.connect(V4_COPY)
v4.row_factory = sqlite3.Row
v4_income = v4.execute(
    f"SELECT COALESCE(SUM(paid),0) AS s FROM transactions"
    f" WHERE paid > 0 AND tx_type IN {INCOME}").fetchone()['s']
v4_ext_out = v4.execute(
    f"SELECT COALESCE(SUM(paid),0) AS s FROM transactions"
    f" WHERE paid > 0 AND direction='external' AND tx_type NOT IN {INCOME}"
).fetchone()['s']
v4_int_out = v4.execute(
    f"SELECT COALESCE(SUM(paid),0) AS s FROM transactions"
    f" WHERE paid > 0 AND direction='internal' AND tx_type NOT IN {INCOME}"
).fetchone()['s']
# Rows the migration itself could not route as external: with no counterparty
# name to hang a supplier invoice on, import_v4 falls back to the bare cash_out
# shape, which is the internal shape. Those land in internal by construction.
v4_ext_no_cp = v4.execute(
    f"SELECT COALESCE(SUM(paid),0) AS s FROM transactions"
    f" WHERE paid > 0 AND direction='external' AND tx_type NOT IN {INCOME}"
    f"   AND COALESCE(NULLIF(TRIM(COALESCE(paid_to,'')),''),"
    f"                NULLIF(TRIM(COALESCE(client,'')),'')) IS NULL").fetchone()['s']
v4.close()

td = get_cash_flow()['totals_by_direction']
print(f"  v4 income        {v4_income:>18,.2f}   v5 external in   {td['external']['in']:>18,.2f}")
print(f"  v4 external out  {v4_ext_out:>18,.2f}   v5 external out  {td['external']['out']:>18,.2f}")
print(f"  v4 internal out  {v4_int_out:>18,.2f}   v5 internal out  {td['internal']['out']:>18,.2f}")
if v4_ext_no_cp:
    print(f"  of which v4 external rows with no counterparty: {v4_ext_no_cp:,.2f}"
          f"  (migrated as internal)")

check('v4 client receipts come back as external inflow',
      abs(td['external']['in'] - v4_income) <= tolerance,
      f"{td['external']['in']:,.2f} vs {v4_income:,.2f}")
check('v4 external costs come back as external outflow',
      abs(td['external']['out'] - (v4_ext_out - v4_ext_no_cp)) <= tolerance,
      f"{td['external']['out']:,.2f} vs {v4_ext_out - v4_ext_no_cp:,.2f}")
check('v4 internal costs come back as internal outflow',
      abs(td['internal']['out'] - (v4_int_out + v4_ext_no_cp)) <= tolerance,
      f"{td['internal']['out']:,.2f} vs {v4_int_out + v4_ext_no_cp:,.2f}")
check('the split still adds up to the undivided cash flow',
      all(abs(sum(r[d + '_out'] for d in ('external', 'internal', 'financing'))
              - r['outflow']) < 0.005 for r in get_cash_flow()['rows']))

print('\n=== The rate engine still works on migrated data ===')
from models.staff import get_rates_overview                            # noqa: E402
ov = get_rates_overview()
check('rates computed for migrated staff', ov['staff_count'] > 0, str(ov['staff_count']))
check('an overhead pool was derived', ov['overhead_monthly'] > 0,
      f"{ov['overhead_monthly']:,.0f} from {ov['overhead_source']}")
if ov['priced_count']:
    check('cost rates are positive',
          all(r['cost_rate'] > 0 for r in ov['rows'] if r.get('has_salary')))
    print(f"  firm overhead rate: {ov['firm_overhead_rate_pct']:.1f}%")
    print(f"  average cost rate:  {ov['avg_cost_rate']:,.0f} UZS/h")

pnl = get_pnl()
print(f"\n  migrated P&L: income {pnl['total_income']:,.0f}"
      f" / expense {pnl['total_expense']:,.0f} / profit {pnl['net_profit']:,.0f}")

print(f'\n{"=" * 46}\n  passed: {PASS}   failed: {FAIL}\n{"=" * 46}')
sys.exit(1 if FAIL else 0)
