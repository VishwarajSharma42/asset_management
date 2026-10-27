# Architecture

Derived from the v3 college-asset-register specification, scoped to what this project needs:
**tap-counter clone detection is intentionally left out**, and the register is built around one
question — *who used which asset, when, and was it working?*

## 1. Three lanes, three trust domains

```
 ENROL (Pi, once per asset)          TAP (anyone, anonymous)           CONSOLE (staff)
 ──────────────────────────          ───────────────────────           ───────────────
 validate → write NDEF → verify      phone reads tag, opens            sign in (Argon2id) → Ed25519
 → seal record → outbox (disk)       /c?t=<token>&d=<AES-128 id>       session token, in memory only
 → signed POST (Ed25519)             → POST /api/v1/scan               → scoped API (viewer / admin)
        │                                   │                                  │
        └──────────────► one PostgreSQL database ◄───────────────────────────┘
                         assets (signed, immutable)   asset_profile / asset_state (mutable, audited)
                         usage_log (append-only, +location)      fault_report   audit_log (hash chain)
```

They meet only at the database. The tag and the phone are untrusted; nginx canonicalises and
rate-limits but decides nothing; the backend is the sole authority.

## 2. The tag

One NDEF URI record, 28–32 pages on an NTAG216:

```
https://<PUBLIC_HOST>/c?t=<token>&d=<sealed product id>
```

| Part | Content |
|---|---|
| `t` | 16 random bytes, base64url. The server stores only `SHA-256(token)`. |
| `d` | The product ID, **AES-128-GCM** under a key derived per tag: `HKDF-SHA256(TAG_KEY, salt=token)`. Format `0x01 ‖ nonce(12) ‖ ciphertext+tag`. |

`d` has a purpose beyond appearing encrypted: on every tap the server opens it and compares the result
with the stored product ID (by keyed HMAC index). A tag whose data was copied from another tag, edited,
or paired with a different token gives `tag_mismatch` instead of a passport. A link for one tag cannot be
re-used for another, because the key depends on the token.

**Locking.** When enabled (`tag_lock_enabled = true` on the Pi and a per-tag confirmation), the Pi sets the
static (pages 03h–0Fh) and dynamic lock bits for every page it wrote, **after** the record is durable in
the outbox. A failed lock leaves a registered, working, merely-unlocked tag and the UI offers a retry.
Irreversible; off by default.

## 3. Enrolment (Pi → server)

`POST /api/v1/enrol`, body = JSON, headers `X-Device-Id`, `X-Timestamp`, `X-Idempotency-Key`, `X-Signature`.

* **Signature:** Ed25519 over `DSO1 \n METHOD \n path \n timestamp \n idempotency-key \n SHA-256(body)`.
  Verified against the key registered for that device **before the body is parsed**; wrong signature and
  unknown device are indistinguishable; ±90 s timestamp window; revoked devices refused.
* **Sealed record:** the product ID, tag UID and full link travel as an X25519 + HKDF + **AES-128-GCM**
  sealed box to the backend's public key, bound (AAD) to the device and the token hash. The Pi can write
  records but cannot read the register back; its outbox on disk holds no readable product IDs.
* **Consistency checks:** the link's host must be ours, `SHA-256(t)` must equal `token_hash`, and `d` must open
  to exactly the sealed product ID.
* **Idempotency:** the same key + same body returns the stored reply (a lost response never duplicates);
  the same key + different body is a conflict.
* **Quota:** a database-checked daily limit per device bounds what a stolen device key can register.
* **Photo:** base64 in the body; the server decodes it to pixels, re-encodes a fresh ≤1024 px JPEG (no EXIF,
  no embedded payloads), names it by its SHA-256, serves it with `nosniff`.
* One transaction writes the signed asset row, profile, state, audit entry and idempotency record.

**Offline first.** The Pi writes the record to a SQLite outbox (WAL, `synchronous=FULL`) *before* telling the
operator it worked. A background sender signs **at send time** (timestamps are short-lived; records may wait
hours) and retries with back-off. Permanent rejections (duplicate product, bad data, revoked device) stop
retrying and show in the UI.

## 4. Data at rest

| Column | Protection |
|---|---|
| product ID, tag UID, link | AES-128-GCM per cell, key = `HKDF(KEK, salt=row id, info=column)`, AAD binds the ciphertext to its own row and column |
| lookups (uniqueness, search) | keyed HMAC indexes (`TAG_INDEX_KEY`) — a dump cannot be tested against guesses |
| asset row identity | Ed25519 signature over `{id, token_hash, product_index, uid_index, device, created_at}`; checked on every tap and dashboard read |
| private keys in env | AES-256-GCM-wrapped under the **KEK**, which is *not* in the env file or backups |
| passwords | Argon2id |
| client IPs | HMAC with a daily-rotating seed, 24 hex chars; the address itself is never stored |

