"""The HTTP API end to end (digital-twin mode, real engine, temporary database)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hestia.main import create_app
from hestia.security.auth import Role
from hestia.settings import Settings

PASSWORD = "correct-horse-battery"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path, mode="twin", twin_speed=600, login_max_failures=3)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        users = c.app.state.services.users  # type: ignore[attr-defined]
        users.create("ada", PASSWORD, Role.ADMIN)
        users.create("olga", PASSWORD, Role.OPERATOR)
        users.create("vic", PASSWORD, Role.VIEWER)
        yield c


def login(client: TestClient, username: str) -> dict[str, str]:
    r = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf"]}


def wait_for(client: TestClient, predicate: Any, timeout: float = 10.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = client.get("/api/state").json()
        if predicate(state):
            return state
        time.sleep(0.1)
    raise AssertionError("condition not reached; last state: " + str(state)[:500])


def test_health_is_public(client: TestClient) -> None:
    assert client.get("/api/health").json()["status"] == "ok"


def test_everything_else_needs_a_session(client: TestClient) -> None:
    for path in ("/api/state", "/api/trust", "/api/journal", "/api/report"):
        assert client.get(path).status_code == 401


def test_security_headers_are_set(client: TestClient) -> None:
    headers = client.get("/api/health").headers
    assert "default-src 'self'" in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"


def test_wrong_password_is_refused_and_journaled_then_locked_out(client: TestClient) -> None:
    for _ in range(3):
        r = client.post("/api/auth/login", json={"username": "olga", "password": "nope-nope-nope"})
        assert r.status_code == 401
    # Locked out, even with the right password, and from this address for every account.
    locked = client.post("/api/auth/login", json={"username": "olga", "password": PASSWORD})
    assert locked.status_code == 429
    assert client.post("/api/auth/login", json={"username": "ada", "password": PASSWORD}).status_code == 429
    journal = client.app.state.services.journal  # type: ignore[attr-defined]
    assert [e.kind for e in journal.entries()].count("login_failed") == 3


def test_session_cookie_is_hardened(client: TestClient) -> None:
    r = client.post("/api/auth/login", json={"username": "vic", "password": PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie


def test_viewer_can_read_but_not_command(client: TestClient) -> None:
    csrf = login(client, "vic")
    assert client.get("/api/state").status_code == 200
    r = client.post("/api/commands", json={"kind": "set_mode", "mode": "MANUAL"}, headers=csrf)
    assert r.status_code == 403


def test_commands_need_the_csrf_token(client: TestClient) -> None:
    login(client, "olga")
    r = client.post("/api/commands", json={"kind": "set_mode", "mode": "MANUAL"})
    assert r.status_code == 403 and "CSRF" in r.json()["detail"]


def test_operator_command_is_applied_and_journaled(client: TestClient) -> None:
    csrf = login(client, "olga")
    r = client.post(
        "/api/commands", json={"kind": "set_threshold", "name": "temp_alert_c", "value": 66}, headers=csrf
    )
    assert r.status_code == 200, r.text
    wait_for(client, lambda s: s["telemetry"] and s["telemetry"]["temp_alert_c"] == 66)
    entry = client.get("/api/journal").json()["entries"][0]
    assert entry["kind"] == "threshold_changed" and entry["actor"] == "olga"


def test_unsafe_threshold_is_rejected(client: TestClient) -> None:
    csrf = login(client, "olga")
    r = client.post(
        "/api/commands", json={"kind": "set_threshold", "name": "temp_alert_c", "value": 200}, headers=csrf
    )
    assert r.status_code == 409


def test_leak_alarm_reset_requires_a_reason(client: TestClient) -> None:
    csrf = login(client, "olga")
    assert (
        client.post("/api/twin/fault", json={"name": "leak_large", "on": True}, headers=csrf).status_code
        == 200
    )
    wait_for(client, lambda s: s["telemetry"] and s["telemetry"]["h2_alarm_latched"])
    client.post("/api/twin/fault", json={"name": "leak_large", "on": False}, headers=csrf)
    no_reason = client.post("/api/commands", json={"kind": "reset_h2_alarm", "reason": ""}, headers=csrf)
    assert no_reason.status_code == 422
    ok = client.post(
        "/api/commands",
        json={"kind": "reset_h2_alarm", "reason": "Valve resealed, leak test OK"},
        headers=csrf,
    )
    assert ok.status_code == 200, ok.text
    kinds = [e["kind"] for e in client.get("/api/journal").json()["entries"]]
    assert "alarm_latched" in kinds and "alarm_reset" in kinds


def test_report_includes_verification_and_is_journaled(client: TestClient) -> None:
    login(client, "vic")
    report = client.get("/api/report?days=7").json()
    assert report["integrity"]["ok"] is True
    assert len(report["integrity"]["head_hash"]) == 64
    assert "digital twin" in " ".join(report["findings"])
    assert client.get("/api/journal").json()["entries"][0]["kind"] == "report_generated"


def test_tampering_shows_in_the_api(client: TestClient) -> None:
    login(client, "vic")
    db = client.app.state.services.db  # type: ignore[attr-defined]
    with db.transaction() as cur:
        cur.execute("UPDATE journal SET summary = 'nothing to see' WHERE id = 1")
    result = client.post("/api/journal/verify", headers=login(client, "vic")).json()
    assert result["ok"] is False and result["first_broken_id"] == 1


def test_bump_test_on_a_poisoned_twin_sensor_fails_and_withdraws_production(client: TestClient) -> None:
    csrf = login(client, "olga")
    client.post("/api/twin/fault", json={"name": "h2_poisoned", "on": True}, headers=csrf)
    peak = client.post("/api/twin/bump-test", json={"gas_ppm": 1000}, headers=csrf).json()["peak_reading_ppm"]
    r = client.post(
        "/api/maintenance",
        headers=csrf,
        json={"kind": "bump_test", "sensor": "h2", "data": {"gas_ppm": 1000, "peak_reading_ppm": peak}},
    )
    assert r.json()["result"] == "fail"
    state = wait_for(client, lambda s: s["trust"] and not s["trust"]["h2_trusted"])
    assert "bump test" in state["trust"]["h2_why"]


def test_admin_manages_users_others_cannot(client: TestClient) -> None:
    csrf = login(client, "olga")
    assert client.get("/api/users").status_code == 403
    csrf = login(client, "ada")
    r = client.post(
        "/api/users",
        headers=csrf,
        json={"username": "tech", "role": "operator", "password": "a-long-password-1"},
    )
    assert r.status_code == 201
    weak = client.post(
        "/api/users", headers=csrf, json={"username": "weak", "role": "viewer", "password": "short"}
    )
    assert weak.status_code == 422


def test_logout_revokes_the_session(client: TestClient) -> None:
    csrf = login(client, "vic")
    cookie = client.cookies.get("hestia_session")
    client.post("/api/auth/logout", headers=csrf)
    client.cookies.set("hestia_session", cookie)  # replay the old cookie
    assert client.get("/api/state").status_code == 401


def test_dashboard_is_served_with_spa_fallback(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    (static / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside the static folder", encoding="utf-8")
    monkeypatch.setattr("hestia.main.STATIC_DIR", static)
    with TestClient(create_app(settings)) as c:
        page = c.get("/planner")  # a dashboard route, resolved by the browser app
        assert page.status_code == 200 and "root" in page.text
        assert page.headers["cache-control"] == "no-cache"
        assert c.get("/assets/app.js").text == "console.log(1)"
        assert c.get("/api/nope").status_code == 404
        assert "outside" not in c.get("/../secret.txt").text  # no path traversal


def test_health_says_whether_demo_logins_exist(settings: Settings) -> None:
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/health").json()["demo_users"] is False


def test_demo_logins_never_reach_a_real_device(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="only allowed in twin mode"):
        Settings(data_dir=tmp_path, mode="device", demo_users=True)


def test_a_public_demo_of_the_twin_may_run_in_production(tmp_path: Path) -> None:
    s = Settings(
        data_dir=tmp_path,
        env="production",
        mode="twin",
        demo_users=True,
        secret_key="s" * 40,
        journal_key="a" * 64,
    )
    assert s.cookie_secure is True


def test_a_configuration_error_never_prints_secrets(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as error:
        Settings(data_dir=tmp_path, env="production", mode="device", smtp_password="leak-me-not-123")
    assert "HESTIA_MQTT_PASSWORD" in str(error.value)
    assert "leak-me-not-123" not in str(error.value)


def test_validation_errors_do_not_echo_what_was_sent(client: TestClient) -> None:
    csrf = login(client, "ada")
    r = client.post(
        "/api/users", headers=csrf, json={"username": "eve", "role": "viewer", "password": "tiny-pw"}
    )
    assert r.status_code == 422
    assert "tiny-pw" not in r.text
    assert r.json()["detail"][0]["msg"]  # still says what is wrong


def test_alert_settings_are_for_administrators(client: TestClient) -> None:
    login(client, "olga")
    assert client.get("/api/alerts").status_code == 403
    csrf = login(client, "ada")
    assert client.get("/api/alerts").json()["enabled"] is False
    r = client.post("/api/alerts/test", headers=csrf)
    assert r.status_code == 409 and "HESTIA_EMAIL_ALERTS" in r.json()["detail"]


def test_incoherent_planner_inputs_get_a_clean_422(client: TestClient) -> None:
    csrf = login(client, "vic")
    r = client.post("/api/planner/run", json={"heat_setpoint_c": 22, "cool_setpoint_c": 23}, headers=csrf)
    assert r.status_code == 422 and "cooling setpoint" in r.text
