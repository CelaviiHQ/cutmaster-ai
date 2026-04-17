"""HTTP backend for the CutMaster React panel.

Serves the panel UI + exposes cutmaster endpoints over local HTTP/SSE. Same
underlying Resolve logic as the MCP stdio server — both layers call into
``celavii_resolve.cutmaster`` plain Python functions.

Install with:  pip install celavii-resolve[panel]
Start with:    celavii-resolve-panel
"""