The signed row is **immutable**. Anything that changes (name, room, condition, lifecycle) lives in
`asset_profile` / `asset_state`, each with an optimistic-concurrency `version` (`If-Match` → 409 on conflict).

## 5. The tap and the usage form

`POST /api/v1/scan {t, d}` → one of `ok`, `unregistered`, `tag_mismatch`, `integrity_error`, `retired`.
Unknown / malformed input gets the same `unregistered` answer (no token probing). `ok` returns the **public**
fields only — name, room, photo, condition, how many are using it now — plus a **scan ticket**: an
HMAC-signed, 30-minute, single-use proof that this asset was just scanned.

`POST /api/v1/usage {ticket, user_name, start_at, end_at, working, fault_note}`:

* the ticket must be valid, unexpired and unused (the `ticket_jti` column is `UNIQUE`);
* times must be timezone-aware, end ≥ start, ≤ 24 h long, within 7 days back / 12 h forward;
* `working = false` requires a description, opens a `fault_report` and sets the asset's condition to
  `faulty`; a later "working" report does **not** hide an unresolved fault — only staff can resolve it;
* **overlapping sessions are allowed** — there is deliberately no exclusion rule. The dashboard computes
  "in use now" and, per session, how many others overlapped it.

## 6. Dashboard API

Staff sign in at `/api/v1/auth/login` (5 failures → lock-out with growing back-off; uniform timing and
messages for unknown users). Tokens are Ed25519-signed, 30-minute, carry the user's `epoch`; changing a
password or disabling an account bumps it and kills every session at once. Roles are read from the database
on every request. `viewer`: read everything except secrets. `admin`: also edit, resolve faults, retire/restore,
manage users, read the audit log (which includes tag UID and link in the asset detail).

Every console mutation writes its **audit row in the same transaction**. The audit log is a hash chain
(`hash = SHA-256(prev ‖ canonical entry)`); `GET /api/v1/audit/verify` and `dsoreg.cli verify-audit` walk it
and report the first broken link.

## 7. Front end

No framework, no build step, no inline script or style. Everything user-visible is set with `textContent`; the
session token is a module variable (a reload signs you out); the CSP is `default-src 'none'` with same-origin
exceptions. The usage timeline is an SVG with one bar per session, overlapping sessions in separate lanes.

## 8. The Pi app

`dso_enroller`: CustomTkinter GUI (light, follows system light/dark), designed for an 800×480 touch screen.

* PIN lock (Argon2id, persistent lock-out, auto-lock after 5 idle minutes); first run asks you to choose it.
* Tap a text box → on-screen keyboard (names auto-capitalise; product ID and room auto-uppercase and only
  offer allowed characters). Touch photo picker with thumbnails, Pictures and USB shortcuts.
* Live write progress, plain-language errors, "No record was created" on failure, status chips (reader,
  server, queue, clock drift, lock posture).
* The reader is an interface: `PN532Reader` (I²C or SPI) or `SimulatedReader` (in-memory NTAG216 with lock-bit
  enforcement and fault injection) — the whole app runs without hardware.

## 9. What changed relative to the v2 enroller code, and why

Found while porting `pi/` from the medicine build:

| v2 behaviour | Here |
|---|---|
| `write_page` ignored `ntag2xx_write_block`'s return value. The adafruit function returns `False` for a refused write instead of raising, so a failed write could look like success. | `False` is treated as a failed attempt and retried. |
| Every failure surfaced as a one-line `InCommunicateThru status N`. | Each attempt logs its full exception; the final error names the page and attempt count. |
| `fast_read` refused pages below 04h, but the lock sequence needed page 02h. | `read_pages` reads any page. |
| The lock sequence wrote `CFG0` (setting `AUTH0=E3h`) and then `CFG1`, which needs a password at that point. | This project locks with the lock **bits** only (no counter or mirror to protect), avoiding the config pages. |
| The write path used InDataExchange while every other command used InCommunicateThru. | `write_mode = "raw"` sends the write through InCommunicateThru too (experimental, untested on hardware). |

## 10. Decisions

| | Decision | Why |
|---|---|---|
| D1 | No tap counter / mirror | Out of scope for this project. Consequence: cloned tags are not detected (see SECURITY). |
| D2 | AES-128-GCM for product ID and fields | As requested; 128-bit GCM is not the weak link here. Key *wrapping* uses AES-256-GCM. |
| D3 | `d` uses a symmetric tag key shared by Pi and server | Keeps the URL short (fewer page writes on a marginal reader). Compromise of the Pi reveals product IDs of tags someone can already read; it does not allow registering anything. |
| D4 | SQLAlchemy Core, SQLite for tests, PostgreSQL in production | Portable; the Postgres run is the open item in README. |
| D5 | Local staff accounts, no LDAP | As chosen. LDAP can be added behind `users.login`. |
| D6 | Staff tokens in memory, not cookies | No CSRF surface and nothing persistent for XSS to steal, at the cost of signing in after a reload. |
