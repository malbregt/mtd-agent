"""
Agent-versie, afgeleid van de git-tag i.p.v. een handmatig bij te werken
constante — voorkomt dat je vergeet 'm te bumpen bij een release. Bewust
losstaand van plugin-versies (zie core/plugin_download.py), die hebben hun
eigen tag-namespace en release-cyclus.

--match beperkt `git describe` bewust tot tags die eruitzien als een
core-versie ('v' + cijfer). Zonder deze restrictie kan `git describe --tags`
een plugin-tag ("plugin-{id}-{versie}") als "dichtstbijzijnde tag" kiezen
zodra die recenter is dan (of op dezelfde commit zit als) de laatste
core-release-tag — de agent zou dan zijn eigen kernversie rapporteren als
bv. "plugin-homewizard_p1-2.0.0", wat de heartbeat-reconciliatie in
app/routers/agent.py breekt (agent_version matcht dan nooit meer met
target_version)."""
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
            ["git", "describe", "--tags", "--always", "--match", "v[0-9]*"],
            cwd=_INSTALL_DIR, capture_output=True, text=True, timeout=5,
        )
        _cached = result.stdout.strip() or "onbekend"
    except Exception as e:
        log.warning("kon agent-versie niet via git bepalen: %s", e)
        _cached = "onbekend"
    return _cached
