---
name: grade-log
description: >
  Set up and guide the 6-node log footage color grading workflow.
  Builds the WB / EXP / SAT / CURVES / CST / LUT node structure,
  applies the DECSFILM or any other LUT, and walks through each step.
---

# /grade-log — 6-Node Log Footage Color Grading

A structured, step-by-step color grading workflow for Log footage (Sony S-Log3,
ARRI LogC, BRAW, RED Log3G10, DJI D-Log, Panasonic V-Log, and more).

## Why 6 Nodes?

Nodes process left-to-right. By placing corrections (WB, EXP, SAT, CURVES) **before**
the Color Space Transform, you work in the uncompressed Log space with maximum dynamic
range. The CST then converts to Rec.709, and the LUT adds a subtle film look on top.

```
[WB] → [EXP] → [SAT] → [CURVES] → [CST] → [LUT]
 Log space corrections          Rec.709   Film look
```

## Usage

```
/grade-log
/grade-log camera=sony-slog3
/grade-log camera=arri-logc lut=fuji3513-d60
/grade-log camera=bmpcc6k lut=decsfilm lut_gain=0.15
/grade-log camera=sony-slog3 all        ← apply to every clip on V1
```

## Step-by-Step Instructions

### Step 1 — Project Settings (do once per project)
- Go to **Project Settings → Color Management**
- Set both **Timeline color space** and **Output color space** to `Rec.709-A`
- On Mac this prevents color shift on export
- Save as a preset

### Step 2 — Setup (automated)
Run `celavii_setup_log_grade` with your camera format. This will:
- Create 6 serial nodes labeled: WB · EXP · SAT · CURVES · CST · LUT
- Apply a log-to-Rec.709 CST LUT to node 5 (camera-specific)
- Apply your look LUT to node 6 with key output gain ~0.20
- Switch to the Color page

### Step 3 — CST Node (Node 5)
If a built-in LUT was applied, verify it matches your camera's log profile.
If no LUT was available, manually drag **Color Space Transform** from Effects onto node 5 and set:
- **Input Color Space** → your camera's gamut (e.g. S-Gamut3.Cine)
- **Input Gamma** → your camera's log (e.g. S-Log3)

Your flat log footage will now look like standard Rec.709.

### Step 4 — White Balance (Node 1)
- Open the **Vectorscope** (2x Zoom, Show Skin Tone Indicator)
- Use the **Offset** wheel to center the blob
- Creative tip: leave slightly cool for overcast, warm for sunny

### Step 5 — Exposure (Node 2)
- Open the **Waveform** (Y mode): 0 = pure black, 100 = pure white
- Use **Lift** (shadows), **Gamma** (midtones), **Gain** (highlights)
- Softer than the Log wheels — avoids crushing shadows / clipping highlights
- Use **Offset** to uniformly lift underexposed footage (night shots)

### Step 6 — Saturation (Node 3)
- Bump global **Sat** from 50 → 60–70
- For specific colors (sky, foliage): **Curves → Hue vs Sat** → click color → drag up

### Step 7 — Curves / Contrast (Node 4)
- Draw a subtle **S-curve**: top-right anchor up (highlights), bottom-left anchor down (shadows)
- Optional: **Hue vs Hue** to shift foliage from yellow-green to richer teal-green

### Step 8 — The LUT (Node 6)
- LUT has been applied with key output gain ~0.20 (the "icing on the cake")
- **The secret**: go to the **Key tab** on node 6 → **Key Output Gain** → 0.15–0.25
- Too strong? Lower it. The LUT should be barely visible but add warmth/texture

## Available LUTs

| Key | LUT |
|-----|-----|
| `decsfilm` | DECSFILM.cube (custom, installed) |
| `kodak2383` | Rec.709 Kodak 2383 D65 (classic film) |
| `fuji3513-d55` | Rec.709 Fujifilm 3513DI D55 |
| `fuji3513-d60` | Rec.709 Fujifilm 3513DI D60 |
| `fuji3513-d65` | Rec.709 Fujifilm 3513DI D65 |

