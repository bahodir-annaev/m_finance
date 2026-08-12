"""Regression test: follow-up payments linked to a partially-paid transaction.

Run: python test_tx_payments.py

A later payment on a partial invoice is its own `transactions` row carrying
parent_tx_id + amount=0. These tests pin the two properties that make that safe:
nothing double counts (SUM(amount) and SUM(paid) each see the money once), and
"is it settled?" is measured against paid + SUM(children), never raw `paid`.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from models import (
    get_db, init_db, get_cash_flow_by_month, get_ar_aging, calculate_project_cost,
    add_transaction_payment, delete_transaction, update_transaction,
    get_rate_for_date, calculate_fx_gain_loss,
)

TEST_TAG = '__TEST_TX_PAY__'


def setup():
    """Test izolyatsiyasi: sxemani ishga tushir + eski test qatorlarini tozala."""
    init_db()
    _purge()


def teardown():
    _purge()


def _purge():
    conn = get_db()
    # Payment rows first — the parent delete would otherwise trip the FK.
    conn.execute(
        "DELETE FROM transactions WHERE parent_tx_id IN"
        " (SELECT id FROM transactions WHERE doc_id LIKE ?)", (TEST_TAG + '%',)
    )
    conn.execute("DELETE FROM transactions WHERE doc_id LIKE ?", (TEST_TAG + '%',))
    conn.execute("DELETE FROM projects WHERE name LIKE ?", (TEST_TAG + '%',))
    conn.commit()
    conn.close()


def insert_project(conn, suffix=''):
    cur = conn.execute(
        "INSERT INTO projects (name, client, status, is_billable) VALUES (?,?,'active',1)",
        (f"{TEST_TAG}{suffix}", 'test client')
    )
    return cur.lastrowid


def insert_invoice(conn, amount, paid, date, project_id=None, currency='UZS',
                   tx_type='tushum', suffix=''):
    rate = get_rate_for_date(date)
    status = 'partial' if 0 < paid < amount else ('paid' if paid >= amount else 'pending')
    cur = conn.execute(
        "INSERT INTO transactions (direction, tx_type, date, doc_id, project_id,"
        " description, amount, amount_usd, paid, currency, exchange_rate, status)"
        " VALUES ('external',?,?,?,?,'test invoice',?,?,?,?,?,?)",
        (tx_type, date, f"{TEST_TAG}{suffix}", project_id, amount,
         amount / rate if currency == 'USD' and rate else 0,
         paid, currency, rate, status)
    )
    return cur.lastrowid


def _row(tx_id):
    conn = get_db()
    r = conn.execute("SELECT * FROM transactions WHERE id=?", (tx_id,)).fetchone()
    conn.close()
    return r


def _settled(tx_id):
    conn = get_db()
    s = conn.execute(
        "SELECT t.paid + COALESCE((SELECT SUM(paid) FROM transactions"
        " WHERE parent_tx_id = t.id),0) AS s FROM transactions t WHERE t.id=?", (tx_id,)
    ).fetchone()['s']
    conn.close()
    return s


def test_partial_invoice_baseline():
    """Bog'langan to'lovsiz: status 'partial', settled == paid."""
    setup()
    conn = get_db()
    tx = insert_invoice(conn, 100_000_000, 30_000_000, '2026-03-01')
    conn.commit()
    conn.close()

    assert _row(tx)['status'] == 'partial', f"kutilgan partial, olindi {_row(tx)['status']}"
    assert _settled(tx) == 30_000_000, f"settled: kutilgan 30M, olindi {_settled(tx)}"
    teardown()
    print("  [OK] test_partial_invoice_baseline")


def test_payments_settle_parent():
    """Ikki to'lovdan keyin ota-qator 'paid' ga o'tadi, settled = 100M."""
    setup()
    conn = get_db()
    tx = insert_invoice(conn, 100_000_000, 30_000_000, '2026-03-01')
    conn.commit()
    conn.close()

    p1, err = add_transaction_payment(tx, {'date': '2026-05-14', 'amount': 45_000_000})
    assert err is None, f"1-to'lov xatosi: {err}"
    assert _row(tx)['status'] == 'partial', "45M dan keyin hali partial bo'lishi kerak"

    p2, err = add_transaction_payment(tx, {'date': '2026-07-02', 'amount': 25_000_000})
    assert err is None, f"2-to'lov xatosi: {err}"

    assert _settled(tx) == 100_000_000, f"settled: kutilgan 100M, olindi {_settled(tx)}"
    assert _row(tx)['status'] == 'paid', f"kutilgan paid, olindi {_row(tx)['status']}"

    # A payment row is a leaf carrying amount=0 and the parent's classification.
    child = _row(p2)
    assert child['amount'] == 0, f"to'lov qatori amount=0 bo'lishi kerak, olindi {child['amount']}"
    assert child['paid'] == 25_000_000
    assert child['status'] == 'paid'
    assert child['parent_tx_id'] == tx
    assert child['tx_type'] == 'tushum', "to'lov qatori ota tx_type ni olishi kerak"
    assert child['direction'] == 'external'
    teardown()
    print("  [OK] test_payments_settle_parent")


def test_cash_flow_books_each_payment_in_its_own_month():
    """Har bir to'lov o'z oyida — jami 100M, ikki marta sanalmaydi."""
    setup()
    conn = get_db()
    tx = insert_invoice(conn, 100_000_000, 30_000_000, '2026-03-01')
    conn.commit()
    conn.close()
    add_transaction_payment(tx, {'date': '2026-05-14', 'amount': 45_000_000})
    add_transaction_payment(tx, {'date': '2026-07-02', 'amount': 25_000_000})

    by_month = {r['month']: r for r in get_cash_flow_by_month()}
    for month, expected in (('2026-03', 30_000_000), ('2026-05', 45_000_000),
                            ('2026-07', 25_000_000)):
        assert month in by_month, f"{month} cash flow da yo'q"
        got = by_month[month]['income']
        assert got >= expected, f"{month}: kutilgan >= {expected:,.0f}, olindi {got:,.0f}"
    teardown()
    print("  [OK] test_cash_flow_books_each_payment_in_its_own_month")


def test_project_income_not_double_counted():
    """Loyiha daromadi 100M — 200M emas (ota + bolalar qo'shilib ketmaydi)."""
    setup()
    conn = get_db()
    pid = insert_project(conn)
    tx = insert_invoice(conn, 100_000_000, 30_000_000, '2026-03-01', project_id=pid)
    conn.commit()
    conn.close()
    add_transaction_payment(tx, {'date': '2026-05-14', 'amount': 45_000_000})
    add_transaction_payment(tx, {'date': '2026-07-02', 'amount': 25_000_000})

    result = calculate_project_cost(pid)
    assert abs(result['income'] - 100_000_000) < 0.01, \
        f"loyiha daromadi: kutilgan 100M, olindi {result['income']:,.0f}"
    teardown()
    print("  [OK] test_project_income_not_double_counted")


def test_ar_aging_uses_settled():
    """AR aging qoldig'i to'lovlar bilan kamayadi va nolga tushadi.

    get_ar_aging() butun bazani jamlaydi, shuning uchun mutlaq emas, farq
    (delta) tekshiriladi — bazadagi boshqa qarzlar natijaga ta'sir qilmasin.
    """
    setup()
    before = get_ar_aging()['total_outstanding']

    conn = get_db()
    tx = insert_invoice(conn, 100_000_000, 30_000_000, '2026-03-01')
    conn.commit()
    conn.close()

    base = get_ar_aging()['total_outstanding'] - before
    assert abs(base - 70_000_000) < 0.01, f"boshlang'ich AR farqi: kutilgan 70M, olindi {base:,.0f}"

    add_transaction_payment(tx, {'date': '2026-05-14', 'amount': 45_000_000})
    mid = get_ar_aging()['total_outstanding'] - before
    assert abs(mid - 25_000_000) < 0.01, f"45M dan keyin AR farqi: kutilgan 25M, olindi {mid:,.0f}"

    add_transaction_payment(tx, {'date': '2026-07-02', 'amount': 25_000_000})
    end = get_ar_aging()['total_outstanding'] - before
    assert abs(end) < 0.01, f"to'liq to'langach AR farqi: kutilgan 0, olindi {end:,.0f}"
    teardown()
    print("  [OK] test_ar_aging_uses_settled")


def test_delete_payment_reverts_parent():
    """To'lovni o'chirish ota statusini qayta hisoblaydi."""
    setup()
    conn = get_db()
    tx = insert_invoice(conn, 100_000_000, 30_000_000, '2026-03-01')
    conn.commit()
    conn.close()
    p1, _ = add_transaction_payment(tx, {'date': '2026-05-14', 'amount': 70_000_000})
    assert _row(tx)['status'] == 'paid', "70M to'lovdan keyin paid bo'lishi kerak"

    ok, err = delete_transaction(p1)
    assert ok, f"to'lovni o'chirish muvaffaqiyatsiz: {err}"
    assert _row(tx)['status'] == 'partial', \
        f"o'chirilgach partial bo'lishi kerak, olindi {_row(tx)['status']}"
    assert _settled(tx) == 30_000_000, f"settled: kutilgan 30M, olindi {_settled(tx)}"
    teardown()
    print("  [OK] test_delete_payment_reverts_parent")


def test_delete_parent_with_payments_refused():
    """To'lovlari bor hisob-fakturani o'chirib bo'lmaydi (yetim qator qolmasin)."""
    setup()
    conn = get_db()
    tx = insert_invoice(conn, 100_000_000, 30_000_000, '2026-03-01')
    conn.commit()
    conn.close()
    add_transaction_payment(tx, {'date': '2026-05-14', 'amount': 20_000_000})

    ok, err = delete_transaction(tx)
    assert not ok, "to'lovlari bor ota-qator o'chirilmasligi kerak edi"
    assert err == 'tx_del_has_payments', f"kutilgan tx_del_has_payments, olindi {err}"
    assert _row(tx) is not None, "ota-qator saqlanib qolishi kerak"
    teardown()
    print("  [OK] test_delete_parent_with_payments_refused")


def test_rejects_bad_input():
    """0 yoki manfiy summa, va to'lov qatoriga to'lov — rad etiladi."""
    setup()
    conn = get_db()
    tx = insert_invoice(conn, 100_000_000, 30_000_000, '2026-03-01')
    conn.commit()
    conn.close()

    _, err = add_transaction_payment(tx, {'date': '2026-05-14', 'amount': 0})
    assert err == 'tx_pay_amount_err', f"0 summa: kutilgan tx_pay_amount_err, olindi {err}"
    _, err = add_transaction_payment(tx, {'date': '2026-05-14', 'amount': -5})
    assert err == 'tx_pay_amount_err', f"manfiy summa: kutilgan tx_pay_amount_err, olindi {err}"
    _, err = add_transaction_payment(999_999_999, {'date': '2026-05-14', 'amount': 10})
    assert err == 'tx_pay_parent_missing', f"yo'q ota: kutilgan tx_pay_parent_missing, olindi {err}"

    p1, _ = add_transaction_payment(tx, {'date': '2026-05-14', 'amount': 10_000_000})
    _, err = add_transaction_payment(p1, {'date': '2026-06-01', 'amount': 5_000_000})
    assert err == 'tx_pay_on_payment', f"zanjir: kutilgan tx_pay_on_payment, olindi {err}"
    teardown()
    print("  [OK] test_rejects_bad_input")


def test_usd_payment_stored_in_uzs_and_excluded_from_fx():
    """USD to'lov UZS da saqlanadi; FX foyda/zarari o'zgarmaydi (bola hisobga olinmaydi)."""
    setup()
    conn = get_db()
    pid = insert_project(conn)
    tx = insert_invoice(conn, 10_000, 3_000, '2026-03-01', project_id=pid, currency='USD')
    conn.commit()
    conn.close()

    fx_before = calculate_fx_gain_loss(pid)
    pay_date = '2026-05-14'
    p1, err = add_transaction_payment(
        tx, {'date': pay_date, 'amount': 4_000, 'currency': 'USD'})
    assert err is None, f"USD to'lov xatosi: {err}"

    rate = get_rate_for_date(pay_date)
    child = _row(p1)
    assert abs(child['paid'] - round(4_000 * rate, 0)) < 1, \
        f"USD to'lov UZS da saqlanishi kerak: kutilgan {4_000 * rate:,.0f}, olindi {child['paid']:,.0f}"
    assert abs(child['exchange_rate'] - rate) < 0.01, "to'lov sanasidagi kurs saqlanishi kerak"

    fx_after = calculate_fx_gain_loss(pid)
    assert abs(fx_after - fx_before) < 0.01, \
        f"FX o'zgarmasligi kerak: oldin {fx_before:,.0f}, keyin {fx_after:,.0f}"
    teardown()
    print("  [OK] test_usd_payment_stored_in_uzs_and_excluded_from_fx")


def test_edit_parent_paid_still_settlement_aware():
    """Otaning `paid` ini tahrirlash bolalar bilan birga hisoblanadi."""
    setup()
    conn = get_db()
    tx = insert_invoice(conn, 100_000_000, 30_000_000, '2026-03-01')
    conn.commit()
    conn.close()
    add_transaction_payment(tx, {'date': '2026-05-14', 'amount': 45_000_000})

    # 55M + 45M bola = 100M -> paid
    update_transaction(tx, {'paid': 55_000_000})
    assert _row(tx)['status'] == 'paid', \
        f"55M + 45M = 100M -> paid kutilgan, olindi {_row(tx)['status']}"

    update_transaction(tx, {'paid': 10_000_000})
    assert _row(tx)['status'] == 'partial', \
        f"10M + 45M = 55M -> partial kutilgan, olindi {_row(tx)['status']}"
    teardown()
    print("  [OK] test_edit_parent_paid_still_settlement_aware")


def test_editing_a_payment_cannot_set_amount():
    """To'lov qatorining amount'i 0 bo'lib qoladi — SUM(amount) ikki marta sanamasin.

    Umumiy tahrirlash modali hamma ustunni yuboradi, shuning uchun server
    invariantni har safar qayta o'rnatishi kerak (loyiha tannarxi, milestone
    `direct` va harajat plitalari parent_tx_id bo'yicha filtrlamaydi).
    """
    setup()
    conn = get_db()
    pid = insert_project(conn)
    tx = insert_invoice(conn, 100_000_000, 30_000_000, '2026-03-01', project_id=pid,
                        tx_type='outsourcing')
    conn.commit()
    conn.close()
    p1, _ = add_transaction_payment(tx, {'date': '2026-05-14', 'amount': 45_000_000})

    # Modal amount + paid ni birga yuboradi — amount e'tiborga olinmasligi kerak.
    update_transaction(p1, {'amount': 45_000_000, 'paid': 45_000_000,
                            'notes': 'tahrirlangan'})
    child = _row(p1)
    assert child['amount'] == 0, \
        f"to'lov qatori amount=0 bo'lib qolishi kerak, olindi {child['amount']:,.0f}"
    assert child['amount_usd'] == 0, f"amount_usd 0 bo'lishi kerak, olindi {child['amount_usd']}"
    assert child['notes'] == 'tahrirlangan', 'boshqa maydonlar saqlanishi kerak'
    assert child['status'] == 'paid'

    # Loyiha outsourcing tannarxi 100M bo'lib qolsin, 145M emas.
    conn = get_db()
    total = conn.execute(
        "SELECT COALESCE(SUM(amount),0) AS s FROM transactions"
        " WHERE project_id=? AND tx_type='outsourcing'", (pid,)
    ).fetchone()['s']
    conn.close()
    assert total == 100_000_000, f"SUM(amount): kutilgan 100M, olindi {total:,.0f}"
    teardown()
    print("  [OK] test_editing_a_payment_cannot_set_amount")


if __name__ == '__main__':
    print("Running follow-up payment regression tests...")
    try:
        test_partial_invoice_baseline()
        test_payments_settle_parent()
        test_cash_flow_books_each_payment_in_its_own_month()
        test_project_income_not_double_counted()
        test_ar_aging_uses_settled()
        test_delete_payment_reverts_parent()
        test_delete_parent_with_payments_refused()
        test_rejects_bad_input()
        test_usd_payment_stored_in_uzs_and_excluded_from_fx()
        test_edit_parent_paid_still_settlement_aware()
        test_editing_a_payment_cannot_set_amount()
        print("\nAll tests passed.")
    except AssertionError as e:
        print(f"\nFAIL: {e}")
        teardown()
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: {e}")
        teardown()
        raise
