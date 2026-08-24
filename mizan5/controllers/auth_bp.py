"""Login, logout and language switching."""
from flask import Blueprint, redirect, request, make_response, render_template
from flask_login import login_user, logout_user, login_required
from werkzeug.security import check_password_hash

from auth import User
from models import get_db
from utils import t, get_lang, SUPPORTED_LANGS

bp = Blueprint('auth', __name__)


@bp.route('/login', methods=['GET', 'POST'])
def login():
    error = ''
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        conn = get_db()
        row = conn.execute("SELECT * FROM users WHERE username=? AND is_active=1",
                           (username,)).fetchone()
        conn.close()
        if row and check_password_hash(row['password_hash'], password):
            login_user(User(row['id'], row['username'], row['role'], row['is_active']))
            return redirect(request.args.get('next') or '/')
        error = t('login_failed')
    return render_template('login.html', t=t, lang=get_lang(), error=error)


@bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect('/login')


@bp.route('/lang/<code>')
def set_lang(code):
    """Store the chosen language in a cookie and return where we came from."""
    if code not in SUPPORTED_LANGS:
        code = 'uz'
    response = make_response(redirect(request.referrer or '/'))
    response.set_cookie('mizan_lang', code, max_age=60 * 60 * 24 * 365)
    return response
