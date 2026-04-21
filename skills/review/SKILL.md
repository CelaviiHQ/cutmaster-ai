---
name: review
description: Review the current timeline — get AI editorial feedback on pacing, structure, and continuity.
---

# /review — Timeline Review

Get editorial feedback on the current timeline.

## Usage

```
/review [focus]
```

## Examples

- `/review` — General editorial feedback
- `/review pacing` — Focus on pacing and rhythm
- `/review structure` — Focus on narrative structure
- `/review continuity` — Check for continuity issues

## Workflow

1. Call `cutmaster_get_current_timeline` to confirm a timeline is active
2. Call `cutmaster_timeline_critique` with the user's focus area
3. Present the AI's editorial feedback
4. If the user wants marker suggestions, call `cutmaster_suggest_markers`
5. If markers are suggested, offer to add them via `cutmaster_add_timeline_marker`
6. If the user wants continuity checking, call `cutmaster_visual_continuity_check`

## Review Aspects

| Focus | What It Checks |
|-------|---------------|
| pacing | Clip durations, rhythm, cuts per minute |
| structure | Scene order, act breaks, flow |
| transitions | Cut types, gaps, hard cuts vs. dissolves |
| audio | Audio track usage, sync, gaps |
| continuity | Jump cuts, repeated clips, naming patterns |
| (empty) | All of the above |

## Requirements

- GEMINI_API_KEY for AI-powered analysis
- A timeline with clips must be open
