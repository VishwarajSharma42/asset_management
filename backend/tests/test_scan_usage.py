import os
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select, update

from dsoreg.db import asset_state, fault_report, usage_log
from dsoreg.security import taglink
from dsoreg.security.util import b64u

from conftest import Env, iso_now, png_b64


def enrol_asset(env, product_id="DSO-1", **kw):
    token, link = env.pi.link(product_id)
    out = env.enrol_ok(product_id=product_id, link=link, token=token, **kw)
    return out["asset_id"], link


def scan(env, link, **override):
    q = parse_qs(urlsplit(link).query)
    body = {"t": q["t"][0], "d": q["d"][0], **override}
    return env.client.post("/api/v1/scan", json=body).get_json()


def usage(env, ticket, **kw):
    body = {"ticket": ticket, "user_name": "Asha Patil", "start_at": iso_now(-30),
            "end_at": iso_now(-5), "working": True, **kw}
    return env.client.post("/api/v1/usage", json=body)


def test_tap_shows_passport_and_leaks_no_secrets(env):
    _, link = enrol_asset(env, "DSO-SHOW", name="Keysight DSOX1102G", room="EXTC-7", image=png_b64())
    res = scan(env, link)
    assert res["verdict"] == "ok"
    a = res["asset"]
    assert a["name"] == "Keysight DSOX1102G" and a["room_no"] == "EXTC-7"
    assert a["image_url"].startswith("/media/") and a["condition"] == "working"
    blob = repr(res)
    for secret in ("DSO-SHOW", "04A1B2C3D4E5F6", "token_hash", "product"):
        assert secret not in blob
    assert env.client.get(a["image_url"]).status_code == 200


def test_unknown_and_malformed_tags_get_one_uniform_answer(env):
    enrol_asset(env)
    ghost = {"t": b64u(os.urandom(16)), "d": "AAAA"}
    for body in (ghost, {"t": "x", "d": "y"}, {"t": 5, "d": None}, {}):
        assert env.client.post("/api/v1/scan", json=body).get_json() == {"verdict": "unregistered"}


def test_tag_whose_data_was_swapped_is_flagged(env):
    _, link_a = enrol_asset(env, "DSO-A", tag_uid="04000000000001")
    _, link_b = enrol_asset(env, "DSO-B", tag_uid="04000000000002")
    t_a = parse_qs(urlsplit(link_a).query)["t"][0]
    d_b = parse_qs(urlsplit(link_b).query)["d"][0]
    assert env.client.post("/api/v1/scan", json={"t": t_a, "d": d_b}).get_json()["verdict"] == "tag_mismatch"
    assert env.client.post("/api/v1/scan", json={"t": t_a, "d": "AAAA"}).get_json()["verdict"] == "tag_mismatch"


def test_usage_submission_working(env):
    asset_id, link = enrol_asset(env)
    r = usage(env, scan(env, link)["ticket"])
    assert r.status_code == 201 and r.get_json() == {"status": "recorded", "fault_flagged": False, "location_recorded": False}
    with env.engine.connect() as c:
        row = c.execute(select(usage_log)).one()
        assert row.user_name == "Asha Patil" and row.working and row.asset_id == asset_id
        assert c.execute(select(asset_state.c.condition)).scalar() == "working"


def test_not_working_requires_a_description_and_flags_the_asset(env):
    asset_id, link = enrol_asset(env)
    ticket = scan(env, link)["ticket"]
    assert usage(env, ticket, working=False).status_code == 422  # no note
    r = usage(env, ticket, working=False, fault_note="Channel 2 shows no signal")
    assert r.status_code == 201 and r.get_json()["fault_flagged"] is True
    with env.engine.connect() as c:
        assert c.execute(select(asset_state.c.condition)).scalar() == "faulty"
        f = c.execute(select(fault_report)).one()
        assert f.note == "Channel 2 shows no signal" and f.resolved_at is None
    assert scan(env, link)["asset"]["condition"] == "faulty"


