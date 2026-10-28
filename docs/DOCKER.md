# Running the server in Docker

Three containers: **db** (Postgres 16), **backend** (Flask under gunicorn), **nginx** (HTTPS, the front
end, tag-photo files). Only nginx is reachable from outside (ports 80/443); the database and the API
are on the internal compose network. The Raspberry Pi enrolment app is *not* containerised (it needs
the NFC reader and a screen); it talks to this server over HTTPS as before.

> **Status:** built and run on Docker Desktop (Windows, `localhost`, self-signed certificate) with
> Postgres 16: tables and the restricted role created, staff login, a Pi-signed enrolment, a tap, a usage
> entry with location and fault, audit chain verified, and data kept across `docker compose down` / `up`.
> Not yet tried: a real host name with a CA-signed certificate, a Linux server, and a real NTAG write
> against this stack. See "If something goes wrong" if anything differs.

## 1. One-time setup

Needs Docker with the compose plugin (`docker compose version`) and Python with the project's
requirements on the machine where you generate keys.

```bash
# passwords (hex, because they go into a URL)
cp deploy/docker/env.example .env          # then fill in DB_OWNER_PASSWORD and DB_APP_PASSWORD
openssl rand -hex 24                       # run twice, one value for each password

# keys. PUBLIC_HOST is written on every tag and can never change afterwards, so use the final name.
python scripts/gen_keys.py --out-dir secrets.docker --host assets.college.edu \
    --backend-url https://assets.college.edu \
    --database-url "postgresql+psycopg://dsoreg_app:<DB_APP_PASSWORD>@db:5432/dsoreg"
chmod 700 secrets.docker && chmod 644 secrets.docker/backend.env secrets.docker/KEK.hex
```

`--host` is a bare host name (no port). The `chmod 644` is needed because the container's user is not
you; the `700` on the folder is what keeps other users on the host out. `secrets.docker/pi/` holds the
Pi's new keys and config; it must replace the Pi's current ones (new keys, new `tag.key`, so tags
written earlier will not open).

Certificate: put `fullchain.pem` and `privkey.pem` in `certs/`. For a test on one machine, make a
self-signed one (browsers will warn, and a Pi needs `ca_bundle` pointing at it):

```bash
mkdir -p certs
docker run --rm -v "$PWD/certs:/certs" alpine/openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
    -keyout /certs/privkey.pem -out /certs/fullchain.pem -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
```

Then create the tables and the restricted database role, and start everything:

```bash
bash deploy/docker/setup.sh
docker compose up -d --build
docker compose ps                          # all three should be running / healthy
```

## 2. First users and the Pi

```bash
docker compose run --rm backend python -m dsoreg.cli create-user --username admin --display-name Admin --role admin
```

The Pi's registration command is in `secrets.docker/DEVICE_REGISTER.txt`. Run it the same way, with
`docker compose run --rm backend` in front of `python -m dsoreg.cli` and without the `--env-file` part.

## 3. Day to day

| Want to | Command |
|---|---|
| see what is happening | `docker compose logs -f backend` (or `nginx`, `db`) |
| restart one part | `docker compose restart backend` |
| deploy a code change | `git pull && docker compose up -d --build` |
| open a shell in the API | `docker compose exec backend sh` |
| check the audit chain | `docker compose run --rm backend python -m dsoreg.cli verify-audit` |
| stop everything, keep data | `docker compose down` |
| stop and **delete all data** | `docker compose down -v` (never run this by accident) |

Data lives in two named volumes, `pgdata` (database) and `media` (tag photos). Rebuilding, restarting
or `docker compose down` does not touch them. Back up with:

```bash
docker compose exec -T db pg_dump -U dsoreg_owner dsoreg > backup-$(date +%F).sql
```

and keep `secrets.docker/KEK.hex` somewhere that is **not** on the same disk or in the same backup:
without it the keys in `backend.env` cannot be unwrapped, and with the backup it protects nothing.

## If something goes wrong

* **A container keeps restarting:** `docker compose logs backend`. A `ConfigError` names the missing
  or malformed variable in `secrets.docker/backend.env`.
* **`permission denied` reading `/run/secrets/kek`:** the file is not world-readable (`chmod 644`).
* **Backend is up but pages fail with database errors:** the app role cannot see the schema. Re-run
  `bash deploy/docker/setup.sh`; if it says the roles exist, compare the error with the grants in
  `deploy/postgres_roles.sql` (it covers all ten tables and the audit-log sequence as of this writing).
* **nginx will not start:** `docker compose logs nginx`; usually a missing `certs/fullchain.pem`.
* **Browser says the connection is not private:** expected with a self-signed certificate.
* **Need to change a variable:** edit `secrets.docker/backend.env` or `.env`, then
  `docker compose up -d` (it recreates only what changed).
* **Start over (test machines only):** `docker compose down -v`, then section 1 again.

HSTS is off until you set `HSTS_VALUE` in `.env`. Turn it on only on the real server: browsers
remember it per host name for a year, which breaks plain-http use of the same name afterwards.
