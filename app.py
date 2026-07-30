"""MIZAN Finance v4.0 — Flask app factory + startup."""
import os
import secrets
import socket
import threading
import webbrowser

from flask import Flask, request
from models.base import init_db, get_db
from import_nizam import ensure_staff_exists, seed_equipment_and_licenses
from auth import login_manager

# Local-network prefixes that are allowed to reach the app.
_ALLOWED_PREFIXES = ('127.', '::1', '192.168.', '10.', '172.')

# File holding the auto-generated session secret (gitignored, never committed).
_SECRET_KEY_FILE = os.path.join(os.path.dirname(__file__), '.mizan_secret_key')

# Local environment file (gitignored). See .env.example for the template.
_ENV_FILE = os.path.join(os.path.dirname(__file__), '.env')


def _load_dotenv(path=_ENV_FILE):
    """Load KEY=VALUE lines from a local .env into os.environ.

    Minimal parser (no external dependency): ignores blank lines and comments,
    strips optional surrounding quotes, and never overrides variables already
    set in the real environment.
    """
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# Populate os.environ from .env before anything reads configuration.
_load_dotenv()


def _get_secret_key():
    """Return the Flask session secret.

    Priority: MIZAN_SECRET_KEY env var → persisted random key file → newly
    generated random key (persisted for next run). No secret is ever hardcoded
    in source, so this file is safe to publish.
    """
    env_key = os.environ.get('MIZAN_SECRET_KEY')
    if env_key:
        return env_key
    try:
        with open(_SECRET_KEY_FILE, 'r', encoding='utf-8') as f:
            key = f.read().strip()
        if key:
            return key
    except OSError:
        pass
    key = secrets.token_hex(32)
    try:
        with open(_SECRET_KEY_FILE, 'w', encoding='utf-8') as f:
            f.write(key)
    except OSError:
        pass
    return key


def create_app():
    app = Flask(__name__, static_folder='static', template_folder='templates')
    app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')
    app.config['SECRET_KEY'] = _get_secret_key()
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    login_manager.init_app(app)

    @app.before_request
    def restrict_to_local_network():
        ip = request.remote_addr or ''
        if not any(ip.startswith(p) for p in _ALLOWED_PREFIXES):
            return 'Access denied: this app is only available on the local network.', 403

    @app.errorhandler(403)
    def forbidden(e):
        return '<h2 style="font-family:sans-serif;color:#c00;padding:40px">403 — Ruxsat yo\'q</h2>', 403

    from controllers.auth_bp import bp as auth_bp
    from controllers.api_bp import bp as api_bp
    from controllers.dashboard_bp import bp as dashboard_bp
    from controllers.staff_bp import bp as staff_bp
    from controllers.projects_bp import bp as projects_bp
    from controllers.equipment_bp import bp as equipment_bp
    from controllers.accounting_bp import bp as accounting_bp
    from controllers.pricing_bp import bp as pricing_bp
    from controllers.cashflow_bp import bp as cashflow_bp
    from controllers.loans_bp import bp as loans_bp
    from controllers.dividends_bp import bp as dividends_bp
    from controllers.settings_bp import bp as settings_bp
    from controllers.external_bp import bp as external_bp
    from controllers.internal_bp import bp as internal_bp
    from controllers.import_export_bp import bp as import_export_bp
    from controllers.periods_bp import bp as periods_bp
    from controllers.reference_bp import bp as reference_bp
    from controllers.ai_bp import bp as ai_bp
    from controllers.milestones_bp import bp as milestones_bp

    for blueprint in [
        auth_bp, api_bp, dashboard_bp, staff_bp, projects_bp, equipment_bp,
        accounting_bp, pricing_bp, cashflow_bp, loans_bp, settings_bp,
        external_bp, internal_bp, import_export_bp, periods_bp, reference_bp,
        ai_bp, dividends_bp, milestones_bp,
    ]:
        app.register_blueprint(blueprint)

    return app


def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]


if __name__ == '__main__':
    init_db()
    conn = get_db()
    ensure_staff_exists(conn)
    seed_equipment_and_licenses(conn)
    conn.close()

    run_port = int(os.environ.get('PORT', 0))
    if run_port == 0:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', 5000))
            run_port = 5000
        except OSError:
            run_port = _find_free_port()

    print("\n" + "=" * 50)
    print("  MIZAN Finance v4.0")
    print(f"  http://127.0.0.1:{run_port}")
    print("  To'xtatish: Ctrl+C")
    print("=" * 50 + "\n")

    app = create_app()
    threading.Timer(1.5, lambda: webbrowser.open(f'http://127.0.0.1:{run_port}')).start()
    app.run(host='127.0.0.1', port=run_port, debug=False)
