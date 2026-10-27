# Development

## Everything on one machine (no hardware, no Postgres)

```bash
python -m venv .venv && . .venv/bin/activate                # Windows: .venv\Scripts\activate
pip install -r backend/requirements-dev.txt customtkinter requests

python scripts/gen_keys.py --dev --out-dir secrets.local --host localhost:5000 --backend-url http://localhost:5000
python scripts/seed_demo.py --env-file secrets.local/backend.env --pi-home secrets.local/pi      # 8 scopes, usage, faults
cd backend && python -m dsoreg.cli --env-file ../secrets.local/backend.env run-dev --port 5000
```

* Dashboard: <http://localhost:5000/app/> — `admin / Demo-Passphrase-Alpha-2026`, `viewer / Demo-Passphrase-Bravo-2026`
  (dev data only; the seed script refuses to run outside `DSOREG_ENV=development`).
* Public tap page: the seed script prints a tap URL per asset — open one in a browser (a phone-sized window shows the
  real layout).
* Pi app with the tag simulator:
  `cd pi && DSO_ENROLLER_HOME=../secrets.local/pi python -m dso_enroller --sim --size 800x480`
  (the first start asks for a PIN). Tags written there reach the dev server through the real outbox and sender.
* `secrets.local/` is git-ignored. It holds real (development) key material.

## Tests

```bash
python scripts/test_all.py
```
runs three separate pytest sessions — they must stay separate because the backend and the Pi each have their own
`conftest.py` and never import each other:

| Suite | Covers |
|---|---|
| `backend/` | crypto, enrolment attacks (replay, bad signature, stale clock, revoked device, duplicate, swapped data, overlong/foreign links), tap + usage rules, dashboard roles, concurrency, audit tampering, injection strings, headers |
| `pi/` | cross-implementation vectors, tag layout, validation, service ordering and failure paths, lock ordering, outbox, PIN, config permissions |
| `tests/` | the whole path with both code bases: offline queue → reconnect, lost reply, server rejection, revoked device, swapped tag data |

### Running the backend tests against PostgreSQL

Not done by the suite itself. To check it manually, point a development environment at a scratch database:

```bash
createdb dsoreg_test
DATABASE_URL='postgresql+psycopg:///dsoreg_test' DSOREG_ENV=development …gen_keys --dev… then run seed_demo.py and the console flow
```
and run `deploy/postgres_roles.sql` against it to confirm the grants work with the real queries.

## Regenerating shared test vectors

`python scripts/gen_vectors.py` rewrites `vectors/crypto.json` from the backend implementation. Do this **only** when
the wire format deliberately changes, and update the Pi implementation in the same change — both test suites load that
file and must reproduce it.

## Layout rules that keep this maintainable

* Routes validate and serialise; **services decide**. No business logic in `routes/`.
* Nothing in `backend/` imports `pi/` and vice versa. Shared logic is duplicated on purpose and pinned by vectors.
* Every error is the envelope `{"error": {"code", "message", "request_id"}}` — no hand-built error JSON.
* Every console mutation writes its audit row in the same transaction.
* Frontend: no inline script/style, no `innerHTML`, no framework, no build step.
