"""Preset + format + STT provider listing endpoints (Configure screen fodder)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from ....cutmaster.analysis import auto_detect as auto_detect_mod
from ....cutmaster.analysis import themes as themes_mod
from ....cutmaster.data.presets import PRESETS, all_presets, get_preset
from ....cutmaster.media.formats import all_formats
from ._helpers import _require_scrubbed
from ._models import AnalyzeThemesRequest, DetectPresetRequest

router = APIRouter()


@router.get("/presets")
async def list_presets() -> dict:
    """List all preset bundles (metadata only — useful for the panel's picker)."""
    return {"presets": [p.model_dump() for p in all_presets()]}


@router.get("/formats")
async def list_formats() -> dict:
    """List all output-format specs (horizontal / vertical_short / square)."""
    return {"formats": [f.model_dump() for f in all_formats()]}


@router.get("/stt-providers")
async def list_stt_providers() -> dict:
    """Report which STT backends are configured + which is the current default.

    Drives the Preset screen's provider picker: if both Gemini and
    Deepgram have keys present, the user sees a selector; otherwise we
    hide the choice and just use whichever is available.
    """
    from ....cutmaster.stt import DEFAULT_PROVIDER, available_providers

    status = available_providers()
    return {
        "default": DEFAULT_PROVIDER,
        "providers": [
            {
                "key": name,
                "label": {
                    "gemini": "Gemini Flash-Lite (≤ 8 min audio validated)",
                    "deepgram": "Deepgram Nova-3 (long-form, diarized)",
                }.get(name, name),
                "configured": configured,
            }
            for name, configured in status.items()
        ],
    }


@router.post("/detect-preset")
async def detect_preset(body: DetectPresetRequest) -> dict:
    """Classify the scrubbed transcript into a preset recommendation."""
    _, scrubbed = _require_scrubbed(body.run_id)
    rec = await asyncio.to_thread(auto_detect_mod.detect_preset, scrubbed)
    return rec.model_dump()


@router.post("/analyze-themes")
async def analyze_themes(body: AnalyzeThemesRequest) -> dict:
    """Produce chapters + hook candidates + theme axes for the Configure screen."""
    _, scrubbed = _require_scrubbed(body.run_id)
    if body.preset not in PRESETS:
        raise HTTPException(status_code=400, detail=f"unknown preset '{body.preset}'")
    preset = get_preset(body.preset)
    analysis = await asyncio.to_thread(themes_mod.analyze_themes, scrubbed, preset)
    return analysis.model_dump()
