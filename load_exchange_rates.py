"""
Fetch USD/UZS exchange rates from cbu.uz for a date range and insert into the DB.

Usage:
    python load_exchange_rates.py 2024-01-01 2024-12-31
    python load_exchange_rates.py 2025-06-01 2025-06-16  --dry-run

The CBU API only supports single-day requests, so dates are fetched one by one.
Days where CBU returns no data (e.g. future dates) are skipped automatically.
Already-existing rows are skipped (INSERT OR IGNORE).
"""

import sys
import json
import time
import sqlite3
import urllib.request
from datetime import date, timedelta


DB_PATH = 'mizan_finance.db'
CBU_URL = 'https://cbu.uz/oz/arkhiv-kursov-valyut/json/USD/{}/'
PAUSE_SECONDS = 0.3  # be polite to the API


def fetch_rate(day: date) -> float | None:
    url = CBU_URL.format(day.strftime('%Y-%m-%d'))
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        if data:
            return float(data[0]['Rate'])
    except Exception as e:
        print(f'  ERROR {day}: {e}')
    return None


def iter_dates(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    dry_run = '--dry-run' in sys.argv

    if len(args) != 2:
        print('Usage: python load_exchange_rates.py <start_date> <end_date> [--dry-run]')
        print('Example: python load_exchange_rates.py 2024-01-01 2024-12-31')
        sys.exit(1)

    try:
        start = date.fromisoformat(args[0])
        end = date.fromisoformat(args[1])
    except ValueError as e:
        print(f'Invalid date: {e}')
        sys.exit(1)

    if start > end:
        print('start_date must be <= end_date')
        sys.exit(1)

    total_days = (end - start).days + 1
    print(f'Fetching rates {start} → {end} ({total_days} days){"  [DRY RUN]" if dry_run else ""}')

    conn = sqlite3.connect(DB_PATH)
    inserted = skipped = errors = 0

    for day in iter_dates(start, end):
        rate = fetch_rate(day)
        if rate is None:
            print(f'  {day}  — fetch failed (network error or no data from CBU)')
            errors += 1
        else:
            if dry_run:
                print(f'  {day}  {rate:,.2f} UZS  [dry run]')
                inserted += 1
            else:
                cur = conn.execute(
                    'INSERT OR IGNORE INTO exchange_rates (date, rate) VALUES (?, ?)',
                    (day.isoformat(), rate),
                )
                if cur.rowcount:
                    print(f'  {day}  {rate:,.2f} UZS  ✓ inserted')
                    inserted += 1
                else:
                    print(f'  {day}  {rate:,.2f} UZS  — already exists, skipped')
                    skipped += 1
                conn.commit()

        time.sleep(PAUSE_SECONDS)

    conn.close()
    print(f'\nDone. inserted={inserted}  skipped={skipped}  no-data={errors}')


if __name__ == '__main__':
    main()
