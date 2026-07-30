"""Authentication and user-management routes."""
from flask import Blueprint, request, redirect, render_template, abort
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from auth import User, require_role
from models.base import get_db
from utils import render_page

bp = Blueprint('auth', __name__)


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect('/')

    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        conn = get_db()
        row = conn.execute(
            "SELECT * FROM users WHERE username=? AND is_active=1", (username,)
        ).fetchone()
        conn.close()
        if row and check_password_hash(row['password_hash'], password):
            user = User(row['id'], row['username'], row['role'], row['is_active'])
            login_user(user, remember=bool(request.form.get('remember')))
            return redirect(request.args.get('next') or '/')
        error = 'Noto\'g\'ri login yoki parol.'

    return render_template('login.html', error=error)


@bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect('/login')


# ── User management (admin only) ─────────────────────────────────────────────

@bp.route('/admin/users')
@require_role('admin')
def admin_users():
    conn = get_db()
    users = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    conn.close()
    return render_page('admin_users', 'admin_users.html', users=users)


@bp.route('/admin/users/add', methods=['POST'])
@require_role('admin')
def admin_users_add():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    role = request.form.get('role', 'viewer')
    if not username or not password or role not in ('admin', 'manager', 'viewer'):
        return redirect('/admin/users')
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
            (username, generate_password_hash(password), role)
        )
        conn.commit()
    except Exception:
        pass
    conn.close()
    return redirect('/admin/users')


@bp.route('/admin/users/<int:user_id>/toggle', methods=['POST'])
@require_role('admin')
def admin_users_toggle(user_id):
    if user_id == current_user.id:
        return redirect('/admin/users')
    conn = get_db()
    row = conn.execute("SELECT is_active FROM users WHERE id=?", (user_id,)).fetchone()
    if row:
        conn.execute("UPDATE users SET is_active=? WHERE id=?",
                     (0 if row['is_active'] else 1, user_id))
        conn.commit()
    conn.close()
    return redirect('/admin/users')


@bp.route('/admin/users/<int:user_id>/reset-password', methods=['POST'])
@require_role('admin')
def admin_users_reset_password(user_id):
    new_password = request.form.get('new_password', '')
    if not new_password:
        return redirect('/admin/users')
    conn = get_db()
    conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                 (generate_password_hash(new_password), user_id))
    conn.commit()
    conn.close()
    return redirect('/admin/users')
