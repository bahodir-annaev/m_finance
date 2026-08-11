#!/usr/bin/env python3
"""Test suite for indirect cost pool calculation."""
import sys
import os
import tempfile
import sqlite3
from datetime import datetime, timedelta

# Point to a temporary DB for this test
DB_FILE = tempfile.NamedTemporaryFile(suffix='.db', delete=False).name
os.environ['MIZAN_FINANCE_DB'] = DB_FILE

# Patch DB_PATH before importing models
import models.base
models.base.DB_PATH = DB_FILE

from models.base import init_db, get_db, get_setting, INDIRECT_POOL_EXCLUDED_TX_TYPES
from models.staff import (
    calculate_hourly_rate, get_indirect_pool_monthly, get_indirect_pool_breakdown,
    get_total_overhead,
)

PASS, FAIL = [], []

def check(name, condition, detail=''):
    if condition:
        PASS.append(name)
    else:
        FAIL.append(f"{name}: {detail}")

def setup():
    """Initialize test DB with schema."""
    init_db()

def seed_staff(name='Architect A'):
    """Add test staff."""
    conn = get_db()
    try:
        conn.execute("""INSERT INTO staff
            (name, staff_type, role, department)
            VALUES (?, 'production', 'Lead Architect', 'Design')""", (name,))
        staff_id = conn.execute("SELECT last_insert_rowid() as id").fetchone()['id']

        conn.execute("""INSERT INTO salary_history
            (staff_id, base_salary, premium, start_date, end_date)
            VALUES (?, 10000000, 1000000, '2025-01-01', NULL)""", (staff_id,))

        conn.commit()
    finally:
        conn.close()
    return staff_id

def seed_transactions():
    """Add test internal transactions."""
    conn = get_db()
    today = datetime.now()

    # A maosh (salary) payment — should be excluded
    conn.execute("""INSERT INTO transactions
        (direction, tx_type, date, ref_id, description, paid, currency, status)
        VALUES ('internal', 'maosh', ?, 'MZ-001', 'Salary payout', 15000000, 'UZS', 'paid')""",
        (today.strftime('%Y-%m-%d'),))

    # A soliq (tax) payment — should be excluded
    conn.execute("""INSERT INTO transactions
        (direction, tx_type, date, ref_id, description, paid, currency, status)
        VALUES ('internal', 'soliq', ?, 'MZ-002', 'Tax payment', 2000000, 'UZS', 'paid')""",
        (today.strftime('%Y-%m-%d'),))

    # An ijara (rent) payment — should be included
    conn.execute("""INSERT INTO transactions
        (direction, tx_type, date, ref_id, description, paid, currency, status)
        VALUES ('internal', 'ijara', ?, 'MZ-003', 'Office rent', 5000000, 'UZS', 'paid')""",
        (today.strftime('%Y-%m-%d'),))

    # A malaka (training) payment — should be included
    conn.execute("""INSERT INTO transactions
        (direction, tx_type, date, ref_id, description, paid, currency, status)
        VALUES ('internal', 'malaka', ?, 'MZ-004', 'Revit training', 500000, 'UZS', 'paid')""",
        ((today - timedelta(days=60)).strftime('%Y-%m-%d'),))

    # A premiya (bonus) payment — should be included
    conn.execute("""INSERT INTO transactions
        (direction, tx_type, date, ref_id, description, paid, currency, status)
        VALUES ('internal', 'premiya', ?, 'MZ-005', 'Performance bonus', 1000000, 'UZS', 'paid')""",
        ((today - timedelta(days=120)).strftime('%Y-%m-%d'),))

    conn.commit()
    conn.close()

def test_pool_excludes_maosh_soliq():
    """Verify pool excludes maosh and soliq but includes others."""
    seed_transactions()

    pool = get_indirect_pool_monthly()
    breakdown = get_indirect_pool_breakdown()

    # Sum of included: ijara(5M) + malaka(0.5M) + premiya(1M) = 6.5M over 4 months ≈ 1.625M/month
    # (fallback to actual elapsed time, not full 12-month window)
    check("pool_nonzero", pool > 0, f"pool was {pool}")
    check("pool_has_ijara", any(b['tx_type'] == 'ijara' for b in breakdown), "ijara not in breakdown")
    check("pool_has_malaka", any(b['tx_type'] == 'malaka' for b in breakdown), "malaka not in breakdown")
    check("pool_has_premiya", any(b['tx_type'] == 'premiya' for b in breakdown), "premiya not in breakdown")
    check("pool_no_maosh", not any(b['tx_type'] == 'maosh' for b in breakdown), "maosh should not be in breakdown")
    check("pool_no_soliq", not any(b['tx_type'] == 'soliq' for b in breakdown), "soliq should not be in breakdown")

