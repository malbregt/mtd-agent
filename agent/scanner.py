import logging
import socket
import subprocess
import ipaddress

logger = logging.getLogger("scanner")

# Bekende integraties met hun mDNS naam en herkenningspatroon
KNOWN_DEVICES = [
    {
        "type": "homewizard_p1",
        "mdns": "homewizard.local",
        "port": 80,
        "path": "/api",
        "name": "HomeWizard P1"
    },
    {
        "type": "enphase_envoy",
        "mdns": "envoy.local",
        "port": 80,
        "path": "/info",
        "name": "Enphase Envoy"
    },
]


def resolve_mdns(hostname: str):
    """Probeer mDNS hostname op te lossen naar IP."""
    try:
        ip = socket.gethostbyname(hostname)
        return ip
    except socket.gaierror:
        return None


def check_port(ip: str, port: int, timeout: float = 1.0) -> bool:
    """Check of een poort open is."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def get_local_subnet() -> str:
    """Haal het lokale subnet op (bijv. 192.168.1.0/24)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        # Veronderstel /24 subnet
        parts = ip.rsplit(".", 1)
        return f"{parts[0]}.0/24"
    except Exception:
        return "192.168.1.0/24"


def scan_network() -> list:
    """Scan netwerk op bekende apparaten, retourneert lijst van gevonden devices."""
    results = []
    logger.info("Netwerkscan gestart")

    for device in KNOWN_DEVICES:
        # Probeer eerst via mDNS
        ip = resolve_mdns(device["mdns"])
        if ip:
            logger.info(f"Gevonden via mDNS: {device['name']} op {ip}")
            results.append({
                "type": device["type"],
                "ip": ip,
                "name": device["name"],
                "method": "mdns"
            })
            continue

        # Fallback: scan subnet op poort
        subnet = get_local_subnet()
        logger.info(f"mDNS mislukt voor {device['name']}, scan subnet {subnet}")
        try:
            network = ipaddress.ip_network(subnet, strict=False)
            for host in network.hosts():
                ip_str = str(host)
                if check_port(ip_str, device["port"]):
                    logger.info(f"Gevonden via poortscan: {device['name']} op {ip_str}")
                    results.append({
                        "type": device["type"],
                        "ip": ip_str,
                        "name": device["name"],
                        "method": "portscan"
                    })
                    break
        except Exception as e:
            logger.warning(f"Subnet scan fout: {e}")

    logger.info(f"Netwerkscan klaar: {len(results)} apparaten gevonden")
    return results
