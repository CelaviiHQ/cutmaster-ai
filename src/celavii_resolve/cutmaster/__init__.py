"""CutMaster AI — pipeline primitives for the A-Roll assistant.

Each module exposes both a plain Python function (callable from the HTTP
backend and the agent pipeline) and a thin ``@mcp.tool`` wrapper so Claude
Code and Claude Desktop can drive the same primitives over MCP stdio.
"""
