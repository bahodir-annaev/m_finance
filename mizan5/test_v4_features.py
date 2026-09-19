# -*- coding: utf-8 -*-
"""The v4 features ported after the gap review (V4_FEATURE_GAP.md).

Time-phased plan lens and on-course verdict, unassigned actuals, NIZAM
import, KPI, scratch quote → project, loan balance and auto-close, period
create/delete and the hard-close lock, document paging/sorting/responsible,
payment-method summary, per-project FX, the dashboard extras.

    python test_v4_features.py
"""
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DB_FILE = os.path.join(tempfile.gettempdir(), 'mizan5_test_v4_features.db')
os.environ['MIZAN5_DB'] = DB_FILE
os.environ['MIZAN_ADMIN_PASSWORD'] = 'test'
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

from models.base import (                                            # noqa: E402
    init_db, get_db, create_fiscal_period, delete_fiscal_period, set_period_notes,
    get_period_status,
)
from models.ledger import account_balance, get_trial_balance, account_id_for  # noqa: E402
from models.documents import (                                       # noqa: E402
    save_document, post_document, void_document, list_documents, count_documents,
    get_document,
)
from models.milestones import (                                      # noqa: E402
    add_milestone, get_project_milestones, get_project_monthly_rollup,
    get_unassigned_actuals, suggest_phase_for_period, assign_hours_to_milestone,
    assign_document_to_milestone, get_milestone_actuals,
)
from models.pricing import set_milestone_staff, save_quote_to_project, quote_schedule  # noqa: E402
from models.projects import (                                        # noqa: E402
    get_budget_overview, get_projects_on_course_summary, calculate_fx_gain_loss,
    calculate_project_cost, list_projects, get_top_projects,
)
from models.staff import get_staff_kpi                               # noqa: E402
from models.reports import (                                         # noqa: E402
    close_period, reopen_period, list_periods, payment_method_summary, get_cash_flow,
    get_dashboard,
)
from models.loans import (                                           # noqa: E402
    list_loans, loan_balance, loan_summary, save_loan, post_loan_issue,
    record_loan_payment, sync_loan_status, loan_documents,
)
from models.import_nizam import import_nizam_file, parse_hhmm, read_nizam_sheet  # noqa: E402

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


TODAY = date.today()
CUR = TODAY.strftime('%Y-%m')


def month_shift(n):
    """YYYY-MM n months before today."""
    y, m = TODAY.year, TODAY.month - n
    while m <= 0:
        m += 12
        y -= 1
    return f'{y:04d}-{m:02d}'


P1, P2, P3 = month_shift(3), month_shift(2), month_shift(1)   # all strictly before now

init_db()
conn = get_db()
CLIENT = conn.execute(
    "INSERT INTO counterparties (name, counterparty_type) VALUES ('Mijoz MChJ','client')"
).lastrowid
BANK = conn.execute(
    "INSERT INTO counterparties (name, counterparty_type) VALUES ('Ipoteka Bank','bank')"
).lastrowid
PROJECT = conn.execute(
    "INSERT INTO projects (name, counterparty_id, is_billable, estimated_total_hours,"
    " currency, contract_amount) VALUES ('Biznes markaz', ?, 1, 1000, 'UZS', 0)",
    (CLIENT,)).lastrowid
OTHER = conn.execute(
    "INSERT INTO projects (name, counterparty_id, is_billable) VALUES ('Villa', ?, 1)",
    (CLIENT,)).lastrowid
STAFF = {}
for name, base, nizam in (('Aziz', 9000000, 'AZIZ MUXAMEDOV'), ('Bekzod', 5000000, None)):
    sid = conn.execute(
        "INSERT INTO staff (name, role, department, staff_type, nizam_name)"
        " VALUES (?,?,?,'production',?)", (name, 'Arxitektor', 'Arxitektura', nizam)).lastrowid
    conn.execute("INSERT INTO salary_history (staff_id, base_salary, premium, start_date)"
                 " VALUES (?,?,0,'2025-01-01')", (sid, base))
    STAFF[name] = sid
