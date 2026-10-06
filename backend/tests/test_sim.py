"""The simulator: a private, whole-site sandbox per sign-in, apart from the live bench."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hestia.main import create_app
from hestia.security.auth import Role
from hestia.settings import Settings
from hestia.sim.config import SimConfig
from hestia.sim.scenarios import SCENARIOS

PASSWORD = "correct-horse-battery"
REASON = "Line replaced, leak test passed, room ventilated"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path, mode="twin", twin_speed=60, sim_max_sessions=2, planner_internet=False)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        users = c.app.state.services.users  # type: ignore[attr-defined]
        users.create("vic", PASSWORD, Role.VIEWER)
        users.create("val", PASSWORD, Role.VIEWER)
        yield c


def login(client: TestClient, username: str = "vic") -> dict[str, str]:
    r = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf"]}


def start(client: TestClient, csrf: dict[str, str], **body: object) -> dict:
    r = client.post("/api/sim/start", json=body, headers=csrf)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_visitor_runs_a_whole_site_while_the_live_view_stays_the_bench(client: TestClient) -> None:
    csrf = login(client)
    snap = start(client, csrf)
    assert snap["twin"]["model"] == "site" and snap["sim"]["config"]["end_use"] == "fuel_cell"
    assert client.get("/api/state").json()["twin"]["model"] == "bench"
    catalog = client.get("/api/sim/catalog").json()
    assert {s["id"] for s in catalog["scenarios"]} == {s.id for s in SCENARIOS}


def test_each_sign_in_has_its_own_simulation(client: TestClient) -> None:
    start(client, login(client, "vic"))
    other = TestClient(client.app)
    login(other, "val")
    assert other.get("/api/sim/state").status_code == 404


def test_a_leak_in_the_simulator_never_reaches_the_live_journal(client: TestClient) -> None:
    csrf = login(client)
    start(client, csrf, scenario="large_leak")
    r = client.post("/api/sim/time", json={"advance_hours": 0.25}, headers=csrf)
    assert r.status_code == 200, r.text
    snap = client.get("/api/sim/state").json()
    assert snap["telemetry"]["h2_alarm_latched"] and snap["telemetry"]["ventilation"]
    sim_kinds = [e["kind"] for e in client.get("/api/sim/journal").json()["entries"]]
    assert "alarm_latched" in sim_kinds and "scenario" in sim_kinds
    live = client.app.state.services.journal  # type: ignore[attr-defined]
    assert "alarm_latched" not in [e.kind for e in live.entries()]
    # A visitor may reset the alarm in their own simulation, with a reason.
    client.post("/api/sim/fault", json={"name": "leak_large", "on": False}, headers=csrf)
    r = client.post("/api/sim/commands", json={"kind": "reset_h2_alarm", "reason": REASON}, headers=csrf)
    assert r.status_code == 200, r.text
    # …but never on the live installation.
    r = client.post("/api/commands", json={"kind": "reset_h2_alarm", "reason": REASON}, headers=csrf)
    assert r.status_code == 403


def test_a_scenario_that_needs_other_equipment_rebuilds_the_site(client: TestClient) -> None:
    csrf = login(client)
    start(client, csrf, config={"pv_kwp": 8})
    r = client.post("/api/sim/scenario", json={"id": "winter_boiler"}, headers=csrf)
    assert r.status_code == 200, r.text
    config = r.json()["sim"]["config"]
    assert config["end_use"] == "boiler" and config["pv_kwp"] == 8  # the visitor's own settings stay


def test_time_conditions_and_fast_forward(client: TestClient) -> None:
    csrf = login(client)
    start(client, csrf)
    client.post(
        "/api/sim/time", json={"jump": {"month": 1, "day": 15, "hour": 21}, "paused": True}, headers=csrf
    )
    client.post("/api/sim/conditions", json={"tank_pct": 50, "battery_pct": 20, "indoor_c": 18}, headers=csrf)
    before = client.get("/api/sim/state").json()["twin"]["local_time"]
    client.post("/api/sim/time", json={"advance_hours": 2}, headers=csrf)
    snap = client.get("/api/sim/state").json()
    assert before.startswith("2026-01-15 21")
    assert snap["twin"]["local_time"] >= "2026-01-15 22:59"  # two hours on (shown at the last step)
    assert snap["twin"]["paused"]
    assert snap["site"]["hvac_mode"] == "heat" and snap["twin"]["totals"]["h2_used_kg"] > 0


def test_incoherent_sites_are_refused(client: TestClient) -> None:
    csrf = login(client)
    r = client.post(
        "/api/sim/start", json={"config": {"heat_setpoint_c": 22, "cool_setpoint_c": 23}}, headers=csrf
    )
    assert r.status_code == 422
    r = client.post("/api/sim/start", json={"config": {"tank_mawp_bar": 700}}, headers=csrf)
    assert r.status_code == 422


def test_an_idle_simulation_makes_room_for_a_new_visitor(client: TestClient) -> None:
    start(client, login(client, "vic"))
    second = TestClient(client.app)
    start(second, login(second, "val"))
    third = TestClient(client.app)
    start(third, login(third, "vic"))  # a third sign-in: the oldest unwatched simulation goes
    assert client.get("/api/sim/state").status_code == 404
    assert third.get("/api/sim/state").status_code == 200


def test_simulated_alarms_never_send_e_mail(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        email_alerts=True,
        gmail_address="someone@example.com",
        gmail_app_password="abcdabcdabcdabcd",
        alert_recipients=["someone@example.com"],
        planner_internet=False,
    )
    with TestClient(create_app(settings)) as c:
        c.app.state.services.users.create("vic", PASSWORD, Role.VIEWER)  # type: ignore[attr-defined]
        start(c, login(c))
        sims = c.app.state.services.sims  # type: ignore[attr-defined]
        (session,) = sims.sessions.values()
        assert not session.engine.notifier.enabled
        assert c.app.state.services.engine.notifier.enabled  # type: ignore[attr-defined]


def test_every_scenario_builds_a_valid_site() -> None:
    for scenario in SCENARIOS:
        SimConfig().model_copy(update=scenario.config).profile()
        SimConfig.model_validate({**SimConfig().model_dump(), **scenario.config})
