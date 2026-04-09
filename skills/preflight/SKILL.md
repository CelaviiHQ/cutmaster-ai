---
name: preflight
description: Pre-render checklist — verify media, timeline, settings, and render config before delivering.
---

# /preflight — Pre-Render Checklist

Run a comprehensive check on the current project and timeline before rendering.

## Usage

```
/preflight
```

## Workflow

Run these checks in order. Report results as a checklist with pass/fail for each:

### 1. Project Status
- Call `celavii_get_current_project` — verify a project is open
- Call `celavii_get_current_timeline` — verify a timeline exists

### 2. Media Verification
- Call `celavii_verify_timeline_media` — check all clips are online
- Report any offline or missing media with clip names

### 3. Timeline Structure
- Call `celavii_list_timeline_items` for video track 1 — verify clips exist
- Check for very short clips (< 2 frames) that might be errors
- Check for gaps (if detectable via frame numbers)

### 4. Render Configuration
- Call `celavii_get_render_format_and_codec` — report current format
- Call `celavii_get_render_settings` — check TargetDir is set and writable
- Verify resolution matches project settings

### 5. Markers Check
- Call `celavii_get_timeline_markers` — report any Red markers (issues)
- Flag any "TODO" or "FIX" markers

### 6. Summary
- Print a summary: X checks passed, Y warnings, Z failures
- If all clear: "Ready to render. Use /deliver to start."
- If issues: list them with suggested fixes
