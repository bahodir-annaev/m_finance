# MIZAN v5 — Linux deployment alongside a running v4

Step-by-step guide for installing **mizan5** on a Linux server that is **already running v4**,
as its own systemd service with its own Python virtualenv. v4 keeps running untouched.

Commands assume Debian/Ubuntu. For RHEL/Rocky swap `apt` → `dnf` and `ufw` → `firewalld`.

---

## 0. Ground rules for coexisting with v4

Read this before typing anything — the first three are the mistakes that actually break a
side-by-side install.

| Concern | v4 | v5 (this deploy) |
|---|---|---|
| Install dir | `/opt/mizan` | `/opt/mizan5` (repo root); app runs from `/opt/mizan5/mizan5` |
| venv | `/opt/mizan/.venv` | `/opt/mizan5/.venv` — **separate, never shared** |
| Database | `mizan_finance.db` | `mizan5.db` — **separate file** |
| Port | 5000 | **5001** |
| systemd unit | `mizan.service` | `mizan5.service` |
| Service user | `mizan` | `mizan5` |
| Secret key file | `.mizan_secret_key` | `.mizan5_secret_key` |

1. **Never share a virtualenv or `PYTHONPATH` between v4 and v5.** Both codebases have a
   top-level package called `models`, plus their own `auth.py`, `utils.py` and
   `translations.py`. If v4's directory is on `sys.path` when v5 starts, v5 imports v4's
   modules and fails in confusing ways. That is why the unit file in §9 sets
   `WorkingDirectory=/opt/mizan5/mizan5` and nothing else — cwd is the only path entry v5 needs.
2. **Never point v5 at v4's database.** v5's schema is a double-entry ledger.
   `mizan_finance.db` is read exactly once, by the optional one-shot migration in §8, where you
   pass its path explicitly.
3. **One writer per SQLite file.** Run gunicorn with a single worker plus threads (§9), and
   don't leave a `python app.py` running against the same DB.

---

## 1. Prerequisites

```bash
python3 --version            # need 3.10+
git --version
sudo apt update && sudo apt install -y python3-venv git sqlite3
timedatectl                  # should read Asia/Tashkent
```

Timezone matters: v5 stamps `created_at` / posting dates from the server clock, and period-close
and AR-aging day counts derive from it. If the server is on UTC:

```bash
sudo timedatectl set-timezone Asia/Tashkent
```

v5 needs only **Flask, Flask-Login and gunicorn** (`mizan5/requirements.txt`). No openpyxl, no
compilers, no system libraries.

---

## 2. Values used throughout

```
INSTALL_DIR = /opt/mizan5          # git clone target (repo root)
APP_DIR     = /opt/mizan5/mizan5   # the v5 app — gunicorn's working directory
SERVICE     = mizan5
PORT        = 5001
USER        = mizan5
```

---

## 3. Create the service user and directory

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin mizan5
sudo mkdir -p /opt/mizan5
sudo chown mizan5:mizan5 /opt/mizan5
```

> Reusing v4's `mizan` user works too — just substitute it everywhere below. A separate user is
> preferred: it guarantees v5 can never write to v4's database file.

---

## 4. Get the code

v5 lives in the `mizan5/` subdirectory of the same repository as v4, so clone the whole repo
into a **second, independent working copy**. Do not run v5 out of v4's checkout — updating one
would then force-update the other.

```bash
sudo -u mizan5 git clone git@github.com:you/mizan.git /opt/mizan5
cd /opt/mizan5
sudo -u mizan5 git checkout double_entry_version   # the branch v5 lives on
ls mizan5/app.py mizan5/wsgi.py mizan5/requirements.txt   # sanity check
```

No GitHub access from the server? Copy the checkout from your dev machine instead:

```bash
# from the dev machine
rsync -av --exclude .git --exclude '*.db' --exclude .venv --exclude __pycache__ \
      ./MIZAN_Finance_App_v4/ mizan5-server:/tmp/mizan5-src/
# on the server
sudo cp -r /tmp/mizan5-src/. /opt/mizan5/ && sudo chown -R mizan5:mizan5 /opt/mizan5
```

⚠️ Never copy a dev `mizan5.db` onto a server that already holds live data.

---

## 5. Virtualenv and dependencies

```bash
cd /opt/mizan5
sudo -u mizan5 python3 -m venv .venv
sudo -u mizan5 .venv/bin/pip install --upgrade pip
sudo -u mizan5 .venv/bin/pip install -r mizan5/requirements.txt
sudo -u mizan5 .venv/bin/pip list      # flask, flask-login, gunicorn, werkzeug, jinja2
```

---

## 6. Configuration

v5 loads `mizan5/.env` at startup (`_load_dotenv()` in `app.py`); the real environment always
wins over it. Put the secrets there:

```bash
cd /opt/mizan5/mizan5
printf 'MIZAN_SECRET_KEY=%s\nMIZAN_ADMIN_PASSWORD=%s\nTZ=Asia/Tashkent\n' \
  "$(python3 -c 'import secrets;print(secrets.token_hex(32))')" \
  "$(python3 -c 'import secrets;print(secrets.token_urlsafe(12))')" \
  | sudo -u mizan5 tee .env >/dev/null
