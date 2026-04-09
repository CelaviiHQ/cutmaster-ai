---
name: assembly
description: Create a timeline from clips in a bin. Supports sorting, track configuration, and selective clip addition.
---

# /assembly — Timeline Assembly

Create a rough-cut timeline from media pool clips.

## Usage

```
/assembly <timeline_name> [options]
```

## Examples

- `/assembly "Rough Cut v1"` — Create timeline from current bin
- `/assembly "Assembly" --bin Footage/Day1` — From specific bin
- `/assembly "Selects" --clips clip1.mp4 clip2.mp4` — Specific clips
- `/assembly "Edit v1" --bin Footage --tracks 2v 4a` — With track config

## Workflow

### From a Bin
1. Parse timeline name and bin path
2. Call `celavii_quick_assembly` with timeline_name and bin_path
3. Report: "Timeline 'X' created with Y clips"

### From Specific Clips
1. Parse clip names from the user's request
2. Call `celavii_quick_assembly` with timeline_name and clip_names list
3. Report results and any clips not found

### With Track Configuration
1. Call `celavii_assembly_from_bin` with timeline_name, bin_path, video_tracks, audio_tracks
2. This creates the timeline, adds tracks, and names them V1/V2/A1/A2/etc.
3. Report the final track configuration

## Sort Options

- `name` (default) — Alphabetical by clip name
- `none` — Pool order (as they appear in the bin)
