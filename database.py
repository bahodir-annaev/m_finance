"""Backward-compatibility shim — all logic lives in models/.

Existing imports like `from database import init_db, get_db, ...` keep working.
"""
from models import *  # noqa: F401, F403
from models.base import init_db, get_db  # explicit re-export for clarity
