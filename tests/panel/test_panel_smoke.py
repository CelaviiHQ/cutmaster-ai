"""Smoke test for the FastAPI panel app. Does not touch Resolve."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from celavii_resolve import __version__  # noqa: E402
from celavii_resolve.http.app import create_app  # noqa: E402


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app())


def test_ping_returns_ok(client: TestClient) -> None:
    response = client.get("/ping")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["service"] == "celavii-resolve-panel"
    assert body["version"] == __version__


def test_cors_headers_present(client: TestClient) -> None:
    response = client.options(
        "/ping",
        headers={
            "Origin": "null",
            "Access-Control-Request-Method": "GET",
        },
    )
    # TestClient runs without a server, but the CORS middleware still sets headers.
    assert "access-control-allow-origin" in {k.lower() for k in response.headers}


def test_docs_exposed_at_custom_path(client: TestClient) -> None:
    # Confirms we moved Swagger to /_docs (away from the React app's root)
    assert client.get("/_docs").status_code == 200


def test_mcp_entry_point_still_works() -> None:
    """Phase 2 must not break the existing stdio MCP entry point."""
    import celavii_resolve

    assert hasattr(celavii_resolve, "main")
    assert hasattr(celavii_resolve, "mcp")
