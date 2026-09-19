"""Settings, exchange rates (manual + CBU fetch) and user administration."""
import json
import urllib.request

from flask import Blueprint, request, redirect, flash, jsonify
from flask_login import login_required, current_user
from werkzeug.security import generate_password_hash

from auth import require_level
from models import get_db, get_current_usd_rate, today_str
from utils import render_page, t, parse_float, parse_int

bp = Blueprint('settings', __name__)


@bp.route('/settings')
@login_required
def settings_page():
    conn = get_db()
    try:
        settings = [dict(r) for r in conn.execute(
            "SELECT * FROM settings ORDER BY key")]
        rates = [dict(r) for r in conn.execute(
            "SELECT * FROM exchange_rates ORDER BY date DESC")]
        # Only an admin may see the user register (the card is hidden for others).
        users = [dict(r) for r in conn.execute(
            "SELECT id, username, role, is_active, created_at FROM users ORDER BY username")
        ] if current_user.can('admin') else []
        lookups = {name: [dict(r) for r in conn.execute(
            f"SELECT * FROM {name} ORDER BY sort_order, code")]
            for name in ('payment_types', 'work_types', 'departments', 'staff_roles')}
    finally:
        conn.close()
    return render_page('settings', 'settings.html', settings=settings, rates=rates,
                       users=users, lookups=lookups, usd_rate=get_current_usd_rate(),
                       today=today_str(), title=t('nav_settings'))


@bp.route('/settings/save', methods=['POST'])
@login_required
@require_level('admin')
def settings_save():
    conn = get_db()
    try:
        for key, value in request.form.items():
            if not key.startswith('setting_'):
                continue
            try:
                conn.execute("UPDATE settings SET value=? WHERE key=?",
                             (float(value), key[len('setting_'):]))
            except ValueError:
                continue
        conn.commit()
        flash(f'<div class="alert alert-success">{t("saved")}</div>', 'success')
    finally:
        conn.close()
    return redirect('/settings')


@bp.route('/settings/rate', methods=['POST'])
@login_required
@require_level('manager')
def rate_save():
    date = request.form.get('date') or today_str()
    rate = parse_float(request.form.get('rate'))
    if rate > 0:
        conn = get_db()
        try:
            conn.execute("INSERT INTO exchange_rates (date, rate) VALUES (?,?)"
                         " ON CONFLICT(date) DO UPDATE SET rate=excluded.rate",
                         (date, rate))
            conn.commit()
            flash(f'<div class="alert alert-success">{t("saved")}</div>', 'success')
        finally:
            conn.close()
    return redirect('/settings')


CBU_URL = 'https://cbu.uz/oz/arkhiv-kursov-valyut/json/USD/{date}/'


@bp.route('/settings/fetch-rate')
@login_required
@require_level('manager')
def fetch_rate():
    """The Central Bank's official UZS/USD rate for a date, as JSON.

    Looked up on demand from the browser; nothing is stored until the user
    saves it, so a network failure only disables the button.
    """
    d = (request.args.get('date') or '').strip()
    if len(d) != 10:
        return jsonify({'error': 'date required'}), 400
    try:
        with urllib.request.urlopen(CBU_URL.format(date=d), timeout=8) as resp:
            data = json.loads(resp.read().decode())
        return jsonify({'rate': float(data[0]['Rate']), 'date': data[0].get('Date', d)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/settings/user', methods=['POST'])
@login_required
@require_level('admin')
def user_save():
    user_id = parse_int(request.form.get('id'))
    username = (request.form.get('username') or '').strip()
    password = request.form.get('password') or ''
    role = request.form.get('role') or 'viewer'
    is_active = 1 if request.form.get('is_active') else 0

    # Nobody may lock themselves out — deactivating or demoting the account
    # that is doing the editing would leave the app with no admin session.
    if user_id and user_id == current_user.id and (not is_active or role != 'admin'):
        flash(f'<div class="alert alert-warn">{t("user_self_lock")}</div>', 'warn')
        return redirect('/settings')

    conn = get_db()
    try:
        if user_id:
            conn.execute("UPDATE users SET username=?, role=?, is_active=? WHERE id=?",
                         (username, role, is_active, user_id))
            if password:
                conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                             (generate_password_hash(password), user_id))
        elif username and password:
            conn.execute("INSERT INTO users (username, password_hash, role, is_active)"
                         " VALUES (?,?,?,?)",
                         (username, generate_password_hash(password), role, is_active))
        conn.commit()
        flash(f'<div class="alert alert-success">{t("saved")}</div>', 'success')
    except Exception:
        flash(f'<div class="alert alert-error">{t("error")}</div>', 'error')
    finally:
        conn.close()
    return redirect('/settings')


@bp.route('/settings/lookup', methods=['POST'])
@login_required
@require_level('admin')
def lookup_save():
    table = request.form.get('table')
    if table not in ('payment_types', 'work_types', 'departments', 'staff_roles'):
        return redirect('/settings')
    rid = parse_int(request.form.get('id'))
    fields = {
        'code': (request.form.get('code') or '').strip(),
        'label_uz': (request.form.get('label_uz') or '').strip(),
        'label_en': request.form.get('label_en') or None,
        'label_ru': request.form.get('label_ru') or None,
        'sort_order': parse_int(request.form.get('sort_order'), 100) or 100,
        'is_active': 1 if request.form.get('is_active') else 0,
    }
    conn = get_db()
    try:
        if rid:
            sets = ', '.join(f'{k}=?' for k in fields)
            conn.execute(f"UPDATE {table} SET {sets} WHERE id=?",
                         list(fields.values()) + [rid])
        elif fields['code'] and fields['label_uz']:
            cols = ','.join(fields)
            ph = ','.join('?' * len(fields))
            conn.execute(f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({ph})",
                         list(fields.values()))
        conn.commit()
        flash(f'<div class="alert alert-success">{t("saved")}</div>', 'success')
    finally:
        conn.close()
    return redirect('/settings')
