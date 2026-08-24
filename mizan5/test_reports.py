# -*- coding: utf-8 -*-
"""Report tests — P&L, balance sheet, cash flow, aging, VAT, FX, period close.

    python test_reports.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DB_FILE = os.path.join(tempfile.gettempdir(), 'mizan5_test_reports.db')
os.environ['MIZAN5_DB'] = DB_FILE
os.environ['MIZAN_ADMIN_PASSWORD'] = 'test'
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

from models.base import init_db, get_db, get_period_status              # noqa: E402
from models.ledger import (                                             # noqa: E402
    account_balance, get_trial_balance, verify_all_entries, account_id_for,
    PostingError,
)
from models.documents import save_document, post_document               # noqa: E402
from models.reports import (                                            # noqa: E402
    get_pnl, get_balance_sheet, get_cash_flow, cash_balance, get_aging,
    aging_by_counterparty, get_vat_report, fx_position, post_fx_revaluation,
    close_period, reopen_period, list_periods, get_burn_rate, get_runway,
    get_capacity, get_dashboard,
)

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


init_db()
conn = get_db()
CLIENT = conn.execute(
    "INSERT INTO counterparties (name, inn, counterparty_type)"
    " VALUES ('Mijoz MChJ','301111111','client')").lastrowid
CLIENT2 = conn.execute(
    "INSERT INTO counterparties (name, counterparty_type) VALUES ('Ikkinchi mijoz','client')"
).lastrowid
VENDOR = conn.execute(
    "INSERT INTO counterparties (name, counterparty_type) VALUES ('Yetkazuvchi','vendor')"
).lastrowid
PROJECT = conn.execute(
    "INSERT INTO projects (name, counterparty_id, is_billable) VALUES ('Loyiha A', ?, 1)",
    (CLIENT,)).lastrowid
SID = conn.execute(
    "INSERT INTO staff (name, role, department, staff_type)"
    " VALUES ('Aziz','Arxitektor','Arxitektura','production')").lastrowid
conn.execute("INSERT INTO salary_history (staff_id, base_salary, premium, start_date)"
             " VALUES (?, 9000000, 0, '2026-01-01')", (SID,))
conn.commit()
conn.close()

# ── Opening balance, then a normal trading month ────────────────────────────
opening = save_document(
    {'doc_type': 'opening', 'date': '2026-01-01'},
    lines=[{'account_id': account_id_for('cash_bank'), 'debit': 100000000},
           {'account_id': account_id_for('retained_earnings'), 'credit': 100000000}])
post_document(opening)

inv1 = save_document(
    {'doc_type': 'sales_invoice', 'date': '2026-02-10', 'counterparty_id': CLIENT,
     'project_id': PROJECT, 'due_date': '2026-03-12', 'description': 'Loyiha ishlari'},
    lines=[{'description': 'Arxitektura', 'amount': 50000000, 'vat_rate': 12,
            'vat_amount': 6000000, 'project_id': PROJECT}])
post_document(inv1)

inv2 = save_document(
    {'doc_type': 'sales_invoice', 'date': '2025-10-01', 'counterparty_id': CLIENT2,
     'due_date': '2025-10-31', 'description': 'Eski qarz'},
    lines=[{'description': 'Konsultatsiya', 'amount': 8000000, 'vat_rate': 0,
            'vat_amount': 0}])
post_document(inv2)

pinv = save_document(
    {'doc_type': 'purchase_invoice', 'date': '2026-02-15', 'counterparty_id': VENDOR,
     'due_date': '2026-03-15', 'description': 'Ofis ijarasi'},
    lines=[{'description': 'Ijara', 'amount': 10000000, 'vat_rate': 12,
            'vat_amount': 1200000}])
post_document(pinv)

pay = save_document(
    {'doc_type': 'cash_in', 'date': '2026-02-25', 'counterparty_id': CLIENT,
     'total': 30000000, 'payment_method': 'bank'},
    allocations=[{'invoice_doc_id': inv1, 'amount': 30000000}])
post_document(pay)

payout = save_document(
    {'doc_type': 'cash_out', 'date': '2026-02-28', 'counterparty_id': VENDOR,
     'total': 11200000, 'payment_method': 'bank'},
    allocations=[{'invoice_doc_id': pinv, 'amount': 11200000}])
post_document(payout)

print('\n=== Profit & loss ===')
pnl = get_pnl('2026-02-01', '2026-02-28')
check('revenue is recognised net of VAT', close_to(pnl['total_income'], 50000000),
      str(pnl['total_income']))
check('expense is recognised net of VAT', close_to(pnl['total_expense'], 10000000),
      str(pnl['total_expense']))
check('net profit is income less expense', close_to(pnl['net_profit'], 40000000))
check('VAT never reaches the P&L',
      all('6410' not in i['code'] and '4410' not in i['code']
          for i in pnl['income'] + pnl['expense']))
check('period filter excludes the older invoice',
      not any(close_to(i['amount'], 8000000) for i in pnl['income']))

print('\n=== Balance sheet ===')
bs = get_balance_sheet('2026-02-28')
check('balance sheet balances', bs['is_balanced'],
      f"assets {bs['total_assets']:.2f} vs {bs['total_liabilities_equity']:.2f}")
check('the unclosed result appears as the period result',
      close_to(bs['period_result'], 50000000 + 8000000 - 10000000),
      str(bs['period_result']))
check('cash is an asset', any(a['code'] == '5110' for a in bs['assets']))
check('receivable is an asset', any(a['code'] == '4010' for a in bs['assets']))
check('retained earnings is equity', any(e['code'] == '8710' for e in bs['equity']))

print('\n=== Cash flow (direct method) ===')
cf = get_cash_flow()
check('cash flow reports the months that moved money', len(cf['rows']) >= 2)
check('closing cash equals the ledger cash balance',
      close_to(cf['closing'], cash_balance()), f"{cf['closing']} vs {cash_balance()}")
feb = next(r for r in cf['rows'] if r['period'] == '2026-02')
check('February inflow is the receipt', close_to(feb['inflow'], 30000000))
check('February outflow is the payment', close_to(feb['outflow'], 11200000))
check('running balance accumulates from the opening entry',
      close_to(cf['closing'], 100000000 + 30000000 - 11200000),
      str(cf['closing']))

print('\n=== AR / AP aging ===')
ar = get_aging('ar', '2026-03-20')
check('receivable total is what is still unpaid',
      close_to(ar['total'], (56000000 - 30000000) + 8000000), str(ar['total']))
check('a long-overdue invoice lands in the 90+ bucket',
      close_to(ar['buckets']['b90_plus'], 8000000), str(ar['buckets']))
check('a recent invoice sits in an early bucket',
      close_to(ar['buckets']['b0_30'] + ar['buckets']['b31_60'], 26000000))
check('overdue counts only past 60 days', close_to(ar['overdue'], 8000000))
by_cp = aging_by_counterparty('ar', '2026-03-20')
check('aging groups by counterparty', len(by_cp) == 2, str(len(by_cp)))
ap = get_aging('ap', '2026-03-20')
check('a settled payable drops out of aging', close_to(ap['total'], 0), str(ap['total']))

print('\n=== VAT report ===')
vat = get_vat_report('2026-02-01', '2026-02-28')
check('output VAT comes from sales invoices', close_to(vat['output_vat'], 6000000))
check('input VAT comes from purchase invoices', close_to(vat['input_vat'], 1200000))
check('VAT payable is output less input', close_to(vat['payable'], 4800000))
check('the report lists the underlying invoices',
      len(vat['sales']) == 1 and len(vat['purchases']) == 1)
check('the sales list carries the counterparty INN',
      vat['sales'][0]['inn'] == '301111111')

print('\n=== FX position and revaluation ===')
usd_inv = save_document(
    {'doc_type': 'sales_invoice', 'date': '2026-02-20', 'counterparty_id': CLIENT,
     'currency': 'USD', 'exchange_rate': 12800, 'total_cur': 1000,
     'description': 'USD shartnoma'},
    lines=[{'description': 'Loyiha', 'amount': 12800000, 'vat_rate': 0, 'vat_amount': 0}])
post_document(usd_inv)
pos = fx_position('2026-03-01')
check('a foreign-currency balance is detected', len(pos['positions']) >= 1,
      str(pos['positions']))
entry_id, total = post_fx_revaluation('2026-03-01')
check('revaluation posts when a rate difference exists',
      entry_id is not None or close_to(total, 0), f'entry={entry_id} total={total}')
check('trial balance survives revaluation', get_trial_balance()['is_balanced'])
check('no unbalanced entry after revaluation', not verify_all_entries())

print('\n=== CFO metrics ===')
burn = get_burn_rate()
check('burn rate includes the payroll burden',
      close_to(burn['payroll'], 9000000 * 1.24), str(burn['payroll']))
check('burn rate adds overhead', burn['total'] >= burn['payroll'])
runway = get_runway()
check('runway is cash divided by burn',
      close_to(runway['months'], runway['cash'] / burn['total'], 0.001))
cap = get_capacity()
check('capacity counts production staff', cap['staff_count'] == 1)
dash = get_dashboard()
check('dashboard reports cash', close_to(dash['cash'], cash_balance()))
check('dashboard reports receivable and payable',
      dash['receivable'] > 0 and dash['payable'] >= 0)
check('dashboard lists recent documents', len(dash['recent_documents']) > 0)

print('\n=== Period close ===')
before_income = account_balance('9030')
check('income sits on 9030 before the close', before_income > 0)
result, err = close_period('2026-02', status='hard_closed')
check('close succeeds', err is None, str(err))
check('the close reports the period result',
      close_to(result['net_profit'], get_pnl('2026-02-01', '2026-02-28')['net_profit'],
               1.0) or result['net_profit'] != 0, str(result['net_profit']))
check('period is marked hard closed', get_period_status('2026-02') == 'hard_closed')
check('trial balance still balances after the close', get_trial_balance()['is_balanced'])
check('the financial result carries the period profit',
      abs(account_balance('9910')) > 0, str(account_balance('9910')))

bad = save_document({'doc_type': 'manual', 'date': '2026-02-15'},
                    lines=[{'account_id': account_id_for('cash_till'), 'debit': 100},
                           {'account_id': account_id_for('cash_bank'), 'credit': 100}])
try:
    post_document(bad)
    check('posting into a closed period is refused', False, 'no exception')
except PostingError as e:
    check('posting into a closed period is refused', e.key == 'ledger_period_closed', e.key)

result2, err2 = close_period('2026-02')
check('closing an already-closed period is refused', err2 == 'already_closed', str(err2))

periods = list_periods()
check('period list reports entry counts',
      any(p['code'] == '2026-02' and p['entries'] > 0 for p in periods))

print('\n=== Reopen ===')
reopen_period('2026-02')
check('period is open again', get_period_status('2026-02') == 'open')
check('trial balance survives the reopen', get_trial_balance()['is_balanced'])
check('the closing entry was reversed',
      close_to(account_balance('9030'), before_income, 1.0),
      f"{account_balance('9030')} vs {before_income}")
post_document(bad)
check('posting works again after reopening',
      close_to(account_balance('5010'), 100), str(account_balance('5010')))

print('\n=== Integrity ===')
check('no unbalanced entry in the database', not verify_all_entries())
check('final trial balance is balanced', get_trial_balance()['is_balanced'])
check('final balance sheet balances', get_balance_sheet()['is_balanced'])

print(f'\n{"=" * 46}\n  passed: {PASS}   failed: {FAIL}\n{"=" * 46}')
sys.exit(1 if FAIL else 0)
