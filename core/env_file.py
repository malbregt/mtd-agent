"""
Beheert /etc/mtd-agent/env — het bestand waar systemd (via EnvironmentFile=)
AGENT_KEY/PLATFORM_WS_URL/etc. uit leest. Los van de git-working-tree in
/opt/mtd-agent, dus overleeft een `git pull` + herstart. Gedeeld door
onboarding/portal.py (eerste keer, tijdens hotspot-onboarding) en
web/server.py (later bijwerken via de lokale statuspagina)."""
import os

ENV_FILE = "/etc/mtd-agent/env"


def write_agent_key(agent_key: str) -> None:
    """Schrijft/vervangt AGENT_KEY in het env-bestand. device_id zelf hoeft
    de agent niet te kennen — het platform herleidt dat server-side uit dit
    token bij elke request/WS-verbinding (zie auth.get_current_device)."""
    os.makedirs(os.path.dirname(ENV_FILE), exist_ok=True)
    lines = []
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            lines = [l for l in f if not l.startswith("AGENT_KEY=")]
    lines.append(f"AGENT_KEY={agent_key}\n")
    with open(ENV_FILE, "w") as f:
        f.writelines(lines)
    os.chmod(ENV_FILE, 0o600)
