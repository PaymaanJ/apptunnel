"""AppTunnel core: settings, WireGuard profiles, launching and process tracking.

No GTK in here, so everything can be tested without a display.
Author: Paymaan Jafari
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
from collections import namedtuple
from pathlib import Path

HOME = Path.home()
CONFIG_DIR = HOME / ".config" / "apptunnel"
PROFILE_DIR = CONFIG_DIR / "profiles"
CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_DIR = HOME / ".cache" / "apptunnel"
INSTALLED_HELPER = "/usr/local/lib/apptunnel/helper"
NETNS_DIR = "/run/netns"
IP_URL = "https://api.ipify.org"

DEFAULT_SETTINGS = {
    "dns": "",                    # blank = use the DNS servers from the profile
    "killswitch": True,           # block the app's traffic if the tunnel drops
    "keep_alive": False,          # keep the tunnel after the last app closes
    "disable_ipv6": False,
    "allow_host_access": False,   # let tunneled apps reach services on this computer
    "firewall": "auto",           # auto | nftables | iptables
}

# Session variables the helper passes on so GUI apps can open a window and find sound/DBus.
PASS_ENV = (
    "DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS", "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE",
    "XDG_DATA_DIRS", "LANG", "LANGUAGE", "PULSE_SERVER",
)

BAD_KEYS = re.compile(r"^\s*(PreUp|PostUp|PreDown|PostDown)\s*=", re.I | re.M)
FIELD_CODE = re.compile(r"^(%[A-Za-z%]|@@u?)$")
APP_ID = re.compile(r"^[A-Za-z_][\w-]*(\.[A-Za-z_][\w-]*){2,}$")
SYSTEM_DIRS = {"/usr/bin", "/bin", "/usr/local/bin", "/usr/sbin", "/sbin", "/snap/bin", ""}

Proc = namedtuple("Proc", "pid ppid uid cmd ns")


# ---------------------------------------------------------------- settings ----

def clean_app(raw: dict) -> dict:
    return {
        "name": str(raw.get("name", "")).strip(),
        "command": str(raw.get("command", "")).strip(),
        "match": str(raw.get("match", "")).strip(),
        "profile": str(raw.get("profile", "")).strip(),
        "in_start_all": bool(raw.get("in_start_all", True)),
    }


class Config:
    """Everything the user chose, stored as JSON in ~/.config/apptunnel/config.json."""

    def __init__(self, path=None):
        self.path = Path(path) if path else CONFIG_FILE
        self.default_profile = ""
        self.settings = dict(DEFAULT_SETTINGS)
        self.apps: list[dict] = []
        self.load()

    def load(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        self.default_profile = str(raw.get("default_profile", ""))
        saved = raw.get("settings")
        if isinstance(saved, dict):
            for key, default in DEFAULT_SETTINGS.items():
                if key in saved and isinstance(saved[key], type(default)):
                    self.settings[key] = saved[key]
        self.apps = [clean_app(a) for a in raw.get("apps", [])
                     if isinstance(a, dict) and a.get("name") and a.get("command")]

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"default_profile": self.default_profile, "settings": self.settings, "apps": self.apps}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def find(self, name: str):
        for app in self.apps:
            if app["name"].lower() == name.strip().lower():
                return app
        return None


# ---------------------------------------------------------------- profiles ----

def safe_name(text: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", text.strip()).strip("._")
    return name[:40] or "profile"


def profile_path(name: str) -> Path:
    return PROFILE_DIR / f"{name}.conf"


def list_profiles() -> list[str]:
    try:
        return sorted(p.stem for p in PROFILE_DIR.glob("*.conf"))
    except OSError:
        return []


def validate_profile_text(text: str):
    """Return an error message, or None if this looks like a usable WireGuard client config."""
    if len(text) > 65536:
        return "That file is too large to be a WireGuard config."
    for needle, what in (("[Interface]", "an [Interface] section"), ("PrivateKey", "a PrivateKey"),
                         ("[Peer]", "a [Peer] section"), ("Endpoint", "an Endpoint")):
        if needle.lower() not in text.lower():
            return f"This does not look like a WireGuard client config: it has no {what}."
    if BAD_KEYS.search(text):
        return "This config has PostUp/PreUp/PostDown/PreDown lines. Remove them first; AppTunnel does not run extra commands as root."
    return None


def parse_profile(name: str) -> dict:
    """Readable details of a profile. Keys are never returned."""
    info = {"Address": "", "DNS": "", "Endpoint": "", "Routes": ""}
    try:
        text = profile_path(name).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return info
    section = ""
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line.startswith("["):
            section = line.strip("[]").lower()
        elif "=" in line:
            key, _, value = (s.strip() for s in line.partition("="))
            key = key.lower()
            if section == "interface" and key == "address":
                info["Address"] = value
            elif section == "interface" and key == "dns":
                info["DNS"] = value
            elif section == "peer" and key == "endpoint":
                info["Endpoint"] = value
            elif section == "peer" and key == "allowedips":
                info["Routes"] = value
    return info


def import_profile(src, name=None) -> str:
    text = Path(src).read_text(encoding="utf-8", errors="replace")
    error = validate_profile_text(text)
    if error:
        raise ValueError(error)
    name = safe_name(name or Path(src).stem)
    dest = profile_path(name)
    if dest.exists():
        raise ValueError(f"A profile named '{name}' already exists. Remove it first, or rename the file you are importing.")
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(PROFILE_DIR, 0o700)
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text if text.endswith("\n") else text + "\n")
    return name


def remove_profile(name: str):
    try:
        profile_path(name).unlink()
    except FileNotFoundError:
        pass


def effective_profile(cfg: Config, app: dict):
    """The profile an app will use: its own choice, else the default, else None."""
    available = list_profiles()
    if app.get("profile") in available:
        return app["profile"]
    if cfg.default_profile in available:
        return cfg.default_profile
    return None


def valid_dns(text: str) -> bool:
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not text.strip():
        return True
    try:
        for part in parts:
            ipaddress.ip_address(part)
    except ValueError:
        return False
    return bool(parts)


# --------------------------------------------------------------- commands ----

def clean_exec(commandline: str) -> list[str]:
    """Turn a .desktop Exec line into a plain command (drops %U, @@u and friends)."""
    return [t for t in shlex.split(commandline) if not FIELD_CODE.match(t)]


def flatpak_id(argv: list[str]):
    if argv and os.path.basename(argv[0]) == "flatpak" and "run" in argv:
        for token in argv[argv.index("run") + 1:]:
            if not token.startswith("-") and APP_ID.match(token):
                return token
    return None


def resolve_command(command: str, wrap: bool = True) -> list[str]:
    """Split the command, find the program (root's PATH is shorter than yours), wrap Flatpak."""
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise ValueError(f"The command has unbalanced quotes ({exc}).")
    if not argv:
        raise ValueError("The command is empty.")
    exe = argv[0]
    if "/" not in exe:
        found = shutil.which(exe)
        if not found:
            raise ValueError(f"'{exe}' was not found. Type the full path to the program.")
        exe = found
    elif not os.access(exe, os.X_OK):
        raise ValueError(f"'{exe}' does not exist or is not executable.")
    argv[0] = exe
    if wrap and os.path.basename(exe) == "flatpak":
        # vopono starts apps through sudo; flatpak refuses to run when it sees sudo's variables.
        env = shutil.which("env") or "/usr/bin/env"
        argv = [env, "-u", "SUDO_USER", "-u", "SUDO_UID", "-u", "SUDO_GID", "-u", "SUDO_COMMAND"] + argv
    return argv


def derive_match(command: str) -> str:
    """Text that appears in the command line of the running app (used to spot it)."""
    try:
        argv = resolve_command(command, wrap=False)
    except ValueError:
        return ""
    if flatpak_id(argv):
        return ""
    real = os.path.realpath(argv[0])
    folder = os.path.dirname(real)
    return os.path.basename(real) if folder in SYSTEM_DIRS else folder.rstrip("/") + "/"


def helper_path():
    if os.path.exists(INSTALLED_HELPER):
        return INSTALLED_HELPER
    dev = Path(__file__).resolve().parent.parent / "helper" / "apptunnel-helper"
    return str(dev) if dev.exists() else None


def exec_flags(cfg: Config, profile: str) -> list[str]:
    s = cfg.settings
    flags = ["--custom", str(profile_path(profile)), "--protocol", "wireguard"]
    if s["dns"].strip():
        flags += ["--dns", ",".join(p.strip() for p in s["dns"].split(",") if p.strip())]
    if not s["killswitch"]:
        flags.append("--no-killswitch")
    if s["keep_alive"]:
        flags.append("--keep-alive")
    if s["disable_ipv6"]:
        flags.append("--disable-ipv6")
    if s["allow_host_access"]:
        flags.append("--allow-host-access")
    if s["firewall"] == "nftables":
        flags += ["--firewall", "NfTables"]
    elif s["firewall"] == "iptables":
        flags += ["--firewall", "IpTables"]
    return flags


def build_launch(cfg: Config, command: str, profile: str) -> list[str]:
    helper, pkexec = helper_path(), shutil.which("pkexec")
    if not pkexec:
        raise ValueError("pkexec is missing. Install it with: sudo apt install pkexec")
    if not helper:
        raise ValueError("The AppTunnel helper is not installed. Run install.sh.")
    argv = resolve_command(command)
    env = [f"{k}={os.environ[k]}" for k in PASS_ENV if k in os.environ]
    return [pkexec, helper, "run", *env, "--", *exec_flags(cfg, profile), shlex.join(argv)]


def log_path(name: str) -> Path:
    return LOG_DIR / f"{safe_name(name)}.log"


def launch(cfg: Config, app: dict) -> subprocess.Popen:
    profile = effective_profile(cfg, app)
    if not profile:
        raise ValueError("Choose a VPN profile first (Profiles tab).")
    argv = build_launch(cfg, app["command"], profile)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(log_path(app["name"]), "wb") as log:
        log.write(f"# {time.strftime('%Y-%m-%d %H:%M:%S')} starting {app['name']} with profile {profile}\n".encode())
        log.flush()
        return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                cwd=str(HOME), start_new_session=True)


