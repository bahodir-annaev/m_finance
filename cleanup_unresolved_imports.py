"""One-time cleanup of spent `unresolved_imports` rows.

`unresolved_imports.transaction_id` is NOT NULL with no ON DELETE action, so every
row holds a foreign key on its transaction and blocks deleting it. Nothing in the
app ever removes these rows: the reconcile page's link / create_new / ignore
actions all just set `resolved=1`, which hides the row from the page but leaves
the FK in place. That is why an empty reconcile screen can still coexist with
"FOREIGN KEY constraint failed" on delete.

A `resolved=1` row is spent — link/create_new already wrote the FK onto the
transaction and stored the alias, and ignore means the name was dismissed. This
script deletes those, plus any dangling rows whose transaction is already gone.

Rows with `resolved=0` are NEVER touched: they are the outstanding reconciliation
queue the page still shows.

Usage:
    python cleanup_unresolved_imports.py --dry-run      # report only, no writes
    python cleanup_unresolved_imports.py                # report, then ask to confirm
    python cleanup_unresolved_imports.py --yes          # no prompt (deploy scripts)
    python cleanup_unresolved_imports.py --db /opt/m_fin/mizan_finance.db
"""
import argparse
import os
import sqlite3

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mizan_finance.db")

# Spent rows: reconciled/ignored, or pointing at a transaction that no longer exists.
TARGET_WHERE = """
    resolved = 1
    OR transaction_id NOT IN (SELECT id FROM transactions)
"""


def report(conn):
    """Print what is in the table and what this script would remove."""
    total = conn.execute("SELECT COUNT(*) FROM unresolved_imports").fetchone()[0]
    # Kept = the complement of what gets deleted, not simply resolved=0: a
    # resolved=0 row whose transaction is gone is dangling and goes too.
    pending = conn.execute(
        f"SELECT COUNT(*) FROM unresolved_imports WHERE NOT ({TARGET_WHERE})"
    ).fetchone()[0]
    spent = conn.execute(
        f"SELECT COUNT(*) FROM unresolved_imports WHERE {TARGET_WHERE}"
    ).fetchone()[0]
    dangling = conn.execute(
        "SELECT COUNT(*) FROM unresolved_imports"
        " WHERE transaction_id NOT IN (SELECT id FROM transactions)"
    ).fetchone()[0]
    blocked = conn.execute(
        f"SELECT COUNT(DISTINCT transaction_id) FROM unresolved_imports WHERE {TARGET_WHERE}"
    ).fetchone()[0]

    print(f"unresolved_imports rows total : {total}")
    print(f"  resolved=0 (kept, still on the reconcile page) : {pending}")
    print(f"  spent (resolved=1 or dangling), to delete      : {spent}")
    print(f"      of which dangling (transaction already gone): {dangling}")
    print(f"  transactions unblocked for delete by this run  : {blocked}")

    by_type = conn.execute(
        f"SELECT entity_type, COUNT(*) FROM unresolved_imports WHERE {TARGET_WHERE}"
        " GROUP BY entity_type ORDER BY 2 DESC"
    ).fetchall()
    for entity_type, n in by_type:
        print(f"      {entity_type:<14} {n}")
    return spent


def cleanup(db_path, dry_run=False, assume_yes=False):
    if not os.path.exists(db_path):
        print(f"DB not found: {db_path}")
        return 1

    conn = sqlite3.connect(db_path)
    try:
        print(f"DB: {db_path}\n")
        spent = report(conn)

        if not spent:
            print("\nNothing to clean up.")
            return 0
        if dry_run:
            print("\n--dry-run: no changes written.")
            return 0
        if not assume_yes:
            answer = input(
                f"\nPermanently delete {spent} spent unresolved_imports rows"
                " (resolved=0 rows are kept)? Type YES to proceed: "
            )
            if answer.strip() != "YES":
                print("Aborted.")
                return 1

        cur = conn.execute(f"DELETE FROM unresolved_imports WHERE {TARGET_WHERE}")
        deleted = cur.rowcount
        conn.commit()
        print(f"\nDeleted {deleted} rows.")

        remaining = conn.execute("SELECT COUNT(*) FROM unresolved_imports").fetchone()[0]
        print(f"Remaining (all resolved=0): {remaining}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", default=DEFAULT_DB, help="path to mizan_finance.db")
    parser.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args()
    raise SystemExit(cleanup(args.db, dry_run=args.dry_run, assume_yes=args.yes))
