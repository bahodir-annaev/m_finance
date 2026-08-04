"""Consistent SQLite backup — safe to run while the app is serving requests.

Uses sqlite3's online backup API, so the snapshot is transactionally consistent
even under concurrent writes. A plain file copy of a live database can catch it
mid-transaction and produce a file that will not open.

    python backup.py                    # -> backups/mizan_finance_YYYYmmdd_HHMMSS.db
    python backup.py --keep-days 30     # also prune backups older than 30 days

Platform-neutral: the same script runs on the Windows workstation and on the
Linux server (see DEPLOYMENT.md §9).
"""
import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--db', default=os.path.join(ROOT, 'mizan_finance.db'),
                    help='database to back up (default: mizan_finance.db next to this script)')
    ap.add_argument('--out', default=os.path.join(ROOT, 'backups'),
                    help='directory to write the snapshot into (default: backups/)')
    ap.add_argument('--keep-days', type=int, default=0,
                    help='delete snapshots older than N days (default: keep everything)')
    args = ap.parse_args()

    if not os.path.exists(args.db):
        sys.exit(f'Database not found: {args.db}')
    os.makedirs(args.out, exist_ok=True)

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    dest = os.path.join(args.out, f'mizan_finance_{stamp}.db')

    # timeout: on a live server the backup can start while a request holds a
    # write lock — wait for it instead of failing the nightly cron job.
    src = sqlite3.connect(args.db, timeout=30)
    dst = sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    verdict = dst.execute('PRAGMA integrity_check').fetchone()[0]
    dst.close()
    src.close()

    # A snapshot that fails verification is worse than no snapshot — it looks
    # like a rollback option until the day it is needed.
    if verdict != 'ok':
        os.remove(dest)
        sys.exit(f'Backup failed integrity check: {verdict}')

    print(f'OK  {dest}  ({os.path.getsize(dest) / 1048576:.2f} MB)')

    if args.keep_days:
        cutoff = time.time() - args.keep_days * 86400
        for name in sorted(os.listdir(args.out)):
            path = os.path.join(args.out, name)
            if (name.startswith('mizan_finance_') and name.endswith('.db')
                    and os.path.getmtime(path) < cutoff):
                os.remove(path)
                print(f'pruned {name}')


if __name__ == '__main__':
    main()
