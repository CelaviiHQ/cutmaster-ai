---
name: deliver
description: One-command render and export from DaVinci Resolve. Accepts a preset name or shorthand like "h265 4k", "prores proxy", "youtube", "instagram".
---

# /deliver — Quick Render & Export

Render the current timeline with minimal friction.

## Usage

```
/deliver [preset] [output_path]
```

## Examples

- `/deliver` — Render with h264 defaults to ~/Documents/resolve-exports
- `/deliver h265` — Render as H.265/HEVC
- `/deliver prores422hq /Volumes/Exports/` — ProRes 422 HQ to specific path
- `/deliver prores4444 ~/Desktop/final.mov` — ProRes 4444

## Preset Shorthands

| Shorthand | Format | Codec |
|-----------|--------|-------|
| h264 | mp4 | H.264 |
| h265 | mp4 | H.265/HEVC |
| prores422 | mov | Apple ProRes 422 |
| prores422hq | mov | Apple ProRes 422 HQ |
| prores4444 | mov | Apple ProRes 4444 |
| proxylt | mov | Apple ProRes Proxy |
| dnxhd | mxf | DNxHD |
| dnxhr | mxf | DNxHR |
| tiff | tif | 16-bit TIFF |
| dpx | dpx | 10-bit DPX |
| exr | exr | OpenEXR Half |

## Workflow

1. Parse the user's preset and output preferences
2. Call `celavii_quick_deliver` with the appropriate preset, output path, and filename
3. Report the render job status
4. If the user asks to monitor, use `celavii_render_status` to poll progress

## Multi-Format Delivery

If the user requests multiple formats (e.g. "deliver h264 and prores422hq"), use `celavii_batch_deliver` instead.
