"""Open the real window under Xvfb, save a screenshot of each tab, then quit.
Run: xvfb-run -a python3 tests/gui_smoke.py OUTDIR
Author: Paymaan Jafari
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
tmp = Path(tempfile.mkdtemp())
os.environ["HOME"] = str(tmp)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import GLib, Gdk, Gtk  # noqa: E402

from apptunnel import core, gui  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/shots")
out.mkdir(parents=True, exist_ok=True)

core.CONFIG_DIR = tmp / "cfg"
core.PROFILE_DIR = core.CONFIG_DIR / "profiles"
core.CONFIG_FILE = core.CONFIG_DIR / "config.json"
core.LOG_DIR = tmp / "log"
sample = tmp / "xorpdx.conf"
sample.write_text(Path(__file__).with_name("test_core.py").read_text().split('GOOD = """')[1].split('"""')[0])
core.import_profile(sample)
cfg = core.Config()
cfg.default_profile = "xorpdx"
cfg.apps = [
    core.clean_app({"name": "Chrome", "command": "echo chrome"}),
    core.clean_app({"name": "Signal", "command": "echo signal"}),
    core.clean_app({"name": "Telegram", "command": "echo flatpak run org.telegram.desktop"}),
]
cfg.save()


def shot(win, name):
    w = win.get_window()
    pix = Gdk.pixbuf_get_from_window(w, 0, 0, w.get_width(), w.get_height())
    pix.savev(str(out / name), "png", [], [])


class App(gui.Application):
    def do_activate(self):
        self.window = gui.MainWindow(self, core.Config())
        self.window.present()
        win = self.window
        # Pretend a few states so the colours can be checked.
        def fake():
            win.states = {"Chrome": {"state": "vpn"}, "Signal": {"state": "direct"}, "Telegram": {"state": "starting"}}
            win.apply_states()
        steps = [(500, fake), (800, lambda: shot(win, "apps.png")),
                 (900, lambda: win.notebook.set_current_page(1)), (1300, lambda: shot(win, "profiles.png")),
                 (1400, lambda: win.notebook.set_current_page(2)), (1800, lambda: shot(win, "settings.png")),
                 (1900, lambda: self.quit())]
        for delay, fn in steps:
            GLib.timeout_add(delay, lambda f=fn: (f(), False)[1])


rc = App(unique=False).run([])
shutil.rmtree(tmp, ignore_errors=True)
print("smoke ok", rc)
