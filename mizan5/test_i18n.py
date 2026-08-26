# -*- coding: utf-8 -*-
"""Translation completeness — English and Russian must cover every Uzbek key,
and every t('...') key used in a template must exist.

A missing key renders as the raw key in the UI, which looks like a bug to the
user, so this runs as part of the suite rather than as an occasional audit.

    python test_i18n.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from translations import TRANSLATIONS, get_text, missing_keys          # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PASS, FAIL = 0, 0


def check(label, condition, detail=''):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f'  [ok]   {label}')
    else:
        FAIL += 1
        print(f'  [FAIL] {label}' + (f'  — {detail}' if detail else ''))


print('\n=== Language coverage ===')
uz_keys = set(TRANSLATIONS['uz'])
check('Uzbek reference has keys', len(uz_keys) > 200, str(len(uz_keys)))
for lang in ('en', 'ru'):
    missing = missing_keys(lang)
    check(f'{lang} covers every Uzbek key', not missing,
          f'{len(missing)} missing: {missing[:8]}')
    extra = sorted(set(TRANSLATIONS[lang]) - uz_keys)
    check(f'{lang} has no orphan keys', not extra, f'{extra[:8]}')

print('\n=== No blank translations ===')
for lang, table in TRANSLATIONS.items():
    blanks = [k for k, v in table.items() if not str(v).strip()]
    check(f'{lang} has no empty strings', not blanks, str(blanks[:8]))

print('\n=== Keys used in templates exist ===')
# t('key') and t("key") in templates and controllers
pattern = re.compile(r"""\bt\(\s*['"]([a-z0-9_]+)['"]""")
# Keys built dynamically, e.g. t('doc_' ~ doc_type) — the prefixes to accept.
dynamic_prefixes = ('doc_', 'kind_', 'pool_', 'ms_', 'project_', 'client_',
                    'complexity_', 'role_', 'type_', 'period_', 'equipment_',
                    'aging_', 'base_', 'source_')
used = {}
for folder in ('templates', 'controllers'):
    root = os.path.join(HERE, folder)
    if not os.path.isdir(root):
        continue
    for name in os.listdir(root):
        if not name.endswith(('.html', '.py')):
            continue
        path = os.path.join(root, name)
        with open(path, encoding='utf-8') as f:
            for key in pattern.findall(f.read()):
                used.setdefault(key, set()).add(name)

# A key equal to a dynamic prefix is the literal half of t('doc_' ~ doc_type);
# the families themselves are checked below.
unknown = {k: sorted(v) for k, v in used.items()
           if k not in uz_keys and k not in dynamic_prefixes}
check('every literal t() key in templates is defined', not unknown,
      '; '.join(f'{k} ({", ".join(files)})' for k, files in list(unknown.items())[:6]))
print(f'  {len(used)} distinct keys used across templates and controllers')

print('\n=== Dynamic key families are complete ===')
FAMILIES = {
    'doc_': ('sales_invoice', 'purchase_invoice', 'cash_in', 'cash_out',
             'payroll', 'dividend', 'loan', 'manual', 'opening'),
    'kind_': ('A', 'KA', 'P', 'T'),
    'pool_': ('direct_labor', 'indirect', 'excluded'),
    'ms_': ('planned', 'in_progress', 'done', 'cancelled'),
    'project_': ('active', 'completed', 'paused'),
    'client_': ('regular', 'new', 'government'),
    'complexity_': ('simple', 'medium', 'high'),
    'role_': ('admin', 'manager', 'viewer'),
    'type_': ('production', 'admin'),
    'period_': ('open', 'soft_closed', 'hard_closed'),
    'equipment_': ('personal', 'general'),
}
for prefix, suffixes in FAMILIES.items():
    missing = [f'{prefix}{s}' for s in suffixes if f'{prefix}{s}' not in uz_keys]
    check(f'{prefix}* family is complete', not missing, str(missing))
    for lang in ('en', 'ru'):
        gaps = [f'{prefix}{s}' for s in suffixes if f'{prefix}{s}' not in TRANSLATIONS[lang]]
        check(f'{prefix}* complete in {lang}', not gaps, str(gaps))

print('\n=== Posting-error keys are translated ===')
ERROR_KEYS = [
    'ledger_unbalanced', 'ledger_period_closed', 'ledger_empty_entry',
    'ledger_no_account', 'ledger_no_account_map', 'ledger_no_date',
    'ledger_negative_amount', 'ledger_both_sides', 'ledger_entry_missing',
    'ledger_already_reversed', 'doc_no_lines', 'doc_no_counterparty',
    'doc_not_found', 'doc_already_posted', 'doc_not_posted',
    'doc_posted_readonly', 'doc_number_taken', 'alloc_exceeds_payment',
    'alloc_exceeds_invoice', 'alloc_invoice_not_posted',
    'depreciation_account_in_pool',
]
for key in ERROR_KEYS:
    ok = all(key in TRANSLATIONS[lang] for lang in ('uz', 'en', 'ru'))
    if not ok:
        check(f'error key {key} is translated everywhere', False,
              str([l for l in ('uz', 'en', 'ru') if key not in TRANSLATIONS[l]]))
check('every posting-error key is translated in all three languages',
      all(all(k in TRANSLATIONS[l] for l in ('uz', 'en', 'ru')) for k in ERROR_KEYS))

print('\n=== get_text behaviour ===')
check('a known key translates', get_text('ru', 'debit') == 'Дебет')
check('an unknown key returns itself', get_text('en', 'no_such_key_xyz') == 'no_such_key_xyz')
check('an unknown language falls back to Uzbek',
      get_text('de', 'debit') == TRANSLATIONS['uz']['debit'])

print(f'\n{"=" * 46}\n  passed: {PASS}   failed: {FAIL}\n{"=" * 46}')
sys.exit(1 if FAIL else 0)
