"""
Agent-versie, afgeleid van de git-tag i.p.v. een handmatig bij te werken
constante — voorkomt dat je vergeet 'm te bumpen bij een release. Bewust
losstaand van plugin-versies (zie core/plugin_download.py), die hebben hun
eigen tag-namespace en release-cyclus."""
import logging
import subprocess
from pathlib import Path

log = logging.getLogger("version")

_INSTALL_DIR = Path(__file__).resolve().parent.parent
_cached: str | None = None


def get_agent_version() -> str:
    global _cached
    if _cached is not None:
        return _cached
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            cwd=_INSTALL_DIR, capture_output=True, text=True, timeout=5,
        )
        _cached = result.stdout.strip() or "onbekend"
    except Exception as e:
        log.warning("kon agent-versie niet via git bepalen: %s", e)
        _cached = "onbekend"
    return _cached
