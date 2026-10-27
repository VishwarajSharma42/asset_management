from urllib.parse import parse_qs, urlsplit

from sqlalchemy import update

from dsoreg.db import audit_log, users

from conftest import iso_now


def enrol(env, pid, name="Scope", room="EXTC-1", uid=None):
    token, link = env.pi.link(pid)
    out = env.enrol_ok(product_id=pid, link=link, token=token, name=name, room=room,
                       tag_uid=uid or ("04" + pid.encode().hex().upper().ljust(12, "0")[:12]))
    return out["asset_id"], link


def use(env, link, *, working=True, note=None, start=-30, end=-5, who="Asha Patil"):
    q = parse_qs(urlsplit(link).query)
    ticket = env.client.post("/api/v1/scan", json={"t": q["t"][0], "d": q["d"][0]}).get_json()["ticket"]
    body = {"ticket": ticket, "user_name": who, "start_at": iso_now(start), "end_at": iso_now(end),
            "working": working}
    if note:
        body["fault_note"] = note
    r = env.client.post("/api/v1/usage", json=body)
    assert r.status_code == 201, r.get_data(as_text=True)


def test_login_good_and_bad(env):
    env.make_user()
    ok = env.client.post("/api/v1/auth/login", json={"username": "admin1", "password": "correct horse battery"})
    assert ok.status_code == 200 and ok.get_json()["role"] == "admin"
    bad = env.client.post("/api/v1/auth/login", json={"username": "admin1", "password": "wrong password!!"})
    ghost = env.client.post("/api/v1/auth/login", json={"username": "nobody", "password": "wrong password!!"})
    assert bad.status_code == ghost.status_code == 401
    assert bad.get_json()["error"]["message"] == ghost.get_json()["error"]["message"]


def test_account_locks_after_repeated_failures(env):
    env.make_user()
    for _ in range(5):
        env.client.post("/api/v1/auth/login", json={"username": "admin1", "password": "wrong password!!"})
    r = env.client.post("/api/v1/auth/login", json={"username": "admin1", "password": "correct horse battery"})
    assert r.status_code == 429 and r.get_json()["error"]["code"] == "account_locked"


def test_endpoints_require_a_valid_token(env):
    assert env.client.get("/api/v1/assets").status_code == 401
    assert env.client.get("/api/v1/assets", headers={"Authorization": "Bearer nonsense"}).status_code == 401
    assert env.client.get("/api/v1/overview").status_code == 401


def test_viewer_can_read_but_not_write_or_see_secrets(env):
    aid, _ = enrol(env, "DSO-V")
    v = env.login("viewer1", "viewer")
    assert env.client.get("/api/v1/assets", headers=v).status_code == 200
    detail = env.client.get(f"/api/v1/assets/{aid}", headers=v).get_json()
    assert detail["product_id"] == "DSO-V" and "tag_link" not in detail and "tag_uid" not in detail
    assert env.client.patch(f"/api/v1/assets/{aid}", json={"name": "X Y"}, headers={**v, "If-Match": "1"}).status_code == 403
    assert env.client.get("/api/v1/users", headers=v).status_code == 403
    assert env.client.get("/api/v1/audit", headers=v).status_code == 403
    assert env.client.post(f"/api/v1/assets/{aid}/lifecycle", json={"lifecycle": "retired", "reason": "old"}, headers=v).status_code == 403


def test_admin_sees_secrets(env):
    aid, link = enrol(env, "DSO-ADM")
    a = env.login()
    d = env.client.get(f"/api/v1/assets/{aid}", headers=a).get_json()
    assert d["tag_link"] == link and d["tag_uid"] and d["integrity"] is True


def test_asset_list_filters_search_and_sort(env):
    enrol(env, "DSO-A", name="Alpha Scope", room="EXTC-1")
    _, link_b = enrol(env, "DSO-B", name="Beta Scope", room="EXTC-2")
    enrol(env, "DSO-C", name="Gamma Meter", room="EXTC-2")
    use(env, link_b, working=False, note="No display")
    a = env.login()
    get = lambda qs: env.client.get("/api/v1/assets" + qs, headers=a).get_json()
    assert get("")["total"] == 3
    assert [i["name"] for i in get("?room=EXTC-2")["items"]] == ["Beta Scope", "Gamma Meter"]
    assert [i["name"] for i in get("?condition=faulty")["items"]] == ["Beta Scope"]
    assert [i["name"] for i in get("?q=gamma")["items"]] == ["Gamma Meter"]
    assert [i["name"] for i in get("?q=DSO-A")["items"]] == ["Alpha Scope"]     # exact product ID
    assert get("?q=%25")["total"] == 0                                         # wildcard is literal
    assert [i["name"] for i in get("?sort=-name")["items"]][0] == "Gamma Meter"
    assert env.client.get("/api/v1/assets?sort=password_hash", headers=a).status_code == 400
    assert get("?limit=1")["items"].__len__() == 1


