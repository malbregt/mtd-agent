import json
import logging
import os
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

logger = logging.getLogger("status")

DB_PATH = os.environ.get("MTD_DB", "/opt/mtd-agent/cache.db")
CONFIG_PATH = os.environ.get("MTD_CONFIG", "/opt/mtd-agent/config.json")
VERSION = "1.0.0"
_start_time = time.time()


def get_uptime():
    seconds = int(time.time() - _start_time)
    h, m = divmod(seconds // 60, 60)
    return f"{h}u {m}m"


def get_pending_readings():
    try:
        con = sqlite3.connect(DB_PATH)
        count = con.execute("SELECT COUNT(*) FROM readings WHERE synced = 0").fetchone()[0]
        con.close()
        return count
    except Exception:
        return "?"


def get_network_info():
    info = {"ip": "onbekend", "ssid": "onbekend", "interface": "eth0"}
    try:
        result = subprocess.run(["ip", "route", "get", "8.8.8.8"], capture_output=True, text=True)
        for part in result.stdout.split():
            if part == "src":
                info["ip"] = result.stdout.split()[result.stdout.split().index("src") + 1]
            if part == "dev":
                info["interface"] = result.stdout.split()[result.stdout.split().index("dev") + 1]
    except Exception:
        pass
    try:
        result = subprocess.run(["iwgetid", "-r"], capture_output=True, text=True)
        ssid = result.stdout.strip()
        if ssid:
            info["ssid"] = ssid
    except Exception:
        pass
    return info


def factory_reset():
    try:
        os.remove(CONFIG_PATH)
    except Exception:
        pass
    subprocess.Popen(["bash", "-c", "sleep 2 && bash /opt/mtd-agent/scripts/setup-hotspot.sh && systemctl stop mtd-agent"])


def connect_wifi(ssid: str, password: str):
    config = f"""country=NL
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1

network={{
    ssid="{ssid}"
    psk="{password}"
    key_mgmt=WPA-PSK
}}
"""
    with open("/etc/wpa_supplicant/wpa_supplicant.conf", "w") as f:
        f.write(config)
    subprocess.run(["wpa_cli", "-i", "wlan0", "reconfigure"])


def get_wifi_networks():
    try:
        result = subprocess.run(["iwlist", "wlan0", "scan"], capture_output=True, text=True, timeout=10)
        ssids = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("ESSID:"):
                ssid = line.split('"')[1]
                if ssid and ssid not in ssids:
                    ssids.append(ssid)
        return ssids
    except Exception:
        return []


def _format_error_time(iso_time):
    try:
        return datetime.fromisoformat(iso_time).astimezone().strftime("%H:%M:%S")
    except Exception:
        return "?"


def _build_integration_row(iid, integration, agent):
    """Bouw een status-rij voor 1 integratie. Geeft nooit een exception door,
    zodat een kapotte/onverwachte configuratie nooit de hele statuspagina meesleurt."""
    try:
        last = agent._last_poll.get(iid, None)
        return {
            "name": integration.name,
            "type": integration.config.get("type", "?"),
            "poll_interval": integration.poll_interval,
            "last_poll": datetime.fromtimestamp(last).strftime("%H:%M:%S") if last else "nog niet",
            "errors": integration._error_count,
            "recent_errors": list(integration._recent_errors),
        }
    except Exception as e:
        logger.error(f"Fout bij opbouwen statusrij voor {iid}: {e}")
        return {
            "name": getattr(integration, "name", str(iid)),
            "type": "?",
            "poll_interval": "?",
            "last_poll": "?",
            "errors": "?",
            "recent_errors": [],
        }


class StatusHandler(BaseHTTPRequestHandler):
    agent_ref = None

    def log_message(self, format, *args):
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except Exception as e:
            logger.error(f"Onverwachte fout bij afhandelen request: {e}")

    def send_json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            self._do_POST()
        except Exception as e:
            logger.error(f"Onverwachte fout in do_POST ({self.path}): {e}")
            try:
                self.send_json(500, {"error": "Interne fout"})
            except Exception:
                pass

    def _do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if self.path == "/api/wifi":
            ssid = body.get("ssid")
            password = body.get("password", "")
            if not ssid:
                self.send_json(400, {"error": "Geen SSID opgegeven"})
                return
            connect_wifi(ssid, password)
            self.send_json(200, {"status": "ok", "message": f"Verbinden met {ssid}..."})

        elif self.path == "/api/reset":
            factory_reset()
            self.send_json(200, {"status": "ok", "message": "Factory reset gestart, Pi herstart in hotspot modus."})

        else:
            self.send_json(404, {"error": "Niet gevonden"})

    def do_GET(self):
        try:
            self._do_GET()
        except Exception as e:
            logger.error(f"Onverwachte fout in do_GET ({self.path}): {e}")
            try:
                self.send_json(500, {"error": "Interne fout"})
            except Exception:
                pass

    def _do_GET(self):
        agent = StatusHandler.agent_ref
        net = get_network_info()

        if self.path == "/status.json":
            integrations = []
            if agent:
                for iid, integration in list(agent.integrations.items()):
                    integrations.append(_build_integration_row(iid, integration, agent))
            self.send_json(200, {
                "version": VERSION,
                "uptime": get_uptime(),
                "pending_readings": get_pending_readings(),
                "integrations": integrations,
                "network": net,
            })
            return

        if self.path == "/api/networks":
            self.send_json(200, {"networks": get_wifi_networks()})
            return

        # HTML
        integrations = []
        if agent:
            for iid, integration in list(agent.integrations.items()):
                integrations.append(_build_integration_row(iid, integration, agent))

        rows = ""
        for idx, i in enumerate(integrations):
            try:
                has_errors = isinstance(i["errors"], int) and i["errors"] > 0
                kleur = "#f8d7da" if has_errors else "#d4edda"
                klik = f'onclick="toggleErrors({idx})" style="cursor:pointer"' if has_errors else ""
                rows += f"""<tr style="background:{kleur}" {klik}>
                    <td>{i['name']}</td><td>{i['type']}</td>
                    <td>{i['poll_interval']}s</td><td>{i['last_poll']}</td>
                    <td>{'✓' if not has_errors else f"✗ {i['errors']} fout(en) &#9662;"}</td>
                </tr>"""
                if has_errors:
                    error_items = "".join(
                        f"<li><span class='err-time'>{_format_error_time(e.get('time'))}</span> {e.get('message', '?')}</li>"
                        for e in reversed(i["recent_errors"])
                    )
                    rows += f"""<tr id="errors-{idx}" class="error-detail" style="display:none">
                        <td colspan="5"><ul class="error-list">{error_items}</ul></td>
                    </tr>"""
            except Exception as e:
                logger.error(f"Fout bij renderen statusrij {idx}: {e}")
                rows += f"""<tr style="background:#f8d7da"><td colspan="5">Fout bij weergeven van deze integratie</td></tr>"""

        html = f"""<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MTD Agent Status</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,sans-serif;background:#f4f6f9;padding:20px;color:#1a1a1a}}
    h1{{color:#082B56;font-size:1.3rem;margin-bottom:4px}}
    .sub{{color:#666;font-size:0.85rem;margin-bottom:20px}}
    .tabs{{display:flex;gap:8px;margin-bottom:20px}}
    .tab{{padding:8px 16px;border:2px solid #e0e0e0;border-radius:8px;background:none;cursor:pointer;font-size:0.85rem;color:#666}}
    .tab.active{{border-color:#082B56;color:#082B56;font-weight:600}}
    .panel{{display:none}}.panel.active{{display:block}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:20px}}
    .card{{background:#fff;border-radius:10px;padding:16px;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
    .card .label{{font-size:0.75rem;color:#888;margin-bottom:4px}}
    .card .value{{font-size:1.2rem;font-weight:700;color:#082B56}}
    table{{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
    th{{background:#082B56;color:#fff;text-align:left;padding:10px 12px;font-size:0.85rem}}
    td{{padding:10px 12px;font-size:0.9rem;border-bottom:1px solid #f0f0f0}}
    label{{display:block;font-size:0.85rem;font-weight:600;margin-bottom:4px;color:#444;margin-top:12px}}
    input,select{{width:100%;padding:10px;border:1.5px solid #e0e0e0;border-radius:8px;font-size:0.95rem}}
    button.primary{{margin-top:16px;width:100%;padding:11px;background:linear-gradient(135deg,#082B56,#0a9b6e);color:#fff;border:none;border-radius:8px;font-size:0.95rem;font-weight:600;cursor:pointer}}
    button.danger{{margin-top:12px;width:100%;padding:11px;background:#dc3545;color:#fff;border:none;border-radius:8px;font-size:0.95rem;font-weight:600;cursor:pointer}}
    .netrow{{display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid #f0f0f0;font-size:0.9rem}}
    .netrow .k{{color:#888}}.netrow .v{{font-weight:600}}
    .status-msg{{margin-top:12px;padding:10px;border-radius:8px;font-size:0.9rem;display:none}}
    .status-msg.ok{{background:#d4edda;color:#155724;display:block}}
    .status-msg.err{{background:#f8d7da;color:#721c24;display:block}}
    .footer{{margin-top:16px;font-size:0.75rem;color:#aaa}}
    .error-detail td{{background:#fff5f5;padding:0}}
    .error-list{{list-style:none;padding:10px 16px;margin:0;max-height:220px;overflow-y:auto}}
    .error-list li{{font-size:0.8rem;color:#721c24;padding:4px 0;border-bottom:1px solid #f5dcdc}}
    .error-list li:last-child{{border-bottom:none}}
    .err-time{{color:#999;margin-right:8px;font-variant-numeric:tabular-nums}}
  </style>
</head>
<body>
  <h1>MTD Agent</h1>
  <p class="sub">Versie {VERSION} · Uptime {get_uptime()}</p>

  <div class="tabs">
    <button class="tab active" onclick="tab('status')">Status</button>
    <button class="tab" onclick="tab('netwerk')">Netwerk</button>
    <button class="tab" onclick="tab('reset')">Reset</button>
  </div>

  <!-- Status -->
  <div class="panel active" id="panel-status">
    <div class="grid">
      <div class="card"><div class="label">Wachtend op sync</div><div class="value">{get_pending_readings()}</div></div>
      <div class="card"><div class="label">Integraties</div><div class="value">{len(integrations)}</div></div>
      <div class="card"><div class="label">IP adres</div><div class="value" style="font-size:0.95rem">{net['ip']}</div></div>
    </div>
    <table>
      <thead><tr><th>Naam</th><th>Type</th><th>Interval</th><th>Laatste poll</th><th>Status</th></tr></thead>
      <tbody>{rows if rows else '<tr><td colspan="5" style="color:#aaa;text-align:center;padding:20px">Geen integraties actief</td></tr>'}</tbody>
    </table>
  </div>

  <!-- Netwerk -->
  <div class="panel" id="panel-netwerk">
    <div class="card" style="margin-bottom:16px">
      <div class="netrow"><span class="k">IP adres</span><span class="v">{net['ip']}</span></div>
      <div class="netrow"><span class="k">Interface</span><span class="v">{net['interface']}</span></div>
      <div class="netrow" style="border:none"><span class="k">WiFi netwerk</span><span class="v">{net['ssid']}</span></div>
    </div>
    <div class="card">
      <strong style="font-size:0.95rem">WiFi wijzigen</strong>
      <label>Netwerk</label>
      <select id="wifi-ssid"><option value="">-- Laden... --</option></select>
      <label>Wachtwoord</label>
      <input type="password" id="wifi-pass" placeholder="Laat leeg voor open netwerk">
      <button class="primary" onclick="saveWifi()">Verbinden</button>
      <div class="status-msg" id="wifi-status"></div>
    </div>
  </div>

  <!-- Reset -->
  <div class="panel" id="panel-reset">
    <div class="card">
      <strong>Factory Reset</strong>
      <p style="font-size:0.9rem;color:#666;margin-top:8px">Wist alle instellingen en herstart het apparaat in hotspot modus. Daarna moet je het apparaat opnieuw configureren via de MTD-Setup hotspot.</p>
      <button class="danger" onclick="doReset()">Factory Reset uitvoeren</button>
      <div class="status-msg" id="reset-status"></div>
    </div>
  </div>

  <p class="footer">mtd-agent.local:8080</p>

<script>
  function toggleErrors(idx) {{
    const row = document.getElementById('errors-' + idx);
    row.style.display = row.style.display === 'none' ? 'table-row' : 'none';
  }}

  function tab(name) {{
    document.querySelectorAll('.tab').forEach((t,i) => t.classList.toggle('active', ['status','netwerk','reset'][i] === name));
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    document.getElementById('panel-' + name).classList.add('active');
    if (name === 'netwerk') loadNetworks();
  }}

  async function loadNetworks() {{
    const sel = document.getElementById('wifi-ssid');
    sel.innerHTML = '<option>Laden...</option>';
    const resp = await fetch('/api/networks');
    const data = await resp.json();
    sel.innerHTML = '<option value="">-- Kies netwerk --</option>';
    data.networks.forEach(n => sel.innerHTML += `<option value="${{n}}">${{n}}</option>`);
  }}

  async function saveWifi() {{
    const msg = document.getElementById('wifi-status');
    const resp = await fetch('/api/wifi', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{ssid: document.getElementById('wifi-ssid').value, password: document.getElementById('wifi-pass').value}})
    }});
    const data = await resp.json();
    msg.className = 'status-msg ' + (resp.ok ? 'ok' : 'err');
    msg.textContent = data.message || data.error;
  }}

  async function doReset() {{
    if (!confirm('Weet je zeker dat je een factory reset wilt uitvoeren?')) return;
    const msg = document.getElementById('reset-status');
    const resp = await fetch('/api/reset', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: '{{}}'}});
    const data = await resp.json();
    msg.className = 'status-msg ok';
    msg.textContent = data.message;
  }}
</script>
</body>
</html>"""

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())


def start_status_server(agent, port=8080):
    StatusHandler.agent_ref = agent
    server = HTTPServer(("0.0.0.0", port), StatusHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    logger.info(f"Statuspagina beschikbaar op http://mtd-agent.local:8080")