# Timesheet: three past months on the project, untagged.
for period, hours in ((P1, 40), (P2, 60), (P3, 80)):
    conn.execute("INSERT INTO project_hours (project_id, staff_id, hours, period)"
                 " VALUES (?,?,?,?)", (PROJECT, STAFF['Aziz'], hours, period))
conn.execute("INSERT INTO project_hours (project_id, staff_id, hours, period)"
             " VALUES (?,?,?,?)", (OTHER, STAFF['Bekzod'], 10, P3))
conn.commit()
conn.close()

# Two dated milestones spanning P1..P3, priced.
M1 = add_milestone(PROJECT, 'Eskiz', work_type='eskiz', start_date=f'{P1}-01',
                   end_date=f'{P2}-15', planned_revenue=60000000, planned_outsourcing=5000000)
M2 = add_milestone(PROJECT, 'AR', work_type='ar', start_date=f'{P2}-16',
                   end_date=f'{P3}-28', planned_revenue=90000000)
set_milestone_staff(M1, [{'staff_id': STAFF['Aziz'], 'est_hours': 100}])
set_milestone_staff(M2, [{'staff_id': STAFF['Aziz'], 'est_hours': 120}])

# Revenue recognised in P2 (untagged to any phase).
INV = save_document({'doc_type': 'sales_invoice', 'date': f'{P2}-10',
                     'counterparty_id': CLIENT, 'project_id': PROJECT},
                    lines=[{'description': 'Eskiz', 'amount': 100000000, 'vat_rate': 0,
                            'project_id': PROJECT}])
post_document(INV)

print('\n=== Monthly roll-up: the time-phased lens ===')
r = get_project_monthly_rollup(PROJECT)
months = [m['period'] for m in r['months']]
check('every planned and actual month appears', {P1, P2, P3} <= set(months), str(months))
plan_income = sum(m['plan_income'] for m in r['months'])
check('day-weighted plan sums back to the milestone revenue',
      close(plan_income, 150000000, 1.0), str(plan_income))
check('fact income lands in its posting month',
      close(next(m for m in r['months'] if m['period'] == P2)['fact_income'], 100000000))
check('labor from the timesheet is in fact expense every month',
      all(m['fact_expense'] > 0 for m in r['months'] if m['period'] in (P1, P2, P3)))
check('to-date covers only months before the current one',
      close(r['to_date']['fact_income'], 100000000) and CUR not in [
          m['period'] for m in r['months'] if not m['is_current']])
check('cumulative profit is a running sum',
      close(r['months'][-1]['cum_fact_profit'], sum(m['fact_profit'] for m in r['months']), 1.0))
check('the verdict carries the three lights and a headline',
      set(r['status']) == {'money', 'schedule', 'collections', 'expense', 'headline'})
check('past-due milestones make the schedule light red',
      r['status']['schedule'] == 'off' and r['any_late'])
check('undated milestones are reported, not silently dropped', r['undated'] == [])

# An undated milestone with a plan must be named.
M3 = add_milestone(PROJECT, 'Smeta', planned_revenue=1000000)
r2 = get_project_monthly_rollup(PROJECT)
check('an undated milestone is listed by name', r2['undated'] == ['Smeta'], str(r2['undated']))
check('a cancelled milestone leaves the plan',
      __import__('models.milestones', fromlist=['set_milestone_status'])
      .set_milestone_status(M3, 'cancelled') and get_project_monthly_rollup(PROJECT)['undated'] == [])

print('\n=== Budget overview carries both lenses ===')
ov = get_budget_overview()
row = next(x for x in ov['rows'] if x['id'] == PROJECT)
check('whole-project lens still there', 'd_total' in row and 'status' in row)
check('time-phased lens attached to the row', 'to_date' in row and 'light' in row)
check('light counts sum to the project count',
      sum(ov['light_counts'].values()) == len(ov['rows']))
