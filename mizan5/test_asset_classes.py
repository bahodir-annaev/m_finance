# -*- coding: utf-8 -*-
"""Asset classes — useful life and account pairing per kind of asset.

v4's register was one flat list: every asset, from a laptop to a desk, carried
the same 36-month life and landed on the same two accounts. This file pins the
two dimensions that fixes, and the one thing that must NOT change as a result.

  asset_class = WHAT the thing is  -> its useful life and its account pair
  kind        = WHO BEARS THE COST -> personal (one employee) / general (all)

They are orthogonal: a desk and a laptop can both be 'personal'.

THE INVARIANT: a class default is a default. Editing it pre-fills new assets
and must never re-life an asset already on the books, because that would move
depreciation already posted and every rate quoted from it.

    python test_asset_classes.py
"""
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DB_FILE = os.path.join(tempfile.gettempdir(), 'mizan5_test_asset_classes.db')
os.environ['MIZAN5_DB'] = DB_FILE
os.environ['MIZAN_ADMIN_PASSWORD'] = 'test'
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

from models.base import (                                                # noqa: E402
    init_db, get_db, asset_class_map, default_lifespan_for, ASSET_CLASS_SEED,
)
from models.ledger import (                                              # noqa: E402
    account_balance, get_account_by_code, get_trial_balance, natural_side,
    verify_all_entries,
)
from models.depreciation import (                                        # noqa: E402
    classify_asset, classify_register, depreciation_schedule,
    depreciation_preview, post_period_depreciation, CLASSIFY_RULES,
)
from models.staff import depreciation_reconciliation                     # noqa: E402

PASS, FAIL = 0, 0

# Every accumulated-depreciation account. Depreciation credits the contra
# account of the asset's CLASS, so a check that reads 0200 alone sees zero.
ACCUM_CODES = ('0200', '0220.1', '0230', '0240', '0250', '0260', '0290')


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


def total_accumulated():
    return sum(account_balance(c) for c in ACCUM_CODES)


init_db()

print('\n=== The classes and their account pairs ===')
classes = asset_class_map()
check('every seeded class exists', len(classes) == len(ASSET_CLASS_SEED),
      str(sorted(classes)))
check('every class names an asset account and a contra account',
      all(c['asset_account'] and c['accum_account'] for c in classes.values()))
missing = sorted({code for c in classes.values()
                  for code in (c['asset_account'], c['accum_account'])
                  if not get_account_by_code(code)})
check('every account a class names exists in the chart', not missing, str(missing))
check('every contra account is credit-normal',
      all(natural_side(c['accum_account'], 'KA') == 'credit'
          for c in classes.values()))
pairs = [(c['asset_account'], c['accum_account']) for c in classes.values()]
check('no two classes share an account pair', len(set(pairs)) == len(pairs))
check('furniture is its own class, on 0140/0240',
      classes['furniture']['asset_account'] == '0140'
      and classes['furniture']['accum_account'] == '0240')
check('computers are on 0150/0250',
      classes['computer']['asset_account'] == '0150'
      and classes['computer']['accum_account'] == '0250')

print('\n=== Lives ship unchanged and are configurable ===')
check('every class ships at 36 months — the value the v4 register carried',
      all(c['default_lifespan_months'] == 36 for c in classes.values()),
      str({k: c['default_lifespan_months'] for k, c in classes.items()}))
check('an unknown class resolves to 36 rather than 0',
      default_lifespan_for('no_such_class') == 36)
conn = get_db()
conn.execute("UPDATE asset_classes SET default_lifespan_months=0 WHERE code='other'")
conn.commit()
conn.close()
check('a zero default is refused — it would divide by zero in the rate engine',
      default_lifespan_for('other') == 36)
conn = get_db()
conn.execute("UPDATE asset_classes SET default_lifespan_months=36 WHERE code='other'")
conn.commit()
conn.close()

