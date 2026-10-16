#!/bin/sh
# AppTunnel uninstaller v1.0 (build 2026-10-16)
# Author: Paymaan Jafari
# Removes the program. Your profiles and app list in ~/.config/apptunnel are kept.
set -eu
[ "$(id -u)" -eq 0 ] || exec sudo "$0" "$@"
rm -rf /usr/local/lib/apptunnel
rm -f /usr/local/bin/apptunnel \
      /usr/share/polkit-1/actions/org.apptunnel.policy \
      /usr/share/applications/apptunnel.desktop \
      /usr/share/icons/hicolor/scalable/apps/apptunnel.svg
echo "AppTunnel removed. vopono was left installed. Your data stays in ~/.config/apptunnel."
