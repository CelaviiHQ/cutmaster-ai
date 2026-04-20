"""License tier detection — stub.

OSS ships exactly one tier: ``oss``. If *any* plugin has registered into
either entry-point group (see :mod:`celavii_resolve.plugins`), the tier is
reported as ``standard``. This lets third parties — including the Celavii
Studio bundle — indicate their presence without OSS hardcoding any
specific module name.

No entitlement logic lives here in v1. Real feature gating is done
server-side by the closed-source customer API, not by this stub.
"""

from __future__ import annotations

from typing import Literal

from .plugins import any_plugin_registered

Tier = Literal["oss", "standard"]


def current_tier() -> Tier:
    """Return ``"standard"`` if any plugin is registered, else ``"oss"``."""
    return "standard" if any_plugin_registered() else "oss"
