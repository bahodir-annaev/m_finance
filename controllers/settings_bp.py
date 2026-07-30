"""Settings route."""
from datetime import date as _date
from flask import Blueprint, request, redirect, jsonify
import urllib.request
import json
from auth import require_role
from utils import render_page
from models import get_db

bp = Blueprint('settings', __name__)


@bp.route('/settings', methods=['GET', 'POST'])
@require_role('admin')
def settings_page():
    if request.method == 'POST':
        conn = get_db()
        for key in request.form:
            if key.startswith('setting_'):
                try:
                    value = float(request.form[key].replace(',', '.').strip())
                except (TypeError, ValueError):
                    continue  # skip unparseable input instead of 500-ing the page
                conn.execute("UPDATE settings SET value=? WHERE key=?", (value, key[8:]))
        conn.commit()
        conn.close()
        return redirect('/settings')

    conn = get_db()
    settings = conn.execute("SELECT * FROM settings ORDER BY key").fetchall()
    rates = conn.execute("SELECT * FROM exchange_rates ORDER BY date DESC").fetchall()
    conn.close()
    return render_page('settings', 'settings.html',
                       settings=settings, rates=rates,
                       today=_date.today().isoformat())


@bp.route('/settings/rates', methods=['POST'])
@require_role('admin')
def add_rate():
    d = request.form.get('date', '').strip()
    r = request.form.get('rate', '').strip()
    if d and r:
        conn = get_db()
        conn.execute(
            "INSERT INTO exchange_rates (date, rate) VALUES (?,?) "
            "ON CONFLICT(date) DO UPDATE SET rate=excluded.rate",
            (d, float(r))
        )
        conn.commit()
        conn.close()
    return redirect('/settings')


@bp.route('/settings/fetch-rate')
@require_role('admin')
def fetch_rate():
    d = request.args.get('date', '').strip()
    if not d:
        return jsonify({'error': 'date required'}), 400
    url = f'https://cbu.uz/oz/arkhiv-kursov-valyut/json/USD/{d}/'
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        rate = float(data[0]['Rate'])
        return jsonify({'rate': rate})
    except Exception as e:
        return jsonify({'error': str(e)}), 502