print('\n=== The classifier ===')
# Names taken verbatim from the real 156-row register.
CASES = [
    ('Ноутбук HP Victus ( Core i5 , 2x8gb, SSD 512 gb, RTX 3050 4gb, BenQ GW2780)', 'computer'),
    ('ПК в комплекте (Gigabyte Z390 UD / Core i7-9700 / 2x 16GB)', 'computer'),
    ('Monoblok HP EliteOne 1000 G2', 'computer'),
    ('Mi Monitor 2шт', 'computer'),
    ('ION V-1000T(UPS)', 'computer'),
    ('Принтер Epson L1800 модел B472C', 'computer'),
    ('logitech  Мышь +Клав.', 'computer'),
    ('Canon F810100 sn: 5CR12793 MF752cdw', 'computer'),
    ('Стол с тумбой, Кресло (ИНВ-М003)', 'furniture'),
    ('Кресло офисное ELIAN (ИНВ-М053)', 'furniture'),
    ('Диван ММ 223.01.04', 'furniture'),
    ('Стол,Тумба мобильная,Кресло (ИНВ-М014)', 'furniture'),
    ('Телевизор SAMSUNG QLED The Frame 75LS03BAU 4K UHD Smart TV 75', 'furniture'),
    ('Stol va stul', 'furniture'),
    ('Kondisioner LG', 'machinery'),
    ('Chevrolet Cobalt', 'vehicle'),
    ('Nimadir tushunarsiz', 'other'),
]
wrong = [(n[:40], want, classify_asset(n)[0]) for n, want in CASES
         if classify_asset(n)[0] != want]
check('the classifier places every known asset shape', not wrong, str(wrong[:3]))
check('an unrecognised name falls back to "other" rather than guessing',
      classify_asset('Nimadir tushunarsiz') == ('other', None))
check('a confident match is distinguishable from the fallback',
      classify_asset('Диван')[1] is not None)
# A laptop's name routinely lists its monitor ("Ноутбук … BenQ GW2780"). If the
# accessory rules ran first the machine would be classified by its accessory,
# so rule ORDER is load-bearing — and no name may satisfy two classes at once.
ambiguous = [n[:45] for n, _ in CASES
             if len({c for c, pat in CLASSIFY_RULES
                     if re.search(pat, n.lower())}) > 1]
check('no asset name matches two different classes', not ambiguous, str(ambiguous))

print('\n=== Depreciation credits the class, not one lump on 0200 ===')
conn = get_db()
STAFF = conn.execute(
    "INSERT INTO staff (name, role, department, staff_type)"
    " VALUES ('Aziz','Arxitektor','Arxitektura','production')").lastrowid
conn.execute("INSERT INTO salary_history (staff_id, base_salary, premium, start_date)"
             " VALUES (?,9000000,0,'2025-01-01')", (STAFF,))
PC = conn.execute(
    "INSERT INTO equipment (name, kind, asset_class, staff_id, quantity, price,"
    " lifespan_months, purchase_date, is_active)"
    " VALUES ('Ноутбук HP','personal','computer',?,1,36000000,36,'2026-01-10',1)",
    (STAFF,)).lastrowid
DESK = conn.execute(
    "INSERT INTO equipment (name, kind, asset_class, quantity, price,"
    " lifespan_months, purchase_date, is_active)"
    " VALUES ('Стол, Кресло (ИНВ-М900)','general','furniture',1,24000000,36,"
    "         '2026-01-10',1)").lastrowid
conn.commit()
conn.close()

sched = depreciation_schedule('2026-06')
by_class = {c['asset_class']: c for c in sched['by_class']}
check('the schedule groups the charge by class',
      set(by_class) == {'computer', 'furniture'}, str(sorted(by_class)))
check('the furniture charge is its own cost over its own life',
      close_to(by_class['furniture']['charge'], 24000000 / 36))
check('the computer charge is separate',
      close_to(by_class['computer']['charge'], 36000000 / 36))
check('the class charges sum to the schedule total',
      close_to(sum(c['charge'] for c in sched['by_class']), sched['total']))

prev = depreciation_preview('2026-06')
check('the preview names both contra accounts it will credit',
      prev['accum_codes'] == ['0240', '0250'], str(prev['accum_codes']))

entry_id, total = post_period_depreciation('2026-06')
check('a mixed register posts one balanced entry', entry_id is not None)
check('furniture credits 0240',
      close_to(account_balance('0240'), 24000000 / 36), str(account_balance('0240')))
check('computers credit 0250',
      close_to(account_balance('0250'), 36000000 / 36), str(account_balance('0250')))
check('nothing lands on the 0200 parent',
      close_to(account_balance('0200'), 0), str(account_balance('0200')))
