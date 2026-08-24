"""Flask-Login setup, User model, and the role decorator."""
from functools import wraps

from flask import redirect, abort
from flask_login import LoginManager, UserMixin, current_user

from models.base import get_db

login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = ''

# Who may do what. Viewers read; managers enter and post documents; only an
# admin may close a period or void a posted document.
ROLE_LEVELS = {'viewer': 0, 'manager': 1, 'admin': 2}


class User(UserMixin):
    def __init__(self, id, username, role, is_active):
        self.id = id
        self.username = username
        self.role = role
        self._active = is_active

    @property
    def is_active(self):
        return bool(self._active)

    def can(self, level):
        return ROLE_LEVELS.get(self.role, 0) >= ROLE_LEVELS.get(level, 0)


@login_manager.user_loader
def load_user(user_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id=?", (int(user_id),)).fetchone()
    conn.close()
    if row:
        return User(row['id'], row['username'], row['role'], row['is_active'])
    return None


def require_role(*roles):
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


def require_level(level):
    """Allow anyone at or above a privilege level ('manager', 'admin')."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect('/login')
            if not current_user.can(level):
                abort(403)
            return f(*args, **kwargs)
        return wrapped
    return decorator