def test_each_scan_ticket_works_exactly_once(env):
    _, link = enrol_asset(env)
    ticket = scan(env, link)["ticket"]
    assert usage(env, ticket).status_code == 201
    r = usage(env, ticket)
    assert r.status_code == 409 and r.get_json()["error"]["code"] == "ticket_used"


def test_usage_needs_a_valid_unexpired_ticket(env):
    _, link = enrol_asset(env)
    assert usage(env, "garbage").status_code == 401
    assert usage(env, "").status_code == 401
    ticket, exp = env.ctx.tickets.mint("any", 60, now=1)
    assert usage(env, ticket).status_code == 401  # expired long ago


def test_many_people_can_use_one_asset_in_the_same_time_window(env):
    _, link = enrol_asset(env)
    for name in ("Asha Patil", "Rohan Mehta", "Sara D'Souza"):
        t = scan(env, link)["ticket"]
        assert usage(env, t, user_name=name, start_at=iso_now(-60), end_at=iso_now(-10)).status_code == 201
    with env.engine.connect() as c:
        assert len(c.execute(select(usage_log)).all()) == 3


def test_in_use_count_reflects_current_sessions(env):
    _, link = enrol_asset(env)
    for n in ("Asha Patil", "Rohan Mehta"):
        usage(env, scan(env, link)["ticket"], user_name=n, start_at=iso_now(-10), end_at=iso_now(60))
    assert scan(env, link)["asset"]["in_use_count"] == 2


def test_bad_names_and_times_are_rejected(env):
    _, link = enrol_asset(env)
    ticket = scan(env, link)["ticket"]
    for kw in ({"user_name": "<img src=x onerror=1>"}, {"user_name": "A"}, {"user_name": "x" * 200},
               {"end_at": iso_now(-60), "start_at": iso_now(-5)},          # end before start
               {"start_at": iso_now(-60 * 24 * 30)},                        # a month ago
               {"end_at": iso_now(60 * 24 * 3)},                            # days ahead
               {"start_at": "yesterday"}, {"start_at": "2026-01-01T10:00:00"},  # no timezone
               {"working": "yes"}):
        assert usage(env, ticket, **kw).status_code in (400, 422), kw


def test_unknown_usage_fields_rejected(env):
    _, link = enrol_asset(env)
    r = usage(env, scan(env, link)["ticket"], admin=True)
    assert r.status_code == 400


def test_retired_asset_cannot_be_used(env):
    asset_id, link = enrol_asset(env)
    ticket = scan(env, link)["ticket"]
    with env.engine.begin() as c:
        c.execute(update(asset_state).values(lifecycle="retired"))
    assert usage(env, ticket).status_code == 409
    assert scan(env, link)["verdict"] == "retired"


def test_scan_endpoint_is_rate_limited(env):
    for _ in range(40):
        env.client.post("/api/v1/scan", json={"t": "x", "d": "y"})
    r = env.client.post("/api/v1/scan", json={"t": "x", "d": "y"})
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_campus_network_restriction(tmp_path):
    import ipaddress
    e = Env(tmp_path, scan_allowed_cidrs=(ipaddress.ip_network("10.20.0.0/16"),))
    r = e.client.post("/api/v1/scan", json={"t": "x", "d": "y"})  # test client is 127.0.0.1
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "network_not_allowed"
    r = e.client.post("/api/v1/scan", json={"t": "x", "d": "y"}, environ_overrides={"REMOTE_ADDR": "10.20.3.4"})
    assert r.status_code == 200


def test_tampered_database_row_is_reported_not_displayed(env):
    from dsoreg.db import assets
    _, link = enrol_asset(env)
    with env.engine.begin() as c:
        c.execute(update(assets).values(device_id=env.device_id, token_hash=assets.c.token_hash))
        c.exec_driver_sql("UPDATE assets SET product_index = '" + "0" * 64 + "'")
    assert scan(env, link)["verdict"] == "integrity_error"
