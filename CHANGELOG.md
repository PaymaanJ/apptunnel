# Changelog

## v1.2 (build 2026-10-16)
- Fixed: the Add app, Edit app and "Add from installed apps" windows stayed open after Save or Cancel.
  They now always close.

## v1.1 (build 2026-10-16)
- Fixed: a start could fail with "Failed to create veth pair ... File exists" after an interrupted run, until
  the computer was restarted. AppTunnel now offers "Clean up and try again" when it sees that error.
- "Remove stuck tunnels..." in the menu also removes leftover tunnel interfaces, not just namespaces.
- New helper action `links-clean`; it refuses to run while any tunnel still has apps in it.

## v1.0 (build 2026-10-16)
- First release: GTK window to pick a WireGuard profile, choose which apps use the VPN,
  start and stop each app, and set kill switch, DNS, IPv6, host access and firewall.
- Detects apps that are already running outside the VPN and offers "Quit and restart in VPN".
- Root helper with polkit (one password prompt per few minutes), argument whitelist.
- Flatpak apps work (sudo variables are stripped automatically).
- Command line: `--version`, `--list`, `--launch APP`, `--stop APP`.

Author: Paymaan Jafari
