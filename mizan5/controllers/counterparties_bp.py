"""Counterparties — clients, vendors, founders, staff payees."""
from flask import Blueprint, request, redirect, flash
from flask_login import login_required

from auth import require_level
from models import get_db, now_ts, get_aging, account_id_for, balances_by_analytic
from utils import render_page, t, parse_int

bp = Blueprint('counterparties', __name__)

TYPES = ('client', 'vendor', 'founder', 'bank', 'state', 'staff', 'other')


@bp.route('/counterparties')
@login_required
def counterparties_page():
    search = request.args.get('q') or ''
    cp_type = request.args.get('type') or ''
    conn = get_db()
    try:
        sql = ("SELECT c.*, (SELECT COUNT(*) FROM documents d"
               "   WHERE d.counterparty_id = c.id) AS doc_count"
               " FROM counterparties c WHERE 1=1")
        params = []
        if search:
            sql += " AND (ulower(c.name) LIKE ulower(?) OR c.inn LIKE ?)"
            params += [f'%{search}%', f'%{search}%']
        if cp_type:
            sql += " AND c.counterparty_type = ?"
            params.append(cp_type)
        sql += " ORDER BY c.is_active DESC, c.name"
        rows = [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()

    # Outstanding balances per counterparty, so the list doubles as a debtor view.
    ar = {b['key']: b['balance'] for b in
          balances_by_analytic([account_id_for('ar')], 'counterparty_id')}
    ap = {b['key']: b['balance'] for b in
          balances_by_analytic([account_id_for('ap')], 'counterparty_id')}
    for r in rows:
        r['receivable'] = ar.get(r['id'], 0.0)
        r['payable'] = ap.get(r['id'], 0.0)

    return render_page('counterparties', 'counterparties.html', rows=rows,
                       types=TYPES, search=search, cp_type=cp_type,
                       title=t('nav_counterparties'))


@bp.route('/counterparties/save', methods=['POST'])
@login_required
@require_level('manager')
def counterparty_save():
    cid = parse_int(request.form.get('id'))
    fields = {
        'name': (request.form.get('name') or '').strip(),
        'legal_name': (request.form.get('legal_name') or '').strip() or None,
        'inn': (request.form.get('inn') or '').strip() or None,
        'vat_reg_code': (request.form.get('vat_reg_code') or '').strip() or None,
        'counterparty_type': request.form.get('counterparty_type') or 'other',
        'default_currency': request.form.get('default_currency') or 'UZS',
        'bank_details': (request.form.get('bank_details') or '').strip() or None,
        'address': (request.form.get('address') or '').strip() or None,
        'phone': (request.form.get('phone') or '').strip() or None,
        'notes': (request.form.get('notes') or '').strip() or None,
        'is_active': 1 if request.form.get('is_active') else 0,
    }
    if not fields['name']:
        flash(f'<div class="alert alert-error">{t("required_field")}</div>', 'error')
        return redirect('/counterparties')

    conn = get_db()
    try:
        if cid:
            sets = ', '.join(f'{k}=?' for k in fields)
            conn.execute(f"UPDATE counterparties SET {sets}, updated_at=? WHERE id=?",
                         list(fields.values()) + [now_ts(), cid])
        else:
            cols = ','.join(fields)
            ph = ','.join('?' * len(fields))
            conn.execute(f"INSERT INTO counterparties ({cols}) VALUES ({ph})",
                         list(fields.values()))
        conn.commit()
        flash(f'<div class="alert alert-success">{t("saved")}</div>', 'success')
    except Exception:
        flash(f'<div class="alert alert-error">{t("error")}</div>', 'error')
    finally:
        conn.close()
    return redirect('/counterparties')
