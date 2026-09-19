"""MIZAN Finance v5 — Flask app factory + startup."""
import os
import secrets
import socket
import threading
import webbrowser

from flask import Flask, request

from auth import login_manager
from models.base import init_db

# Local-network prefixes allowed to reach the app.
_ALLOWED_PREFIXES = ('127.', '::1', '192.168.', '10.', '172.', '100.')  # 100. = Tailscale CGNAT range

_SECRET_KEY_FILE = os.path.join(os.path.dirname(__file__), '.mizan5_secret_key')
_ENV_FILE = os.path.join(os.path.dirname(__file__), '.env')


def _load_dotenv(path=_ENV_FILE):
    """Load KEY=VALUE lines from a local .env; never overrides the real environment."""
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
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


def _get_secret_key():
    """Session secret: env var → persisted random key → freshly generated."""
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


BLUEPRINT_MODULES = [
    'auth_bp', 'dashboard_bp', 'accounts_bp', 'journal_bp', 'documents_bp',
    'counterparties_bp', 'staff_bp', 'rates_bp', 'projects_bp', 'payroll_bp',
    'reports_bp', 'periods_bp', 'settings_bp', 'api_bp', 'loans_bp',
    'bank_accounts_bp', 'pricing_bp',
]


def create_app():
    app = Flask(__name__, static_folder='static', template_folder='templates')
    app.config['SECRET_KEY'] = _get_secret_key()
    app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    login_manager.init_app(app)

    @app.before_request
    def restrict_to_local_network():
        ip = request.remote_addr or ''
        if not any(ip.startswith(p) for p in _ALLOWED_PREFIXES):
            return 'Access denied: this app is only available on the local network.', 403

    @app.errorhandler(403)
    def forbidden(e):
        return ('<h2 style="font-family:sans-serif;color:#c00;padding:40px">'
                "403 — Ruxsat yo'q</h2>"), 403

    import importlib
    for name in BLUEPRINT_MODULES:
        module = importlib.import_module(f'controllers.{name}')
        app.register_blueprint(module.bp)

    return app


def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]


if __name__ == '__main__':
    init_db()

    run_port = int(os.environ.get('PORT', 0))
    if run_port == 0:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', 5000))
            run_port = 5000
        except OSError:
            run_port = _find_free_port()

    print('\n' + '=' * 50)
    print('  MIZAN Finance v5.0')
    print(f'  http://127.0.0.1:{run_port}')
    print("  To'xtatish: Ctrl+C")
    print('=' * 50 + '\n')

    application = create_app()
    if os.environ.get('MIZAN_NO_BROWSER') != '1':
        threading.Timer(1.5, lambda: webbrowser.open(f'http://127.0.0.1:{run_port}')).start()
    application.run(host='127.0.0.1', port=run_port, debug=False)