oc = get_projects_on_course_summary(ov)
check('on-course summary tracks the same counts',
      oc['tracked'] + oc['none'] == oc['total'] == len(ov['rows']))
check('late projects counted', oc['late_projects'] >= 1)

print('\n=== Unassigned actuals → milestone ===')
ua = get_unassigned_actuals(PROJECT)
check('untagged timesheet months are listed', {h['period'] for h in ua['hour_periods']} == {P1, P2, P3})
check('the untagged invoice is listed', [d['id'] for d in ua['documents']] == [INV])
check('the milestone overlapping the month most is suggested',
      suggest_phase_for_period(PROJECT, P1) == M1 and suggest_phase_for_period(PROJECT, P3) == M2)
n = assign_hours_to_milestone(PROJECT, P1, M1)
check('a whole month of hours is attached in one call', n == 1)
check('a phase of another project is refused',
      assign_hours_to_milestone(OTHER, P3, M1) == -1)
before = get_document(INV)
ok, err = assign_document_to_milestone(INV, M1)
check('a posted invoice can be tagged to a milestone', ok, str(err))
after = get_document(INV)
check('tagging never touches the money', close(before['total'], after['total'])
      and after['phase_id'] == M1 and after['status'] == 'posted')
acts = get_milestone_actuals(PROJECT)
check('milestone actuals now see the income and the hours',
      close(acts[M1]['income'], 100000000) and close(acts[M1]['hours'], 40))
check('trial balance untouched by tagging', get_trial_balance()['is_balanced'])
check('wrong project is refused',
      assign_document_to_milestone(INV, __import__('models.milestones', fromlist=['add_milestone'])
                                   .add_milestone(OTHER, 'X')) == (False, 'wrong_project'))
check('nothing left unassigned for that month',
      P1 not in {h['period'] for h in get_unassigned_actuals(PROJECT)['hour_periods']}
      and not get_unassigned_actuals(PROJECT)['documents'])

print('\n=== Scratch quote → project ===')
rows = [{'name': 'Eskiz', 'work_type': 'eskiz', 'start': '2027-01', 'end': '2027-02',
         'outsourcing': 1000000, 'staff': [{'staff_id': STAFF['Aziz'], 'hours': 50}]},
        {'name': 'AR', 'work_type': 'ar', 'start': '2027-03', 'end': '2027-04',
         'staff': [{'staff_id': STAFF['Bekzod'], 'hours': 80}]}]
risk = {'deadline_months': 6, 'client_type': 'new', 'complexity': 'medium', 'currency': 'UZS'}
q = quote_schedule(rows, risk)
created, err = save_quote_to_project(OTHER, rows, risk_kwargs=risk, overwrite=True)
check('quote saved as milestones', created == 2 and err is None, str(err))
ms = get_project_milestones(OTHER)
check('per-milestone employee mix is kept',
      any(m['name'] == 'Eskiz' and m['staff'] and m['staff'][0]['est_hours'] == 50 for m in ms))
check('planned cost is derived from the employee rows',
      close(sum(m['planned_cost'] for m in ms if m['name'] in ('Eskiz', 'AR')),
            q['total_labor'], 1.0))
check('planned revenue equals the quoted price',
      close(sum(m['planned_revenue'] for m in ms if m['name'] in ('Eskiz', 'AR')),
            q['plan_revenue_total'], 1.0))
check('a second save without overwrite is refused',
      save_quote_to_project(OTHER, rows, risk) == (0, 'exists'))
check('missing project reported', save_quote_to_project(99999, rows, risk) == (0, 'notfound'))
check('empty schedule reported', save_quote_to_project(OTHER, [], risk) == (0, 'empty'))

