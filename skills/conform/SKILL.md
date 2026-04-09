---
name: conform
description: Import a timeline from EDL/XML/AAF/FCPXML, relink to source media, and verify all clips are online.
---

# /conform — Timeline Conform

Import an editorial timeline and relink it to source media.

## Usage

```
/conform <timeline_file> [media_path]
```

## Examples

- `/conform ~/Desktop/edit_v3.edl /Volumes/Media/` — Import EDL and relink
- `/conform project.fcpxml` — Import FCPXML without relinking
- `/conform cut.aaf /Volumes/Raw/` — Import AAF with media path

## Supported Formats

- EDL (.edl) — Edit Decision List
- AAF (.aaf) — Advanced Authoring Format
- FCPXML (.fcpxml) — Final Cut Pro XML
- FCP7 XML (.xml) — Final Cut Pro 7 XML
- OTIO (.otio) — OpenTimelineIO

## Workflow

1. Call `celavii_conform_timeline` with the timeline file path and media path
2. Report import results: timeline name, clip count, online/offline status
3. If offline clips remain, offer to run `celavii_relink_offline_clips` with a different path
4. Run `celavii_verify_timeline_media` for final verification
5. Report: "Conform complete — X/Y clips online"

## If Clips Are Offline

Ask the user for the correct media path, then:
1. Call `celavii_relink_offline_clips` with the new path
2. Re-verify with `celavii_verify_timeline_media`
3. Repeat until all clips are online or user accepts the state