def log_tail(name: str, lines: int = 12) -> str:
    try:
        text = log_path(name).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


# ------------------------------------------------------- process tracking ----

def read_procs() -> dict:
    me, out = os.getuid(), {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            uid = os.stat(f"/proc/{entry}").st_uid
            with open(f"/proc/{entry}/stat", encoding="utf-8", errors="replace") as fh:
                stat_line = fh.read()
            ppid = int(stat_line[stat_line.rindex(")") + 2:].split()[1])
            with open(f"/proc/{entry}/cmdline", "rb") as fh:
                cmd = fh.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        except (OSError, ValueError, IndexError):
            continue
        ns = None
        if uid == me:
            try:
                ns = os.readlink(f"/proc/{entry}/ns/net")
            except OSError:
                pass
        out[int(entry)] = Proc(int(entry), ppid, uid, cmd, ns)
    return out


def vpn_namespaces() -> dict:
    """{'net:[inode]': 'vo_name'} for every namespace vopono created."""
    found = {}
    try:
        names = os.listdir(NETNS_DIR)
    except OSError:
        return found
    for name in names:
        if name.startswith("vo_"):
            try:
                found[f"net:[{os.stat(os.path.join(NETNS_DIR, name)).st_ino}]"] = name
            except OSError:
                pass
    return found


def flatpak_instances() -> dict:
    """{app id: [pids]} of running Flatpak apps."""
    exe = shutil.which("flatpak")
    if not exe:
        return {}
    try:
        out = subprocess.run([exe, "ps", "--columns=application,pid,child-pid"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    found = {}
    for line in out.splitlines():
        parts = line.split()
        if parts and APP_ID.match(parts[0]):
            found.setdefault(parts[0], []).extend(int(p) for p in parts[1:] if p.isdigit() and int(p) > 0)
    return found


class Snapshot:
    """One look at the running system: processes, VPN namespaces and Flatpak instances."""

    def __init__(self, with_flatpak: bool = False):
        self.procs = read_procs()
        self.vns = vpn_namespaces()
        self.host = os.readlink("/proc/self/ns/net")
        self.flatpak = flatpak_instances() if with_flatpak else {}

    def ns_of(self, pid: int):
        try:
            return os.readlink(f"/proc/{pid}/ns/net")
        except OSError:
            return None


def _ignored(p: Proc) -> bool:
    first = os.path.basename(p.cmd.split(" ", 1)[0]) if p.cmd else ""
    return p.pid == os.getpid() or first in ("pkexec", "sudo", "apptunnel") or "/apptunnel" in p.cmd


def app_state(app: dict, snap: Snapshot, tracked_alive: bool = False) -> dict:
    """state: vpn (all good) | direct (running OUTSIDE the VPN) | starting | stopped."""
    me = os.getuid()
    vpn, direct = [], []
    try:
        argv = shlex.split(app["command"])
    except ValueError:
        argv = []
    fid = flatpak_id(argv)
    if fid:
        for pid in snap.flatpak.get(fid, []):
            ns = snap.ns_of(pid)
            if ns in snap.vns:
                vpn.append(pid)
            elif ns == snap.host:
                direct.append(pid)
    else:
        match = app.get("match") or derive_match(app["command"])
        if match:
            for p in snap.procs.values():
                if p.uid != me or not p.ns or match not in p.cmd or _ignored(p):
                    continue
                if p.ns in snap.vns:
                    vpn.append(p.pid)
                elif p.ns == snap.host:
                    direct.append(p.pid)
    state = "direct" if direct else "vpn" if vpn else "starting" if tracked_alive else "stopped"
    return {"state": state, "vpn": vpn, "direct": direct, "fid": fid}


def descendants(pid: int, procs: dict) -> list[int]:
    kids = {}
    for p in procs.values():
        kids.setdefault(p.ppid, []).append(p.pid)
    out, stack = [], [pid]
    while stack:
        for child in kids.get(stack.pop(), []):
            out.append(child)
            stack.append(child)
    return out


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as fh:
            data = fh.read()
        return data[data.rindex(")") + 2] != "Z"
    except (OSError, ValueError, IndexError):
        return False


def terminate(pids, grace: float = 4.0):
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.time() + grace
    while time.time() < deadline and any(alive(p) for p in pids):
        time.sleep(0.2)
    for pid in pids:
        if alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def stop_app(app: dict, snap: Snapshot, popen=None) -> int:
    """Close the app wherever it runs (tunnel or not). Returns how many processes were signalled."""
    me = os.getuid()
    running = popen is not None and popen.poll() is None
    info = app_state(app, snap, running)
    pids = set(info["vpn"]) | set(info["direct"]) if not info["fid"] else set()
    if running:
        pids |= {d for d in descendants(popen.pid, snap.procs) if d in snap.procs and snap.procs[d].uid == me}
    if info["fid"] and (info["vpn"] or info["direct"]):
        subprocess.run([shutil.which("flatpak") or "flatpak", "kill", info["fid"]], capture_output=True)
    terminate(sorted(pids))
    return len(pids)


# ------------------------------------------------------ helper operations ----

def helper_call(*args, timeout: int = 120):
    pkexec, helper = shutil.which("pkexec"), helper_path()
    if not pkexec or not helper:
        raise ValueError("pkexec or the AppTunnel helper is missing. Run install.sh.")
    return subprocess.run([pkexec, helper, *args], capture_output=True, text=True, timeout=timeout)


def system_profiles() -> list[str]:
    result = helper_call("list-system")
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or "Authorization was cancelled.")
    return result.stdout.split()


def import_system_profile(name: str):
    result = helper_call("import-system", name)
    if result.returncode != 0:
        raise ValueError(result.stderr.strip().replace("apptunnel-helper: ", "") or "Authorization was cancelled.")


def cleanup_tunnels(names):
    for name in names:
        helper_call("netns-delete", name, timeout=60)


def cleanup_stale() -> str:
    """Remove leftovers of interrupted runs. Refused by the helper while any tunnel is in use."""
    result = helper_call("links-clean", timeout=60)
    if result.returncode != 0:
        raise ValueError(result.stderr.strip().replace("apptunnel-helper: ", "") or "Authorization was cancelled.")
    return result.stdout.strip()


def direct_ip() -> str:
    import urllib.request
    with urllib.request.urlopen(IP_URL, timeout=8) as reply:
        return reply.read().decode().strip()


def tunnel_ip(cfg: Config, profile: str) -> str:
    argv = build_launch(cfg, f"curl -s --max-time 15 {IP_URL}", profile)
    result = subprocess.run(argv, capture_output=True, text=True, timeout=120, cwd=str(HOME))
    for line in reversed(result.stdout.splitlines()):
        try:
            ipaddress.ip_address(line.strip())
            return line.strip()
        except ValueError:
            continue
    if result.returncode in (126, 127):
        raise RuntimeError("Authorization was cancelled.")
    detail = (result.stderr or result.stdout).strip().splitlines()
    raise RuntimeError(detail[-1] if detail else "The tunnel did not return an IP address.")
