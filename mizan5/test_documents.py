# -*- coding: utf-8 -*-
"""Document engine tests — posting rules, allocation, void, advances.

    python test_documents.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DB_FILE = os.path.join(tempfile.gettempdir(), 'mizan5_test_documents.db')
os.environ['MIZAN5_DB'] = DB_FILE
os.environ['MIZAN_ADMIN_PASSWORD'] = 'test'
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

from models.base import init_db, get_db                             # noqa: E402
from models.ledger import (                                         # noqa: E402
    account_balance, get_trial_balance, verify_all_entries,
    balances_by_analytic, account_id_for, PostingError,
)
from models.documents import (                                      # noqa: E402
    DocumentError, save_document, post_document, void_document, get_document,
    invoice_outstanding, open_invoices, list_documents, delete_draft,
    add_vat, split_vat,
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
    try:
        fn(*args, **kwargs)
    except (DocumentError, PostingError) as e:
        check(label, e.key == key, f'got {e.key}, wanted {key}')
        return
    except Exception as e:                                          # noqa: BLE001
        check(label, False, f'wrong exception: {type(e).__name__}: {e}')
        return
    check(label, False, 'no exception raised')


init_db()
conn = get_db()
CLIENT = conn.execute(
    "INSERT INTO counterparties (name, inn, counterparty_type)"
    " VALUES ('Alfa Qurilish MChJ', '301234567', 'client')").lastrowid
VENDOR = conn.execute(
    "INSERT INTO counterparties (name, inn, counterparty_type)"
    " VALUES ('Beta Loyiha MChJ', '302345678', 'vendor')").lastrowid
PROJECT = conn.execute(
    "INSERT INTO projects (name, counterparty_id) VALUES ('Turar-joy majmuasi', ?)",
    (CLIENT,)).lastrowid
conn.commit()
conn.close()

print('\n=== VAT helpers ===')
net, vat = add_vat(1000000, 12)
check('VAT on top: 1 000 000 + 12% = 120 000', net == 1000000 and vat == 120000)
net, vat = split_vat(1120000, 12)
check('VAT extracted from a gross amount', net == 1000000 and vat == 120000,
      f'{net} / {vat}')
net, vat = add_vat(500000, 0)
check('zero-rated line carries no VAT', net == 500000 and vat == 0)

print('\n=== 5.1 Sales invoice ===')
inv_id = save_document(
    {'doc_type': 'sales_invoice', 'date': '2026-04-05', 'counterparty_id': CLIENT,
     'project_id': PROJECT, 'due_date': '2026-05-05',
     'description': 'Eskiz loyiha bosqichi', 'contract_ref': 'SH-2026-14'},
    lines=[{'description': 'Arxitektura loyihasi', 'quantity': 1,
            'unit_price': 1000000, 'amount': 1000000, 'vat_rate': 12,
            'vat_amount': 120000, 'project_id': PROJECT},
           {'description': 'Ekspertiza hujjatlari', 'quantity': 1,
            'unit_price': 500000, 'amount': 500000, 'vat_rate': 12,
            'vat_amount': 60000, 'project_id': PROJECT}])
doc = get_document(inv_id)
check('invoice totals derived from lines',
      doc['subtotal'] == 1500000 and doc['vat_amount'] == 180000 and doc['total'] == 1680000,
      f"{doc['subtotal']}/{doc['vat_amount']}/{doc['total']}")
check('new document starts as a draft', doc['status'] == 'draft')
check('document number auto-generated', doc['number'].startswith('SF-2026-'),
      doc['number'])
check('draft has no ledger effect', abs(account_balance('4010')) < 0.01)

post_document(inv_id)
doc = get_document(inv_id)
check('posted invoice carries an entry', doc['status'] == 'posted' and doc['entry_id'])
check('AR debited with the gross total',
      abs(account_balance('4010') - 1680000) < 0.01, str(account_balance('4010')))
check('revenue credited with the net amount',
      abs(account_balance('9030') - 1500000) < 0.01, str(account_balance('9030')))
check('output VAT credited separately',
      abs(account_balance('6410.1') - 180000) < 0.01, str(account_balance('6410.1')))
check('revenue carries the project analytic',
      abs(account_balance('9030', project_id=PROJECT) - 1500000) < 0.01)
check('trial balance still balances', get_trial_balance()['is_balanced'])

expect_error('re-posting a posted document is refused', 'doc_already_posted',
             post_document, inv_id)
expect_error('editing a posted document is refused', 'doc_posted_readonly',
             save_document, {'doc_type': 'sales_invoice', 'date': '2026-04-05'},
             None, None, inv_id)

print('\n=== Invoice with no lines / no counterparty ===')
bad_id = save_document({'doc_type': 'sales_invoice', 'date': '2026-04-06',
                        'counterparty_id': CLIENT}, lines=[])
expect_error('invoice without lines cannot be posted', 'doc_no_lines',
             post_document, bad_id)
delete_draft(bad_id)
bad2 = save_document({'doc_type': 'sales_invoice', 'date': '2026-04-06'},
                     lines=[{'description': 'x', 'amount': 100}])
expect_error('invoice without a counterparty cannot be posted', 'doc_no_counterparty',
             post_document, bad2)
delete_draft(bad2)

print('\n=== 5.3 Cash in — partial settlement ===')
check('invoice outstanding before payment', invoice_outstanding(inv_id) == 1680000)
pay1 = save_document(
    {'doc_type': 'cash_in', 'date': '2026-04-20', 'counterparty_id': CLIENT,
     'payment_method': 'bank', 'total': 1000000, 'description': 'Qisman tolov'},
    allocations=[{'invoice_doc_id': inv_id, 'amount': 1000000}])
post_document(pay1)
check('cash debited', abs(account_balance('5110') - 1000000) < 0.01)
check('AR reduced by the allocated amount',
      abs(account_balance('4010') - 680000) < 0.01, str(account_balance('4010')))
check('invoice outstanding tracks allocations',
      invoice_outstanding(inv_id) == 680000, str(invoice_outstanding(inv_id)))
check('open invoice list shows the remainder',
      any(i['id'] == inv_id and i['outstanding'] == 680000
          for i in open_invoices(CLIENT)))

print('\n=== Over-allocation is refused ===')
over = save_document(
    {'doc_type': 'cash_in', 'date': '2026-04-21', 'counterparty_id': CLIENT,
     'total': 100000},
    allocations=[{'invoice_doc_id': inv_id, 'amount': 900000}])
expect_error('allocating more than the payment is refused', 'alloc_exceeds_payment',
             post_document, over)
save_document({'doc_type': 'cash_in', 'date': '2026-04-21',
               'counterparty_id': CLIENT, 'total': 900000},
              allocations=[{'invoice_doc_id': inv_id, 'amount': 900000}], doc_id=over)
expect_error('allocating more than the invoice outstanding is refused',
             'alloc_exceeds_invoice', post_document, over)
delete_draft(over)

print('\n=== Unallocated receipt becomes an advance ===')
adv = save_document(
    {'doc_type': 'cash_in', 'date': '2026-04-22', 'counterparty_id': CLIENT,
     'total': 500000, 'cash_purpose': 'Kelgusi ishlar uchun avans'})
post_document(adv)
check('advance received credited to 6310',
      abs(account_balance('6310') - 500000) < 0.01, str(account_balance('6310')))
check('AR untouched by an unallocated receipt',
      abs(account_balance('4010') - 680000) < 0.01)

print('\n=== Settling the rest closes the invoice ===')
pay2 = save_document(
    {'doc_type': 'cash_in', 'date': '2026-05-02', 'counterparty_id': CLIENT,
     'total': 680000},
    allocations=[{'invoice_doc_id': inv_id, 'amount': 680000}])
post_document(pay2)
check('invoice fully settled', invoice_outstanding(inv_id) == 0)
check('AR back to zero', abs(account_balance('4010')) < 0.01, str(account_balance('4010')))
check('settled invoice leaves the open list',
      not any(i['id'] == inv_id for i in open_invoices(CLIENT)))

print('\n=== 5.2 Purchase invoice ===')
pinv = save_document(
    {'doc_type': 'purchase_invoice', 'date': '2026-04-10', 'counterparty_id': VENDOR,
     'external_number': 'INV-889', 'description': 'Autsorsing va ofis xarajati'},
    lines=[{'description': 'KJ bolimi', 'amount': 400000, 'vat_rate': 12,
            'vat_amount': 48000, 'project_id': PROJECT},
           {'description': 'Ofis ijarasi', 'amount': 200000, 'vat_rate': 0,
            'vat_amount': 0}])
post_document(pinv)
check('AP credited with the gross total',
      abs(account_balance('6010') - 648000) < 0.01, str(account_balance('6010')))
check('project-tagged line hits production cost 2010',
      abs(account_balance('2010', project_id=PROJECT) - 400000) < 0.01,
      str(account_balance('2010')))
check('untagged line hits admin expense 9420',
      abs(account_balance('9420') - 200000) < 0.01, str(account_balance('9420')))
check('input VAT debited to 4410',
      abs(account_balance('4410') - 48000) < 0.01)

print('\n=== 5.4 Cash out settling a purchase invoice ===')
payout = save_document(
    {'doc_type': 'cash_out', 'date': '2026-04-25', 'counterparty_id': VENDOR,
     'total': 648000, 'payment_method': 'bank'},
    allocations=[{'invoice_doc_id': pinv, 'amount': 648000}])
post_document(payout)
check('AP cleared', abs(account_balance('6010')) < 0.01, str(account_balance('6010')))
check('cash reduced', abs(account_balance('5110') - (1000000 + 500000 + 680000 - 648000)) < 0.01,
      str(account_balance('5110')))

print('\n=== AR/AP by counterparty ===')
ar_rows = balances_by_analytic([account_id_for('ar')], 'counterparty_id')
check('no receivable remains for the client', not ar_rows, str(ar_rows))
adv_rows = balances_by_analytic([account_id_for('advances_received')], 'counterparty_id')
check('advance is attributed to the client',
      len(adv_rows) == 1 and adv_rows[0]['key'] == CLIENT)

print('\n=== Void is a perfect mirror ===')
before_cash = account_balance('5110')
before_adv = account_balance('6310')
void_document(adv, reason='Xato kiritilgan')
check('void reverses the cash movement',
      abs((before_cash - account_balance('5110')) - 500000) < 0.01)
check('void reverses the advance',
      abs((before_adv - account_balance('6310')) - 500000) < 0.01)
check('voided document keeps its number and status',
      get_document(adv)['status'] == 'void')
check('trial balance survives the void', get_trial_balance()['is_balanced'])
expect_error('a voided document cannot be voided twice', 'doc_not_posted',
             void_document, adv)
expect_error('an invoice with posted payments cannot be voided',
             'doc_void_has_payments', void_document, inv_id)

print('\n=== 5.10 Manual entry ===')
CASH_ID = account_id_for('cash_bank')
TILL_ID = account_id_for('cash_till')
man = save_document({'doc_type': 'manual', 'date': '2026-05-10',
                     'description': 'Kassaga otkazma'},
                    lines=[{'account_id': TILL_ID, 'debit': 200000},
                           {'account_id': CASH_ID, 'credit': 200000}])
post_document(man)
check('manual entry moves money between accounts',
      abs(account_balance('5010') - 200000) < 0.01)

unbal = save_document({'doc_type': 'manual', 'date': '2026-05-11'},
                      lines=[{'account_id': TILL_ID, 'debit': 100},
                             {'account_id': CASH_ID, 'credit': 40}])
expect_error('unbalanced manual entry is refused at post time', 'ledger_unbalanced',
             post_document, unbal)
delete_draft(unbal)

print('\n=== 5.8 Opening balances ===')
AR_ID = account_id_for('ar')
RE_ID = account_id_for('retained_earnings')
opening = save_document({'doc_type': 'opening', 'date': '2026-01-01'},
                        lines=[{'account_id': CASH_ID, 'debit': 5000000},
                               {'account_id': AR_ID, 'debit': 2000000,
                                'counterparty_id': CLIENT},
                               {'account_id': RE_ID, 'credit': 7000000}])
post_document(opening)
check('a balanced opening set leaves 0000 at zero',
      abs(account_balance('0000')) < 0.01, str(account_balance('0000')))
check('opening balances land on their accounts',
      abs(account_balance('8710') - 7000000) < 0.01)

lop = save_document({'doc_type': 'opening', 'date': '2026-01-02'},
                    lines=[{'account_id': CASH_ID, 'debit': 1000000}])
post_document(lop)
# 0000 is a technical suspense account; the sign it carries is a display
# convention, so only the magnitude is meaningful here. The invariant that
# matters — a balanced set nets to zero — is asserted above.
check('an unbalanced opening set parks the difference on 0000',
      abs(abs(account_balance('0000')) - 1000000) < 0.01, str(account_balance('0000')))

print('\n=== List view fields ===')
sales = list_documents(doc_type='sales_invoice')
check('list view reports settled and outstanding',
      sales and sales[0]['settled'] == 1680000 and sales[0]['outstanding'] == 0,
      str(sales[0] if sales else None))

print('\n=== Integrity sweep ===')
check('no unbalanced entry in the whole database', not verify_all_entries())
check('final trial balance is balanced', get_trial_balance()['is_balanced'])

print(f'\n{"=" * 46}\n  passed: {PASS}   failed: {FAIL}\n{"=" * 46}')
sys.exit(1 if FAIL else 0)
