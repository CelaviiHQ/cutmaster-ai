---
name: export-stills
description: Export stills from the gallery to image files. Supports batch export with format and path options.
---

# /export-stills — Gallery Still Export

Export stills from the current gallery album to image files.

## Usage

```
/export-stills [output_path] [format]
```

## Examples

- `/export-stills` — Export all stills as DPX to ~/Documents/resolve-exports
- `/export-stills ~/Desktop/stills/ png` — Export as PNG to Desktop
- `/export-stills /Volumes/Exports/ tif` — Export as TIFF

## Supported Formats

- `dpx` (default) — 10-bit DPX
- `tif` / `tiff` — 16-bit TIFF
- `jpg` / `jpeg` — JPEG
- `png` — PNG

## Workflow

1. Call `celavii_list_stills` to show what's in the current album
2. If no stills, inform the user and suggest `celavii_grab_still`
3. Call `celavii_export_stills` with the output path and format
4. Report: "Exported X stills to [path]"

## Selective Export

If the user wants to export specific stills:
1. Show the still list with indices via `celavii_list_stills`
2. Ask which indices to export
3. Call `celavii_export_stills` with the still_indices parameter
