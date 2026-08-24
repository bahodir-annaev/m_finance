# -*- coding: utf-8 -*-
"""Ledger core tests — balance enforcement, trial balance, periods, reversal.

Standalone script (house style): builds a temp database, no server needed.

    python test_ledger.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DB_FILE = os.path.join(tempfile.gettempdir(), 'mizan5_test_ledger.db')
os.environ['MIZAN5_DB'] = DB_FILE
os.environ['MIZAN_ADMIN_PASSWORD'] = 'test'
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

from models.base import init_db, get_db, BALANCE_EPSILON          # noqa: E402
from models import ledger                                          # noqa: E402
from models.ledger import (                                        # noqa: E402
    PostingError, post_entry, reverse_entry, account_id_for,
    get_trial_balance, account_balance, verify_all_entries,
    natural_side, pnl_section,
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


def expect_error(label, key, fn, *args, **kwargs):
    """Assert fn raises PostingError with the given translation key."""
    try:
        fn(*args, **kwargs)
    except PostingError as e:
        check(label, e.key == key, f'got {e.key}, wanted {key}')
        return
    except Exception as e:                                          # noqa: BLE001
        check(label, False, f'wrong exception: {type(e).__name__}: {e}')
        return
    check(label, False, 'no exception raised')


init_db()
CASH = account_id_for('cash_bank')
AR = account_id_for('ar')
REVENUE = account_id_for('revenue')
VAT_OUT = account_id_for('vat_output')

print('\n=== Account classification ===')
check('asset 5110 is debit-normal', natural_side('5110', 'A') == 'debit')
check('liability 6010 is credit-normal', natural_side('6010', 'P') == 'credit')
check('contra-asset 0200 is credit-normal', natural_side('0200', 'KA') == 'credit')
check('income 9030 is credit-normal', natural_side('9030', 'T') == 'credit')
check('expense 9420 is debit-normal', natural_side('9420', 'T') == 'debit')
check('9030 classified as income', pnl_section('9030') == 'income')
check('9130 classified as expense', pnl_section('9130') == 'expense')
check('9420 classified as expense', pnl_section('9420') == 'expense')
check('9910 classified as result', pnl_section('9910') == 'result')

print('\n=== post_entry: balance enforcement ===')
conn = get_db()
expect_error('unbalanced entry is refused', 'ledger_unbalanced',
             post_entry, conn, '2026-03-10',
             [{'account_id': CASH, 'debit': 1000},
              {'account_id': REVENUE, 'credit': 900}])
expect_error('empty entry is refused', 'ledger_empty_entry',
             post_entry, conn, '2026-03-10', [])
expect_error('entry with no date is refused', 'ledger_no_date',
             post_entry, conn, '', [{'account_id': CASH, 'debit': 1}])
expect_error('negative amount is refused', 'ledger_negative_amount',
             post_entry, conn, '2026-03-10',
             [{'account_id': CASH, 'debit': -5},
              {'account_id': REVENUE, 'credit': -5}])
expect_error('debit and credit on one line is refused', 'ledger_both_sides',
             post_entry, conn, '2026-03-10',
             [{'account_id': CASH, 'debit': 5, 'credit': 5}])
expect_error('line without an account is refused', 'ledger_no_account',
             post_entry, conn, '2026-03-10',
             [{'debit': 5}, {'account_id': REVENUE, 'credit': 5}])
conn.rollback()

# A balanced entry goes through, and sub-so'm rounding is tolerated.
entry_id = post_entry(conn, '2026-03-10', [
    {'account_id': CASH, 'debit': 1120000},
    {'account_id': REVENUE, 'credit': 1000000},
    {'account_id': VAT_OUT, 'credit': 120000},
], memo='test sale')
conn.commit()
check('balanced entry is accepted', entry_id > 0)

tolerated = post_entry(conn, '2026-03-11', [
    {'account_id': CASH, 'debit': 100.00},
    {'account_id': REVENUE, 'credit': 99.40},
], memo='rounding tolerance')
conn.commit()
check('sub-epsilon rounding difference is tolerated', tolerated > 0,
      f'epsilon={BALANCE_EPSILON}')
conn.close()

print('\n=== Balances ===')
check('cash balance is the debit total',
      abs(account_balance('5110') - 1120100) < 0.01,
      f"got {account_balance('5110')}")
check('revenue balance is positive on its natural (credit) side',
      abs(account_balance('9030') - 1000099.40) < 0.01,
      f"got {account_balance('9030')}")

print('\n=== Trial balance ===')
tb = get_trial_balance()
check('trial balance is balanced', tb['is_balanced'],
      f"debit {tb['totals']['close_d']:.2f} vs credit {tb['totals']['close_c']:.2f}")
check('turnover debit equals turnover credit',
      abs(tb['totals']['turn_d'] - tb['totals']['turn_c']) < BALANCE_EPSILON)
check('every posted account appears', len(tb['rows']) == 3,
      f"rows: {[r['code'] for r in tb['rows']]}")

print('\n=== Fiscal period enforcement ===')
conn = get_db()
conn.execute("UPDATE fiscal_periods SET status='hard_closed' WHERE code='2026-03'")
conn.commit()
expect_error('posting into a closed period is refused', 'ledger_period_closed',
             post_entry, conn, '2026-03-15',
             [{'account_id': CASH, 'debit': 10}, {'account_id': REVENUE, 'credit': 10}])
open_ok = post_entry(conn, '2026-04-01',
                     [{'account_id': CASH, 'debit': 10},
                      {'account_id': REVENUE, 'credit': 10}], memo='open period')
conn.commit()
check('posting into an open period still works', open_ok > 0)
conn.execute("UPDATE fiscal_periods SET status='open' WHERE code='2026-03'")
conn.commit()
conn.close()

print('\n=== Reversal is a perfect mirror ===')
before = {code: account_balance(code) for code in ('5110', '9030', '6410.1')}
conn = get_db()
rev_id = reverse_entry(conn, entry_id, memo='storno test')
conn.commit()
conn.close()
after = {code: account_balance(code) for code in ('5110', '9030', '6410.1')}
check('cash returns to pre-entry level',
      abs((before['5110'] - after['5110']) - 1120000) < 0.01)
check('revenue returns to pre-entry level',
      abs((before['9030'] - after['9030']) - 1000000) < 0.01)
check('VAT returns to zero', abs(after['6410.1']) < 0.01)
check('trial balance still balances after reversal', get_trial_balance()['is_balanced'])

conn = get_db()
expect_error('double reversal is refused', 'ledger_already_reversed',
             reverse_entry, conn, entry_id)
conn.close()

print('\n=== Integrity sweep ===')
bad = verify_all_entries()
check('no unbalanced entry exists in the database', not bad, str(bad))

print(f'\n{"=" * 46}\n  passed: {PASS}   failed: {FAIL}\n{"=" * 46}')
sys.exit(1 if FAIL else 0)
