# DSO Asset Register

NFC-tagged tracking for the college's digital storage oscilloscopes (and any other lab equipment).

* **Admins** write a tag on a Raspberry Pi: name, product ID, room, optional photo. The tag carries a
  link plus the product ID encrypted with AES-128-GCM. The record goes to the college server.
* **Anyone** taps the tag with a phone — no app. They see the equipment's name, photo and room, then
  log their name, start/end time and whether it worked. Several people can use one asset at the
  same time. A "No" flags a fault.
* **Staff** sign in to a dashboard: every asset, who is using it now, who used it when (with overlaps
  drawn on a timeline), and every fault.

```
 Raspberry Pi            phone (no app)             college server
 ───────────             ──────────────             ──────────────
 GUI enrolment app  ──►  tap tag → /c?t=…&d=…  ──►  nginx → gunicorn (Flask) → PostgreSQL
 PN532 + NTAG216         passport + usage form       /app dashboard (staff login)
 signed, offline-first   one-time scan ticket        hash-chained audit log
```

## What is in the repository

| Path | What it is |
|---|---|
| `backend/` | Flask API (`dsoreg`): enrolment, tap, usage, dashboard API, auth, audit chain. 60 tests. |
| `frontend/` | Plain HTML/CSS/JS, **no build step, no npm**. `/c` public tap page, `/app` dashboard. Strict CSP. |
| `pi/` | The enrolment app (`dso_enroller`): touch GUI with on-screen keyboard, PIN lock, PN532 driver, tag simulator, durable outbox. 17 tests. |
| `tests/` | End-to-end: Pi code → outbox → drainer → backend → public tap, using both real code bases. |
| `deploy/` | nginx, systemd units, least-privilege Postgres roles. |
| `scripts/` | `gen_keys.py` (all keys), `seed_demo.py` (dev data), `gen_vectors.py`, `test_all.py`. |
| `vectors/` | Shared crypto test vectors — the contract between backend and Pi. |
| `docs/` | ARCHITECTURE, SECURITY, DEPLOY, PI_SETUP, DEVELOPMENT. |

## Try it on one laptop (no hardware)

```bash
python -m venv .venv && . .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r backend/requirements-dev.txt customtkinter requests
python scripts/gen_keys.py --dev --out-dir secrets.local --host localhost:5000 --backend-url http://localhost:5000
python scripts/seed_demo.py --env-file secrets.local/backend.env --pi-home secrets.local/pi
cd backend && python -m dsoreg.cli --env-file ../secrets.local/backend.env run-dev      # http://localhost:5000/app/
# in another terminal — the Pi app with the tag simulator:
cd pi && DSO_ENROLLER_HOME=../secrets.local/pi python -m dso_enroller --sim --size 800x480
python scripts/test_all.py                                                              # all three suites
```
More in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Status — read this before relying on it

**Done and tested** (80+ automated tests, plus the GUI and both web pages exercised in a browser): enrolment
with signatures, idempotency and quotas; the sealed-box and tag-data encryption; row signatures; the audit chain;
the public tap and usage flow; the dashboard; the Pi app against a simulated tag.

**Not yet exercised, and where I would look first if something misbehaves:**
1. **Real NFC hardware.** The PN532 driver is adapted from the earlier v2 enroller; this repository was built
   and tested without a reader attached. Write reliability on a particular PN532 board is a hardware matter —
   see the troubleshooting section of [docs/PI_SETUP.md](docs/PI_SETUP.md) before blaming the software.
2. **PostgreSQL.** The suite runs on SQLite (the code is SQLAlchemy Core, so the same code targets both). Run it
   against a Postgres instance before the first real deployment, then apply `deploy/postgres_roles.sql`.
3. **Tag locking** is implemented (static + dynamic lock bits) and enforced by the simulator, but untested on
   a physical tag. It is **off by default** and irreversible: try it on spare tags first.
4. **The Linux build** (`pi/packaging/build_pi.sh`) must be run on the Pi itself; a Windows `.exe` cannot run
   on a Raspberry Pi. The Windows build script produces a simulator-only demo exe.

**By design, not a bug:** there is no tap counter, so this system does **not** detect a cloned tag. Anyone
who can read a tag can copy its link. Mitigations: one-time scan tickets, rate limits, an optional campus-only
network restriction (`SCAN_ALLOWED_CIDRS`), and the product-ID cross-check at every tap. See
[docs/SECURITY.md](docs/SECURITY.md).
