"""Filesystem locations owned by ida-mcp, independent of ida-nexus's own state.

Semantic session traces used to live under ida-nexus's state directory. They
now live under ida-mcp's own directory so the two packages can version and
relocate their state independently. ``ida-mcp dashboard`` and ``ida-mcp
logs`` still look in the old location too, so sessions recorded before the
move remain visible.
"""

import os
from pathlib import Path

STATE_DIR_ENVIRONMENT_VARIABLE = "IDA_MCP_STATE_DIR"


def _idausr_dir() -> Path:
    """Return IDA's main user directory."""
    idausr = os.environ.get("IDAUSR")
    if idausr:
        first = idausr.split(os.pathsep)[0].strip()
        if first:
            return Path(first).expanduser()
    if os.name == "nt":
        return Path(os.environ["APPDATA"]) / "Hex-Rays" / "IDA Pro"
    return Path.home() / ".idapro"


def get_mcp_state_dir() -> Path:
    """Return the directory where ida-mcp stores its own state.

    Overridable with ``IDA_MCP_STATE_DIR``; defaults to ``<IDAUSR>/mcp``.
    """
    state_dir = os.environ.get(STATE_DIR_ENVIRONMENT_VARIABLE)
    if state_dir:
        return Path(state_dir).expanduser()
    return _idausr_dir() / "mcp"


def get_legacy_sessions_dir() -> Path:
    """Return the pre-migration sessions directory under ida-nexus's state dir."""
    from ida_nexus import get_state_dir

    return get_state_dir() / "sessions"


def default_sessions_dirs() -> tuple[Path, ...]:
    """Directories searched for sessions when none is given explicitly.

    Joins ida-mcp's own sessions directory with the legacy location so
    ``ida-mcp dashboard`` and ``ida-mcp logs`` keep showing sessions recorded
    before the sessions folder moved out of ida-nexus's state directory.
    """
    primary = get_mcp_state_dir() / "sessions"
    legacy = get_legacy_sessions_dir()
    if primary.resolve() == legacy.resolve():
        return (primary,)
    return (primary, legacy)
