"""Tests for AppTunnel core and helper. Run: python3 -m unittest discover -s tests -v

Author: Paymaan Jafari
"""
import importlib.machinery
import importlib.util
import os
import pwd
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from apptunnel import core  # noqa: E402

GOOD = """[Interface]
PrivateKey = AAAA
Address = 10.6.0.21/24
DNS = 10.6.0.1, 8.8.8.8

[Peer]
PublicKey = BBBB
PresharedKey = CCCC
Endpoint = vpn.example.com:51820
AllowedIPs = 0.0.0.0/0, ::0/0
"""


def load_helper():
    loader = importlib.machinery.SourceFileLoader("apptunnel_helper", str(ROOT / "helper" / "apptunnel-helper"))
    spec = importlib.util.spec_from_loader("apptunnel_helper", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = (core.CONFIG_DIR, core.PROFILE_DIR, core.CONFIG_FILE)
        core.CONFIG_DIR = self.tmp / "cfg"
        core.PROFILE_DIR = core.CONFIG_DIR / "profiles"
        core.CONFIG_FILE = core.CONFIG_DIR / "config.json"

    def tearDown(self):
        core.CONFIG_DIR, core.PROFILE_DIR, core.CONFIG_FILE = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)


class ProfileTests(Sandbox):
    def write(self, text, name="a.conf"):
        path = self.tmp / name
        path.write_text(text)
        return path

    def test_import_and_parse(self):
        name = core.import_profile(self.write(GOOD, "My VPN.conf"))
        self.assertEqual(name, "My_VPN")
        self.assertEqual(core.list_profiles(), ["My_VPN"])
        info = core.parse_profile(name)
        self.assertEqual(info["Endpoint"], "vpn.example.com:51820")
        self.assertEqual(info["Address"], "10.6.0.21/24")
        self.assertNotIn("AAAA", str(info))
        self.assertEqual(oct(core.profile_path(name).stat().st_mode & 0o777), "0o600")

    def test_duplicate_is_refused(self):
        core.import_profile(self.write(GOOD))
        with self.assertRaises(ValueError):
            core.import_profile(self.write(GOOD))

    def test_rejects_postup_and_garbage(self):
        self.assertIn("PostUp", core.validate_profile_text(GOOD + "PostUp = rm -rf /\n"))
        self.assertIsNotNone(core.validate_profile_text("hello"))
        self.assertIsNone(core.validate_profile_text(GOOD))

    def test_dns_validation(self):
        self.assertTrue(core.valid_dns(""))
        self.assertTrue(core.valid_dns("10.6.0.1, 1.1.1.1"))
        self.assertFalse(core.valid_dns("not-an-ip"))

    def test_effective_profile(self):
        core.import_profile(self.write(GOOD, "one.conf"))
        core.import_profile(self.write(GOOD, "two.conf"))
        cfg = core.Config()
        cfg.default_profile = "one"
        self.assertEqual(core.effective_profile(cfg, {"profile": ""}), "one")
        self.assertEqual(core.effective_profile(cfg, {"profile": "two"}), "two")
        self.assertEqual(core.effective_profile(cfg, {"profile": "gone"}), "one")


class ConfigTests(Sandbox):
    def test_roundtrip_and_defaults(self):
        cfg = core.Config()
        self.assertTrue(cfg.settings["killswitch"])
        cfg.default_profile = "x"
        cfg.settings["dns"] = "1.1.1.1"
        cfg.apps.append(core.clean_app({"name": "A", "command": "true"}))
        cfg.save()
        again = core.Config()
        self.assertEqual(again.default_profile, "x")
        self.assertEqual(again.settings["dns"], "1.1.1.1")
        self.assertEqual(again.apps[0]["in_start_all"], True)
        self.assertIsNotNone(again.find("a"))

    def test_bad_file_is_ignored(self):
        core.CONFIG_DIR.mkdir(parents=True)
        core.CONFIG_FILE.write_text("{not json")
        self.assertEqual(core.Config().apps, [])


