# -*- coding: utf-8 -*-
"""Bank-account subdivision tests — the «Банковские счета» subconto.

    python test_bank_accounts.py

Pins: a ledger account with no bank accounts posts as before; once one is
registered the analytic is required and checked; balances per bank account
plus the unassigned remainder equal the ledger balance; a storno mirrors the
analytic; the one-time backfill moves no amount.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DB_FILE = os.path.join(tempfile.gettempdir(), 'mizan5_test_bank_accounts.db')
os.environ['MIZAN5_DB'] = DB_FILE
os.environ['MIZAN_ADMIN_PASSWORD'] = 'test'
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

from models.base import init_db, get_db                             # noqa: E402
from models.ledger import (                                         # noqa: E402
    account_balance, get_trial_balance, balances_by_analytic, account_id_for,
    get_account_ledger, PostingError,
)
from models.documents import (                                      # noqa: E402
    DocumentError, save_document, post_document, void_document, get_document,
)
from models.bank_accounts import (                                  # noqa: E402
    save_bank_account, list_bank_accounts, bank_account_balances,
    assign_unassigned_lines, default_bank_account_id,
)
from models.reports import cash_by_account, cash_balance            # noqa: E402

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
    try:
        fn(*args, **kwargs)
    except (DocumentError, PostingError) as e:
        check(label, e.key == key, f'got {e.key}, wanted {key}')
        return
    except Exception as e:                                          # noqa: BLE001
        check(label, False, f'wrong exception: {type(e).__name__}: {e}')
        return
    check(label, False, 'no exception raised')


def near(a, b):
    return abs(a - b) < 0.01


init_db()
conn = get_db()
VENDOR = conn.execute(
    "INSERT INTO counterparties (name, counterparty_type) VALUES ('Beta Servis', 'vendor')"
).lastrowid
conn.execute("INSERT OR REPLACE INTO exchange_rates (date, rate) VALUES ('2026-01-01', 12500)")
conn.commit()
conn.close()

BANK = account_id_for('cash_bank')   # 5110
FX = account_id_for('cash_fx')       # 5210
TILL = account_id_for('cash_till')   # 5010


def cash_out(total, bank_account_id=None, currency='UZS', method='bank', date='2026-03-10'):
    doc = save_document({
        'doc_type': 'cash_out', 'date': date, 'counterparty_id': VENDOR,
        'payment_method': method, 'currency': currency, 'total': total,
        'bank_account_id': bank_account_id, 'description': 'test'})
    post_document(doc)
    return doc


print('\n=== Schema: columns exist on a fresh DB and on an old one ===')
conn = get_db()
cols = [d[1] for d in conn.execute("PRAGMA table_info(journal_lines)")]
check('journal_lines.bank_account_id exists', 'bank_account_id' in cols)
cols = [d[1] for d in conn.execute("PRAGMA table_info(documents)")]
check('documents.bank_account_id exists', 'bank_account_id' in cols)
# Simulate a database created before the column shipped: drop it and re-run init_db.
conn.execute("DROP INDEX idx_jl_bank_account")
conn.execute("ALTER TABLE journal_lines DROP COLUMN bank_account_id")
conn.execute("ALTER TABLE documents DROP COLUMN bank_account_id")
conn.commit()
conn.close()
init_db()
conn = get_db()
cols = [d[1] for d in conn.execute("PRAGMA table_info(journal_lines)")]
check('init_db() re-adds journal_lines.bank_account_id on an old DB', 'bank_account_id' in cols)
cols = [d[1] for d in conn.execute("PRAGMA table_info(documents)")]
check('init_db() re-adds documents.bank_account_id on an old DB', 'bank_account_id' in cols)
conn.close()


print('\n=== Before any bank account is registered: nothing changes ===')
legacy = cash_out(300000)
check('cash_out posts with no bank account', get_document(legacy)['status'] == 'posted')
check('5110 credited', near(account_balance(account_id=BANK), -300000))
check('no bank accounts listed', list_bank_accounts() == [])
check('default bank account is None', default_bank_account_id(BANK) is None)


print('\n=== Register: two UZS accounts on 5110, one USD on 5210 ===')
main = save_bank_account({'account_id': BANK, 'name': 'Asosiy', 'account_number': '2020 8000 1234 5678 9012',
                          'bank_name': 'Kapitalbank', 'mfo': '01088', 'currency': 'UZS',
                          'is_default': 1, 'is_active': 1})
second = save_bank_account({'account_id': BANK, 'name': 'Ikkinchi', 'currency': 'UZS',
                            'is_default': 0, 'is_active': 1})
usd = save_bank_account({'account_id': FX, 'name': 'Valyuta', 'currency': 'USD',
                         'is_default': 1, 'is_active': 1})
check('three bank accounts listed', len(list_bank_accounts()) == 3)
check('default for 5110 is the flagged one', default_bank_account_id(BANK) == main)
save_bank_account({'account_id': BANK, 'name': 'Ikkinchi', 'currency': 'UZS',
                   'is_default': 1, 'is_active': 1}, second)
check('setting a new default clears the old one', default_bank_account_id(BANK) == second
      and not [b for b in list_bank_accounts() if b['id'] == main][0]['is_default'])
save_bank_account({'account_id': BANK, 'name': 'Asosiy', 'currency': 'UZS',
                   'is_default': 1, 'is_active': 1}, main)


print('\n=== Posting rule ===')
d1 = cash_out(100000, main)
d2 = cash_out(50000, second)
d3 = cash_out(250000, usd, currency='USD')   # 20 USD at 12 500
check('money line carries the bank account',
      get_db().execute("SELECT bank_account_id FROM journal_lines WHERE account_id=?"
                       " AND entry_id=(SELECT entry_id FROM documents WHERE id=?)",
                       (BANK, d1)).fetchone()[0] == main)
check('document detail joins the bank account',
      get_document(d1)['bank_account_number'] == '2020 8000 1234 5678 9012')
check('USD document lands on 5210 through its bank account',
      near(account_balance(account_id=FX), -250000))

draft = save_document({'doc_type': 'cash_out', 'date': '2026-03-11', 'counterparty_id': VENDOR,
                       'payment_method': 'bank', 'total': 1000})
expect_error('5110 now refuses a line without a bank account', 'bank_account_required',
             post_document, draft)
check('the refused document stays a draft', get_document(draft)['status'] == 'draft')

draft = save_document({'doc_type': 'cash_out', 'date': '2026-03-11', 'counterparty_id': VENDOR,
                       'payment_method': 'bank', 'total': 1000, 'bank_account_id': usd})
expect_error('a USD bank account on a UZS document is refused', 'bank_account_currency',
             post_document, draft)

draft = save_document({'doc_type': 'cash_out', 'date': '2026-03-11', 'counterparty_id': VENDOR,
                       'payment_method': 'bank', 'total': 10, 'currency': 'USD',
                       'bank_account_id': main})
expect_error('a UZS bank account on a USD document is refused', 'bank_account_currency',
             post_document, draft)

till = cash_out(7000, method='naqd')
check('cash-till payment needs no bank account (5010 has none)',
      get_document(till)['status'] == 'posted')
check('5010 credited', near(account_balance(account_id=TILL), -7000))

inactive = save_bank_account({'account_id': BANK, 'name': 'Yopiq', 'currency': 'UZS',
                              'is_default': 0, 'is_active': 0})
draft = save_document({'doc_type': 'cash_out', 'date': '2026-03-11', 'counterparty_id': VENDOR,
                       'payment_method': 'bank', 'total': 1000, 'bank_account_id': inactive})
expect_error('an inactive bank account is refused', 'bank_account_inactive',
             post_document, draft)

# A manual entry that names a bank account of another ledger account.
from models.ledger import post_entry                                # noqa: E402
conn = get_db()
try:
    post_entry(conn, '2026-03-12', [
        {'account_id': BANK, 'debit': 500, 'bank_account_id': usd},
        {'account_id': account_id_for('other_income'), 'credit': 500}])
    check('bank account of another ledger account is refused', False, 'no exception')
except PostingError as e:
    check('bank account of another ledger account is refused', e.key == 'bank_account_mismatch', e.key)
finally:
    conn.rollback()
    conn.close()


print('\n=== Balances: per bank + unassigned == ledger (invariant #4 intact) ===')
groups = {g['account']['id']: g for g in bank_account_balances()}
g = groups[BANK]
by_id = {b['id']: b['balance'] for b in g['banks']}
check('main bank balance', near(by_id[main], -100000), str(by_id[main]))
check('second bank balance', near(by_id[second], -50000), str(by_id[second]))
check('unassigned = the legacy posting', near(g['unassigned'], -300000), str(g['unassigned']))
check('unassigned line count', g['unassigned_lines'] == 1, str(g['unassigned_lines']))
check('sum(banks) + unassigned == 5110 balance',
      near(sum(by_id.values()) + g['unassigned'], account_balance(account_id=BANK)))
check('bank_account_id accepted by balances_by_analytic',
      len(balances_by_analytic([BANK], 'bank_account_id')) == 3)   # main, second, None
tile = {a['code']: a for a in cash_by_account()}
check('dashboard tile lists bank accounts under 5110',
      {b['name'] for b in tile['5110']['bank_accounts']} == {'Asosiy', 'Ikkinchi'})
check('dashboard cash unchanged by the subdivision',
      near(cash_balance(), sum(a['balance'] for a in tile.values())))
card = get_account_ledger(BANK, bank_account_id=second)
check('account card filters by bank account',
      len(card['rows']) == 1 and near(card['closing'], -50000))
check('account card row names the bank account', card['rows'][0]['bank_account_name'] == 'Ikkinchi')


print('\n=== Storno mirrors the analytic ===')
void_document(d2, 'test')
groups = {g['account']['id']: g for g in bank_account_balances()}
by_id = {b['id']: b['balance'] for b in groups[BANK]['banks']}
check('voided bank account nets to zero', near(by_id[second], 0), str(by_id[second]))
check('the other bank account is untouched', near(by_id[main], -100000))
check('trial balance still balances', get_trial_balance()['is_balanced'])


print('\n=== One-time backfill ===')
before = account_balance(account_id=BANK)
n = assign_unassigned_lines(main)
check('one legacy line assigned', n == 1, str(n))
groups = {g['account']['id']: g for g in bank_account_balances()}
check('no unassigned lines remain', groups[BANK]['unassigned_lines'] == 0)
check('unassigned balance is zero', near(groups[BANK]['unassigned'], 0))
check('ledger balance unchanged', near(account_balance(account_id=BANK), before))
by_id = {b['id']: b['balance'] for b in groups[BANK]['banks']}
check('legacy posting now sits on the main account', near(by_id[main], -400000), str(by_id[main]))
check('legacy document now names the bank account', get_document(legacy)['bank_account_id'] == main)
check('trial balance still balances', get_trial_balance()['is_balanced'])
check('second run is a no-op', assign_unassigned_lines(main) == 0)

print(f'\npassed: {PASS}   failed: {FAIL}')
sys.exit(1 if FAIL else 0)
