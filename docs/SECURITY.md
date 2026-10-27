# Security

## Who and what we defend against

| Threat | Mechanism | Where |
|---|---|---|
| Someone registers a fake asset | Ed25519-signed enrolment; unknown/revoked device refused; per-device daily quota; signature checked before the body is parsed | `routes/device.py`, `services/enrol.py` |
| Replay of a captured enrolment | ±90 s timestamp window **and** idempotency key bound into the signature; replays return the stored reply | `security/signing.py` |
| Request tampering in transit | Signature covers method, path, timestamp, key and SHA-256 of the exact body; TLS on top | |
| Stolen Pi / stolen device key | Revoke the device (`cli revoke-device`) — effective immediately; quota bounds what it could register; key file is mode 600 and refused otherwise; PIN gate on the app | |
| Database dump / SQL injection reads | Product ID, UID and link are AES-128-GCM ciphertext, bound to their row and column; lookups use keyed HMAC; queries are parameterised (verified with injection tests) | `security/fieldcrypt.py`, `db.py` |
| Someone edits a row in the database | Signed asset rows (verified on every read); hash-chained audit log; app DB role cannot update/delete `assets`, `usage_log`, `audit_log` | `deploy/postgres_roles.sql` |
| Rewriting history | Audit chain — `audit/verify` names the first broken entry. Copy the head hash off-box regularly | `services/audit.py` |
| Tag data swapped or edited | `d` is AES-128-GCM bound to the token; server compares it with the stored product ID on every tap | `security/taglink.py` |
| Blind form-spamming of usage logs | One-time, 30-minute scan tickets; per-address rate limits (database-backed, correct across workers); strict validation; optional campus-only network | `services/usage.py` |
| Malicious uploads (photo) | Decoded to pixels and re-encoded; EXIF stripped; named by hash; served `nosniff` + `default-src 'none'`; 2 MB / 16 MP caps | `services/images.py` |
| XSS | No inline script/style, `default-src 'none'` CSP, everything via `textContent`; token in memory only | `frontend/` |
| Password guessing | Argon2id; 5 failures lock the account with growing delay; uniform timing and message for unknown users; per-address login limit | `services/users.py` |
| Stale or hijacked sessions | 30-minute tokens, per-user epoch (password change/disable revokes all), role re-read from DB each request | `security/tokens.py`, `http.py` |
| CSV formula injection | Cells starting `= + - @` are prefixed with `'` | `services/registry.py` |
| Pi device walk-up | PIN (Argon2id) with persistent lock-out, auto-lock after 5 idle minutes | `pi/dso_enroller/security.py` |

## What this system does NOT do

1. **It does not detect cloned tags.** There is no tap counter or mirror (by decision). Anyone who can read a tag
   can write its link onto another NTAG. What limits the damage: a clone only opens the *public* passport; every
   usage entry needs a fresh one-time ticket from a real scan request, is rate-limited, and is recorded; and the
   dashboard shows who logged what. If clone detection matters later, the NTAG216 counter and ASCII mirror can be
   added (the v2 code has the design) without changing the rest.
2. **The public tap page is anonymous**, so usage names are self-reported and unverified. Restrict it to the
   campus network (`SCAN_ALLOWED_CIDRS`) if outside submissions are a concern.
3. **Tag transplant** (peeling a tag off one instrument onto another) is not detectable by any software here.
   Use tamper-evident labels.
4. **One box.** The KEK and the database live on the same server; key separation is only as strong as the
   difference in access control between the KEK credential and the database. See DEPLOY.md.
5. **Location is a claim, not proof.** When someone submits the usage form their phone is asked for its
   position and the result is stored AES-128-GCM-encrypted (bound to the row). The browser, not the server,
   supplies the coordinates, so anyone can fake them; treat them as supporting evidence. Only admins see
   coordinates (dashboard + CSV); viewers see whether one was captured. People can refuse: that is recorded
   as `denied` unless `LOCATION_REQUIRED=1`, which rejects submissions without one. Browsers only offer
   location on HTTPS (or localhost), so the production site must use TLS.
6. **Personal data.** Usage entries hold people's names and, if shared, their position. There is no automatic retention/scrub job; decide a
   policy and add one before collecting data from students.
7. **PostgreSQL role grants and RLS in `postgres_roles.sql` are untested** by the automated suite (SQLite).

## Rotating things

| What | How |
|---|---|
| A Pi's key (lost/stolen/leaked) | `python -m dsoreg.cli revoke-device --device-id …`; generate a new pair, register it, replace `device.key` and `device_id` on the Pi |
| A staff password | Dashboard → Users → Reset password (kills their sessions) |
| `TAG_KEY` | **Cannot be rotated for tags already written** (their `d` was sealed under it). Rotating means re-writing tags. Keep it secret and treat a leak as "product IDs are readable". |
| KEK | Re-wrap the three private keys with the new KEK (`security/keys.py`), update the credential, restart |
| Session/ticket keys | Replace and restart; everyone signs in again / open forms must be re-scanned |

## Secrets that must never be committed

`secrets.local/`, any `*.env`, `device.key`, `tag.key`, `lock.key`, `KEK.hex`, `config.toml`, `pin.hash`.
They are in `.gitignore`. If one is ever pasted into a chat, an issue or a commit, treat it as leaked and rotate it.