print('\n=== NIZAM import ===')
check('HH:MM parses to decimal hours', close(parse_hhmm('12:30'), 12.5) and parse_hhmm('') == 0)
check('a bare number is hours', close(parse_hhmm(7), 7.0))
import openpyxl                                                       # noqa: E402
wb = openpyxl.Workbook()
ws = wb.active
ws.cell(2, 4, 'AZIZ MUXAMEDOV')          # matches staff.nizam_name
ws.cell(2, 5, 'Bekzod')                  # matches staff.name
ws.cell(2, 6, 'NOBODY KNOWN')            # unmatched → reported, never created
ws.cell(4, 2, 'Biznes markaz'); ws.cell(4, 3, '10:00'); ws.cell(4, 4, '6:30'); ws.cell(4, 5, '3:30')
ws.cell(5, 2, 'Office');        ws.cell(5, 3, '5:00');  ws.cell(5, 4, '5:00')
ws.cell(6, 2, 'Yangi loyiha');  ws.cell(6, 3, '2:00');  ws.cell(6, 6, '2:00'); ws.cell(6, 5, '1:00')
ws.cell(7, 2, 'Bo\'sh');        ws.cell(7, 3, '0:00')
XLSX = os.path.join(tempfile.gettempdir(), 'mizan5_nizam_test.xlsx')
wb.save(XLSX)
sheet = read_nizam_sheet(XLSX)
check('sheet parser finds the employee columns', len(sheet['employees']) == 3)
res = import_nizam_file(XLSX, P3)
check('hours imported for matched employees', res['hour_entries'] == 3, str(res))
check('unmatched employee reported, not created',
      res['unmatched_names'] == ['NOBODY KNOWN'] and res['employees_mapped'] == 2)
check('non-billable and empty rows skipped with reasons',
      sorted(sk['reason'] for sk in res['skipped']) == ['no_hours', 'non_billable'], str(res['skipped']))
check('an unknown project was created', res['projects_created'] == 1)
conn = get_db()
h = conn.execute("SELECT hours, phase_id, source FROM project_hours"
                 " WHERE project_id=? AND staff_id=? AND period=?",
                 (PROJECT, STAFF['Aziz'], P3)).fetchone()
check('existing row updated in place with the sheet hours', close(h['hours'], 6.5) and h['source'] == 'nizam')
conn.execute("UPDATE project_hours SET phase_id=? WHERE project_id=? AND staff_id=? AND period=?",
             (M2, PROJECT, STAFF['Aziz'], P3))
conn.commit()
conn.close()
res2 = import_nizam_file(XLSX, P3)
conn = get_db()
h2 = conn.execute("SELECT hours, phase_id FROM project_hours"
                  " WHERE project_id=? AND staff_id=? AND period=?",
                  (PROJECT, STAFF['Aziz'], P3)).fetchone()
n_new = conn.execute("SELECT COUNT(*) FROM projects WHERE name='Yangi loyiha'").fetchone()[0]
nz = conn.execute("SELECT nizam_name FROM staff WHERE id=?", (STAFF['Bekzod'],)).fetchone()[0]
conn.close()
check('re-import keeps the milestone tag on the row', h2['phase_id'] == M2 and close(h2['hours'], 6.5))
check('re-import does not fork the project', n_new == 1 and res2['projects_created'] == 0)
check('a name match writes nizam_name back for next time', nz == 'Bekzod')
ws.cell(8, 2, "Noma'lum loyiha"); ws.cell(8, 3, '1:00'); ws.cell(8, 4, '1:00')
wb.save(XLSX)
res3 = import_nizam_file(XLSX, month_shift(0), create_projects=False)
conn = get_db()
n_unknown = conn.execute("SELECT COUNT(*) FROM projects WHERE name LIKE 'Noma%'").fetchone()[0]
n_queue = conn.execute("SELECT COUNT(*) FROM unresolved_imports WHERE entity_type='project'"
                       " AND resolved=0").fetchone()[0]
