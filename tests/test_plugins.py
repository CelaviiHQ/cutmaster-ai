"""Plugin discovery tests.

Uses fake entry-point objects injected via monkeypatch so the tests don't
need to install a second distribution into the venv. Each test verifies
one side of the ``(tools, panel_routes)`` contract.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from cutmaster_ai import plugins


@dataclass
class _FakeEntryPoint:
    name: str
    target: object

    def load(self) -> object:
        return self.target


@pytest.fixture(autouse=True)
def _reset_registry():
    """Each test starts from an empty registry."""
    before = {k: list(v) for k, v in plugins._registered.items()}
    for k in plugins._registered:
        plugins._registered[k] = []
    yield
    plugins._registered.clear()
    plugins._registered.update(before)


def _patch_entry_points(monkeypatch, mapping: dict[str, list[_FakeEntryPoint]]) -> None:
    def fake_entry_points(group: str):
        return mapping.get(group, [])

    monkeypatch.setattr(plugins, "entry_points", fake_entry_points)


def test_discover_tools_calls_register_with_mcp(monkeypatch):
    seen: list[object] = []

    def register(mcp):
        seen.append(mcp)

    _patch_entry_points(
        monkeypatch,
        {plugins.TOOLS_GROUP: [_FakeEntryPoint("fake_tool", register)]},
    )

    sentinel = object()
    names = plugins.discover_tools(sentinel)  # type: ignore[arg-type]

    assert names == ["fake_tool"]
    assert seen == [sentinel]
    assert plugins.registered_plugins()["tools"] == ["fake_tool"]


def test_discover_panel_routes_calls_register_with_app(monkeypatch):
    seen: list[object] = []

    def register(app):
        seen.append(app)

    _patch_entry_points(
        monkeypatch,
        {plugins.PANEL_ROUTES_GROUP: [_FakeEntryPoint("fake_panel", register)]},
    )

    sentinel = object()
    names = plugins.discover_panel_routes(sentinel)  # type: ignore[arg-type]

    assert names == ["fake_panel"]
    assert seen == [sentinel]
    assert plugins.registered_plugins()["panel_routes"] == ["fake_panel"]


def test_plugin_can_register_in_both_groups(monkeypatch):
    def reg_tools(mcp):
        pass

    def reg_routes(app):
        pass

    _patch_entry_points(
        monkeypatch,
        {
            plugins.TOOLS_GROUP: [_FakeEntryPoint("dual_plugin", reg_tools)],
            plugins.PANEL_ROUTES_GROUP: [_FakeEntryPoint("dual_plugin", reg_routes)],
        },
    )

    plugins.discover_tools(object())  # type: ignore[arg-type]
    plugins.discover_panel_routes(object())  # type: ignore[arg-type]

    snapshot = plugins.registered_plugins()
    assert snapshot == {"tools": ["dual_plugin"], "panel_routes": ["dual_plugin"]}
    assert plugins.any_plugin_registered() is True


def test_register_exception_is_isolated(monkeypatch):
    def broken(mcp):
        raise RuntimeError("boom")

    def ok(mcp):
        pass

    _patch_entry_points(
        monkeypatch,
        {
            plugins.TOOLS_GROUP: [
                _FakeEntryPoint("broken_plugin", broken),
                _FakeEntryPoint("ok_plugin", ok),
            ],
        },
    )

    names = plugins.discover_tools(object())  # type: ignore[arg-type]

    assert names == ["ok_plugin"]


def test_load_exception_is_isolated(monkeypatch):
    class _BadEP:
        name = "unloadable"

        def load(self):
            raise ImportError("cannot import")

    def ok(mcp):
        pass

    monkeypatch.setattr(
        plugins,
        "entry_points",
        lambda group: (
            [_BadEP(), _FakeEntryPoint("ok_plugin", ok)] if group == plugins.TOOLS_GROUP else []
        ),
    )

    names = plugins.discover_tools(object())  # type: ignore[arg-type]
    assert names == ["ok_plugin"]


def test_no_plugins_yields_oss_tier(monkeypatch):
    _patch_entry_points(monkeypatch, {})
    plugins.discover_tools(object())  # type: ignore[arg-type]
    plugins.discover_panel_routes(object())  # type: ignore[arg-type]

    from cutmaster_ai.licensing import current_tier

    assert current_tier() == "oss"
    assert plugins.any_plugin_registered() is False


def test_any_plugin_flips_tier_to_standard(monkeypatch):
    def reg(mcp):
        pass

    _patch_entry_points(
        monkeypatch,
        {plugins.TOOLS_GROUP: [_FakeEntryPoint("x", reg)]},
    )
    plugins.discover_tools(object())  # type: ignore[arg-type]

    from cutmaster_ai.licensing import current_tier

    assert current_tier() == "standard"


def test_pro_status_endpoint_shape():
    """The /pro/status response must contain both group names even when empty."""
    from fastapi.testclient import TestClient

    from cutmaster_ai.http.app import create_app

    client = TestClient(create_app())
    resp = client.get("/pro/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tier"] in ("oss", "standard")
    assert "plugins" in body
    assert "tools" in body["plugins"]
    assert "panel_routes" in body["plugins"]
    assert isinstance(body["plugins"]["tools"], list)
    assert isinstance(body["plugins"]["panel_routes"], list)


def test_panel_plugin_routes_register_on_app(monkeypatch):
    """A panel_routes plugin can include_router onto the app during create_app()."""
    from fastapi import APIRouter
    from fastapi.testclient import TestClient

    def register_routes(app):
        router = APIRouter(prefix="/plugins/fake")

        @router.get("/hello")
        def hello():
            return {"ok": True, "from": "fake-plugin"}

        app.include_router(router)

    _patch_entry_points(
        monkeypatch,
        {plugins.PANEL_ROUTES_GROUP: [_FakeEntryPoint("fake_plugin", register_routes)]},
    )

    from cutmaster_ai.http.app import create_app

    client = TestClient(create_app())
    resp = client.get("/plugins/fake/hello")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "from": "fake-plugin"}

    resp = client.get("/pro/status")
    body = resp.json()
    assert "fake_plugin" in body["plugins"]["panel_routes"]
    assert body["tier"] == "standard"


def test_migration_runner_is_idempotent(tmp_path):
    from cutmaster_ai.migrations.runner import apply_migrations

    db = tmp_path / "state.db"
    first = apply_migrations(db)
    assert "0001_init.sql" in first
    second = apply_migrations(db)
    assert second == []

    import sqlite3

    conn = sqlite3.connect(db)
    try:
        tables = {
            row[0] for row in conn.execute("select name from sqlite_master where type='table'")
        }
    finally:
        conn.close()
    for expected in (
        "recent_projects",
        "custom_presets",
        "cutmaster_sessions",
        "panel_state",
        "_cutmaster_schema_migrations",
    ):
        assert expected in tables


def test_http_models_reexports_are_public():
    """Stable models import from cutmaster_ai.http.models."""
    from cutmaster_ai.http import models

    for name in (
        "AnalyzeRequest",
        "AnalyzeResponse",
        "BuildPlanRequest",
        "ExecuteRequest",
        "UserSettings",
    ):
        assert hasattr(models, name), f"{name} missing from http.models"
