# AppTunnel

Send chosen Linux apps through a WireGuard VPN and leave everything else on your normal connection.
A small GTK window on top of [vopono](https://github.com/jamesmcm/vopono): each VPN app runs in its
own network namespace, so if the tunnel drops that app loses its internet instead of leaking.

Version 1.2 (build 2026-10-16). Author: Paymaan Jafari. License: MIT.

## What you can do in the window

- **Apps tab:** add apps by command or from the list of installed apps, give each its own VPN profile,
  start and stop them one by one or all at once. Each row shows whether the app is in the VPN,
  not running, or running outside the VPN (with a "Quit and restart in VPN" button).
- **Profiles tab:** import a WireGuard `.conf` from a file or straight from `/etc/wireguard`,
  see its server, address and DNS, choose the default, remove it. Keys are never displayed.
- **Settings tab:** kill switch, keep tunnel open, block IPv6, reach this computer, DNS servers,
  firewall (automatic, nftables, iptables), and a "Test the tunnel" button that compares your normal IP
  with the one apps get through the VPN.
- **Menu:** remove stuck tunnels, open the log folder, About.

## Install (Linux Mint, Ubuntu, Debian)

```sh
git clone https://github.com/PaymaanJ/apptunnel.git
cd apptunnel
./install.sh
```

The installer needs sudo. It installs python3-gi, GTK 3, polkit's `pkexec`, wireguard-tools, nftables and
iptables, copies AppTunnel to `/usr/local/lib/apptunnel`, and adds a menu entry called AppTunnel.

vopono must be installed too. If you already built it with cargo, the installer copies it to
`/usr/local/bin`. Otherwise use the ready-made package, which saves the long compile:

```sh
# download vopono_*_amd64.deb from https://github.com/jamesmcm/vopono/releases
sudo apt install ./vopono_*_amd64.deb
```

## First run

1. Open AppTunnel. On the Profiles tab choose **Import file...** (or **Import from /etc/wireguard...**).
2. On the Apps tab choose **Add from installed apps...** and pick Chrome, Signal, Telegram, whatever you want in the VPN.
3. Press **Start all**, or **Start** on one row. Enter your password when asked; it is remembered for a few minutes.
4. Settings, **Test the tunnel**: the two IP addresses should differ.

Apps you do not add (a game, a second browser) are never touched.

A ready-made list for Chrome, Signal and Telegram is in `examples/config.json`:

```sh
mkdir -p ~/.config/apptunnel && cp examples/config.json ~/.config/apptunnel/config.json
```

## One rule to remember

A running program cannot be moved into the VPN. If Signal is already open (also as a tray icon), starting it
through AppTunnel only hands over to that old copy. AppTunnel spots this and shows
**Running OUTSIDE the VPN**; use **Quit and restart in VPN**. For Chrome also turn off
Settings, System, "Continue running background apps when Google Chrome is closed".

## Command line

```sh
apptunnel --version          # apptunnel v1.0 (build 2026-10-16)
apptunnel --list             # every app and its state
apptunnel --launch Chrome    # start one app through the VPN (use it in menu entries and shortcuts)
apptunnel --stop Chrome
```

## How it works

```
GTK window (your user)  --pkexec-->  helper (root)  -->  vopono exec  -->  your app (your user, in the tunnel)
```

- The window never runs as root. Only `/usr/local/lib/apptunnel/helper` does, started through polkit
  with `auth_admin_keep`, so you type your password once per few minutes.
- The helper accepts a fixed set of vopono options, only a profile from your own
  `~/.config/apptunnel/profiles`, only the WireGuard protocol, IP addresses for DNS, and a short list of
  session variables. It refuses `PostUp`/`PostDown` lines in profiles. It always runs the app as the user who asked.
- Whether an app is in the VPN is read from the kernel: each process's network namespace is compared with the
  `vo_*` namespaces vopono creates. Nothing is guessed from window titles.
- Flatpak apps are detected with `flatpak ps` and started with sudo's variables removed, which Flatpak requires.

Files: `~/.config/apptunnel/config.json`, `~/.config/apptunnel/profiles/*.conf` (mode 600),
logs in `~/.cache/apptunnel/`.

## Troubleshooting

- **An app does not start:** a dialog shows the last lines of its log; the folder is in the menu.
- **"Authorization was cancelled":** the password prompt was closed. Press Start again.
- **App starts but has no internet:** the kill switch is doing its job and the tunnel is not working. Try
  Test the tunnel, then another profile, or set Firewall to iptables in Settings.
- **App not recognised as running:** edit the app and fill **Process match** with text that appears in its
  command line, for example `/opt/google/chrome/`.
- **"Failed to create veth pair ... File exists":** a tunnel from an interrupted run was left behind. AppTunnel offers
  **Clean up and try again**; the menu entry **Remove stuck tunnels...** does the same by hand. No reboot needed.

## Uninstall and test

```sh
./uninstall.sh                              # keeps ~/.config/apptunnel
python3 -m unittest discover -s tests -v    # unit tests, no display needed
xvfb-run -a python3 tests/gui_smoke.py /tmp/shots   # opens the window and saves screenshots
```