conn.close()
check('with create_projects off an unknown project is reported, not created',
      res3['unresolved_projects'] == ["Noma'lum loyiha"] and n_unknown == 0 and n_queue == 1,
      str(res3['unresolved_projects']))
check('known projects still import in that mode', res3['hour_entries'] >= 3)
try:
    import_nizam_file(XLSX, 'bad')
    check('a bad period is refused', False)
except ValueError:
    check('a bad period is refused', True)

print('\n=== KPI ===')
kpi = get_staff_kpi()
aziz = next(r for r in kpi['rows'] if r['id'] == STAFF['Aziz'])
check('rows sorted by hours, most first',
      [r['hours'] for r in kpi['rows']] == sorted((r['hours'] for r in kpi['rows']), reverse=True))
check('utilisation is hours over the KPI window',
      close(aziz['utilization'], aziz['hours'] / kpi['window_hours'], 1e-9))
check('cost and revenue value are hours × rates',
      close(aziz['cost_value'], aziz['hours'] * aziz['cost_rate'], 1.0)
      and close(aziz['revenue_value'], aziz['hours'] * aziz['billing_rate'], 1.0))
check('rating is on the five-band scale', 1 <= aziz['rating'] <= 5)
check('project count reflects the timesheet', aziz['projects'] >= 1)

print('\n=== Loans: ledger balance and auto-close ===')
LOAN = save_loan({'loan_type': 'olgan', 'counterparty_id': BANK, 'description': 'kredit',
                  'total_amount': 10000000, 'currency': 'UZS', 'interest_rate': 20,
                  'term': 'short', 'issue_date': f'{P1}-05', 'due_date': f'{P3}-05',
                  'status': 'ochiq', 'notes': None})
check('an unbooked loan has no balance and stays open',
      loan_balance(LOAN) == 0 and sync_loan_status(LOAN) == 'ochiq')
post_loan_issue({'id': LOAN, 'loan_type': 'olgan', 'counterparty_id': BANK, 'currency': 'UZS',
                 'total_amount': 10000000, 'issue_date': f'{P1}-05', 'description': 'kredit'})
check('disbursement shows as the balance', close(loan_balance(LOAN), 10000000))
record_loan_payment(LOAN, principal=4000000, interest=300000, date=f'{P2}-05')
check('principal reduces the balance, interest does not',
      close(loan_balance(LOAN), 6000000), str(loan_balance(LOAN)))
check('interest went to the expense account', account_balance('9610') >= 300000)
row = next(r for r in list_loans() if r['id'] == LOAN)
check('list carries the split and the history',
      close(row['principal_paid'], 4000000) and close(row['interest_paid'], 300000)
      and len(row['documents']) == 2 and row['status'] == 'ochiq')
check('overdue flagged when past due with a balance', row['is_overdue'])
PAY2 = record_loan_payment(LOAN, principal=6000000, date=f'{P3}-05')
row = next(r for r in list_loans() if r['id'] == LOAN)
check('the loan closes itself when the principal is repaid',
      row['status'] == 'yopilgan' and close(row['ledger_balance'], 0))
void_document(PAY2)
sync_loan_status(LOAN)
check('voiding a repayment reopens it', next(r for r in list_loans() if r['id'] == LOAN)['status'] == 'ochiq')
summ = loan_summary()
check('summary splits taken and given with outstanding per side',
      summ['olgan']['open'] == 1 and close(summ['olgan']['outstanding'], 6000000)
      and summ['bergan']['open'] == 0)
LOAN2 = save_loan({'loan_type': 'olgan', 'counterparty_id': BANK, 'description': 'ikkinchi',
                   'total_amount': 1000000, 'currency': 'UZS', 'interest_rate': 0,
                   'term': 'short', 'issue_date': f'{P3}-01', 'due_date': None,
                   'status': 'ochiq', 'notes': None})
post_loan_issue({'id': LOAN2, 'loan_type': 'olgan', 'counterparty_id': BANK, 'currency': 'UZS',
                 'total_amount': 1000000, 'issue_date': f'{P3}-01', 'description': 'ikkinchi'})
