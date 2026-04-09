"""Celavii-Resolve: Maximum-control MCP server for DaVinci Resolve."""

__version__ = "0.1.0"

from .config import mcp  # noqa: F401 — re-export for entry points


def main():
    """Console script entry point."""
    mcp.run()


# Tool modules — importing registers @mcp.tool decorators.
# Modules listed here plus their cascading imports cover all tools.
from .tools import project  # noqa: F401, E402
from .tools import media_storage  # noqa: F401, E402
from .tools import media_pool  # noqa: F401, E402
from .tools import timeline_mgmt  # noqa: F401, E402
from .tools import timeline_edit  # noqa: F401, E402
from .tools import timeline_items  # noqa: F401, E402
from .tools import markers  # noqa: F401, E402
from .tools import color  # noqa: F401, E402
from .tools import fusion  # noqa: F401, E402
from .tools import render  # noqa: F401, E402
from .tools import gallery  # noqa: F401, E402
from .tools import fairlight  # noqa: F401, E402
from .tools import layout  # noqa: F401, E402
from .tools import graph  # noqa: F401, E402
from .tools import scripting  # noqa: F401, E402
from .tools import interchange  # noqa: F401, E402
from .workflows import ingest  # noqa: F401, E402
from .workflows import assembly  # noqa: F401, E402
from .workflows import delivery  # noqa: F401, E402
from .workflows import conform  # noqa: F401, E402
from .workflows import grade  # noqa: F401, E402
from .ai import vision  # noqa: F401, E402
from .ai import color_assist  # noqa: F401, E402
from .ai import timeline_critique  # noqa: F401, E402
from . import resources  # noqa: F401, E402