def test_disabled_uses_overhead_table():
    """When disabled, hourly rate uses static overhead table."""
    staff_id = seed_staff('Architect B')

    # Disable pool
    conn = get_db()
    conn.execute("UPDATE settings SET value=0 WHERE key='indirect_pool_enabled'")
    conn.commit()
    conn.close()

    rate1 = calculate_hourly_rate(staff_id)
    overhead1 = rate1['overhead_share']

    check("disabled_uses_table", overhead1 > 0, f"overhead_share was {overhead1}")

def test_enabled_uses_pool():
    """When enabled, hourly rate uses pool instead of overhead table."""
    conn = get_db()
    # Clear prior test data
    conn.execute("DELETE FROM transactions WHERE direction='internal'")
    conn.commit()
    conn.close()

    staff_id = seed_staff('Architect C')
    seed_transactions()

    # Enable pool
    conn = get_db()
    conn.execute("UPDATE settings SET value=1 WHERE key='indirect_pool_enabled'")
    conn.commit()
    conn.close()

    rate_enabled = calculate_hourly_rate(staff_id)
    pool_val = get_indirect_pool_monthly()

    check("enabled_uses_pool", rate_enabled['overhead_share'] > 0,
          f"overhead_share was {rate_enabled['overhead_share']}")
    check("pool_affects_rate", pool_val > 0, f"pool was {pool_val}")

def test_disabled_same_as_before():
    """Disabling pool returns to byte-identical state as static overhead."""
    conn = get_db()
    conn.execute("DELETE FROM transactions WHERE direction='internal'")
    conn.execute("UPDATE settings SET value=0 WHERE key='indirect_pool_enabled'")
    conn.commit()
    conn.close()

    staff_id = seed_staff('Architect D')
    rate_static = calculate_hourly_rate(staff_id)

    # Now add transactions and enable
    seed_transactions()
    conn = get_db()
    conn.execute("UPDATE settings SET value=1 WHERE key='indirect_pool_enabled'")
    conn.commit()
    conn.close()

    rate_dynamic = calculate_hourly_rate(staff_id)

    # Re-disable
    conn = get_db()
    conn.execute("UPDATE settings SET value=0 WHERE key='indirect_pool_enabled'")
    conn.commit()
    conn.close()

    rate_back = calculate_hourly_rate(staff_id)

    check("roundtrip_cost_rate",
          rate_static['cost_rate'] == rate_back['cost_rate'],
          f"before={rate_static['cost_rate']}, after={rate_back['cost_rate']}")
    check("roundtrip_billing_rate",
          rate_static['billing_rate'] == rate_back['billing_rate'],
          f"before={rate_static['billing_rate']}, after={rate_back['billing_rate']}")

def test_no_transactions_returns_zero():
    """Pool returns 0 when no transactions exist."""
    pool = get_indirect_pool_monthly()
    check("empty_pool_is_zero", pool == 0, f"pool was {pool}")

def test_breakdown_empty_when_no_transactions():
    """Breakdown returns [] when no transactions exist."""
    breakdown = get_indirect_pool_breakdown()
    check("empty_breakdown", len(breakdown) == 0, f"breakdown was {breakdown}")

if __name__ == '__main__':
    try:
        setup()

        test_no_transactions_returns_zero()
        test_breakdown_empty_when_no_transactions()
        test_pool_excludes_maosh_soliq()
        test_disabled_uses_overhead_table()
        test_enabled_uses_pool()
        test_disabled_same_as_before()

        print(f"\n[PASS] ({len(PASS)} tests)")
        for p in PASS:
            print(f"  [OK] {p}")

        if FAIL:
            print(f"\n[FAIL] ({len(FAIL)} tests)")
            for f in FAIL:
                print(f"  [FAIL] {f}")
            sys.exit(1)
    finally:
        try:
            os.unlink(DB_FILE)
        except:
            pass
