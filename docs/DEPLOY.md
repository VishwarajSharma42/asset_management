# Deploying to the college server

Target: Debian/Ubuntu, Python 3.11+, PostgreSQL 16, nginx, certbot.

**Decide the hostname first.** `PUBLIC_HOST` is written into every tag and cannot change afterwards. Pick a
short real name with a certificate (e.g. `assets.college.edu`).

## 1. Packages and user

```bash
sudo apt install postgresql nginx certbot python3-venv python3-dev libpq-dev libjpeg-dev
sudo useradd --system --home /srv/dsoreg --shell /usr/sbin/nologin dsoreg
sudo mkdir -p /srv/dsoreg /etc/dsoreg /var/lib/dsoreg/media && sudo chown -R dsoreg:dsoreg /srv/dsoreg /var/lib/dsoreg
```

## 2. Code and virtualenv

```bash
sudo -u dsoreg git clone <repo> /srv/dsoreg && cd /srv/dsoreg
sudo -u dsoreg python3 -m venv .venv && sudo -u dsoreg .venv/bin/pip install -r backend/requirements.txt
```

## 3. Keys

On a machine you trust (not necessarily the server):

```bash
python scripts/gen_keys.py --out-dir secrets.local --host assets.college.edu --backend-url https://assets.college.edu \
       --database-url 'postgresql+psycopg://dsoreg_app:PASSWORD@/dsoreg'
```

* `secrets.local/backend.env` → `/etc/dsoreg/backend.env` (mode `640`, `root:dsoreg`).
* **`KEK.hex` → provision separately.** The unit file loads it as a systemd credential:
  `sudo systemd-creds encrypt secrets.local/KEK.hex /etc/dsoreg/kek.cred` on the server (bound to the machine),
  and keep a copy in a password manager or offline. It must **not** be in the backups and not next to
  `backend.env` — otherwise the wrapped private keys are protected by nothing.
* `secrets.local/pi/*` → the Pi (see PI_SETUP.md). `DEVICE_REGISTER.txt` has the registration command.

## 4. Database

```bash
sudo -u postgres createuser dsoreg_migrate && sudo -u postgres createdb -O dsoreg_migrate dsoreg
# create the schema as the owner:
DATABASE_URL='postgresql+psycopg://dsoreg_migrate@/dsoreg' PYTHONPATH=backend .venv/bin/python -m dsoreg.cli --env-file /etc/dsoreg/backend.env init-db
sudo -u postgres psql dsoreg -f deploy/postgres_roles.sql        # least-privilege app role; edit the password first
```
`DSOREG_ENV=production` never creates tables implicitly — `init-db` is a deliberate step.
**Run the test suite against this Postgres once** before going live (see DEVELOPMENT.md) — the automated suite
uses SQLite.

## 5. First accounts and the Pi

```bash
# the CLI needs the KEK once, to unwrap the keys: put a temporary copy where only dsoreg can read it, then delete it
sudo install -o dsoreg -m 400 KEK.hex /tmp/kek.tmp
cd /srv/dsoreg/backend && export KEK_FILE=/tmp/kek.tmp
sudo -E -u dsoreg ../.venv/bin/python -m dsoreg.cli --env-file /etc/dsoreg/backend.env      create-user --username dr.kulkarni --display-name "Dr. Kulkarni" --role admin
sudo -E -u dsoreg ../.venv/bin/python -m dsoreg.cli --env-file /etc/dsoreg/backend.env      register-device --device-id <id> --label pi-lab --public-key <hex>      # exact line is in DEVICE_REGISTER.txt
sudo shred -u /tmp/kek.tmp
```
Passwords are prompted, never passed as flags. Minimum 12 characters; the policy rejects anything containing
the username.

## 6. nginx, TLS, systemd

```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/dsoreg && sudo cp deploy/dsoreg-proxy.conf /etc/nginx/
sudo ln -s /etc/nginx/sites-available/dsoreg /etc/nginx/sites-enabled/ && sudo certbot --nginx -d assets.college.edu
sudo cp deploy/dsoreg.service deploy/dsoreg.socket /etc/systemd/system/
sudo rsync -a frontend/ /srv/dsoreg/frontend/
sudo systemctl daemon-reload && sudo systemctl enable --now dsoreg.socket dsoreg.service && sudo nginx -t && sudo systemctl reload nginx
curl https://assets.college.edu/health
```

Keep gunicorn at **2 workers** (the unit does). Optional but recommended: set `SCAN_ALLOWED_CIDRS` in
`backend.env` to the campus ranges if the tap page should only work on campus networks.

## 7. Backups and monitoring

* Nightly `pg_dump | gzip | gpg` copied to a **second machine**; test a restore monthly. **Do not include the KEK.**
* `python -m dsoreg.cli verify-audit` weekly (exit code 2 on a broken chain); record the printed head hash
  somewhere the server's admin cannot edit (an email to the head of department works).
* `journalctl -u dsoreg` — structured errors carry a `request_id` that also appears in every API error body.

## Updating

`git pull`, `pip install -r backend/requirements.txt`, `systemctl restart dsoreg`. Never edit `PUBLIC_HOST` or
`TAG_KEY_HEX` on a live system: tags written under the old values stop verifying.
