---
name: ingest
description: Import media from a folder into the media pool, organize into bins, and optionally set metadata.
---

# /ingest — Media Ingest

Import media files into DaVinci Resolve's media pool with organisation.

## Usage

```
/ingest <source_path> [options]
```

## Examples

- `/ingest ~/Desktop/footage/` — Import all media from folder
- `/ingest /Volumes/Card/DCIM/ --bin Footage/Day1` — Import into specific bin
- `/ingest ~/Downloads/audio/ --type audio` — Import audio files only
- `/ingest /Volumes/Shoot/ --mirror` — Mirror folder structure as bins

## Workflow

### Simple Ingest
1. Parse the user's source path and options
2. Call `cutmaster_ingest_media` with source_path, target_bin, and media_types
3. Report: "Imported X files into [bin name]"

### Structured Ingest (--mirror)
1. Call `cutmaster_ingest_with_bins` with source_path
2. This auto-creates bins matching the folder structure
3. Report: "Imported X files, created Y bins"

### With Metadata
If the user wants to tag clips (e.g. scene, day, camera):
1. Call `cutmaster_ingest_media` with set_metadata dict
2. Example metadata: `{"Scene": "1", "Camera": "A", "Day": "1"}`

## Media Type Filters

- `all` (default) — video + audio + images
- `video` — .mp4, .mov, .mxf, .avi, .r3d, .braw, etc.
- `audio` — .wav, .mp3, .aac, .flac, etc.
- `image` — .jpg, .png, .tif, .dpx, .exr, etc.
