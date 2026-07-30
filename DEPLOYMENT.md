# MIZAN Finance — Deployment & Update Guide

Step-by-step instructions for deploying MIZAN Finance to a local server and updating it safely.

Covers three deploy paths — **native Windows** (§4A), **native Linux** (§4B), and **Docker** on either OS (§4C). Follow one. Shared concepts (git, database backup, the update sequence) are the same for all three.

---

## 0. Before you start — decisions

| Decision | This guide assumes | Alternative |
|---|---|---|
| **Server OS** | Windows **or** Linux (both covered) | — |
| **Packaging** | venv on the host | **Docker + Compose** (§4C / §5C) — one path for both OSes |
| **WSGI server** | Waitress (Windows) / Gunicorn (Linux, incl. Docker) | Waitress works on both if you want one path |
| **Service manager** | NSSM (Windows) / systemd (Linux) | Docker `restart: unless-stopped` |
| **Repo hosting** | Private GitHub with a read-only **deploy key** | Self-hosted bare repo |
| **Remote access** | Tailscale (private VPN) | Office VPN / LAN only |

> **Host venv or Docker?** Docker gives you a reproducible Python version, one identical command set on Windows and Linux, and restart-on-crash without NSSM/systemd. The trade-off: an extra runtime to install, and the LAN guard behaves differently inside a container (see §4C). Native venv keeps the SQLite file and the process on the metal, closest to how the app was developed. Either is fine for a single-firm LAN deployment — pick one and stay on it; **do not run both against the same `mizan_finance.db`**.

> ⚠️ **Security note.** This app was built local-only: it has a LAN guard (`_ALLOWED_PREFIXES` in `app.py`) and no HTTPS. Keep it on the **LAN or a private VPN (Tailscale)** — do **not** port-forward it to the public internet. If you ever must expose it publicly, first set a real `MIZAN_SECRET_KEY`, add HTTPS, and fix the proxy guard (see §8).

---

## 1. Prerequisites

**On the server**
- Git
- **Native install (§4A / §4B):** Python 3.10+ (`python --version` / `python3 --version`)
- **Docker install (§4C):** Docker Engine 24+ with the Compose plugin (`docker --version`, `docker compose version`). Python is *not* needed on the host. Linux: `curl -fsSL https://get.docker.com | sh`. Windows: Docker Desktop with the WSL2 backend.
- (Recommended) Tailscale, for remote administration and user access

**On your dev machine**
- Git, and push access to the private repo

---

## 2. One-time git setup

The server needs an upstream to `git pull` from. Two options — pick one.

### Option A — Private GitHub with a deploy key (recommended)

The **server** connects to GitHub, so the key is generated **on the server**.

1. Generate a deploy key on the server:
   ```bash
   ssh-keygen -t ed25519 -C "mizan-server-deploy" -f ~/.ssh/mizan_deploy
   ```
   (Windows: run in PowerShell or Git Bash; path is `C:\Users\<user>\.ssh\mizan_deploy`.)

2. Copy the **public** key (`mizan_deploy.pub`) → GitHub repo → **Settings → Deploy keys → Add deploy key** → paste → leave **"Allow write access" unchecked** (pull-only).

3. Tell git to use that key — add to `~/.ssh/config` (`C:\Users\<user>\.ssh\config` on Windows):
   ```
   Host github.com
       IdentityFile ~/.ssh/mizan_deploy
       IdentitiesOnly yes
   ```

4. Verify:
   ```bash
   ssh -T git@github.com     # should greet you by name
   ```

### Option B — Self-hosted bare repo (fully offline)

On the server:
```bash
git init --bare /opt/mizan-repo.git
```
From your dev machine, add it as a remote and push:
```bash
git remote add server ssh://user@<server-ip>/opt/mizan-repo.git
git push server main
```
Then clone the working copy from it in §4.

### `.gitignore` (commit this to the repo)

