"""WSGI entrypoint for MIZAN v5.

`app.py`'s startup block (`init_db()`) lives under `if __name__ == '__main__'`,
which gunicorn never executes. This module runs it explicitly, then exposes
`app` for the WSGI server.
"""
from app import create_app
from models.base import init_db

init_db()

app = create_app()
