---
name: add-workflow
description: "Create a compound workflow tool that orchestrates multiple Resolve API calls into a single high-level operation."
---

# Add a Workflow Tool

Workflow tools live in `src/cutmaster_ai/workflows/` and combine multiple Resolve API calls into a single action.

## Existing Workflows

| File | Tools | Purpose |
|------|-------|---------|
| `grade.py` | 6-node log grading, CST setup, LUT listing | Color grading workflows |
| `deliver.py` | Quick render presets, batch export | Delivery workflows |
| `conform.py` | EDL/XML import, relink, round-trip | Conform workflows |
| `assembly.py` | Rough cut builder, scene detection | Assembly workflows |
| `ingest.py` | Media import, bin organisation | Ingest workflows |

## Pattern

```python
from ..config import mcp
from ..errors import safe_resolve_call
from ..resolve import _boilerplate

@mcp.tool
@safe_resolve_call
def cutmaster_your_workflow(
    param: str,
    option: str = "default",
    apply_to_all: bool = False,
) -> str:
    """Workflow description — explain the multi-step operation.

    Args:
        param: What this controls.
        option: Variant selection. Defaults to "default".
        apply_to_all: Apply to all clips vs current only. Defaults to False.
    """
    resolve, project, media_pool = _boilerplate()
    timeline = project.GetCurrentTimeline()
    if not timeline:
        return "Error: no timeline open"

    results = []

    # Step 1: ...
    # Step 2: ...
    # Step 3: ...

    return f"Workflow complete: {len(results)} items processed"
```

## Guidelines

- Workflows should feel like a single action to the user
- Include sensible defaults so most params are optional
- Return detailed status: what was done, how many items affected
- Handle partial failures gracefully — don't bail on first error
- Add the import to `src/cutmaster_ai/__init__.py` if new file

## After creating

```bash
uv run ruff check src/ --fix && uv run ruff format src/ && uv run pytest tests/ -v
```