Make sure live data is never overwritten by a pull:
```
mizan_finance.db
.venv/
uploads/
backups/
data/            # Docker volume mount (§4C) — live DB, uploads, backups
.env
.mizan_secret_key
__pycache__/
*.pyc
```

### `.gitattributes` (commit this — keeps shell scripts LF on Windows checkouts)
```
*.sh text eol=lf
```

---

## 3. Files to add to the repo

Create these once, commit them, and all three deploy paths use them. (Docker adds three more files — `Dockerfile`, `.dockerignore`, `docker-entrypoint.sh` — listed in §4C.)

### `requirements.txt`
```
flask==3.0.*
flask-login==0.6.*
openpyxl==3.1.*
waitress==3.0.*      ; platform_system == "Windows"
gunicorn==22.0.*     ; platform_system != "Windows"
```

### `wsgi.py` — runs startup once, then exposes the app object

> Needed because `app.py`'s startup block (`init_db()`, seeding) lives under `if __name__ == '__main__'`, which a WSGI server (gunicorn/waitress-serve) does **not** execute. `wsgi.py` runs it explicitly.

```python
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
```

---

## 4. Initial deploy

### 4A. Windows server

```powershell
# 1. Get the code
git clone git@github.com:you/mizan.git C:\MIZAN
cd C:\MIZAN

# 2. Virtual environment + dependencies
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 3. Set a real secret key (persist as a machine env var)
[Environment]::SetEnvironmentVariable("MIZAN_SECRET_KEY", (python -c "import secrets;print(secrets.token_hex(32))"), "Machine")

# 4. Smoke test (Ctrl+C to stop)
.venv\Scripts\python -m waitress --listen=0.0.0.0:5000 wsgi:app
```

**Install as a Windows service with NSSM** (download nssm.exe, place on PATH):
```powershell
nssm install MizanFinance C:\MIZAN\.venv\Scripts\python.exe "-m waitress --listen=0.0.0.0:5000 wsgi:app"
nssm set MizanFinance AppDirectory C:\MIZAN
nssm set MizanFinance AppStdout C:\MIZAN\logs\service.log
nssm set MizanFinance AppStderr C:\MIZAN\logs\service.log
nssm start MizanFinance
```

### 4B. Linux server

```bash
# 1. Get the code
sudo git clone git@github.com:you/mizan.git /opt/mizan
cd /opt/mizan

# 2. Virtual environment + dependencies
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. Smoke test (Ctrl+C to stop)
.venv/bin/gunicorn --bind 0.0.0.0:5000 --workers 2 "wsgi:app"
```

**Install as a systemd service** — create `/etc/systemd/system/mizan.service`:
```ini
[Unit]
Description=MIZAN Finance
After=network.target

[Service]
WorkingDirectory=/opt/mizan
Environment="MIZAN_SECRET_KEY=<paste a random 64-hex string here>"
ExecStart=/opt/mizan/.venv/bin/gunicorn --bind 0.0.0.0:5000 --workers 2 "wsgi:app"
Restart=always
User=mizan

[Install]
WantedBy=multi-user.target
```
> **SQLite note:** keep `--workers 2` (or `--workers 1 --threads 4`). SQLite serializes writes; many workers cause `database is locked`.

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mizan
sudo systemctl status mizan          # verify it's running
```

### 4C. Docker (either OS) — alternative to 4A/4B

Same image on Windows and Linux. Gunicorn runs inside the container; the host only needs Docker. **Do not combine this with 4A/4B** — two processes writing one SQLite file will corrupt it.

#### Files to add to the repo

**`Dockerfile`**
```dockerfile
# MIZAN Finance — LAN-only Flask app served by gunicorn.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Tashkent

RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata sqlite3 curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# All mutable state must live on the mounted volume /data. models/base.py
# hardcodes DB_PATH to the project root and uploads/ sits there too, so
# redirect both (plus the auto-generated session key) into /data with
# symlinks. Without this, `docker compose up --build` would silently start
# on an empty database and every restart would log all users out.
RUN rm -rf mizan_finance.db uploads .mizan_secret_key \
 && ln -sfn /data/mizan_finance.db  /app/mizan_finance.db \
 && ln -sfn /data/uploads           /app/uploads \
 && ln -sfn /data/.mizan_secret_key /app/.mizan_secret_key

COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 5000
ENTRYPOINT ["docker-entrypoint.sh"]
# 1 worker + threads: SQLite serializes writes, several processes cause
# "database is locked" (same reasoning as the systemd note in §4B).
CMD ["gunicorn", "--bind", "0.0.0.0:5000", \
     "--worker-class", "gthread", "--workers", "1", "--threads", "4", \
     "--access-logfile", "-", "wsgi:app"]
```

**`.dockerignore`** — keeps live data and local junk out of the image (`.gitignore` does **not** apply to `docker build`):
```
.git
.venv
data/
backups/
uploads/
*.db
*.sqlite*
.env
.mizan_secret_key
__pycache__/
*.pyc
*.log
*.dmg
*.docx
.DS_Store
.vscode
.claude
```

**`docker-entrypoint.sh`** — creates the volume paths the symlinks point at, before gunicorn starts:
```bash
#!/bin/sh
set -e

# /data is the mounted volume; a fresh bind mount is empty.
mkdir -p /data/uploads /data/backups

exec "$@"
```
> Commit it with LF endings — the `.gitattributes` rule in §2 (`*.sh text eol=lf`) handles that; a CRLF entrypoint fails with `exec format error`.

**`docker-compose.yml`**
```yaml
services:
  mizan:
    build: .
    image: mizan-finance:latest
    container_name: mizan
    restart: unless-stopped
    env_file: .env
    ports:
      # Bind to ONE interface — see the LAN-guard warning below.
      # LAN only:       "192.168.1.50:5000:5000"
      # Tailscale only: "100.x.x.x:5000:5000"
      - "192.168.1.50:5000:5000"
    volumes:
      # Single mount holding the DB, uploads and backups. Directory (not file)
      # mount, so restoring a backup by copying over the DB works (§9).
      - ./data:/data
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://127.0.0.1:5000/login"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s
```

#### Deploy

```bash
# 1. Get the code (Windows: C:\MIZAN, Linux: /opt/mizan)
git clone git@github.com:you/mizan.git /opt/mizan
cd /opt/mizan

# 2. Create .env — MIZAN_SECRET_KEY is the one value you should set explicitly
cp .env.example .env
python3 -c "import secrets;print('MIZAN_SECRET_KEY='+secrets.token_hex(32))"   # paste into .env
#   (no Python on the host? use: openssl rand -hex 32)

# 3. Build and start
docker compose up -d --build

