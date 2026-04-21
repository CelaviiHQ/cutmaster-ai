"""Entry point for `python -m cutmaster_ai`."""

from .config import mcp  # noqa: F401 — triggers tool registration via __init__

if __name__ == "__main__":
    mcp.run()
