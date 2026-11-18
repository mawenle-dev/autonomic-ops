"""Platform policy: AutonomicOps is a Linux product and fails closed elsewhere.

There is no "graceful degradation" on non-Linux hosts for the core agent
(sandbox, collectors, daemon). The release studio console is the only
cross-platform component (it is a local web UI). Any core feature invoked on a
non-Linux host raises ``UnsupportedPlatform`` instead of silently running with
reduced guarantees - a silent downgrade is worse than a clear refusal.
"""
from __future__ import annotations

import sys

IS_LINUX = sys.platform.startswith("linux")


class UnsupportedPlatform(RuntimeError):
    """Raised when a Linux-only capability is invoked on another platform."""


def require_linux(feature: str = "this feature") -> None:
    if not IS_LINUX:
        raise UnsupportedPlatform(
            f"{feature} requires Linux; AutonomicOps targets Linux only "
            f"(detected platform: {sys.platform}). The release studio console is "
            f"the only cross-platform component.")
