"""Scripting escape hatches — execute arbitrary Python or Lua code.

These are power-user tools for operations not covered by specific tools.
Use with caution as they execute arbitrary code.
"""

from ..config import mcp
from ..errors import safe_resolve_call
from ..resolve import get_resolve


@mcp.tool
@safe_resolve_call
def celavii_execute_python(code: str) -> str:
    """Execute arbitrary Python code with DaVinci Resolve API objects in scope.

    Available variables in scope:
        - resolve: The Resolve application object
        - pm: ProjectManager
        - project: Current project (if open)
        - mp: MediaPool (if project open)
        - tl: Current timeline (if available)

    Args:
        code: Python code to execute. The last expression's value is returned.
    """
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."

    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject() if pm else None
    mp = project.GetMediaPool() if project else None
    tl = project.GetCurrentTimeline() if project else None

    # Build execution scope
    scope = {
        "resolve": resolve,
        "pm": pm,
        "project": project,
        "mp": mp,
        "tl": tl,
    }

    try:
        # Try as expression first (returns a value)
        result = eval(code, {"__builtins__": __builtins__}, scope)
        return str(result) if result is not None else "OK (no return value)"
    except SyntaxError:
        # Fall back to exec for statements
        exec(code, {"__builtins__": __builtins__}, scope)
        return "OK"
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool
@safe_resolve_call
def celavii_execute_lua(script: str) -> str:
    """Execute a Lua script in the Fusion environment.

    This runs in the Fusion scripting context, useful for advanced Fusion
    node manipulation not covered by the standard tools.

    Args:
        script: Lua code to execute in Fusion.
    """
    resolve = get_resolve()
    if not resolve:
        return "Error: DaVinci Resolve is not running."

    fusion = resolve.Fusion()
    if not fusion:
        return "Error: Could not access Fusion. Make sure a Fusion comp is loaded."

    try:
        result = fusion.Execute(script)
        return str(result) if result is not None else "Lua script executed."
    except Exception as exc:
        return f"Lua execution error: {exc}"
