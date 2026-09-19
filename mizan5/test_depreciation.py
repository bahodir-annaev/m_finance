# -*- coding: utf-8 -*-
"""Depreciation tests — schedule, posting, idempotency, and NO DOUBLE COUNT.

The headline assertion is that posting depreciation must not move a single
staff cost rate. The equipment register already charges these assets to the
rate engine; the ledger posting exists only for the financial statements. If
the expense account ever joined the overhead pool, every asset would be
counted twice — that is what section "No double count" pins.

    python test_depreciation.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DB_FILE = os.path.join(tempfile.gettempdir(), 'mizan5_test_depreciation.db')
os.environ['MIZAN5_DB'] = DB_FILE
os.environ['MIZAN_ADMIN_PASSWORD'] = 'test'
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

from models.base import init_db, get_db, get_period_status                # noqa: E402
from models.ledger import (                                              # noqa: E402
    account_balance, account_id_for, accounts_in_pool, get_account_by_code,
    get_trial_balance, natural_side, pnl_section, verify_all_entries,
)
from models.depreciation import (                                        # noqa: E402
    depreciation_schedule, depreciation_preview, depreciation_posted_entry,
    post_period_depreciation, reverse_period_depreciation, period_bounds,
    _accumulated, _month_index,
)
from models.reports import (                                             # noqa: E402
    close_period, reopen_period, get_pnl, get_balance_sheet,
)
from models.staff import (                                               # noqa: E402
    rate_context, calculate_hourly_rate, get_rates_overview,
    ledger_overhead_monthly, personal_equipment_monthly,
    general_equipment_monthly, depreciation_reconciliation,
)
from models.ledger import PostingError                                   # noqa: E402

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


ACCUM_CODES = ('0200', '0220.1', '0230', '0240', '0250', '0260', '0290')


def total_accumulated():
    """Every accumulated-depreciation account summed.

    Depreciation credits the contra account of the asset's CLASS (0250 for
    computers, 0240 for furniture...), not one lump on 0200, so a test that
    reads 0200 alone now sees zero. The invariant worth pinning is that the
    charge lands somewhere in the 02xx family and equals the debit.
    """
    return sum(account_balance(c) for c in ACCUM_CODES)


# ── Fixture ─────────────────────────────────────────────────────────────────
# Purchase dates are fixed and far from today on purpose: nothing here may
# depend on the wall clock, unlike the rate engine's NOT_EXPIRED predicate.
init_db()
conn = get_db()
STAFF = {}
for name, base in (('Aziz', 9000000), ('Bekzod', 5000000), ('Dilnoza', 14000000)):
    sid = conn.execute(
        "INSERT INTO staff (name, role, department, staff_type)"
        " VALUES (?,?,?,'production')", (name, 'Arxitektor', 'Arxitektura')).lastrowid
    conn.execute("INSERT INTO salary_history (staff_id, base_salary, premium, start_date)"
                 " VALUES (?,?,0,'2025-01-01')", (sid, base))
    STAFF[name] = sid
EQUIPMENT = [
    # (name, kind, staff, qty, price, life, purchase_date, is_active)
    ('Laptop',      'personal', 'Aziz',    1, 15000000, 36, '2026-03-15', 1),
    ('Server',      'general',  None,      1, 30000000, 60, '2025-01-10', 1),
    ('Workstation', 'personal', 'Dilnoza', 1, 24000000, 12, '2025-08-01', 1),
    ('Printer',     'general',  None,      2,  6000000, 36, '2025-09-01', 1),
    ('Monitor',     'personal', 'Aziz',    1,  9000000, 24, None,         1),
    ('Plotter',     'general',  None,      1,  5000000, 24, '2030-01-01', 1),
    ('Eski PC',     'general',  None,      1,  4000000, 24, '2025-01-01', 0),
    ('Skaner',      'general',  None,      1,  3000000, 24, '01.06.2025', 1),
]
EQ = {}
for name, kind, staff, qty, price, life, pdate, active in EQUIPMENT:
    EQ[name] = conn.execute(
        "INSERT INTO equipment (name, kind, staff_id, quantity, price,"
        " lifespan_months, purchase_date, is_active) VALUES (?,?,?,?,?,?,?,?)",
        (name, kind, STAFF.get(staff), qty, price, life, pdate, active)).lastrowid
conn.commit()
conn.close()

print('\n=== Chart of accounts ===')
expense = get_account_by_code('9420.1')
check('the depreciation expense account is seeded', expense is not None)
check('it classifies as an expense in the P&L', pnl_section('9420.1') == 'expense')
check('it is debit-normal', natural_side('9420.1', 'T') == 'debit')
check('THE CRITICAL FLAG: it is NOT in the indirect pool',
      account_id_for('depreciation_expense') not in accounts_in_pool('indirect'),
      f"cost_pool={expense['cost_pool']}")
pool_codes = {get_account_by_code(c)['code'] for c in ('9410', '9420', '9430')}
actual_pool = set()
conn = get_db()
for aid in accounts_in_pool('indirect'):
    actual_pool.add(conn.execute("SELECT code FROM accounts WHERE id=?",
                                 (aid,)).fetchone()['code'])
conn.close()
check('the indirect pool is still exactly 9410/9420/9430',
      actual_pool == pool_codes, str(sorted(actual_pool)))
check('accumulated depreciation maps to 0200 and is credit-normal',
      get_account_by_code('0200')['id'] == account_id_for('equipment_depreciation')
      and natural_side('0200', 'KA') == 'credit')
check('every asset class has a credit-normal contra account',
      all(get_account_by_code(c) and natural_side(c, 'KA') == 'credit'
          for c in ACCUM_CODES), str(ACCUM_CODES))

print('\n=== Month arithmetic ===')
check('the purchase month is month 1', _month_index('2026-03-15', '2026-03') == 1)
check('the next month is month 2', _month_index('2026-03-15', '2026-04') == 2)
check('a period before purchase is <= 0', _month_index('2026-03-15', '2026-02') == 0)
check('accumulation is capped at cost', _accumulated(15000000, 36, 99) == 15000000)
check('the final month lands exactly on cost',
      _accumulated(15000000, 36, 36) == 15000000)
check('a non-terminating division still ends exactly on cost',
      close_to(sum(_accumulated(15000000, 36, n) - _accumulated(15000000, 36, n - 1)
                   for n in range(1, 37)), 15000000, 0.001))

print('\n=== Schedule is as-of aware ===')
s = depreciation_schedule('2026-06')
rows = {r['name']: r for r in s['rows']}
skipped = {r['name']: r['reason'] for r in s['skipped']}
check('an asset bought in the future is not depreciated',
      'Plotter' in skipped and skipped['Plotter'] == 'not_yet_purchased')
check('an inactive asset is never depreciated', 'Eski PC' not in rows
      and 'Eski PC' not in skipped)
check('an asset with no purchase date is reported, not dropped',
      skipped.get('Monitor') == 'no_purchase_date')
check('a malformed purchase date is reported, not raised',
      skipped.get('Skaner') == 'bad_purchase_date')
check('the charge is cost / lifespan', close_to(rows['Server']['charge'], 500000))
check('quantity multiplies the cost',
      close_to(rows['Printer']['charge'], 2 * 6000000 / 36))
check('the period total is the sum of its rows',
      close_to(s['total'], 416666.67 + 500000 + 2000000 + 333333.33),
      str(s['total']))
check('an asset is charged in its own purchase month',
      any(r['name'] == 'Laptop' for r in depreciation_schedule('2026-03')['rows']))
check('an asset is not charged the month before purchase',
      not any(r['name'] == 'Laptop' for r in depreciation_schedule('2026-02')['rows']))

check('an asset stops at the end of its life',
      any(r['name'] == 'Workstation' for r in depreciation_schedule('2026-07')['rows'])
      and not any(r['name'] == 'Workstation'
                  for r in depreciation_schedule('2026-08')['rows']))
check('the final month is flagged',
      next(r for r in depreciation_schedule('2026-07')['rows']
           if r['name'] == 'Workstation')['is_final_month'])

historic = depreciation_schedule('2025-02')
hist_names = {r['name'] for r in historic['rows']}
check('a historic period charges only what existed then',
      hist_names == {'Server'}, str(sorted(hist_names)))
check('the schedule is as-of, never wall-clock',
      close_to(historic['total'], 500000), str(historic['total']))

print('\n=== Accumulated never exceeds cost ===')
laptop_total = 0.0
for year, month in [(y, m) for y in (2026, 2027, 2028, 2029) for m in range(1, 13)]:
    p = f'{year}-{month:02d}'
    for r in depreciation_schedule(p)['rows']:
        if r['name'] == 'Laptop':
            laptop_total += r['charge']
            check_cap = r['accumulated_after'] <= 15000000 + 0.001
            if not check_cap:
                break
check('summing a full life gives exactly the cost',
      close_to(laptop_total, 15000000, 0.001), f'{laptop_total:.4f}')
check('no single charge exceeds the monthly amount',
      all(r['charge'] <= r['monthly'] + 0.01
          for p in ('2026-03', '2026-06', '2029-02')
          for r in depreciation_schedule(p)['rows']))

print('\n=== Preview writes nothing ===')
conn = get_db()
before_entries = conn.execute("SELECT COUNT(*) AS n FROM journal_entries").fetchone()['n']
conn.close()
prev = depreciation_preview('2026-06')
conn = get_db()
after_entries = conn.execute("SELECT COUNT(*) AS n FROM journal_entries").fetchone()['n']
conn.close()
check('preview creates no journal entry', before_entries == after_entries)
check('preview reports it has not been posted yet', prev['already_posted'] is False)
check('preview total matches the schedule', close_to(prev['total'], s['total']))
check('preview names the accounts it would use',
      prev['expense_code'] == '9420.1' and prev['accum_code'] == '0200')
check('preview names the per-class contra accounts it will credit',
      prev['accum_codes'] == ['0250'], str(prev['accum_codes']))

print('\n=== Posting ===')
entry_id, total = post_period_depreciation('2026-06')
check('posting returns an entry and a total', entry_id and total > 0, str(total))
check('the expense account is debited',
      close_to(account_balance('9420.1'), total), str(account_balance('9420.1')))
check('accumulated depreciation is credited',
      close_to(total_accumulated(), total), str(total_accumulated()))
check('it is credited to the CLASS account, not the 0200 parent',
      close_to(account_balance('0250'), total) and close_to(account_balance('0200'), 0),
      f"0250={account_balance('0250')} 0200={account_balance('0200')}")
check('the posted total equals the schedule', close_to(total, s['total']))

conn = get_db()
entry = conn.execute("SELECT * FROM journal_entries WHERE id=?", (entry_id,)).fetchone()
lines = conn.execute("SELECT * FROM journal_lines WHERE entry_id=? ORDER BY line_no",
                     (entry_id,)).fetchall()
conn.close()
check('the memo carries the period', entry['memo'] == 'Amortizatsiya 2026-06',
      entry['memo'])
check('it is a routine, not a document', entry['document_id'] is None)
check('personal-asset lines carry staff_id',
      any(l['staff_id'] == STAFF['Aziz'] for l in lines if l['debit']))
check('the general line carries no staff',
      any(l['staff_id'] is None for l in lines if l['debit']))
check('debits and credits are equal',
      close_to(sum(l['debit'] for l in lines), sum(l['credit'] for l in lines), 0.001))
check('no unbalanced entry exists', not verify_all_entries())
check('trial balance still balances', get_trial_balance()['is_balanced'])
check('balance sheet still balances', get_balance_sheet()['is_balanced'])

start, end = period_bounds('2026-06')
check('the P&L now shows a depreciation expense',
      any(e['code'] == '9420.1' for e in get_pnl(start, end)['expense']))
check('the reconciliation no longer reads zero',
      depreciation_reconciliation()['ledger_accumulated'] > 0)

print('\n=== Idempotency ===')
again_id, again_total = post_period_depreciation('2026-06')
check('posting the same period twice is a no-op',
      again_id is None and again_total == 0.0)
check('the balance did not move', close_to(total_accumulated(), total))
conn = get_db()
live = conn.execute(
    "SELECT COUNT(*) AS n FROM journal_entries WHERE period='2026-06'"
    " AND memo LIKE 'Amortizatsiya%'").fetchone()['n']
conn.close()
check('exactly one depreciation entry exists for the period', live == 1, str(live))
check('preview now reports it as posted',
      depreciation_preview('2026-06')['already_posted'] is True)

other_id, other_total = post_period_depreciation('2026-05')
check('a different period still posts', other_id is not None and other_total > 0)

rev_id = reverse_period_depreciation('2026-05')
check('reversal returns the storno id', rev_id is not None)
check('reversal clears the charge',
      close_to(total_accumulated(), total), str(total_accumulated()))
redo_id, redo_total = post_period_depreciation('2026-05')
check('a reversed period can be posted again',
      redo_id is not None and close_to(redo_total, other_total))
reverse_period_depreciation('2026-05')
check('reversing when nothing is live returns None',
      reverse_period_depreciation('2026-05') is None)
check('reversing an untouched period returns None',
      reverse_period_depreciation('2024-01') is None)

print('\n=== NO DOUBLE COUNT (the reason this design exists) ===')
ctx = rate_context()
before_rates = {n: calculate_hourly_rate(sid, context=ctx)['cost_rate']
                for n, sid in STAFF.items()}
before_pool = ledger_overhead_monthly()['total']
before_overview = get_rates_overview()['indirect_pool_total']
before_personal = personal_equipment_monthly(STAFF['Aziz'])
before_general = general_equipment_monthly()

for p in ('2026-07', '2026-08', '2026-09'):
    post_period_depreciation(p)

ctx2 = rate_context()
after_rates = {n: calculate_hourly_rate(sid, context=ctx2)['cost_rate']
               for n, sid in STAFF.items()}
for name in STAFF:
    check(f'{name}: cost rate is unchanged by depreciation postings',
          close_to(before_rates[name], after_rates[name]),
          f'{before_rates[name]:.4f} -> {after_rates[name]:.4f}')
check('the overhead pool total is unchanged',
      close_to(before_pool, ledger_overhead_monthly()['total']))
check('no depreciation account appears in the pool breakdown',
      not any(b['code'].startswith('9420.1')
              for b in ledger_overhead_monthly()['breakdown']))
check('the reported indirect pool is unchanged',
      close_to(before_overview, get_rates_overview()['indirect_pool_total']))
check('the register is still the rate engine source',
      close_to(before_personal, personal_equipment_monthly(STAFF['Aziz']))
      and close_to(before_general, general_equipment_monthly()))

# Negative control: prove the guard fires rather than merely trusting the flag.
conn = get_db()
conn.execute("UPDATE accounts SET cost_pool='indirect' WHERE code='9420.1'")
conn.commit()
conn.close()
try:
    post_period_depreciation('2026-10')
    check('a pooled depreciation account is refused', False, 'no exception raised')
except PostingError as e:
    check('a pooled depreciation account is refused',
          e.key == 'account_in_pool', e.key)
conn = get_db()
conn.execute("UPDATE accounts SET cost_pool='excluded' WHERE code='9420.1'")
conn.commit()
conn.close()
check('preview degrades gracefully rather than crashing',
      depreciation_preview('2026-10')['error'] is None)

print('\n=== Period close integration ===')
result, err = close_period('2026-11')
check('close succeeds', err is None, str(err))
check('close posts depreciation', result['depreciation_entry_id'] is not None)
check('close reports the depreciation total', result['depreciation_total'] > 0)
check('THE ORDERING TEST: depreciation was swept into 9910 by the same close',
      close_to(account_balance('9420.1', as_of='2026-11-30'),
               account_balance('9420.1', as_of='2026-10-31')),
      'expense left stranded inside the closed period')
check('the period is closed', get_period_status('2026-11') == 'hard_closed')
check('trial balance balances after the close', get_trial_balance()['is_balanced'])

result2, err2 = close_period('2026-11')
check('closing twice is refused', err2 == 'already_closed')

result3, err3 = close_period('2026-12', post_depreciation=False)
check('depreciation can be skipped at close',
      err3 is None and result3['depreciation_entry_id'] is None)
check('nothing was posted for the skipped period',
      depreciation_posted_entry('2026-12') is None)

print('\n=== Reopen ===')
accum_before_reopen = total_accumulated()
reopen_period('2026-11', allow_hard=True)
check('the period is open again', get_period_status('2026-11') == 'open')
check('reopen reversed the depreciation charge',
      total_accumulated() < accum_before_reopen,
      f"{accum_before_reopen} -> {total_accumulated()}")
check('the depreciation entry is no longer live',
      depreciation_posted_entry('2026-11') is None)
check('trial balance balances after reopen', get_trial_balance()['is_balanced'])

reclose, err4 = close_period('2026-11')
check('re-closing re-posts depreciation from the register',
      err4 is None and reclose['depreciation_entry_id'] is not None)
check('the re-posted total matches the original',
      close_to(reclose['depreciation_total'], result['depreciation_total']))

print('\n=== Period guards ===')
# 2026-11 is closed AND already posted, so the idempotency guard short-circuits
# first — a no-op, not an error. 2026-12 was closed with post_depreciation=False,
# so it is the case that actually reaches post_entry's closed-period check.
check('an already-posted closed period is a quiet no-op',
      post_period_depreciation('2026-11') == (None, 0.0))
try:
    post_period_depreciation('2026-12')
    check('posting into a closed period is refused', False, 'no exception')
except PostingError as e:
    check('posting into a closed period is refused',
          e.key == 'ledger_period_closed', e.key)

empty_id, empty_total = post_period_depreciation('2020-01')
check('a period with nothing eligible posts nothing',
      empty_id is None and empty_total == 0.0)

print('\n=== Integrity ===')
check('no unbalanced entry in the database', not verify_all_entries())
check('final trial balance is balanced', get_trial_balance()['is_balanced'])
check('final balance sheet balances', get_balance_sheet()['is_balanced'])

print(f'\n{"=" * 46}\n  passed: {PASS}   failed: {FAIL}\n{"=" * 46}')
sys.exit(1 if FAIL else 0)