def test_injection_strings_in_filters_are_just_text(env):
    enrol(env, "DSO-A")
    a = env.login()
    for q in ("' OR 1=1 --", "x'; DROP TABLE assets; --", "\\", "%", "_"):
        r = env.client.get("/api/v1/assets", query_string={"q": q, "room": q}, headers=a)
        assert r.status_code == 200 and r.get_json()["total"] == 0
    assert env.client.get("/api/v1/assets", headers=a).get_json()["total"] == 1


def test_usage_history_shows_overlaps(env):
    aid, link = enrol(env, "DSO-O")
    use(env, link, who="Asha Patil", start=-60, end=-10)
    use(env, link, who="Rohan Mehta", start=-50, end=-20)
    use(env, link, who="Sara Khan", start=-8, end=-2)
    a = env.login()
    items = env.client.get(f"/api/v1/assets/{aid}/usage", headers=a).get_json()["items"]
    by = {i["user_name"]: i for i in items}
    assert by["Asha Patil"]["overlaps"] == 1 and by["Rohan Mehta"]["overlaps"] == 1
    assert by["Sara Khan"]["overlaps"] == 0


def test_edit_needs_if_match_and_detects_conflicting_edits(env):
    aid, _ = enrol(env, "DSO-E")
    a = env.login()
    url = f"/api/v1/assets/{aid}"
    assert env.client.patch(url, json={"room_no": "EXTC-9"}, headers=a).status_code == 428
    assert env.client.patch(url, json={"room_no": "EXTC-9"}, headers={**a, "If-Match": "1"}).status_code == 200
    stale = env.client.patch(url, json={"room_no": "EXTC-5"}, headers={**a, "If-Match": "1"})
    assert stale.status_code == 409 and stale.get_json()["error"]["details"]["current_version"] == 2
    assert env.client.get(url, headers=a).get_json()["room_no"] == "EXTC-9"
    assert env.client.patch(url, json={"password_hash": "x"}, headers={**a, "If-Match": "2"}).status_code == 400


def test_resolving_the_only_open_fault_marks_the_asset_working_again(env):
    aid, link = enrol(env, "DSO-F")
    use(env, link, working=False, note="Probe compensation broken")
    a = env.login()
    faults = env.client.get("/api/v1/faults", headers=a).get_json()["items"]
    assert len(faults) == 1 and faults[0]["asset_name"] == "Scope"
    r = env.client.post(f"/api/v1/faults/{faults[0]['id']}/resolve", json={"note": "Replaced probe"}, headers=a)
    assert r.status_code == 200 and r.get_json()["asset_condition"] == "working"
    assert env.client.get(f"/api/v1/assets/{aid}", headers=a).get_json()["condition"] == "working"
    again = env.client.post(f"/api/v1/faults/{faults[0]['id']}/resolve", json={"note": "again"}, headers=a)
    assert again.status_code == 409


def test_a_working_report_does_not_hide_an_unresolved_fault(env):
    aid, link = enrol(env, "DSO-G")
    use(env, link, working=False, note="Dead channel 3")
    use(env, link, working=True, who="Rohan Mehta")
    a = env.login()
    assert env.client.get(f"/api/v1/assets/{aid}", headers=a).get_json()["condition"] == "faulty"


def test_overview_counts(env):
    _, la = enrol(env, "DSO-1")
    _, lb = enrol(env, "DSO-2")
    use(env, la, start=-10, end=30)
    use(env, lb, working=False, note="Broken knob")
    a = env.login()
    o = env.client.get("/api/v1/overview", headers=a).get_json()
    assert (o["total_assets"], o["faulty"], o["in_use_now"], o["open_faults"]) == (2, 1, 1, 1)


