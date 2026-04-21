"""CutMaster AI: Maximum-control MCP server for DaVinci Resolve."""

__version__ = "0.3.0"

from .config import mcp  # noqa: F401 — re-export for entry points


def main():
    """Console script entry point."""
    mcp.run()


# Tool modules — importing registers @mcp.tool decorators.
# Modules listed here plus their cascading imports cover all tools.
from .tools import project  # noqa: F401, E402
from .tools import media_storage  # noqa: F401, E402
from .tools import media_pool  # noqa: F401, E402
from .tools import timeline_mgmt  # noqa: F401, E402
from .tools import timeline_edit  # noqa: F401, E402
from .tools import timeline_items  # noqa: F401, E402
from .tools import markers  # noqa: F401, E402
from .tools import color  # noqa: F401, E402
from .tools import fusion  # noqa: F401, E402
from .tools import render  # noqa: F401, E402
from .tools import gallery  # noqa: F401, E402
from .tools import fairlight  # noqa: F401, E402
from .tools import layout  # noqa: F401, E402
from .tools import graph  # noqa: F401, E402
from .tools import scripting  # noqa: F401, E402
from .tools import interchange  # noqa: F401, E402
from .workflows import ingest  # noqa: F401, E402
from .workflows import assembly  # noqa: F401, E402
from .workflows import delivery  # noqa: F401, E402
from .workflows import conform  # noqa: F401, E402
from .workflows import grade  # noqa: F401, E402
from .workflows import chroma_key  # noqa: F401, E402
from .intelligence import vision  # noqa: F401, E402
from .intelligence import color_assist  # noqa: F401, E402
from .intelligence import timeline_critique  # noqa: F401, E402
from .cutmaster.media import frame_math  # noqa: F401, E402
from .cutmaster.media import ffmpeg_audio  # noqa: F401, E402
from .cutmaster.media import vfr  # noqa: F401, E402
from .cutmaster.resolve_ops import source_mapper  # noqa: F401, E402
from .cutmaster.resolve_ops import subclips  # noqa: F401, E402
from .cutmaster.resolve_ops import segments as resolve_segments  # noqa: F401, E402
from .cutmaster.core import snapshot  # noqa: F401, E402
from .cutmaster.core import state as cutmaster_state  # noqa: F401, E402
from .cutmaster.core import pipeline  # noqa: F401, E402
from .cutmaster.core import director  # noqa: F401, E402
from .cutmaster.core import execute as cutmaster_execute  # noqa: F401, E402
from .cutmaster import stt  # noqa: F401, E402 — subpackage re-exports base symbols
from .cutmaster.analysis import scrubber  # noqa: F401, E402
from .cutmaster.analysis import marker_agent  # noqa: F401, E402
from .cutmaster.analysis import auto_detect  # noqa: F401, E402
from .cutmaster.analysis import themes as cutmaster_themes  # noqa: F401, E402
from .cutmaster.data import presets as cutmaster_presets  # noqa: F401, E402
from .intelligence import llm as cutmaster_llm  # noqa: F401, E402
from .tools import lut_registry  # noqa: F401, E402
from . import resources  # noqa: F401, E402

# Third-party MCP tool plugins — run last so OSS tools register first.
from .plugins import discover_tools  # noqa: E402

discover_tools(mcp)
