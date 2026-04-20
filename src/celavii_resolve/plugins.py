"""Plugin discovery via Python entry points.

Two groups are advertised to plugin authors. A plugin may register into one,
the other, or both:

- ``celavii_resolve.tools`` — ``register_tools(mcp: FastMCP) -> None``
  Exposes the plugin's capabilities as MCP tools on the FastMCP server.
  Consumed by MCP clients (Claude Desktop, Claude Code, Cursor…).

- ``celavii_resolve.panel_routes`` — ``register_routes(app: FastAPI) -> None``
  Adds FastAPI routes onto the Panel HTTP server. Consumed by the React
  panel and native shells that talk to the Panel over HTTP.

Registration is best-effort: a plugin that raises at load or register time
is logged and skipped — it must never break the host.

Each group maintains a module-level list of successfully-registered plugin
names, consumed by ``GET /pro/status`` and ``licensing.current_tier()``.
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastmcp import FastMCP

log = logging.getLogger("celavii-resolve.plugins")

TOOLS_GROUP = "celavii_resolve.tools"
PANEL_ROUTES_GROUP = "celavii_resolve.panel_routes"

# Populated by discover_* below. Read by /pro/status and licensing.current_tier.
# Keyed by group name → list of entry-point names that registered without error.
_registered: dict[str, list[str]] = {
    TOOLS_GROUP: [],
    PANEL_ROUTES_GROUP: [],
}


def _load_group(group: str) -> list[tuple[str, object]]:
    """Return ``[(name, loaded_callable), ...]`` for a group, skipping failures."""
    loaded: list[tuple[str, object]] = []
    try:
        eps = entry_points(group=group)
    except Exception:
        log.exception("entry_points(group=%r) failed", group)
        return loaded
    for ep in eps:
        try:
            loaded.append((ep.name, ep.load()))
        except Exception:
            log.exception("Failed to load plugin %s from group %s", ep.name, group)
    return loaded


def discover_tools(mcp: FastMCP) -> list[str]:
    """Discover and register MCP tool plugins.

    Each entry point must resolve to a callable with signature
    ``register_tools(mcp: FastMCP) -> None``. Returns the names of plugins
    that registered successfully.
    """
    names: list[str] = []
    for name, register in _load_group(TOOLS_GROUP):
        try:
            register(mcp)  # type: ignore[operator]
            names.append(name)
            log.info("Registered MCP tool plugin: %s", name)
        except Exception:
            log.exception("Plugin %s failed during register_tools()", name)
    _registered[TOOLS_GROUP] = names
    return names


def discover_panel_routes(app: FastAPI) -> list[str]:
    """Discover and register Panel HTTP route plugins.

    Each entry point must resolve to a callable with signature
    ``register_routes(app: FastAPI) -> None``. Returns the names of plugins
    that registered successfully.
    """
    names: list[str] = []
    for name, register in _load_group(PANEL_ROUTES_GROUP):
        try:
            register(app)  # type: ignore[operator]
            names.append(name)
            log.info("Registered panel-routes plugin: %s", name)
        except Exception:
            log.exception("Plugin %s failed during register_routes()", name)
    _registered[PANEL_ROUTES_GROUP] = names
    return names


def registered_plugins() -> dict[str, list[str]]:
    """Snapshot of the per-group plugin name lists. Used by /pro/status."""
    return {
        "tools": list(_registered[TOOLS_GROUP]),
        "panel_routes": list(_registered[PANEL_ROUTES_GROUP]),
    }


def any_plugin_registered() -> bool:
    """True when at least one plugin registered into either group."""
    return bool(_registered[TOOLS_GROUP] or _registered[PANEL_ROUTES_GROUP])