sudo chmod 600 .env
sudo cat .env       # write the admin password down now
```

| Variable | Effect |
|---|---|
| `MIZAN_SECRET_KEY` | Flask session key. Set it explicitly — otherwise a random key is written to `.mizan5_secret_key`, and losing that file logs everyone out. |
| `MIZAN_ADMIN_PASSWORD` | Password for the `admin` user seeded on the **first** `init_db()`. If unset, a random one is printed to the log exactly once. Ignored once a user row exists. |
| `MIZAN5_DB` | Overrides the DB path (default `/opt/mizan5/mizan5.db`, one level above `APP_DIR`). Set only if the DB belongs on a separate data volume — then extend `ReadWritePaths` in §9. |
| `PORT`, `MIZAN_NO_BROWSER` | Only affect `python app.py`. Under gunicorn the unit's `--bind` decides the port, and no browser is ever launched. |

`.env` is gitignored, so it survives every `git pull`.

---

## 7. Initialise the database and smoke-test

`wsgi.py` calls `init_db()` (create + migrate + seed accounts/settings/admin) and then exposes
`app`. It exists because gunicorn never runs `app.py`'s `if __name__ == '__main__'` block.

```bash
cd /opt/mizan5/mizan5
sudo -u mizan5 ../.venv/bin/python -c "import wsgi; print('init OK')"
ls -l /opt/mizan5/mizan5.db
```

Optionally run the suite once on the server — each test builds its own temp DB and never touches
`mizan5.db`:

```bash
sudo -u mizan5 ../.venv/bin/python run_tests.py
```

Foreground smoke test (Ctrl+C to stop):

```bash
sudo -u mizan5 ../.venv/bin/gunicorn --bind 127.0.0.1:5001 \
     --worker-class gthread --workers 1 --threads 4 wsgi:app
# from another shell:
curl -sI http://127.0.0.1:5001/login | head -1     # expect HTTP/1.1 200 OK
```

---

## 8. (Optional) Import v4 history into v5

Only if this install should reproduce v4's history instead of starting empty. It is a **one-shot**
migration: run it once, on a fresh v5 database, before letting anyone in.

Work from a **copy** so a running v4 is never touched:

```bash
sudo sqlite3 /opt/mizan/mizan_finance.db ".backup '/tmp/v4_snapshot.db'"   # online, no downtime
sudo chown mizan5:mizan5 /tmp/v4_snapshot.db

cd /opt/mizan5/mizan5
sudo -u mizan5 ../.venv/bin/python -m models.import_v4 /tmp/v4_snapshot.db
```

Every v4 money row is replayed as a posted v5 document, and the run ends with a reconciliation
block — **v4 and v5 cash figures must agree exactly**. If they don't, stop and investigate before
going live; the clean reset is to delete `mizan5.db`, redo §7, and try again.

---

## 9. Install the systemd service

```bash
sudo tee /etc/systemd/system/mizan5.service >/dev/null <<'UNIT'
[Unit]
Description=MIZAN Finance v5 (double-entry)
After=network.target

[Service]
Type=simple
User=mizan5
Group=mizan5
# cwd is the ONLY path entry v5 needs. Do not add v4's directory to PYTHONPATH —
# both codebases ship a top-level `models` package.
WorkingDirectory=/opt/mizan5/mizan5
Environment="PYTHONUNBUFFERED=1"
Environment="TZ=Asia/Tashkent"
# SQLite serialises writes: one worker with threads, never several workers.
ExecStart=/opt/mizan5/.venv/bin/gunicorn \
    --bind 0.0.0.0:5001 \
    --worker-class gthread --workers 1 --threads 4 \
    --timeout 120 \
    --access-logfile - --error-logfile - \
    wsgi:app
Restart=always
RestartSec=5

# Hardening — v5 only ever writes inside its own tree.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/mizan5

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now mizan5
sudo systemctl status mizan5 --no-pager
```

> `ProtectSystem=strict` + `ReadWritePaths=/opt/mizan5` is what stops v5 from ever writing into
> `/opt/mizan`. If you set `MIZAN5_DB` outside `/opt/mizan5`, add that directory to
> `ReadWritePaths` or startup fails with a read-only filesystem error.

Confirm both services run on different ports:

```bash
sudo systemctl is-active mizan mizan5
sudo ss -tlnp | grep -E ':(5000|5001)'
```

---

## 10. Network access

The app rejects any request whose IP is not in `_ALLOWED_PREFIXES` (`127.`, `::1`, `192.168.`,
`10.`, `172.`) — see `mizan5/app.py`. Keep it on the LAN or a private VPN; there is no HTTPS.

```bash
sudo ufw allow from 192.168.0.0/16 to any port 5001 proto tcp
sudo ufw status
```

Open `http://<server-ip>:5001` from a LAN machine, log in as `admin` with the password from §6,
and change it under **Settings → Users**.