check('two loans with one counterparty keep separate balances',
      close(loan_balance(LOAN), 6000000) and close(loan_balance(LOAN2), 1000000))
check('books balance after the loan flow', get_trial_balance()['is_balanced'])

print('\n=== Documents: paging, sorting, responsible ===')
for i in range(6):
    d = save_document({'doc_type': 'sales_invoice', 'date': f'{P3}-{10 + i:02d}',
                       'counterparty_id': CLIENT, 'project_id': OTHER if i % 2 else PROJECT,
                       'responsible_id': STAFF['Bekzod'] if i == 2 else None,
                       'notes': 'sirli belgi' if i == 4 else None},
                      lines=[{'description': f'L{i}', 'amount': 1000000 * (i + 1), 'vat_rate': 0}])
    post_document(d)
total = count_documents(doc_type='sales_invoice')
check('count matches the unpaged list', total == len(list_documents(doc_type='sales_invoice', limit=1000)))
page1 = list_documents(doc_type='sales_invoice', limit=3, offset=0, sort='total', sort_dir='asc')
page2 = list_documents(doc_type='sales_invoice', limit=3, offset=3, sort='total', sort_dir='asc')
check('pages do not overlap and cover the set',
      not {r['id'] for r in page1} & {r['id'] for r in page2}
      and len(page1) + len(page2) == min(total, 6))
check('sort by total ascending holds across pages',
      [r['total'] for r in page1 + page2] == sorted(r['total'] for r in page1 + page2))
desc = list_documents(doc_type='sales_invoice', sort='total', sort_dir='desc', limit=2)
check('sort direction flips', desc[0]['total'] >= desc[1]['total'])
check('an unknown sort key falls back to date',
      list_documents(doc_type='sales_invoice', sort='drop table', limit=1)[0]['date'] >= f'{P3}-10')
by_resp = list_documents(doc_type='sales_invoice', responsible_id=STAFF['Bekzod'])
check('responsible is stored and filterable',
      len(by_resp) == 1 and by_resp[0]['responsible_name'] == 'Bekzod')
check('count honours the same filter', count_documents(doc_type='sales_invoice',
                                                        responsible_id=STAFF['Bekzod']) == 1)
check('project filter works', all(r['project_id'] == OTHER for r in
                                  list_documents(doc_type='sales_invoice', project_id=OTHER)))
check('search reaches notes', len(list_documents(doc_type='sales_invoice', search='sirli')) == 1)
check('search reaches the project name',
      len(list_documents(doc_type='sales_invoice', search='Villa')) >= 3)
check('outstanding sort works', list_documents(doc_type='sales_invoice', sort='outstanding',
                                               sort_dir='desc', limit=1)[0]['outstanding'] > 0)

print('\n=== Payment-method summary reconciles with the cash flow ===')
CASH = save_document({'doc_type': 'cash_in', 'date': f'{P3}-20', 'counterparty_id': CLIENT,
                      'total': 2000000, 'payment_method': 'naqd'},
                     allocations=[{'invoice_doc_id': INV, 'amount': 2000000}])
post_document(CASH)
pm = payment_method_summary()
cf = get_cash_flow()
check('per-method totals sum to the cash flow',
      close(pm['total_in'], cf['total_in'], 1.0) and close(pm['total_out'], cf['total_out'], 1.0),
      f"{pm['total_in']}/{cf['total_in']} {pm['total_out']}/{cf['total_out']}")
check('cash method appears with its label',
      any(r['method'] == 'naqd' and close(r['cash_in'], 2000000) and r['label_en'] for r in pm['rows']))

print('\n=== Per-project FX gain / loss ===')
conn = get_db()
conn.execute("INSERT INTO exchange_rates (date, rate) VALUES (?,?)"
             " ON CONFLICT(date) DO UPDATE SET rate=excluded.rate", (f'{P2}-01', 12000))
