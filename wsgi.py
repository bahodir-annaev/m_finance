"""WSGI entrypoint — runs DB init/seed once, then exposes `app`."""
from app import create_app
from models.base import init_db, get_db
from import_nizam import ensure_staff_exists, seed_equipment_and_licenses

init_db()
conn = get_db()
ensure_staff_exists(conn)
seed_equipment_and_licenses(conn)
conn.close()

app = create_app()