- **Tailscale:** `'100.'` (the Tailscale CGNAT range) is already in `_ALLOWED_PREFIXES`, so
  Tailscale clients pass the guard. No firewall rule is needed for the `tailscale0` interface
  unless `ufw` is set to deny by default — then `sudo ufw allow in on tailscale0 to any port 5001`.
- **Behind nginx:** `request.remote_addr` becomes `127.0.0.1` and the guard passes everyone.
  Either don't proxy it, or add `ProxyFix` and gate on the forwarded client IP.

---

## 11. Verify

```bash
curl -sI http://127.0.0.1:5001/login | head -1            # 200
sudo journalctl -u mizan5 -n 50 --no-pager                # no tracebacks
sudo -u mizan5 sqlite3 /opt/mizan5/mizan5.db \
     "select count(*) from accounts; select count(*) from users;"
sudo systemctl is-active mizan                            # v4 still active
curl -sI http://127.0.0.1:5000/ | head -1                 # v4 still answering
```

---

## 12. Updating v5

Golden rule: **back up the database before swapping code.** `init_db()` migrations run on every
boot and are idempotent, but there is no automatic rollback — the backup is the rollback.

Save as `/opt/mizan5/update5.sh` and `sudo chmod +x` it:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd /opt/mizan5

echo "[1/5] Backing up mizan5.db (online, consistent)..."
mkdir -p backups
sqlite3 mizan5.db ".backup 'backups/mizan5_$(date +%Y%m%d_%H%M%S).db'"

echo "[2/5] Stopping service..."
sudo systemctl stop mizan5

echo "[3/5] Pulling code..."
sudo -u mizan5 git pull

echo "[4/5] Installing dependencies..."
sudo -u mizan5 .venv/bin/pip install -r mizan5/requirements.txt

echo "[5/5] Starting service (migrations run on boot)..."
sudo systemctl start mizan5
sudo systemctl status mizan5 --no-pager
```

`mizan5.db`, `.env`, `.mizan5_secret_key`, `uploads/` and `backups/` are all gitignored, so a pull
never overwrites live data.

---

## 13. Backups

`.backup` is consistent while the app is running, so no downtime is needed. Nightly at 02:30 via
`sudo crontab -e`:

```cron
30 2 * * * sqlite3 /opt/mizan5/mizan5.db ".backup '/opt/mizan5/backups/mizan5_$(date +\%Y\%m\%d).db'" && find /opt/mizan5/backups -name 'mizan5_*.db' -mtime +30 -delete
```

Restore: `sudo systemctl stop mizan5`, copy the backup over `mizan5.db`,
`sudo chown mizan5:mizan5 mizan5.db`, `sudo systemctl start mizan5`.

---

## 14. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `ModuleNotFoundError: No module named 'models.ledger'` | v4's directory is on `sys.path`. Check for a stray `PYTHONPATH` in the unit or shell profile and confirm `WorkingDirectory=/opt/mizan5/mizan5`. |
| `attempt to write a readonly database` | `mizan5.db` not owned by `mizan5`, or it sits outside `ReadWritePaths`. `sudo chown mizan5:mizan5 /opt/mizan5/mizan5.db`. |
| `database is locked` | More than one writer. `--workers 1` only, and no leftover `python app.py`. |
| 403 "Access denied: local network" | Client IP not in `_ALLOWED_PREFIXES` (§10). |
| Login page loads but no password works | `init_db()` ran before `.env` existed, so a random admin password was generated. Recover it from the first boot in `journalctl -u mizan5`, or reset the hash directly in `sqlite3`. |
| v5 shows v4's data | `MIZAN5_DB` points at `mizan_finance.db`. Unset it and restart. |
| Port 5001 taken | `sudo ss -tlnp \| grep 5001`, pick another port, update `--bind` and the firewall rule. |
| Service flaps under `Restart=always` | `sudo journalctl -u mizan5 -n 100` — the real error sits above the restart lines. |

---

## 15. Uninstall / roll back v5

v4 is untouched by all of the above, so removing v5 is self-contained:

```bash
sudo systemctl disable --now mizan5
sudo rm /etc/systemd/system/mizan5.service
sudo systemctl daemon-reload
sudo cp /opt/mizan5/mizan5.db /root/mizan5-final-backup.db   # keep the data
sudo rm -rf /opt/mizan5
sudo userdel mizan5
```
