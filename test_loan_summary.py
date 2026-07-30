"""Regression test: get_loan_summary valyutalarni UZS ga to'g'ri konvertiradi.

Run: python test_loan_summary.py
Bug: USD va UZS qarzlar bir-biriga aralash jamlanardi (database.py:1361, fixed).
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from database import get_db, init_db, get_loan_summary, get_rate_for_date

TEST_TAG = '__TEST_LOAN_SUMMARY__'


def setup():
    """Test izolyatsiyasi: jadvallarni ishga tushir + bizning test qatorlari yo'qligini tekshir."""
    init_db()
    conn = get_db()
    conn.execute("DELETE FROM loan_payments WHERE loan_id IN (SELECT id FROM loans WHERE counterparty LIKE ?)", (TEST_TAG + '%',))
    conn.execute("DELETE FROM loans WHERE counterparty LIKE ?", (TEST_TAG + '%',))
    conn.commit()
    conn.close()


def teardown():
    conn = get_db()
    conn.execute("DELETE FROM loan_payments WHERE loan_id IN (SELECT id FROM loans WHERE counterparty LIKE ?)", (TEST_TAG + '%',))
    conn.execute("DELETE FROM loans WHERE counterparty LIKE ?", (TEST_TAG + '%',))
    conn.commit()
    conn.close()


def insert_loan(conn, loan_type, currency, amount, issue_date, name_suffix):
    cur = conn.execute(
        """INSERT INTO loans (loan_type, counterparty, description, total_amount, currency, issue_date, status)
           VALUES (?, ?, 'test', ?, ?, ?, 'ochiq')""",
        (loan_type, f"{TEST_TAG}{name_suffix}", amount, currency, issue_date)
    )
    return cur.lastrowid


def insert_payment(conn, loan_id, amount, date='2026-02-01'):
    conn.execute("INSERT INTO loan_payments (loan_id, date, amount) VALUES (?, ?, ?)",
                 (loan_id, date, amount))


def test_uzs_only():
    """UZS-only: konversiyasiz to'g'ri summa."""
    setup()
    conn = get_db()
    insert_loan(conn, 'olgan', 'UZS', 100_000_000, '2026-01-15', '_uzs1')
    insert_loan(conn, 'olgan', 'UZS', 50_000_000, '2026-01-15', '_uzs2')
    conn.commit()
    conn.close()

    s = get_loan_summary()
    assert s['total_taken'] == 150_000_000, f"UZS-only total: kutilgan 150M, olindi {s['total_taken']}"
    assert s['taken_remaining'] == 150_000_000, f"UZS-only remaining: kutilgan 150M, olindi {s['taken_remaining']}"
    teardown()
    print("  [OK] test_uzs_only")


def test_mixed_currency_taken():
    """USD + UZS qarz: USD UZS ga konvertirlanadi va to'g'ri jamlanadi."""
    setup()
    conn = get_db()
    insert_loan(conn, 'olgan', 'USD', 10_000, '2026-01-15', '_usd')
    insert_loan(conn, 'olgan', 'UZS', 100_000_000, '2026-01-15', '_uzs')
    conn.commit()
    conn.close()

    rate = get_rate_for_date('2026-01-15')
    expected = 10_000 * rate + 100_000_000

    s = get_loan_summary()
    assert abs(s['total_taken'] - expected) < 0.01, \
        f"Mixed: kutilgan {expected}, olindi {s['total_taken']}, kurs={rate}"
    teardown()
    print(f"  [OK] test_mixed_currency_taken (rate={rate}, expected={expected:,.0f})")


def test_payment_in_loan_currency():
    """USD qarzga USD to'lov: qoldiq UZS ga to'g'ri konvertirlanadi."""
    setup()
    conn = get_db()
    loan_id = insert_loan(conn, 'olgan', 'USD', 10_000, '2026-01-15', '_usd_paid')
    insert_payment(conn, loan_id, 3_000)  # 3,000 USD to'lov
    conn.commit()
    conn.close()

    rate = get_rate_for_date('2026-01-15')
    expected_total = 10_000 * rate
    expected_remaining = (10_000 - 3_000) * rate

    s = get_loan_summary()
    assert abs(s['total_taken'] - expected_total) < 0.01, \
        f"USD total: kutilgan {expected_total}, olindi {s['total_taken']}"
    assert abs(s['taken_remaining'] - expected_remaining) < 0.01, \
        f"USD remaining: kutilgan {expected_remaining}, olindi {s['taken_remaining']}"
    teardown()
    print(f"  [OK] test_payment_in_loan_currency (remaining={expected_remaining:,.0f})")


def test_taken_vs_given_separation():
    """olgan va bergan qarzlar alohida hisoblanadi, aralashmaydi."""
    setup()
    conn = get_db()
    insert_loan(conn, 'olgan', 'UZS', 100_000_000, '2026-01-15', '_takn')
    insert_loan(conn, 'bergan', 'UZS', 30_000_000, '2026-01-15', '_givn')
    conn.commit()
    conn.close()

    s = get_loan_summary()
    assert s['total_taken'] == 100_000_000
    assert s['total_given'] == 30_000_000
    teardown()
    print("  [OK] test_taken_vs_given_separation")


def test_empty_db():
    """Bo'sh holat: barcha summalar 0 bo'lishi kerak."""
    setup()
    s = get_loan_summary()
    assert s['total_taken'] == 0
    assert s['total_given'] == 0
    assert s['taken_remaining'] == 0
    assert s['given_remaining'] == 0
    assert s['overdue_count'] == 0
    print("  [OK] test_empty_db")


if __name__ == '__main__':
    print("Running get_loan_summary regression tests...")
    try:
        test_empty_db()
        test_uzs_only()
        test_mixed_currency_taken()
        test_payment_in_loan_currency()
        test_taken_vs_given_separation()
        print("\nAll tests passed.")
    except AssertionError as e:
        print(f"\nFAIL: {e}")
        teardown()
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: {e}")
        teardown()
        raise