conn.commit()
conn.close()
USD = save_document({'doc_type': 'sales_invoice', 'date': f'{P2}-02', 'counterparty_id': CLIENT,
                     'project_id': OTHER, 'currency': 'USD', 'exchange_rate': 12000,
                     'total_cur': 1000},
                    lines=[{'description': 'usd', 'amount': 12000000, 'vat_rate': 0}])
post_document(USD)
conn = get_db()
conn.execute("INSERT INTO exchange_rates (date, rate) VALUES (?,?)"
             " ON CONFLICT(date) DO UPDATE SET rate=excluded.rate", (TODAY.isoformat(), 13000))
conn.commit()
conn.close()
fx = calculate_fx_gain_loss(OTHER)
check('USD invoice gains when the som weakens', close(fx, 1000 * (13000 - 12000), 1.0), str(fx))
check('a UZS-only project has no FX', calculate_fx_gain_loss(PROJECT) == 0)
check('project cost carries the FX figure',
      close(calculate_project_cost(OTHER)['fx_gain_loss'], fx, 1.0))

print('\n=== Project list and dashboard extras ===')
lst = list_projects(sort='hours')
check('projects sorted by lifetime hours', lst[0]['total_hours'] >= lst[-1]['total_hours']
      and 'staff_count' in lst[0] and 'ms_late' in lst[0])
check('late milestone count is reported', next(p for p in lst if p['id'] == PROJECT)['ms_late'] >= 1)
top = get_top_projects(5)
check('top projects come with the full profitability picture',
      top and {'hours', 'total_cost', 'invoiced', 'profit', 'fx_gain_loss'} <= set(top[0]))
dash = get_dashboard()
check('dashboard carries the v4 tiles',
      {'on_course', 'top_projects', 'fx', 'loans', 'staff', 'cfo', 'ar_buckets'} <= set(dash))
check('break-even is fixed cost over margin',
      dash['cfo']['breakeven_revenue'] > 0 and dash['cfo']['revenue_per_employee'] >= 0)

print('\n=== Periods: create, notes, delete, hard-close lock ===')
ok, err = create_fiscal_period('2031-03', 'test')
check('a period can be opened ahead of time', ok and get_period_status('2031-03') == 'open')
check('duplicate refused', create_fiscal_period('2031-03') == (False, 'exists'))
check('bad code refused', create_fiscal_period('2031-13') == (False, 'bad_code')
      and create_fiscal_period('abc') == (False, 'bad_code'))
set_period_notes('2031-03', 'yangi izoh')
per = next(p for p in list_periods() if p['code'] == '2031-03')
check('notes saved and the list carries snapshot counts',
      per['notes'] == 'yangi izoh' and {'staff_snapshots', 'hours_snapped', 'hours_total'} <= set(per))
check('an empty open period can be deleted', delete_fiscal_period('2031-03') == (True, None)
      and get_period_status('2031-03') is None)
check('a period with entries cannot be deleted',
      delete_fiscal_period(P3) == (False, 'has_entries'))
check('a missing period reports not found', delete_fiscal_period('2040-01') == (False, 'not_found'))
res, err = close_period(P1, status='soft_closed')
check('soft close works', err is None and get_period_status(P1) == 'soft_closed')
check('a soft-closed period reopens', reopen_period(P1) is True and get_period_status(P1) == 'open')
res, err = close_period(P1, status='hard_closed')
check('hard close works', err is None and get_period_status(P1) == 'hard_closed')
check('a hard-closed period is refused by default',
      reopen_period(P1) is False and get_period_status(P1) == 'hard_closed')
check('a scripted correction may still unlock it', reopen_period(P1, allow_hard=True) is True)
check('books balance at the end', get_trial_balance()['is_balanced'])

print('\n' + '=' * 46)
print(f'  passed: {PASS}   failed: {FAIL}')
print('=' * 46)
sys.exit(1 if FAIL else 0)
