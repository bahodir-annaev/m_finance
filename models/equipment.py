"""Equipment and license depreciation — pure DB queries, no cross-module deps."""
from .base import get_db

# Straight-line depreciation runs only while the asset is inside its lifespan.
# Without the date check a fully-depreciated laptop kept inflating its owner's
# monthly cost (and every quoted rate) forever, until someone remembered to
# deactivate it. Rows without a purchase_date are included as before.
_NOT_EXPIRED = ("(purchase_date IS NULL OR purchase_date = '' OR "
                "date(purchase_date, '+' || lifespan_months || ' months') > date('now'))")


def get_general_equipment_monthly():
    conn = get_db()
    rows = conn.execute(
        f"SELECT quantity, price, lifespan_months FROM general_equipment"
        f" WHERE is_active=1 AND {_NOT_EXPIRED}"
    ).fetchall()
    conn.close()
    return sum(
        (r['quantity'] or 1) * r['price'] / max(r['lifespan_months'], 1)
        for r in rows
    )


def get_personal_equipment_monthly(staff_id):
    conn = get_db()
    rows = conn.execute(
        f"SELECT price, lifespan_months FROM personal_equipment"
        f" WHERE staff_id=? AND is_active=1 AND {_NOT_EXPIRED}",
        (staff_id,)
    ).fetchall()
    conn.close()
    return sum(r['price'] / max(r['lifespan_months'], 1) for r in rows)


def get_personal_license_monthly(staff_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT annual_cost FROM personal_licenses WHERE staff_id=? AND is_active=1",
        (staff_id,)
    ).fetchall()
    conn.close()
    return sum(r['annual_cost'] / 12 for r in rows)
