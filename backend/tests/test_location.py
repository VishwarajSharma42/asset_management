import json
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

from dsoreg.db import usage_log

from conftest import Env, iso_now


def setup_asset(env, pid="LOC-1"):
    token, link = env.pi.link(pid)
    env.enrol_ok(product_id=pid, link=link, token=token)
    return link


def scan(env, link):
    q = parse_qs(urlsplit(link).query)
    return env.client.post("/api/v1/scan", json={"t": q["t"][0], "d": q["d"][0]}).get_json()


def submit(env, ticket, **extra):
    body = {"ticket": ticket, "user_name": "Asha Patil", "start_at": iso_now(-30), "end_at": iso_now(-5),
            "working": True, **extra}
    return env.client.post("/api/v1/usage", json=body)


GOOD = {"lat": 19.076012345, "lng": 72.877698765, "accuracy": 12.34}


def test_location_is_stored_encrypted_and_status_ok(env):
    link = setup_asset(env)
    r = submit(env, scan(env, link)["ticket"], location=GOOD)
    assert r.status_code == 201 and r.get_json()["location_recorded"] is True
    with env.engine.connect() as c:
        row = c.execute(select(usage_log)).one()
    assert row.location_status == "ok"
    assert b"19.076" not in bytes(row.location_ct) and b"72.877" not in bytes(row.location_ct)
    plain = json.loads(env.ctx.fields.decrypt("location", row.id, row.location_ct))
    assert plain == {"lat": 19.076012, "lng": 72.877699, "acc": 12.3}           # rounded, not raw


def test_denied_and_unavailable_are_recorded_without_coordinates(env):
    link = setup_asset(env)
    for err, status in (("denied", "denied"), ("unavailable", "unavailable"), ("timeout", "unavailable")):
        assert submit(env, scan(env, link)["ticket"], location_error=err).status_code == 201
    assert submit(env, scan(env, link)["ticket"]).status_code == 201              # location not sent at all
    with env.engine.connect() as c:
        rows = c.execute(select(usage_log.c.location_status, usage_log.c.location_ct)).all()
    assert sorted(r[0] for r in rows) == ["denied", "none", "unavailable", "unavailable"]
    assert all(r[1] is None for r in rows)


@pytest.mark.parametrize("bad", [
    {"lat": 91, "lng": 10}, {"lat": -91, "lng": 10}, {"lat": 10, "lng": 181}, {"lat": 10, "lng": -181},
    {"lat": "19.07", "lng": 72.8}, {"lat": True, "lng": 72.8}, {"lat": 10}, {"lng": 10},
    {"lat": float("nan"), "lng": 10}, {"lat": float("inf"), "lng": 10},
    {"lat": 10, "lng": 10, "accuracy": -5}, {"lat": 10, "lng": 10, "accuracy": 1e9},
    {"lat": 10, "lng": 10, "altitude": 5}, [1, 2], "19.07,72.8", 12,
])
def test_malformed_coordinates_are_rejected_and_the_ticket_survives(env, bad):
    link = setup_asset(env)
    ticket = scan(env, link)["ticket"]
    assert submit(env, ticket, location=bad).status_code == 422
    assert submit(env, ticket, location=GOOD).status_code == 201                  # same ticket still works


def test_location_and_error_together_or_unknown_error_are_rejected(env):
    link = setup_asset(env)
    ticket = scan(env, link)["ticket"]
    assert submit(env, ticket, location=GOOD, location_error="denied").status_code == 422
    assert submit(env, ticket, location_error="because").status_code == 422


def test_required_mode_refuses_a_missing_location_without_burning_the_ticket(tmp_path):
    e = Env(tmp_path, location_required=True)
    link = setup_asset(e)
    res = scan(e, link)
    assert res["location_required"] is True
    for kw in ({}, {"location_error": "denied"}):
        r = submit(e, res["ticket"], **kw)
        assert r.status_code == 422 and r.get_json()["error"]["code"] == "location_required"
    assert submit(e, res["ticket"], location=GOOD).status_code == 201             # allowed after retry
    with e.engine.connect() as c:
        assert len(c.execute(select(usage_log)).all()) == 1


def test_optional_mode_advertises_that_location_is_optional(env):
    assert scan(env, setup_asset(env))["location_required"] is False


def test_only_admins_see_coordinates(env):
    link = setup_asset(env)
    submit(env, scan(env, link)["ticket"], location=GOOD)
    submit(env, scan(env, link)["ticket"], location_error="denied")
    admin, viewer = env.login(), env.login("viewer1", "viewer", "viewer password 123")
    aid = env.client.get("/api/v1/assets", headers=admin).get_json()["items"][0]["id"]

    seen = {i["location_status"]: i for i in env.client.get(f"/api/v1/assets/{aid}/usage", headers=admin).get_json()["items"]}
    assert seen["ok"]["location"] == {"lat": 19.076012, "lng": 72.877699, "accuracy": 12.3}
    assert seen["denied"]["location"] is None

    items = env.client.get(f"/api/v1/assets/{aid}/usage", headers=viewer).get_json()["items"]
    assert {i["location_status"] for i in items} == {"ok", "denied"}
    assert all("location" not in i for i in items)                                # not even the key

    admin_csv = env.client.get("/api/v1/export/usage.csv", headers=admin).get_data(as_text=True)
    viewer_csv = env.client.get("/api/v1/export/usage.csv", headers=viewer).get_data(as_text=True)
    assert "19.076012" in admin_csv and "lat,lng,accuracy_m" in admin_csv
    assert "19.07" not in viewer_csv and "lat" not in viewer_csv.splitlines()[0]


def test_location_ciphertext_is_bound_to_its_own_row(env):
    from dsoreg.security.fieldcrypt import FieldCryptError
    link = setup_asset(env)
    submit(env, scan(env, link)["ticket"], location=GOOD)
    submit(env, scan(env, link)["ticket"], location={"lat": 1.0, "lng": 2.0})
    with env.engine.connect() as c:
        a, b = c.execute(select(usage_log)).all()
    with pytest.raises(FieldCryptError):
        env.ctx.fields.decrypt("location", b.id, a.location_ct)                   # blob copied to another row


def test_browser_is_allowed_to_ask_for_location(env):
    assert "geolocation=(self)" in env.client.get("/health").headers["Permissions-Policy"]
