---
name: add-tool
description: "Scaffold a new MCP tool for DaVinci Resolve with the correct patterns, decorators, and registration."
---

# Add a New MCP Tool

Use this skill to create a new tool for the Celavii-Resolve MCP server.

## Steps

1. **Determine the domain** — which file in `src/celavii_resolve/tools/` does it belong to?
   - `timeline.py` — timeline operations
   - `clips.py` — clip manipulation
   - `color.py` — color grading, nodes, LUTs
   - `markers.py` — markers
   - `media.py` — media pool, bins, import
   - `render.py` — render queue, presets
   - `fusion.py` — Fusion page, compositions
   - `fairlight.py` — Fairlight audio
   - `metadata.py` — clip/timeline metadata
   - `project.py` — project settings, databases
   - `playback.py` — playback, viewer
   - `track.py` — track management
   - `collaboration.py` — cloud, remote
   - `layout.py` — UI pages, windows
   - `scripting.py` — execute Python/Lua
   - `gallery.py` — stills, PowerGrades
   - Create a new file if none fits

2. **Use this exact pattern:**

```python
@mcp.tool
@safe_resolve_call
def celavii_your_tool_name(param: str, optional: int = 1) -> str:
    """One-line description of what this tool does.

    Args:
        param: Description of required parameter.
        optional: Description with default. Defaults to 1.
    """
    resolve, project, media_pool = _boilerplate()
    # ... Resolve API calls ...
    return "Success: description of what happened"
```

3. **Conventions to follow:**
   - Prefix with `celavii_` (namespace)
   - Return `str` always — never dicts or lists
   - Use `_boilerplate()` for Resolve connection
   - Guard None: `items = thing.GetList() or []`
   - All indices are **1-based**
   - Use `_resolve_safe_dir()` for temp paths, not `tempfile.gettempdir()`

4. **If creating a new module file**, add the import to `src/celavii_resolve/__init__.py`:
```python
from . import your_new_module  # noqa: F401, E402
```

5. **Run lint + tests:**
```bash
uv run ruff check src/ --fix && uv run ruff format src/ && uv run pytest tests/ -v
```
