"""HTTP backend for the CutMaster React panel.

Serves the panel UI + exposes cutmaster endpoints over local HTTP/SSE. Same
underlying Resolve logic as the MCP stdio server — both layers call into
``cutmaster_ai.cutmaster`` plain Python functions.

Install with:  pip install cutmaster-ai[panel]
Start with:    cutmaster-ai-panel
"""
