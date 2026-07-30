"""Fiscal period management — create, soft-close, hard-close, reopen."""
from calendar import monthrange
from datetime import datetime, date
from flask import Blueprint, request
from auth import require_role
from utils import render_page
from models import get_db, close_fiscal_period, snapshot_hours_rates, snapshot_period_allocations

bp = Blueprint('periods', __name__)


def _month_bounds(code):
    """'2026-04' → ('2026-04-01', '2026-04-30')"""
    year, month = int(code[:4]), int(code[5:7])
    _, last_day = monthrange(year, month)
    return f"{code}-01", f"{code}-{last_day:02d}"


def _suggest_next(periods):
    """Return the YYYY-MM code for the month after the latest existing period."""
    if periods:
        code = periods[0]['code']
        y, m = int(code[:4]), int(code[5:7])
        m += 1
        if m > 12:
            m, y = 1, y + 1
        return f"{y:04d}-{m:02d}"
    return date.today().strftime('%Y-%m')


@bp.route('/periods', methods=['GET', 'POST'])
@require_role('manager', 'admin')
def periods_page():
    msg = ''
    if request.method == 'POST':
        msg = _handle_post()

    conn = get_db()
    periods = conn.execute(
        "SELECT * FROM fiscal_periods ORDER BY code DESC"
    ).fetchall()

    # Count snapshot completeness for each period
    period_info = []
    for p in periods:
        code = p['code']
        snap_count = conn.execute(
            "SELECT COUNT(*) FROM period_allocations WHERE period=?", (code,)
        ).fetchone()[0]
        hours_snapped = conn.execute(
            "SELECT COUNT(*) FROM project_hours WHERE period=? AND rate_snapshot_source IS NOT NULL",
            (code,)
        ).fetchone()[0]
        hours_total = conn.execute(
            "SELECT COUNT(*) FROM project_hours WHERE period=?", (code,)
        ).fetchone()[0]
        period_info.append({
            'code': code,
            'start_date': p['start_date'],
            'end_date': p['end_date'],
            'status': p['status'],
            'closed_at': p['closed_at'],
            'notes': p['notes'],
            'staff_snapshots': snap_count,
            'hours_snapped': hours_snapped,
            'hours_total': hours_total,
        })
    conn.close()

    stats = {'open': 0, 'soft_closed': 0, 'hard_closed': 0}
    for p in period_info:
        s = p['status']
        if s in stats:
            stats[s] += 1

    return render_page('periods', 'periods.html',
        period_info=period_info,
        stats=stats,
        suggested=_suggest_next(periods),
        msg=msg,
    )


def _handle_post():
    action = request.form.get('action', '')
    code = request.form.get('code', '').strip()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if action == 'create':
        if not code or len(code) != 7 or code[4] != '-':
            return '<div class="alert alert-warn">Noto\'g\'ri format. YYYY-MM ko\'rinishida kiriting (masalan: 2026-04).</div>'
        try:
            start, end = _month_bounds(code)
        except (ValueError, IndexError):
            return '<div class="alert alert-warn">Noto\'g\'ri oy kodi.</div>'
        notes = request.form.get('notes', '').strip()
        conn = get_db()
        if conn.execute("SELECT id FROM fiscal_periods WHERE code=?", (code,)).fetchone():
            conn.close()
            return f'<div class="alert alert-warn">{code} davri allaqachon mavjud.</div>'
        conn.execute(
            "INSERT INTO fiscal_periods (code, start_date, end_date, status, notes) VALUES (?,?,?,?,?)",
            (code, start, end, 'open', notes)
        )
        conn.commit()
        conn.close()
        return f'<div class="alert alert-success">{code} davri yaratildi ({start} — {end}).</div>'

    if action == 'soft_close':
        snapshot_period_allocations(code)
        snapshot_hours_rates(code, source='period_close')
        conn = get_db()
        conn.execute(
            "UPDATE fiscal_periods SET status='soft_closed', closed_at=? WHERE code=?",
            (now, code)
        )
        conn.commit()
        conn.close()
        return (f'<div class="alert alert-success">'
                f'{code} yumshoq yopildi. Xodimlar allokatsiyasi va stavkalar saqlandi.'
                f'</div>')

    if action == 'hard_close':
        close_fiscal_period(code, status='hard_closed')
        return (f'<div class="alert alert-success">'
                f'{code} to\'liq yopildi. Barcha snapshotlar saqlandi — tarix himoyalangan.'
                f'</div>')

    if action == 'reopen':
        conn = get_db()
        row = conn.execute(
            "SELECT status FROM fiscal_periods WHERE code=?", (code,)
        ).fetchone()
        if not row:
            conn.close()
            return '<div class="alert alert-warn">Davr topilmadi.</div>'
        if row['status'] == 'hard_closed':
            conn.close()
            return '<div class="alert alert-warn">Qattiq yopilgan davrni qayta ochib bo\'lmaydi. Tuzatish uchun reversal yozuvidan foydalaning.</div>'
        conn.execute(
            "UPDATE fiscal_periods SET status='open', closed_at=NULL WHERE code=?", (code,)
        )
        conn.commit()
        conn.close()
        return f'<div class="alert alert-success">{code} qayta ochildi.</div>'

    if action == 'delete':
        conn = get_db()
        row = conn.execute(
            "SELECT status FROM fiscal_periods WHERE code=?", (code,)
        ).fetchone()
        if not row:
            conn.close()
            return '<div class="alert alert-warn">Davr topilmadi.</div>'
        if row['status'] != 'open':
            conn.close()
            return '<div class="alert alert-warn">Faqat ochiq (yopilmagan) davrlarni o\'chirish mumkin.</div>'
        conn.execute("DELETE FROM fiscal_periods WHERE code=?", (code,))
        conn.commit()
        conn.close()
        return f'<div class="alert alert-success">{code} o\'chirildi.</div>'

    return ''
