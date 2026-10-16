#!/bin/sh
# AppTunnel installer v1.1 (build 2026-10-16)
# Author: Paymaan Jafari
# Installs AppTunnel system-wide (needs sudo). Re-run it to update.
set -eu

[ "$(id -u)" -eq 0 ] || exec sudo "$0" "$@"
SRC=$(cd "$(dirname "$0")" && pwd)
LIB=/usr/local/lib/apptunnel

echo "== Dependencies"
if command -v apt-get >/dev/null 2>&1; then
    apt-get install -y python3-gi gir1.2-gtk-3.0 wireguard-tools nftables iptables ||
        echo "warning: some packages could not be installed"
    command -v pkexec >/dev/null 2>&1 || apt-get install -y pkexec || apt-get install -y policykit-1 ||
        echo "warning: install pkexec manually"
fi

echo "== Files"
install -d -m 755 "$LIB"
rm -rf "$LIB/apptunnel"
cp -r "$SRC/apptunnel" "$LIB/apptunnel"
find "$LIB/apptunnel" -name __pycache__ -prune -exec rm -rf {} +
chown -R root:root "$LIB"
chmod -R go-w "$LIB"
install -m 755 -o root -g root "$SRC/helper/apptunnel-helper" "$LIB/helper"

cat > /usr/local/bin/apptunnel << 'WRAP'
#!/usr/bin/python3
import sys
sys.path.insert(0, "/usr/local/lib/apptunnel")
from apptunnel.__main__ import main
sys.exit(main())
WRAP
chmod 755 /usr/local/bin/apptunnel

install -D -m 644 "$SRC/data/org.apptunnel.policy" /usr/share/polkit-1/actions/org.apptunnel.policy
install -D -m 644 "$SRC/data/apptunnel.desktop" /usr/share/applications/apptunnel.desktop
install -D -m 644 "$SRC/data/apptunnel.svg" /usr/share/icons/hicolor/scalable/apps/apptunnel.svg
gtk-update-icon-cache -q -f /usr/share/icons/hicolor 2>/dev/null || true
update-desktop-database -q /usr/share/applications 2>/dev/null || true

echo "== vopono"
if [ -x /usr/local/bin/vopono ] || [ -x /usr/bin/vopono ]; then
    echo "found"
else
    USER_HOME=$(getent passwd "${SUDO_USER:-root}" | cut -d: -f6)
    if [ -x "$USER_HOME/.cargo/bin/vopono" ]; then
        install -m 755 -o root -g root "$USER_HOME/.cargo/bin/vopono" /usr/local/bin/vopono
        echo "copied from $USER_HOME/.cargo/bin/vopono"
    else
        echo "vopono is not installed. Get the .deb from https://github.com/jamesmcm/vopono/releases"
        echo "(vopono_*_amd64.deb) and run: sudo apt install ./vopono_*_amd64.deb"
    fi
fi

echo
/usr/local/lib/apptunnel/helper --version
apptunnel --version
echo "Done. Start AppTunnel from the menu, or run: apptunnel"
