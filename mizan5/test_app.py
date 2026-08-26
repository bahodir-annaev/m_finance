# -*- coding: utf-8 -*-
"""End-to-end smoke test — every page renders, and a full document flow works
through the HTTP layer rather than by calling the models directly.

Uses Flask's test client, so no server needs to be running.

    python test_app.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DB_FILE = os.path.join(tempfile.gettempdir(), 'mizan5_test_app.db')
os.environ['MIZAN5_DB'] = DB_FILE
os.environ['MIZAN_ADMIN_PASSWORD'] = 'testpass'
os.environ['MIZAN_SECRET_KEY'] = 'test-secret'
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

from models.base import init_db, get_db                              # noqa: E402
from models.ledger import get_trial_balance, account_balance         # noqa: E402
from app import create_app                                           # noqa: E402

PASS, FAIL = 0, 0


def check(label, condition, detail=''):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f'  [ok]   {label}')
    else:
        FAIL += 1
        print(f'  [FAIL] {label}' + (f'  — {detail}' if detail else ''))


init_db()
conn = get_db()
CLIENT = conn.execute(
    "INSERT INTO counterparties (name, inn, counterparty_type)"
    " VALUES ('Alfa MChJ','301234567','client')").lastrowid
VENDOR = conn.execute(
    "INSERT INTO counterparties (name, counterparty_type) VALUES ('Beta MChJ','vendor')"
).lastrowid
PROJECT = conn.execute(
    "INSERT INTO projects (name, counterparty_id, is_billable, estimated_total_hours)"
    " VALUES ('Biznes markaz', ?, 1, 800)", (CLIENT,)).lastrowid
SID = conn.execute(
    "INSERT INTO staff (name, role, department, staff_type)"
    " VALUES ('Aziz','Arxitektor','Arxitektura','production')").lastrowid
conn.execute("INSERT INTO salary_history (staff_id, base_salary, premium, start_date)"
             " VALUES (?, 9000000, 1000000, '2026-01-01')", (SID,))
conn.execute("INSERT INTO project_hours (project_id, staff_id, hours, period)"
             " VALUES (?,?,?,?)", (PROJECT, SID, 120, '2026-06'))
LOAN = conn.execute(
    "INSERT INTO loans (loan_type, counterparty_id, total_amount, issue_date)"
    " VALUES ('olgan', ?, 50000000, '2026-01-10')", (VENDOR,)).lastrowid
# Equipment so the depreciation card on /periods has real rows to render, and
# one dateless asset so the "skipped" block renders too.
conn.execute(
    "INSERT INTO equipment (name, kind, staff_id, quantity, price,"
    " lifespan_months, purchase_date) VALUES ('Laptop','personal',?,1,15000000,36,"
    " '2026-01-15')", (SID,))
conn.execute(
    "INSERT INTO equipment (name, kind, quantity, price, lifespan_months,"
    " purchase_date) VALUES ('Server','general',1,30000000,60,'2026-01-10')")
conn.execute(
    "INSERT INTO equipment (name, kind, quantity, price, lifespan_months)"
    " VALUES ('Monitor','general',1,4000000,24)")
conn.commit()
conn.close()

app = create_app()
app.config['WTF_CSRF_ENABLED'] = False
client = app.test_client()

print('\n=== Authentication ===')
r = client.get('/')
check('anonymous request is redirected to login',
      r.status_code in (302, 401), str(r.status_code))
r = client.post('/login', data={'username': 'admin', 'password': 'wrong'})
check('a wrong password does not log in', b'MIZAN' in r.data and r.status_code == 200)
r = client.post('/login', data={'username': 'admin', 'password': 'testpass'},
                follow_redirects=True)
check('correct credentials log in', r.status_code == 200 and b'MIZAN' in r.data)

print('\n=== Every page renders ===')
PAGES = [
    ('/', 'dashboard'),
    ('/documents/sales_invoice', 'sales invoices'),
    ('/documents/purchase_invoice', 'purchase invoices'),
    ('/documents/cash_in', 'money in'),
    ('/documents/cash_out', 'money out'),
    ('/documents/manual', 'manual entry'),
    ('/documents/opening', 'opening balances'),
    ('/journal', 'journal'),
    ('/trial-balance', 'trial balance'),
    ('/accounts', 'chart of accounts'),
    ('/counterparties', 'counterparties'),
    ('/staff', 'staff'),
    ('/staff/hours', 'timesheets'),
    ('/staff/equipment', 'equipment'),
    ('/rates', 'rates'),
    ('/rates/overhead', 'overhead reconciliation'),
    ('/projects', 'projects'),
    (f'/projects/{PROJECT}', 'project detail'),
    ('/budget', 'budget'),
    ('/payroll', 'payroll'),
    ('/loans', 'loans'),
    ('/reports/pnl', 'profit and loss'),
    ('/reports/balance-sheet', 'balance sheet'),
    ('/reports/cashflow', 'cash flow'),
    ('/reports/vat', 'VAT report'),
    ('/reports/aging', 'AR aging'),
    ('/reports/aging?kind=ap', 'AP aging'),
    ('/periods', 'periods'),
    ('/settings', 'settings'),
]
for url, label in PAGES:
    r = client.get(url)
    ok = r.status_code == 200
    detail = ''
    if not ok:
        detail = f'HTTP {r.status_code}'
        body = r.data.decode('utf-8', 'replace')
        for marker in ('UndefinedError', 'TemplateSyntaxError', 'jinja2', 'Error'):
            idx = body.find(marker)
            if idx > -1:
                detail += ' — ' + body[idx:idx + 180].replace('\n', ' ')
                break
    check(f'{label} ({url})', ok, detail)

print('\n=== Document flow through HTTP ===')
r = client.post('/documents/sales_invoice/save', data={
    'date': '2026-06-10', 'counterparty_id': CLIENT, 'project_id': PROJECT,
    'due_date': '2026-07-10', 'description': 'Loyiha ishlari', 'currency': 'UZS',
    'line_description': ['Arxitektura loyihasi'], 'line_quantity': ['1'],
    'line_unit_price': ['20000000'], 'line_amount': ['20000000'],
    'line_vat_rate': ['12'], 'line_vat_amount': ['2400000'],
    'post_now': '1',
}, follow_redirects=True)
check('sales invoice posts through the form', r.status_code == 200)
check('AR was debited with the gross total',
      abs(account_balance('4010') - 22400000) < 1, str(account_balance('4010')))
check('revenue was credited net of VAT',
      abs(account_balance('9030') - 20000000) < 1, str(account_balance('9030')))
check('output VAT was recorded',
      abs(account_balance('6410.1') - 2400000) < 1)

conn = get_db()
inv_id = conn.execute("SELECT id FROM documents WHERE doc_type='sales_invoice'"
                      " ORDER BY id DESC LIMIT 1").fetchone()['id']
conn.close()

r = client.post('/documents/cash_in/save', data={
    'date': '2026-06-25', 'counterparty_id': CLIENT, 'payment_method': 'bank',
    'total': '10000000', 'currency': 'UZS',
    'alloc_invoice_doc_id': [str(inv_id)], 'alloc_amount': ['10000000'],
    'post_now': '1',
}, follow_redirects=True)
check('payment posts and allocates', r.status_code == 200)
check('cash increased', abs(account_balance('5110') - 10000000) < 1,
      str(account_balance('5110')))
check('AR reduced by the allocation',
      abs(account_balance('4010') - 12400000) < 1, str(account_balance('4010')))

r = client.get('/reports/aging')
check('the unpaid balance shows in aging', b'12,400,000' in r.data)

print('\n=== Payroll through HTTP ===')
r = client.post('/payroll/save', data={
    'period': '2026-06', 'row_staff_id': [str(SID)], 'row_gross': ['10000000'],
    'row_pit': ['1200000'], 'row_social': ['1200000'], 'post_now': '1',
}, follow_redirects=True)
check('payroll accrues through the form', r.status_code == 200)
check('net pay is owed to staff', abs(account_balance('6710') - 8800000) < 1,
      str(account_balance('6710')))
check('production labor hit 2010',
      abs(account_balance('2010') - 11200000) < 1, str(account_balance('2010')))

r = client.post('/payroll/remit', data={'period': '2026-06', 'date': '2026-07-05',
                                        'payment_method': 'bank'},
                follow_redirects=True)
check('remittance clears the payroll liabilities',
      abs(account_balance('6710')) < 1 and abs(account_balance('6420.1')) < 1
      and abs(account_balance('6520')) < 1,
      f"{account_balance('6710')}/{account_balance('6420.1')}/{account_balance('6520')}")

print('\n=== Manual entry balance guard ===')
r = client.post('/documents/manual/save', data={
    'date': '2026-06-30', 'description': 'Test',
    'line_account_id': ['1', '2'], 'line_debit': ['100', ''],
    'line_credit': ['', '40'], 'post_now': '1',
}, follow_redirects=True)
check('an unbalanced manual entry is rejected with a message',
      b'alert-error' in r.data or b'ledger_unbalanced' in r.data
      or 'balanslashmagan'.encode() in r.data, 'no error surfaced')

print('\n=== Milestones and pricing through HTTP ===')
r = client.post(f'/projects/{PROJECT}/schedule', data={}, follow_redirects=True)
check('standard schedule is created', r.status_code == 200)
conn = get_db()
phase = conn.execute("SELECT id FROM project_phases WHERE project_id=?"
                     " ORDER BY sort_order LIMIT 1", (PROJECT,)).fetchone()
conn.close()
check('milestones exist', phase is not None)

if phase:
    r = client.post(f'/projects/{PROJECT}/milestone', data={
        'id': str(phase['id']), 'name': 'Eskiz loyiha', 'work_type': 'eskiz',
        'start_date': '2026-03-01', 'end_date': '2026-05-31',
        'planned_outsourcing': '2000000', 'status': 'in_progress',
        'has_staff_grid': '1',
        'ms_staff_id': [str(SID)], 'ms_est_hours': ['150'],
    }, follow_redirects=True)
    check('milestone saves with its staff hours', r.status_code == 200)
    conn = get_db()
    row = conn.execute("SELECT planned_hours, planned_cost FROM project_phases"
                       " WHERE id=?", (phase['id'],)).fetchone()
    conn.close()
    check('planned hours derived from the staff grid', row['planned_hours'] == 150,
          str(row['planned_hours']))
    check('planned cost derived from the rate', row['planned_cost'] > 0,
          str(row['planned_cost']))

r = client.post(f'/projects/{PROJECT}/prices', data={}, follow_redirects=True)
check('suggested prices apply', r.status_code == 200)
r = client.post(f'/projects/{PROJECT}/freeze', data={}, follow_redirects=True)
check('plan freezes', r.status_code == 200)
r = client.get('/budget')
check('budget page shows the frozen project', b'Biznes markaz' in r.data)

print('\n=== Counterparty and staff modals ===')
r = client.post('/counterparties/save', data={
    'name': 'Gamma Qurilish', 'inn': '309999999', 'counterparty_type': 'client',
    'is_active': '1'}, follow_redirects=True)
check('counterparty saves', b'Gamma Qurilish' in r.data)
r = client.post('/staff/save', data={
    'name': 'Bekzod', 'role': 'BIM muhandis', 'department': 'Arxitektura',
    'staff_type': 'production', 'is_active': '1'}, follow_redirects=True)
check('staff member saves', b'Bekzod' in r.data)
r = client.post('/staff/salary', data={
    'staff_id': str(SID), 'start_date': '2026-07-01',
    'base_salary': '11000000', 'premium': '0'}, follow_redirects=True)
check('salary change is recorded', r.status_code == 200)
conn = get_db()
closed = conn.execute("SELECT COUNT(*) AS n FROM salary_history"
                      " WHERE staff_id=? AND end_date IS NOT NULL", (SID,)).fetchone()['n']
conn.close()
check('the previous salary row was closed', closed == 1, str(closed))

print('\n=== Period close through HTTP ===')
print('\n=== Depreciation through HTTP ===')
r = client.get('/periods?dep_period=2026-05')
check('the depreciation card renders its schedule',
      r.status_code == 200 and 'Laptop'.encode() in r.data)
check('a dateless asset is reported as skipped, not dropped',
      'Monitor'.encode() in r.data)
r = client.post('/periods/depreciate', data={'period': '2026-05'},
                follow_redirects=True)
check('depreciation posts through the form', r.status_code == 200)
from models.ledger import account_balance as _bal                     # noqa: E402
check('accumulated depreciation is now on the books', _bal('0200') > 0,
      str(_bal('0200')))
check('the expense landed on 9420.1, not 9420',
      _bal('9420.1') > 0 and abs(_bal('9420.1') - _bal('0200')) < 1,
      f"9420.1={_bal('9420.1')} 0200={_bal('0200')}")
r = client.post('/periods/depreciate', data={'period': '2026-05'},
                follow_redirects=True)
check('posting the same period twice is refused politely',
      b'alert-info' in r.data or b'alert-success' in r.data)

r = client.post('/periods/close', data={'period': '2026-06', 'status': 'hard_closed'},
                follow_redirects=True)
check('period closes', r.status_code == 200)
r = client.post('/documents/sales_invoice/save', data={
    'date': '2026-06-15', 'counterparty_id': CLIENT,
    'line_description': ['X'], 'line_amount': ['1000'], 'line_vat_rate': ['0'],
    'post_now': '1'}, follow_redirects=True)
check('posting into the closed period is refused with a message',
      b'alert-error' in r.data, 'no error surfaced')
r = client.post('/periods/reopen', data={'period': '2026-06'}, follow_redirects=True)
check('period reopens', r.status_code == 200)

print('\n=== Language switching ===')
for code, marker in (('ru', 'Бухгалтерия'), ('en', 'Accounting'), ('uz', 'Buxgalteriya')):
    client.get(f'/lang/{code}')
    r = client.get('/')
    check(f'interface switches to {code}', marker.encode('utf-8') in r.data)

print('\n=== Books remain intact ===')
check('trial balance is balanced', get_trial_balance()['is_balanced'])
from models.ledger import verify_all_entries                          # noqa: E402
check('no unbalanced entry exists', not verify_all_entries())
from models.reports import get_balance_sheet                          # noqa: E402
check('balance sheet balances', get_balance_sheet()['is_balanced'])

print('\n=== Logout ===')
r = client.get('/logout', follow_redirects=True)
check('logout returns to the login page', b'MIZAN' in r.data)
r = client.get('/rates')
check('protected pages are closed after logout', r.status_code in (302, 401))

print(f'\n{"=" * 46}\n  passed: {PASS}   failed: {FAIL}\n{"=" * 46}')
sys.exit(1 if FAIL else 0)
