#!/usr/bin/env bash
# Turns the Pi's Wi-Fi into an access point (Raspberry Pi OS Bookworm+, NetworkManager).
# Connect your phone to this network, then open http://10.42.0.1:8080
# NOTE: while the Pi is a hotspot, wlan0 is not on your home Wi-Fi, so the Pi needs internet
# via Ethernet (or a USB Wi-Fi dongle for the hotspot) to download footage and post.
set -euo pipefail
SSID="${1:-LarpCron}"
PASS="${2:?usage: hotspot.sh <ssid> <password (8+ chars)>}"
sudo nmcli device wifi hotspot ifname wlan0 ssid "$SSID" password "$PASS"
# Make it come up on boot
sudo nmcli connection modify Hotspot connection.autoconnect yes
echo "Hotspot '$SSID' up. Panel: http://10.42.0.1:8080  (login: admin)"
