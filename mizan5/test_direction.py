# -*- coding: utf-8 -*-
"""Direction tests — the internal / external / financing reporting axis.

One case per rung of the ladder, then the two properties that matter more than
any individual case: the split adds back up to the undivided cash flow, and a
void still nets to zero inside its own direction.

    python test_direction.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DB_FILE = os.path.join(tempfile.gettempdir(), 'mizan5_test_direction.db')
os.environ['MIZAN5_DB'] = DB_FILE
os.environ['MIZAN_ADMIN_PASSWORD'] = 'test'
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

from models.base import init_db, get_db                             # noqa: E402
from models.ledger import account_id_for, get_trial_balance         # noqa: E402
from models.documents import (                                      # noqa: E402
    save_document, post_document, void_document, list_documents,
)
from models.direction import (                                      # noqa: E402
    DIRECTIONS, document_direction, document_directions,
    cash_turnover_by_direction,
)
from models.reports import get_cash_flow, cash_account_ids          # noqa: E402
from models.payroll import build_payroll_rows, save_payroll         # noqa: E402

PASS, FAIL = 0, 0


def check(label, condition, detail=''):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f'  [ok]   {label}')
    else:
        FAIL += 1
        print(f'  [FAIL] {label}' + (f'  — {detail}' if detail else ''))


def is_dir(label, doc_id, expected):
    got = document_direction(doc_id)
    check(label, got == expected, f'got {got}, wanted {expected}')


init_db()
conn = get_db()
CLIENT = conn.execute(
    "INSERT INTO counterparties (name, counterparty_type)"
    " VALUES ('Alfa Qurilish MChJ','client')").lastrowid
VENDOR = conn.execute(
    "INSERT INTO counterparties (name, counterparty_type)"
    " VALUES ('Beta Loyiha MChJ','vendor')").lastrowid
LANDLORD = conn.execute(
    "INSERT INTO counterparties (name, counterparty_type)"
    " VALUES ('Ijarachi MChJ','vendor')").lastrowid
BANK = conn.execute(
    "INSERT INTO counterparties (name, counterparty_type)"
    " VALUES ('Bank MChJ','bank')").lastrowid
PROJECT = conn.execute(
    "INSERT INTO projects (name, counterparty_id) VALUES ('Turar-joy majmuasi', ?)",
    (CLIENT,)).lastrowid
conn.execute(
    "INSERT INTO staff (name, role, department, staff_type)"
    " VALUES ('Aziz','Arxitektor','Loyiha','production')")
STAFF = conn.execute("SELECT id FROM staff WHERE name='Aziz'").fetchone()['id']
conn.execute(
    "INSERT INTO salary_history (staff_id, base_salary, premium, start_date)"
    " VALUES (?, 8000000, 0, '2026-01-01')", (STAFF,))
LOAN = conn.execute(
    "INSERT INTO loans (loan_type, counterparty_id, total_amount, issue_date)"
    " VALUES ('olgan', ?, 20000000, '2026-03-02')", (BANK,)).lastrowid
conn.commit()
ADMIN_EXPENSE = account_id_for('admin_expense', conn)
LOAN_ACCOUNT = account_id_for('loan_short_in', conn)
conn.close()


print('\n=== Rung 4 — client money is external ===')
inv = save_document(
    {'doc_type': 'sales_invoice', 'date': '2026-03-05', 'counterparty_id': CLIENT,
     'project_id': PROJECT, 'description': 'Eskiz loyiha'},
    lines=[{'description': 'Arxitektura loyihasi', 'amount': 10000000,
            'vat_rate': 0, 'vat_amount': 0, 'project_id': PROJECT}])
is_dir('a draft sales invoice is already external', inv, 'external')
post_document(inv)
is_dir('a posted sales invoice is external', inv, 'external')

print('\n=== Rung 3 — a payment is whatever it settles ===')
receipt = save_document(
    {'doc_type': 'cash_in', 'date': '2026-03-12', 'counterparty_id': CLIENT,
     'payment_method': 'bank', 'total': 6000000},
    allocations=[{'invoice_doc_id': inv, 'amount': 6000000}])
post_document(receipt)
is_dir('a receipt settling a sales invoice is external', receipt, 'external')

advance = save_document(
    {'doc_type': 'cash_in', 'date': '2026-03-14', 'counterparty_id': CLIENT,
     'total': 1000000, 'cash_purpose': 'Avans'})
post_document(advance)
is_dir('an unallocated client advance (6310) is external', advance, 'external')

print('\n=== Rungs 5 and 6 — project cost is external, office cost is not ===')
outsourcing = save_document(
    {'doc_type': 'purchase_invoice', 'date': '2026-03-08', 'counterparty_id': VENDOR,
     'description': 'KJ bolimi autsorsing'},
    lines=[{'description': 'KJ bolimi', 'amount': 3000000, 'vat_rate': 0,
            'vat_amount': 0, 'project_id': PROJECT}])
post_document(outsourcing)
is_dir('a project-tagged purchase invoice is external', outsourcing, 'external')

untagged = save_document(
    {'doc_type': 'purchase_invoice', 'date': '2026-03-09', 'counterparty_id': VENDOR,
     'description': 'Autsorsing, loyihasiz'},
    lines=[{'description': 'KJ bolimi', 'amount': 500000, 'vat_rate': 0,
            'vat_amount': 0, 'account_id': account_id_for('production_cost')}])
post_document(untagged)
is_dir('an invoice on 2010 with no project is still external', untagged, 'external')

rent_inv = save_document(
    {'doc_type': 'purchase_invoice', 'date': '2026-03-03', 'counterparty_id': LANDLORD,
     'description': 'Ofis ijarasi'},
    lines=[{'description': 'Mart ijarasi', 'amount': 4000000, 'vat_rate': 0,
            'vat_amount': 0}])
post_document(rent_inv)
is_dir('a rent purchase invoice is internal', rent_inv, 'internal')

rent_pay = save_document(
    {'doc_type': 'cash_out', 'date': '2026-03-20', 'counterparty_id': LANDLORD,
     'total': 4000000},
    allocations=[{'invoice_doc_id': rent_inv, 'amount': 4000000}])
post_document(rent_pay)
is_dir('paying the rent invoice inherits internal', rent_pay, 'internal')

vendor_pay = save_document(
    {'doc_type': 'cash_out', 'date': '2026-03-21', 'counterparty_id': VENDOR,
     'total': 3000000},
    allocations=[{'invoice_doc_id': outsourcing, 'amount': 3000000}])
post_document(vendor_pay)
check('an AP settlement carries no project of its own, so only rung 3 can place it',
      document_direction(vendor_pay) == 'external')

print('\n=== Rung 7 — a bare running cost is internal ===')
utilities = save_document(
    {'doc_type': 'cash_out', 'date': '2026-03-22', 'counterparty_id': LANDLORD,
     'total': 700000, 'description': 'Kommunal'},
    lines=[{'description': 'Kommunal', 'amount': 700000,
            'account_id': ADMIN_EXPENSE}])
post_document(utilities)
is_dir('a cash_out straight to 9420 is internal', utilities, 'internal')

orphan = save_document(
    {'doc_type': 'cash_in', 'date': '2026-03-23', 'total': 250000,
     'description': 'Nomalum tushum'})
post_document(orphan)
is_dir('an unattributed receipt (9390) is internal, never external', orphan, 'internal')

print('\n=== Rung 1 — payroll is the firm cost it always was ===')
rows = build_payroll_rows('2026-03')
pay_doc = save_payroll('2026-03', rows, date='2026-03-31')
post_document(pay_doc)
is_dir('a payroll accrual is internal', pay_doc, 'internal')
check('payroll debits 2010 yet is not called external',
      document_direction(pay_doc) == 'internal')

payout = save_document(
    {'doc_type': 'cash_out', 'date': '2026-03-31', 'total': 5000000,
     'cash_purpose': 'payroll'},
    lines=[{'description': 'Maosh', 'amount': 5000000,
            'account_id': account_id_for('payroll_payable')}])
post_document(payout)
is_dir('the salary payout is internal', payout, 'internal')

print('\n=== Rung 2 — loans and dividends are neither ===')
loan_doc = save_document(
    {'doc_type': 'loan', 'date': '2026-03-02', 'counterparty_id': BANK,
     'loan_id': LOAN, 'total': 20000000, 'description': 'Bank qarzi'})
post_document(loan_doc)
is_dir('a loan issue is financing', loan_doc, 'financing')

repay = save_document(
    {'doc_type': 'cash_out', 'date': '2026-03-28', 'counterparty_id': BANK,
     'loan_id': LOAN, 'total': 2000000},
    lines=[{'description': 'Asosiy qarz', 'amount': 2000000,
            'account_id': LOAN_ACCOUNT}])
post_document(repay)
is_dir('a loan repayment is financing, not an internal cost', repay, 'financing')

div = save_document(
    {'doc_type': 'dividend', 'date': '2026-03-29', 'counterparty_id': BANK,
     'total': 1000000, 'description': 'Dividend'})
post_document(div)
is_dir('a dividend declaration is financing', div, 'financing')

print('\n=== Every document lands somewhere, exactly once ===')
all_dirs = document_directions()
conn = get_db()
doc_count = conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()['n']
conn.close()
check('every document is classified', len(all_dirs) == doc_count,
      f'{len(all_dirs)} of {doc_count}')
check('no document gets a value outside the vocabulary',
      all(v in DIRECTIONS for v in all_dirs.values()))

print('\n=== Conservation — the split adds back up ===')
cf = get_cash_flow()
worst_in = worst_out = 0.0
for r in cf['rows']:
    d_in = abs(sum(r[f'{d}_in'] for d in DIRECTIONS) - r['inflow'])
    d_out = abs(sum(r[f'{d}_out'] for d in DIRECTIONS) - r['outflow'])
    worst_in = max(worst_in, d_in)
    worst_out = max(worst_out, d_out)
check('monthly inflow equals the sum of its directions', worst_in < 0.005,
      f'worst {worst_in}')
check('monthly outflow equals the sum of its directions', worst_out < 0.005,
      f'worst {worst_out}')

td = cf['totals_by_direction']
check('the totals row splits the same way',
      abs(sum(v['in'] for v in td.values()) - cf['total_in']) < 0.005
      and abs(sum(v['out'] for v in td.values()) - cf['total_out']) < 0.005,
      f"{sum(v['in'] for v in td.values())} vs {cf['total_in']}")
check('client receipts land in external in', td['external']['in'] > 0)
check('running costs land in internal out', td['internal']['out'] > 0)
check('the loan lands in financing, not in either of the other two',
      td['financing']['in'] >= 20000000 - 0.005)
check('a date filter still conserves',
      all(abs(sum(r[f'{d}_out'] for d in DIRECTIONS) - r['outflow']) < 0.005
          for r in get_cash_flow('2026-03-01', '2026-03-31')['rows']))

print('\n=== The list filter agrees with the classifier ===')
for kind in DIRECTIONS:
    filtered = list_documents(direction=kind)
    expected = sum(1 for v in all_dirs.values() if v == kind)
    check(f'{kind}: the filter returns exactly the {kind} documents',
          len(filtered) == expected and all(r['direction'] == kind for r in filtered),
          f'{len(filtered)} rows, wanted {expected}')
check('an unfiltered list still returns everything',
      len(list_documents()) == doc_count)
check('every listed row carries its direction',
      all(r.get('direction') in DIRECTIONS for r in list_documents()))

print('\n=== Void is a mirror inside its own direction ===')
before = {d: dict(v) for d, v in get_cash_flow()['totals_by_direction'].items()}
void_document(utilities, reason='test')
after = get_cash_flow()['totals_by_direction']
is_dir('a voided document keeps its direction', utilities, 'internal')
# The storno mirrors the original: the credit to cash becomes a debit, so it
# reappears as internal *inflow*. What matters is that it lands in the same
# direction — a storno that fell into another bucket would leave both wrong
# while the undivided cash flow still looked right.
check('the storno comes back inside internal',
      abs((after['internal']['in'] - before['internal']['in']) - 700000) < 0.005,
      f"internal in moved {after['internal']['in'] - before['internal']['in']}")
check('the storno touches no other direction',
      all(abs(after[d]['in'] - before[d]['in']) < 0.005
          and abs(after[d]['out'] - before[d]['out']) < 0.005
          for d in ('external', 'financing')))
check('the voided pair nets to zero inside internal',
      abs((after['internal']['in'] - after['internal']['out'])
          - (before['internal']['in'] - before['internal']['out']) - 700000) < 0.005)
check('books still balance after the void', get_trial_balance()['is_balanced'])

print('\n=== Direction is a lens, not an input ===')
from models.staff import get_rates_overview                          # noqa: E402
first = get_rates_overview()
document_directions()
cash_turnover_by_direction(cash_account_ids())
second = get_rates_overview()
check('classifying moves no cost rate',
      [r['cost_rate'] for r in first['rows']] == [r['cost_rate'] for r in second['rows']])
check('classifying moves no overhead figure',
      first['overhead_monthly'] == second['overhead_monthly'])

print(f'\n{"=" * 46}\n  passed: {PASS}   failed: {FAIL}\n{"=" * 46}')
sys.exit(1 if FAIL else 0)
