#!/bin/bash
# Zet WiFi interface in hotspot modus voor onboarding

SSID="MTD-Setup"
INTERFACE="wlan0"

apt-get install -y -qq hostapd dnsmasq

# hostapd config
cat > /etc/hostapd/hostapd.conf << HOSTAPD
interface=$INTERFACE
driver=nl80211
ssid=$SSID
hw_mode=g
channel=7
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
HOSTAPD

# dnsmasq config — alle DNS naar 192.168.4.1 (captive portal)
cat > /etc/dnsmasq.conf << DNSMASQ
interface=$INTERFACE
dhcp-range=192.168.4.2,192.168.4.20,255.255.255.0,24h
address=/#/192.168.4.1
DNSMASQ

# Statisch IP voor hotspot interface
ip addr add 192.168.4.1/24 dev $INTERFACE
ip link set $INTERFACE up

systemctl unmask hostapd
systemctl enable hostapd dnsmasq
systemctl restart hostapd dnsmasq
systemctl start mtd-portal

echo "Hotspot '$SSID' actief op 192.168.4.1"
