import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "mizan_finance.db")

def clear_transactions():
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        # unresolved_imports references transactions.id, so it must be cleared first
        cur.execute("DELETE FROM unresolved_imports")
        unresolved_deleted = cur.rowcount
        cur.execute("DELETE FROM import_skip_log")
        skip_log_deleted = cur.rowcount
        cur.execute("DELETE FROM transactions")
        tx_deleted = cur.rowcount
        conn.commit()
        print(f"Deleted {tx_deleted} transactions.")
        print(f"Deleted {unresolved_deleted} unresolved_imports rows.")
        print(f"Deleted {skip_log_deleted} import_skip_log rows.")
    finally:
        conn.close()

if __name__ == "__main__":
    confirm = input("This will permanently delete ALL transactions, unresolved_imports, and import_skip_log data. Type YES to proceed: ")
    if confirm.strip() == "YES":
        clear_transactions()
    else:
        print("Aborted.")
