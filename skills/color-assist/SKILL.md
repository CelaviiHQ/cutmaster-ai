---
name: color-assist
description: AI-powered color grading assistant. Analyzes the current frame and suggests or applies CDL corrections. Requires GEMINI_API_KEY.
---

# /color-assist — AI Color Grading

Get AI-powered color correction suggestions for the clip at the playhead.

## Usage

```
/color-assist [intent]
```

## Examples

- `/color-assist` — Auto-suggest neutral corrections
- `/color-assist warm cinematic` — Suggest a warm, filmic look
- `/color-assist match reference.jpg` — Match to a reference image
- `/color-assist cool desaturated` — Cool, muted tones

## Workflow

### Standard Color Assist
1. Call `cutmaster_color_assist` with the user's intent
2. Present the analysis and CDL values
3. Ask the user if they want to apply the correction
4. If yes, call `cutmaster_color_assist` again with `apply=True`
5. Suggest grabbing a still: `cutmaster_grab_still`

### Reference Matching
If the user provides a reference image path:
1. Call `cutmaster_match_to_reference` with the reference path
2. Present the comparison and suggested CDL
3. Offer to apply

### Batch Grading
If the user wants to apply a look to multiple clips:
1. First get the look on one clip using color assist
2. Use `cutmaster_copy_grade_to_all` to propagate
3. Or use `cutmaster_batch_apply_lut` if applying a LUT

## Requirements

- GEMINI_API_KEY must be set in environment or .env file
- DaVinci Resolve must be on the Color or Edit page
- A timeline with clips must be open
