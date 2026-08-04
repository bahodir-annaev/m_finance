"""WSGI entrypoint — runs DB init once, then exposes `app`."""
from app import create_app
from models.base import init_db

init_db()

app = create_app()
