"""Equipment and overhead page."""
from flask import Blueprint
from flask_login import login_required
from utils import render_page
from models import get_db

bp = Blueprint('equipment', __name__)


@bp.route('/equipment')
@login_required
def equipment_page():
    conn = get_db()
    personal = conn.execute('''
        SELECT pe.*, s.name as staff_name FROM personal_equipment pe
        LEFT JOIN staff s ON pe.staff_id = s.id WHERE pe.is_active=1 ORDER BY s.name
    ''').fetchall()
    general = conn.execute("SELECT * FROM general_equipment WHERE is_active=1").fetchall()
    licenses = conn.execute('''
        SELECT pl.*, s.name as staff_name FROM personal_licenses pl
        LEFT JOIN staff s ON pl.staff_id = s.id WHERE pl.is_active=1 ORDER BY s.name
    ''').fetchall()
    overhead = conn.execute("SELECT * FROM overhead WHERE is_active=1").fetchall()
    conn.close()

    p_total = sum(e['price'] / max(e['lifespan_months'], 1) for e in personal)
    g_total = sum(e['price'] / max(e['lifespan_months'], 1) for e in general)
    l_total = sum(l['annual_cost'] / 12 for l in licenses)
    o_total = sum(o['monthly_amount'] for o in overhead)

    return render_page('equipment', 'equipment.html',
        personal=personal, general=general, licenses=licenses, overhead=overhead,
        p_total=p_total, g_total=g_total, l_total=l_total, o_total=o_total,
        eq_staff_set=sorted(set(e['staff_name'] or '-' for e in personal)),
        lic_staff_set=sorted(set(l['staff_name'] or '-' for l in licenses)),
        lic_type_set=sorted(set(l['license_type'] for l in licenses)),
    )