# 4. Verify
docker compose ps          # should read "healthy"
docker compose logs -f     # first boot prints the generated admin password
```

First boot creates `data/mizan_finance.db`, runs `init_db()` migrations and the staff/equipment seed via `wsgi.py`. If you set `MIZAN_ADMIN_PASSWORD` in `.env` **before** the first start it is used for the seeded `admin` user; otherwise a random one is printed to the log exactly once — grab it from `docker compose logs`.

**Migrating an existing native install into Docker:** stop the old service, then `mkdir -p data && cp mizan_finance.db data/ && cp -r uploads data/` before `docker compose up -d --build`.

#### Docker-specific notes

- ⚠️ **The LAN guard is unreliable inside a container.** Requests reach Flask through Docker's NAT, so `request.remote_addr` is often the bridge gateway (`172.17.0.1`) rather than the real client — and `172.` is in `_ALLOWED_PREFIXES`, so the guard passes. On Docker Desktop (Windows/macOS) this is true for *every* request. Treat the published port as the only real control: bind it to a single LAN or Tailscale address as shown above, never `0.0.0.0`, and keep the host firewall on. On Linux you can restore the original semantics with `network_mode: host` (drop the `ports:` block; the app then listens on the host's port 5000 directly and sees real client IPs).
- **`PORT` is ignored.** It only affects `python app.py`; under gunicorn the container always listens on 5000. Change the *host* side of the port mapping instead.
- **Timezone.** The image sets `TZ=Asia/Tashkent`; a container defaults to UTC, which would shift `created_at` timestamps and AR-aging day counts by 5 hours. Override in `.env` if the firm moves.
- **AI page.** `models/ai_agent.py` talks to Ollama at `127.0.0.1:11434`, which inside a container means the container itself. If Ollama runs on the host, add to the service: `extra_hosts: ["host.docker.internal:host-gateway"]` and set `MIZAN_AI_URL=http://host.docker.internal:11434` in `.env`.
- **Runs as root inside the container** (default). Acceptable for a single-purpose LAN app; to change it, add `user: "1000:1000"` to the service and `chown -R 1000:1000 data` on the host.
- **Uploads and generated Excel files** land in `data/uploads/` on the host — browse them there, not inside the container.
- ⚠️ **Windows + Docker Desktop: keep the repo inside WSL2.** A bind mount that crosses from the Windows filesystem (`C:\MIZAN`) into the container goes through a network-style translation layer whose file locking SQLite cannot rely on — expect `database is locked` and, in the worst case, corruption. Clone into the WSL2 filesystem instead (e.g. `\\wsl$\Ubuntu\home\mizan\`, working from a WSL shell). If the repo must live on `C:\`, replace the bind mount with a named volume (`volumes: [mizan-data:/data]` plus a top-level `volumes: {mizan-data:}`) and reach the data through `docker compose exec` / `docker cp` rather than Explorer.
- **SELinux hosts (RHEL, Fedora, Rocky):** a bind mount is denied unless relabelled — use `- ./data:/data:z`.
- **Nothing to see in the app after switching to Docker?** The container is running on a fresh, empty `data/mizan_finance.db` — the old database was never copied in (see the migration line above). Stop, copy it into `data/`, start again.

### First-run check

Open `http://<server-ip>:5000` from another machine on the LAN. If it 403s, the requester's IP isn't in `_ALLOWED_PREFIXES` (`app.py`) — see §7 for Tailscale. (Docker: also confirm the port mapping's host address matches the interface you're connecting to — a mismatch shows up as connection refused, not a 403.)

---

## 5. The update workflow

**Golden rule: back up the database *before* swapping code.** Schema migrations run automatically on startup (`init_db()`) and are idempotent, but there is no automatic rollback — the backup is your rollback.

The sequence is always:
1. Stop the service
2. **Back up `mizan_finance.db`**
3. Pull new code
4. Reinstall dependencies (picks up any new ones)
5. Start the service (migrations run on boot)

On Docker the same five steps collapse into back up → pull → `build` → `up -d` (see §5C); the database lives in `data/mizan_finance.db` there.

### 5A. Windows — `update.bat` (commit to repo root)
```bat
@echo off
setlocal
cd /d "%~dp0"

echo [1/5] Stopping service...
nssm stop MizanFinance

echo [2/5] Backing up database...
if not exist backups mkdir backups
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set dt=%%I
set stamp=%dt:~0,8%_%dt:~8,6%
copy /Y mizan_finance.db "backups\mizan_finance_%stamp%.db"

echo [3/5] Pulling code...
git pull

echo [4/5] Installing dependencies...
.venv\Scripts\pip install -r requirements.txt

echo [5/5] Starting service...
nssm start MizanFinance
echo Done.
```
Run it (locally or over SSH): `C:\MIZAN\update.bat`

### 5B. Linux — `update.sh` (commit to repo root, `chmod +x update.sh`)
```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "[1/5] Stopping service..."
sudo systemctl stop mizan

echo "[2/5] Backing up database..."
mkdir -p backups
cp mizan_finance.db "backups/mizan_finance_$(date +%Y%m%d_%H%M%S).db"

echo "[3/5] Pulling code..."
git pull

echo "[4/5] Installing dependencies..."
.venv/bin/pip install -r requirements.txt

echo "[5/5] Starting service..."
sudo systemctl start mizan
echo "Done."
```
Run it (locally or over SSH): `/opt/mizan/update.sh`

### 5C. Docker — `update-docker.sh` (commit to repo root, `chmod +x update-docker.sh`)

Dependencies are baked into the image, so `docker compose build` replaces the "reinstall dependencies" step. The backup uses SQLite's online `.backup` command (`sqlite3` is installed in the image), which is consistent even with the app running — so no stop step is needed and downtime is just the few seconds of the container recreate.

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "[1/4] Backing up database (online, consistent)..."
stamp=$(date +%Y%m%d_%H%M%S)
docker compose exec -T mizan \
  sqlite3 /data/mizan_finance.db ".backup '/data/backups/mizan_finance_${stamp}.db'"

echo "[2/4] Pulling code..."
git pull

echo "[3/4] Rebuilding image..."
docker compose build

echo "[4/4] Recreating container (migrations run on boot)..."
docker compose up -d
docker compose ps
```
Run it: `/opt/mizan/update-docker.sh` — on a Windows server run the same script from **Git Bash**, or do it by hand:
```powershell
cd C:\MIZAN
copy /Y data\mizan_finance.db data\backups\mizan_finance_manual.db
git pull; docker compose build; docker compose up -d
```
> If the container isn't running, the `docker compose exec` backup fails. Back up with a plain file copy of `data\mizan_finance.db` instead — safe while stopped.

### Releasing an update (from your dev machine)
```bash
git add -A && git commit -m "..." && git push
```
Then on the server, run the update script (see §6 for doing this remotely).

---

## 6. Remote administration (running deploy/update from elsewhere)

You administer the server via an **SSH shell**, ideally over **Tailscale** (never a public port).

### Enable SSH on the server
- **Linux:** `sshd` is usually already running. Verify: `sudo systemctl status ssh`.
- **Windows:** Settings → Apps → Optional Features → add **OpenSSH Server**, then:
  ```powershell
  Start-Service sshd
  Set-Service sshd -StartupType Automatic
  ```

### Generate an SSH login key (on your dev machine)
```bash
ssh-keygen -t ed25519 -C "bahodir-admin"
```
Copy the **public** key to the server's `authorized_keys`:
- **Linux:** `ssh-copy-id mizan@<server-ip>` (or paste `id_ed25519.pub` into `~/.ssh/authorized_keys`, then `chmod 600`).
- **Windows server:** append the `.pub` line to `C:\Users\<user>\.ssh\authorized_keys` (for admin accounts: `C:\ProgramData\ssh\administrators_authorized_keys`, ACL-locked to Administrators/SYSTEM).

### Update the server remotely
```bash
ssh mizan@<server-ip-or-tailscale-ip>
cd /opt/mizan          # or C:\MIZAN
./update.sh            # native Linux — or update.bat on native Windows
./update-docker.sh     # Docker (either OS)
```
> Docker note: the SSH user must be in the `docker` group (`sudo usermod -aG docker mizan`, then re-login), otherwise every `docker compose` call needs `sudo`.

---

## 7. Remote access for users (Tailscale)

To let staff use the app from outside the office without exposing it publicly:

1. Install Tailscale on the **server** and on each user's device; sign them into the same Tailscale network.
2. The server gets a stable `100.x.x.x` address; users open `http://100.x.x.x:5000`.
3. **App change required:** Tailscale's range isn't in the LAN guard. Add `'100.'` to `_ALLOWED_PREFIXES` in `app.py`:
   ```python
   _ALLOWED_PREFIXES = ('127.', '::1', '192.168.', '10.', '172.', '100.')
   ```
   Commit and deploy via the normal update workflow.

Nothing is exposed to the public internet — it behaves like being on the office LAN.

**On Docker (§4C)** step 3 is not what protects you — NAT already makes the guard pass (see the warning in §4C). What matters is where the port is published: use the Tailscale address in `docker-compose.yml`, e.g. `ports: ["100.x.x.x:5000:5000"]`, or publish on both the LAN and Tailscale addresses with two entries. Install Tailscale on the **host**, not in the container.

---

## 8. If the app is ever exposed beyond LAN/VPN (avoid if possible)

Only if you put a public tunnel/reverse proxy (Cloudflare Tunnel, nginx) in front:
- **Set a real `MIZAN_SECRET_KEY`** (already done in §4) — otherwise the key is auto-generated per install and regenerating it silently invalidates every session.
- **Terminate HTTPS** at the proxy.
- **Fix the LAN guard:** behind a proxy — or behind Docker's NAT — `request.remote_addr` becomes the proxy/gateway address (`127.0.0.1`, `172.17.0.1`), so the guard passes everyone. Add `werkzeug.middleware.proxy_fix.ProxyFix` and gate on the forwarded client IP, and/or put an auth layer (e.g. Cloudflare Access) in front.

---

## 9. Backups & recovery

- Every update creates a timestamped copy in `backups/` (`data/backups/` on Docker).
- **Schedule a nightly backup** independent of updates:
  - **Linux (cron):** `0 2 * * * cp /opt/mizan/mizan_finance.db /opt/mizan/backups/nightly_$(date +\%Y\%m\%d).db`
  - **Windows (Task Scheduler):** a daily task copying `mizan_finance.db` into `backups\`.
  - **Docker (cron):** `0 2 * * * cd /opt/mizan && docker compose exec -T mizan sqlite3 /data/mizan_finance.db ".backup '/data/backups/nightly_$(date +\%Y\%m\%d).db'"` — consistent without stopping the app.
- **Restore:** stop the service → copy the chosen `backups\mizan_finance_*.db` over `mizan_finance.db` → start the service.
  - **Docker:** `docker compose stop` → `cp data/backups/<chosen>.db data/mizan_finance.db` → `docker compose start`. Delete any stray `data/mizan_finance.db-journal`/`-wal` first; a leftover journal from the old file can be replayed onto the restored one.
- **Off-machine copy.** `backups/` on the same disk is not a backup — sync it to a NAS, another PC, or cloud storage. On Docker everything worth keeping is the single `data/` directory: `tar czf mizan-$(date +%F).tar.gz data/` after a `docker compose stop` gives you a complete, portable snapshot (DB + uploads + session key).

---

## 10. Quick reference

| Task | Windows (native) | Linux (native) | Docker (either OS) |
|---|---|---|---|
| Start | `nssm start MizanFinance` | `sudo systemctl start mizan` | `docker compose up -d` |
| Stop | `nssm stop MizanFinance` | `sudo systemctl stop mizan` | `docker compose stop` |
| Restart | `nssm restart MizanFinance` | `sudo systemctl restart mizan` | `docker compose restart` |
| Status | `nssm status MizanFinance` | `sudo systemctl status mizan` | `docker compose ps` |
| Logs | `C:\MIZAN\logs\service.log` | `journalctl -u mizan -f` | `docker compose logs -f` |
| Update | `C:\MIZAN\update.bat` | `/opt/mizan/update.sh` | `./update-docker.sh` |
| Shell in app | — | — | `docker compose exec mizan bash` |
| SQL prompt | `sqlite3 mizan_finance.db` | `sqlite3 mizan_finance.db` | `docker compose exec mizan sqlite3 /data/mizan_finance.db` |
| Database file | `C:\MIZAN\mizan_finance.db` | `/opt/mizan/mizan_finance.db` | `/opt/mizan/data/mizan_finance.db` (host) |
| App URL | `http://<server-ip>:5000` | `http://<server-ip>:5000` | `http://<server-ip>:5000` |
