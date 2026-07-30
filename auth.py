"""Flask-Login setup, User model, and role-based access decorator."""
from functools import wraps
from flask import redirect, abort
from flask_login import LoginManager, UserMixin, current_user
from models.base import get_db

login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = ''


class User(UserMixin):
    def __init__(self, id, username, role, is_active):
        self.id = id
        self.username = username
        self.role = role
        self._active = is_active

    @property
    def is_active(self):
        return bool(self._active)


@login_manager.user_loader
def load_user(user_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id=?", (int(user_id),)).fetchone()
    conn.close()
    if row:
        return User(row['id'], row['username'], row['role'], row['is_active'])
    return None


def require_role(*roles):
    """Decorator that requires the current user to have one of the given roles."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect('/login')
            if current_user.role not in roles:
                abort(403)
            return f(*args, **kwargs)
        return wrapped
    return decorator