check('the credits sum exactly to the debit',
      close_to(total_accumulated(), total) and close_to(account_balance('9420.1'), total))
check('the entry balances to the tiyin, not merely within tolerance',
      not verify_all_entries())
check('the trial balance holds', get_trial_balance()['is_balanced'])

print('\n=== THE INVARIANT: a class default never re-lifes an existing asset ===')
conn = get_db()
lives_before = {r['id']: r['lifespan_months'] for r in
                conn.execute("SELECT id, lifespan_months FROM equipment")}
conn.execute("UPDATE asset_classes SET default_lifespan_months=80"
             " WHERE code='furniture'")
conn.commit()
conn.close()
check('the class default is editable and took effect',
      default_lifespan_for('furniture') == 80)
conn = get_db()
lives_after = {r['id']: r['lifespan_months'] for r in
               conn.execute("SELECT id, lifespan_months FROM equipment")}
conn.close()
check('no asset already on the books was re-lifed', lives_before == lives_after)
after = {c['asset_class']: c for c in depreciation_schedule('2026-07')['by_class']}
check('and its monthly charge is unchanged',
      close_to(after['furniture']['charge'], 24000000 / 36),
      str(after['furniture']['charge']))
off = {o['equipment_id']: o for o in depreciation_schedule('2026-07')['off_default']}
check('the divergence from the default is reported, not hidden', DESK in off)
check('the report names both the row life and the class default',
      off[DESK]['lifespan_months'] == 36 and off[DESK]['class_default'] == 80)
conn = get_db()
conn.execute("UPDATE asset_classes SET default_lifespan_months=36 WHERE code='furniture'")
conn.commit()
conn.close()

print('\n=== Bulk reclassification is reviewable and non-destructive ===')
conn = get_db()
conn.execute("UPDATE equipment SET asset_class='other' WHERE id=?", (DESK,))
HAND = conn.execute(
    "INSERT INTO equipment (name, kind, asset_class, quantity, price,"
    " lifespan_months, purchase_date, is_active)"
    " VALUES ('Стол (ИНВ-М901)','general','machinery',1,1000000,36,'2026-01-10',1)"
).lastrowid
conn.commit()
conn.close()


def class_of(rid):
    conn = get_db()
    try:
        return conn.execute("SELECT asset_class FROM equipment WHERE id=?",
                            (rid,)).fetchone()['asset_class']
    finally:
        conn.close()


dry = classify_register(apply=False)
check('a dry run reports every row', len(dry['rows']) == 3, str(len(dry['rows'])))
check('a dry run writes nothing',
      dry['changed'] == 0 and not dry['applied'] and class_of(DESK) == 'other')
check('it reports what it WOULD change',
      any(r['equipment_id'] == DESK and r['suggested'] == 'furniture'
          for r in dry['rows']))

applied = classify_register(apply=True)
check('an unclassified row is placed', class_of(DESK) == 'furniture')
check('a hand-corrected class is NEVER overwritten', class_of(HAND) == 'machinery')
check('the skip is reported rather than silent',
      any(r['equipment_id'] == HAND and r['skipped'] for r in applied['rows']))
check('the report summarises by class and by value',
      any(s['asset_class'] == 'furniture' and s['value'] > 0
          for s in applied['summary']))
check('re-running changes nothing further',
      classify_register(apply=True)['changed'] == 0)

print('\n=== Reconciliation is per class ===')
rec = depreciation_reconciliation()
rec_classes = {c['asset_class']: c for c in rec['by_class']}
check('the register is broken down by class',
      {'furniture', 'computer'} <= set(rec_classes), str(sorted(rec_classes)))
check('each class reconciles against its OWN asset account',
      rec_classes['furniture']['asset_account'] == '0140'
      and rec_classes['computer']['asset_account'] == '0150')
check('the class costs sum to the register cost',
      close_to(sum(c['cost'] for c in rec['by_class']), rec['register_cost']))
check('accumulated sums every contra account, not just 0200',
      close_to(rec['ledger_accumulated'], total_accumulated()))
check('uncapitalized is still reported per class',
      all('uncapitalized' in c for c in rec['by_class']))

print(f'\n{"=" * 46}\n  passed: {PASS}   failed: {FAIL}\n{"=" * 46}')
sys.exit(1 if FAIL else 0)