class CommandTests(Sandbox):
    def test_clean_exec_drops_field_codes(self):
        line = "/usr/bin/flatpak run --command=x org.telegram.desktop @@u %U @@"
        self.assertEqual(core.clean_exec(line)[-1], "org.telegram.desktop")
        self.assertEqual(core.clean_exec("firefox %u"), ["firefox"])

    def test_flatpak_wrapping_and_id(self):
        argv = core.resolve_command("flatpak run org.telegram.desktop") if shutil.which("flatpak") else None
        if argv:
            self.assertIn("SUDO_USER", argv)
        self.assertEqual(core.flatpak_id(["/usr/bin/flatpak", "run", "-p", "org.telegram.desktop"]), "org.telegram.desktop")
        self.assertIsNone(core.flatpak_id(["/usr/bin/firefox"]))

    def test_resolve_errors(self):
        for bad in ("", "no-such-program-xyz", "/no/such/file", "echo 'unterminated"):
            with self.assertRaises(ValueError):
                core.resolve_command(bad)
        self.assertEqual(core.resolve_command("echo hi")[1:], ["hi"])

    def test_exec_flags(self):
        cfg = core.Config()
        cfg.settings.update(dns="10.6.0.1, 1.1.1.1", killswitch=False, keep_alive=True,
                            disable_ipv6=True, allow_host_access=True, firewall="nftables")
        flags = core.exec_flags(cfg, "p")
        for expected in ("--no-killswitch", "--keep-alive", "--disable-ipv6", "--allow-host-access", "NfTables"):
            self.assertIn(expected, flags)
        self.assertIn("10.6.0.1,1.1.1.1", flags)
        self.assertEqual(flags[:2], ["--custom", str(core.profile_path("p"))])

    def test_derive_match_uses_install_folder(self):
        folder = self.tmp / "opt" / "Thing"
        folder.mkdir(parents=True)
        exe = folder / "thing"
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
        self.assertEqual(core.derive_match(str(exe)), str(folder) + "/")

    def test_state_detection_with_fake_snapshot(self):
        me = os.getuid()

        class Fake:
            host = "net:[1]"
            vns = {"net:[2]": "vo_test"}
            flatpak = {}
            procs = {
                10: core.Proc(10, 1, me, "/opt/Thing/thing --x", "net:[2]"),
                11: core.Proc(11, 1, me, "/opt/Other/other", "net:[1]"),
                12: core.Proc(12, 1, me, "pkexec /usr/local/lib/apptunnel/helper run /opt/Thing/thing", "net:[1]"),
            }

            def ns_of(self, pid):
                return None

        app = {"name": "T", "command": "x", "match": "/opt/Thing/"}
        self.assertEqual(core.app_state(app, Fake())["state"], "vpn")
        self.assertEqual(core.app_state({**app, "match": "/opt/Other/"}, Fake())["state"], "direct")
        self.assertEqual(core.app_state({**app, "match": "/opt/Nope/"}, Fake(), True)["state"], "starting")
        self.assertEqual(core.app_state({**app, "match": "/opt/Nope/"}, Fake())["state"], "stopped")

    def test_descendants(self):
        procs = {1: core.Proc(1, 0, 0, "", None), 2: core.Proc(2, 1, 0, "", None), 3: core.Proc(3, 2, 0, "", None)}
        self.assertEqual(sorted(core.descendants(1, procs)), [2, 3])


class HelperTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.h = load_helper()
        self.pw = pwd.getpwuid(os.getuid())
        # Pretend the user's home is the sandbox so the profile folder check can be exercised.
        self.pw = pwd.struct_passwd((self.pw.pw_name, "x", self.pw.pw_uid, self.pw.pw_gid, "", str(self.tmp / "home"), "/bin/sh"))
        folder = Path(self.h.profile_dir(self.pw))
        folder.mkdir(parents=True)
        self.good = folder / "p.conf"
        self.good.write_text(GOOD)

    def test_accepts_expected_arguments(self):
        args = ["--custom", str(self.good), "--protocol", "wireguard", "--dns", "10.6.0.1, 1.1.1.1",
                "--no-killswitch", "--firewall", "NfTables", "/usr/bin/echo hi"]
        out = self.h.validate_exec_args(args, self.pw)
        self.assertEqual(out[-1], "/usr/bin/echo hi")
        self.assertIn("10.6.0.1,1.1.1.1", out)

    def test_rejects_dangerous_arguments(self):
        for bad in (
            ["--postup", "/tmp/x", "--custom", str(self.good), "app"],
            ["--user", "root", "--custom", str(self.good), "app"],
            ["--custom", "/etc/passwd", "app"],
            ["--custom", str(self.good), "--protocol", "openvpn", "app"],
            ["--custom", str(self.good), "--dns", "evil;rm", "app"],
            ["--custom", str(self.good), "--firewall", "x", "app"],
            ["--custom", str(self.good), "app", "--no-killswitch"],
            ["--custom", str(self.good)],
            ["app"],
        ):
            with self.assertRaises(SystemExit, msg=str(bad)):
                self.h.validate_exec_args(bad, self.pw)

    def test_profile_with_postup_is_refused(self):
        self.good.write_text(GOOD + "PostUp = id\n")
        with self.assertRaises(SystemExit):
            self.h.validate_exec_args(["--custom", str(self.good), "app"], self.pw)

    def test_symlink_out_of_folder_is_refused(self):
        link = Path(self.h.profile_dir(self.pw)) / "evil.conf"
        link.symlink_to("/etc/hostname")
        with self.assertRaises(SystemExit):
            self.h.validate_exec_args(["--custom", str(link), "app"], self.pw)

    def test_prepare_run_environment(self):
        program, args, env = self.h.prepare_run(
            ["DISPLAY=:0", "EVIL=1", "LD_PRELOAD=/x", "--", "--custom", str(self.good), "app"], self.pw, "/usr/local/bin/vopono")
        self.assertEqual(args[:4], ["/usr/local/bin/vopono", "exec", "--user", self.pw.pw_name])
        self.assertEqual(env["DISPLAY"], ":0")
        self.assertNotIn("EVIL", env)
        self.assertNotIn("LD_PRELOAD", env)
        self.assertEqual(env["SUDO_USER"], self.pw.pw_name)

    def test_gui_command_passes_helper_validation(self):
        """Whatever the GUI builds must be accepted by the helper (every setting switched on)."""
        shutil.copy(self.good, core.PROFILE_DIR if core.PROFILE_DIR.exists() else self._mk(core.PROFILE_DIR))
        core.PROFILE_DIR = Path(self.h.profile_dir(self.pw))
        cfg = core.Config()
        cfg.settings.update(dns="10.6.0.1,1.1.1.1", killswitch=False, keep_alive=True,
                            disable_ipv6=True, allow_host_access=True, firewall="iptables")
        orig = core.helper_path
        core.helper_path = lambda: "/usr/local/lib/apptunnel/helper"
        self.addCleanup(setattr, core, "helper_path", orig)
        real_which = shutil.which
        core.shutil.which = lambda name, *a, **k: "/usr/bin/pkexec" if name == "pkexec" else real_which(name, *a, **k)
        self.addCleanup(setattr, core.shutil, "which", real_which)
        argv = core.build_launch(cfg, "echo hi there", "p")
        self.assertEqual(argv[1:3], ["/usr/local/lib/apptunnel/helper", "run"])
        _prog, args, env = self.h.prepare_run(argv[3:], self.pw, "/usr/local/bin/vopono")
        self.assertEqual(args[-1], "/usr/bin/echo hi there")
        for flag in ("--no-killswitch", "--keep-alive", "--disable-ipv6", "--allow-host-access", "IpTables"):
            self.assertIn(flag, args)

    @staticmethod
    def _mk(path):
        path.mkdir(parents=True)
        return path

    def test_leftover_link_detection(self):
        text = ("1: lo: <LOOPBACK,UP> mtu 65536\n"
                "2: enp3s0: <BROADCAST> mtu 1500\n"
                "12: vo_c_Hh8tnG8_d@vo_c_Hh8tnG8_s: <BROADCAST,MULTICAST> mtu 1500\n"
                "13: vo_c_Hh8tnG8_s@vo_c_Hh8tnG8_d: <BROADCAST,MULTICAST> mtu 1500\n"
                "14: vo_other: <BROADCAST> mtu 1500\n")
        self.assertEqual(self.h.parse_link_names(text), ["vo_c_Hh8tnG8_d", "vo_c_Hh8tnG8_s"])
        self.assertEqual(self.h.parse_netns_names("vo_c_NPxhGXW (id: 0)\nother (id: 1)\n"), ["vo_c_NPxhGXW"])

    def test_namespace_and_name_patterns(self):
        self.assertTrue(self.h.NS_RE.match("vo_c_NPxhGXW"))
        self.assertFalse(self.h.NS_RE.match("../etc"))
        self.assertFalse(self.h.NAME_RE.match("a/b"))


if __name__ == "__main__":
    unittest.main()
