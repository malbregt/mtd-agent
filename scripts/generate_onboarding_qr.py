"""
Genereert twee QR-afbeeldingen voor op de device-sticker, zodat onboarding via
een telefoon geen lange instance-key hoeft te worden overgetypt:

  1. connect.png — standaard WIFI-QR-formaat, native herkend door iOS/Android
     camera's ("Verbinden met netwerk?"). Laat de telefoon automatisch met de
     hotspot "MTD-Setup" verbinden.
  2. setup.png   — QR met een link naar http://192.168.4.1/?key=<instance_key>.
     Na het scannen van (1) opent scannen van (2) de onboarding-pagina met de
     instance key al ingevuld (zie onboarding/templates/index.html).

Draait op je eigen machine (niet op de Pi zelf) op het moment dat je een
nieuw device/token aanmaakt. Vereist het los te installeren pakket `qrcode`
(met Pillow) — bewust niet in requirements.txt van de agent, want dat draait
op de Pi en heeft dit nooit nodig.

Gebruik:
  pip install qrcode[pil]
  python scripts/generate_onboarding_qr.py --instance-key <key> --output-dir ./qr
"""
import argparse

import qrcode

HOTSPOT_SSID = "MTD-Setup"


def generate(instance_key: str, output_dir: str, ssid: str = HOTSPOT_SSID) -> None:
    import os
    os.makedirs(output_dir, exist_ok=True)

    wifi_payload = f"WIFI:T:nopass;S:{ssid};;"
    qrcode.make(wifi_payload).save(os.path.join(output_dir, "connect.png"))

    setup_url = f"http://192.168.4.1/?key={instance_key}"
    qrcode.make(setup_url).save(os.path.join(output_dir, "setup.png"))

    print(f"Klaar: {output_dir}/connect.png (wifi) en {output_dir}/setup.png (instance key)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-key", required=True, help="Agent-token (instance key) van dit device")
    parser.add_argument("--output-dir", default="./qr", help="Map waar de PNG's komen (default: ./qr)")
    parser.add_argument("--ssid", default=HOTSPOT_SSID, help="Hotspot-SSID (default: MTD-Setup)")
    args = parser.parse_args()
    generate(args.instance_key, args.output_dir, args.ssid)