## Camera Formats

The `camera` parameter is **not Sony-only** — it works with any camera that shoots log.
Natural names and common model names all work as aliases.

### Cameras with automatic CST (built-in Resolve LUTs applied automatically)

| Key / Alias | Camera |
|-------------|--------|
| `slog3`, `sony fx3`, `sony a7s`, `zv-e1` | Sony FX3/FX6/FX9/A7S III/A1/ZV-E1 (S-Log3) |
| `arri-logc`, `alexa` | ARRI Alexa (LogC) |
| `bmpcc4k`, `pocket 4k` | Blackmagic Pocket 4K |
| `bmpcc6k`, `pocket 6k` | Blackmagic Pocket 6K |
| `braw-4k`, `braw-46k`, `braw-gen5` | Blackmagic 4K / 4.6K / Gen 5 |
| `red`, `red komodo`, `red monstro` | RED cameras (Log3G10) |
| `dji`, `dji phantom4`, `dji x7` | DJI legacy D-Log (Phantom 4, X7) |
| `vlog`, `gh5`, `gh6`, `s5`, `lumix` | Panasonic V-Log |
| `olympus`, `om system`, `omlog` | Olympus / OM System (OM-Log400) |
| `samsung`, `samsung-log` | Samsung Log |

### Cameras requiring manual CST (node 5 created + instructions provided)

These cameras use log formats **not** in Resolve's built-in LUT library. The tool creates node 5 labeled "CST" and returns exact instructions on what to do.

| Alias | Camera | What to do |
|-------|--------|------------|
| `osmo pocket 3`, `dji mini 4`, `mavic 3`, `air 3` | DJI D-Log M | Apply CST OFX: Input → DJI D-Gamut / D-Log M. Or download [DJI D-Log M LUT](https://www.dji.com/downloads/video/D-Log-M-LUT) |
| `insta360`, `x4`, `x3`, `insta360 ace` | Insta360 X4/X3/Ace/GO | Download [Insta360 LUT pack](https://www.insta360.com/download), pass path via `cst_lut_path` |
| `gopro`, `gopro hero`, `protune` | GoPro (Protune Flat) | Apply CST OFX: Input → GoPro Protune Flat |
| `iphone`, `iphone 16 pro`, `apple log` | iPhone 15/16 Pro (ProRes Log) | Apply CST OFX: Input → Apple Log |

### Custom CST LUT override

If you have a manufacturer LUT (e.g. downloaded DJI D-Log M LUT), pass it directly:

```
celavii_setup_log_grade(
  camera="osmo pocket 3",
  cst_lut_path="/path/to/DJI_DLog_M_to_Rec709.cube"
)
```

## Workflow Execution

When the user runs `/grade-log`, perform these steps in order:

1. Ask (or infer from context):
   - What camera/log format? (default: sony-slog3)
   - Apply to current clip or all clips? (default: current clip)
   - Which look LUT? (default: decsfilm)
   - LUT gain? (default: 0.20)

2. Call `celavii_setup_log_grade` with the resolved parameters

3. Report back:
   - Which nodes were created
   - Which CST LUT was applied (or what to do manually)
   - Which look LUT was applied with what gain
   - The next-steps checklist

4. Offer to use `celavii_color_assist` for AI-powered CDL suggestions on the active clip.

## Tips

- **Less is more**: If it looks obviously color-graded, you've gone too far
- **LUT gain 0.10–0.25**: The exact number matters less than "barely visible"
- **Copy grades**: After grading one clip, use `celavii_copy_grade_to_all` or Cmd+C → Cmd+V in Resolve
- **Grab stills**: Use `celavii_grab_still` to snapshot grades for reference
- **AI assist**: Use `celavii_color_assist` after setting up nodes for Gemini CDL suggestions
