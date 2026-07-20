"""
Downloadt een specifieke plugin-versie van GitHub, losgekoppeld van de
agent-kern zelf. Elke plugin heeft een eigen tag-namespace
("plugin-{plugin_id}-{version}") zodat een plugin-release nooit een
agent-core-release triggert (en andersom) — alleen Pi's die deze plugin
daadwerkelijk gebruiken downloaden 'm, via de target_version/target_sha256
die het platform meestuurt in de config-push (zie app/routers/agent.py::
_build_config_payload, join met plugin_releases).
"""
import hashlib
import logging
import os

import aiohttp

import config

log = logging.getLogger("plugin_download")

RAW_BASE = "https://raw.githubusercontent.com"


async def ensure_plugin_version(plugin_id: str, target_version: str, expected_sha256: str | None) -> bool:
    """Download plugins/{plugin_id}/plugin.py voor tag 'plugin-{plugin_id}-
    {target_version}' naar PLUGIN_DIR, mits de sha256 klopt (voorkomt een
    corrupte/gemanipuleerde download — geen aparte signing-infra nodig omdat
    de checksum al in het platform staat, dezelfde vertrouwensketen als de
    rest van de config die van het platform komt).

    Retourneert True bij succes. Bij falen blijft de eerder geïnstalleerde
    (of vendored) versie gewoon draaien — een mislukte download mag nooit een
    werkende plugin stilleggen."""
    tag = f"plugin-{plugin_id}-{target_version}"
    url = f"{RAW_BASE}/{config.PLUGIN_REPO}/{tag}/plugins/{plugin_id}/plugin.py"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                resp.raise_for_status()
                content = await resp.read()
    except Exception as e:
        log.error("kon plugin %s versie %s niet downloaden (%s): %s", plugin_id, target_version, url, e)
        return False

    if expected_sha256:
        actual = hashlib.sha256(content).hexdigest()
        if actual != expected_sha256:
            log.error("checksum-mismatch voor plugin %s versie %s (verwacht %s, gekregen %s) — download genegeerd",
                       plugin_id, target_version, expected_sha256, actual)
            return False

    plugin_dir = os.path.join(config.PLUGIN_DIR, plugin_id)
    os.makedirs(plugin_dir, exist_ok=True)
    with open(os.path.join(plugin_dir, "plugin.py"), "wb") as f:
        f.write(content)
    log.info("plugin %s bijgewerkt naar versie %s (tag %s)", plugin_id, target_version, tag)
    return True
