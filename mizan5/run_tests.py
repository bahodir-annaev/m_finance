# -*- coding: utf-8 -*-
"""Run every test script and summarise. Each suite is standalone and builds
its own temp database, so they can run in any order.

    python run_tests.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

SUITES = [
    ('test_ledger.py', 'ledger core — balance, periods, reversal'),
    ('test_documents.py', 'documents — posting rules, allocation, void'),
    ('test_rates.py', 'man-hour cost engine — v4 parity, benchmarks'),
    ('test_pricing.py', 'pricing ladder, milestones, budget'),
    ('test_reports.py', 'reports, FX, period close'),
    ('test_direction.py', 'internal/external/financing — ladder, conservation'),
    ('test_depreciation.py', 'depreciation schedule, posting, no double count'),
    ('test_overhead.py', 'overhead pool — no double count, shares, provenance'),
    ('test_asset_classes.py', 'asset classes — lives, account pairs, classifier'),
    ('test_bank_accounts.py', 'bank accounts — the 1C bank-account subconto'),
    ('test_v4_features.py', 'v4 port — plan lens, NIZAM, KPI, quote, loans, paging'),
    ('test_i18n.py', 'translation completeness'),
    ('test_app.py', 'end-to-end HTTP — every page and flow'),
    ('test_migration.py', 'v4 migration + reconciliation'),
]

env = dict(os.environ, PYTHONIOENCODING='utf-8')
results = []
for script, label in SUITES:
    path = os.path.join(HERE, script)
    if not os.path.exists(path):
        results.append((script, label, None, 'missing'))
        continue
    proc = subprocess.run([sys.executable, path], capture_output=True, text=True,
                          cwd=HERE, env=env)
    tail = [l for l in proc.stdout.splitlines() if 'passed:' in l]
    summary = tail[-1].strip() if tail else proc.stderr.strip().splitlines()[-1:] or ''
    results.append((script, label, proc.returncode, summary))

print('\n' + '=' * 72)
print('  MIZAN v5 test suite')
print('=' * 72)
total_pass = total_fail = 0
for script, label, code, summary in results:
    mark = 'OK  ' if code == 0 else 'FAIL'
    if isinstance(summary, str) and 'passed:' in summary:
        parts = summary.replace('passed:', '').replace('failed:', '').split()
        try:
            total_pass += int(parts[0])
            total_fail += int(parts[1])
        except (IndexError, ValueError):
            pass
    print(f'  [{mark}] {script:<22} {label}')
    print(f'         {summary}')
print('=' * 72)
print(f'  TOTAL: {total_pass} passed, {total_fail} failed')
print('=' * 72)
sys.exit(1 if any(c not in (0, None) for _, _, c, _ in results) else 0)