def test_every_mutation_is_in_a_verifiable_audit_chain(env):
    aid, link = enrol(env, "DSO-H")
    use(env, link, working=False, note="Cracked screen")
    a = env.login()
    env.client.patch(f"/api/v1/assets/{aid}", json={"name": "Renamed Scope"}, headers={**a, "If-Match": "1"})
    fid = env.client.get("/api/v1/faults", headers=a).get_json()["items"][0]["id"]
    env.client.post(f"/api/v1/faults/{fid}/resolve", json={"note": "Screen replaced"}, headers=a)
    env.client.post(f"/api/v1/assets/{aid}/lifecycle", json={"lifecycle": "retired", "reason": "End of life"}, headers=a)
    events = [i["event"] for i in env.client.get("/api/v1/audit", headers=a).get_json()["items"]]
    for needed in ("asset.enrol", "fault.reported", "asset.update", "fault.resolve", "asset.retired", "login.success"):
        assert needed in events, (needed, events)
    assert env.client.get("/api/v1/audit/verify", headers=a).get_json()["ok"] is True
    with env.engine.begin() as c:
        c.execute(update(audit_log).where(audit_log.c.event == "fault.resolve").values(actor="user:nobody"))
    res = env.client.get("/api/v1/audit/verify", headers=a).get_json()
    assert res["ok"] is False and res["broken_at_seq"]


def test_disabling_a_user_or_changing_password_kills_their_session(env):
    env.make_user("viewer1", "viewer", "viewer password 123")
    v = env.login("viewer1", "viewer", "viewer password 123")
    a = env.login()
    assert env.client.get("/api/v1/assets", headers=v).status_code == 200
    uid = [u for u in env.client.get("/api/v1/users", headers=a).get_json()["items"] if u["username"] == "viewer1"][0]["id"]
    env.client.post(f"/api/v1/users/{uid}/active", json={"active": False}, headers=a)
    assert env.client.get("/api/v1/assets", headers=v).status_code == 401
    assert env.client.post("/api/v1/auth/login", json={"username": "viewer1", "password": "viewer password 123"}).status_code == 401
    env.client.post(f"/api/v1/users/{uid}/active", json={"active": True}, headers=a)
    v2 = env.login("viewer1", "viewer", "viewer password 123")
    r = env.client.post("/api/v1/auth/password", json={"current": "viewer password 123", "new": "another long passphrase"}, headers=v2)
    assert r.status_code == 200
    assert env.client.get("/api/v1/assets", headers=v2).status_code == 401


def test_admin_cannot_disable_themselves_and_weak_passwords_are_refused(env):
    a = env.login()
    me = env.client.get("/api/v1/auth/me", headers=a).get_json()
    assert env.client.post(f"/api/v1/users/{me['id']}/active", json={"active": False}, headers=a).status_code == 409
    r = env.client.post("/api/v1/users", json={"username": "new1", "display_name": "New One", "role": "viewer", "password": "short"}, headers=a)
    assert r.status_code == 422
    r = env.client.post("/api/v1/users", json={"username": "new1", "display_name": "New One", "role": "root", "password": "long enough password"}, headers=a)
    assert r.status_code == 400


def test_csv_export_neutralises_formula_injection(env):
    _, link = enrol(env, "DSO-X")
    use(env, link, working=False, note="=HYPERLINK(\"http://evil\")")
    a = env.login()
    body = env.client.get("/api/v1/export/usage.csv", headers=a).get_data(as_text=True)
    assert "'=HYPERLINK" in body and ",=HYPERLINK" not in body


def test_security_headers_and_error_envelope(env):
    r = env.client.get("/health")
    assert r.status_code == 200
    assert "default-src 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "DENY"
    nf = env.client.get("/api/v1/nope")
    assert nf.status_code == 404 and set(nf.get_json()["error"]) >= {"code", "message", "request_id"}
    assert env.client.get("/media/../../etc/passwd").status_code == 404
    assert env.client.get("/media/" + "a" * 64 + ".jpg").status_code == 404
    assert env.client.post("/api/v1/scan", data="not json", content_type="text/plain").status_code == 400


def test_public_and_staff_endpoints_reject_oversized_bodies(env):
    big = {"t": "x" * 60_000, "d": "y"}
    assert env.client.post("/api/v1/scan", json=big).status_code == 413
    assert env.client.post("/api/v1/usage", json={"ticket": "z" * 60_000}).status_code == 413
    assert env.client.post("/api/v1/auth/login", json={"username": "a", "password": "p" * 60_000}).status_code == 413
