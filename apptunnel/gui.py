"""AppTunnel GTK3 window.

Author: Paymaan Jafari
"""
from __future__ import annotations

import shlex
import sys
import threading
import time

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402

from . import APP_NAME, AUTHOR, BUILD_DATE, VERSION, core  # noqa: E402

CSS = b"""
.status-vpn { color: #26a269; font-weight: bold; }
.status-direct { color: #c64600; font-weight: bold; }
.status-starting { color: #1c71d8; }
.status-stopped { opacity: 0.6; }
.app-name { font-weight: bold; }
.app-cmd { opacity: 0.65; font-size: 90%; }
.hint { opacity: 0.7; }
"""

STATE_TEXT = {
    "vpn": "In the VPN",
    "direct": "Running OUTSIDE the VPN",
    "starting": "Starting...",
    "stopped": "Not running",
}


def esc(text: str) -> str:
    return GLib.markup_escape_text(text)


def box(vertical=False, spacing=8, margin=0):
    b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL, spacing=spacing)
    b.set_border_width(margin)
    return b


def label(text="", css=None, markup=False, wrap=False, xalign=0.0):
    lab = Gtk.Label()
    lab.set_xalign(xalign)
    (lab.set_markup if markup else lab.set_text)(text)
    if wrap:
        lab.set_line_wrap(True)
    if css:
        lab.get_style_context().add_class(css)
    return lab


def icon_button(icon, tooltip):
    btn = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
    btn.set_tooltip_text(tooltip)
    btn.set_relief(Gtk.ReliefStyle.NONE)
    return btn


def message(parent, text, secondary="", kind=Gtk.MessageType.INFO):
    dlg = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=kind,
                            buttons=Gtk.ButtonsType.OK, text=text)
    if secondary:
        dlg.format_secondary_text(secondary)
    dlg.run()
    dlg.destroy()


def confirm(parent, text, secondary="", yes="Continue"):
    dlg = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=Gtk.MessageType.QUESTION,
                            buttons=Gtk.ButtonsType.NONE, text=text)
    dlg.format_secondary_text(secondary)
    dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, yes, Gtk.ResponseType.OK)
    ok = dlg.run() == Gtk.ResponseType.OK
    dlg.destroy()
    return ok


# ------------------------------------------------------------------ dialogs ----

class AppDialog(Gtk.Dialog):
    """Add or edit one app."""

    def __init__(self, parent, cfg, app=None, preset=None):
        super().__init__(title="Edit app" if app else "Add app", transient_for=parent, modal=True)
        self.cfg, self.original = cfg, app
        data = app or preset or {"name": "", "command": "", "match": "", "profile": "", "in_start_all": True}
        self.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save", Gtk.ResponseType.OK)
        self.set_default_response(Gtk.ResponseType.OK)
        grid = Gtk.Grid(column_spacing=10, row_spacing=10, border_width=16)
        self.get_content_area().add(grid)

        self.name = Gtk.Entry(text=data["name"], hexpand=True, activates_default=True)
        self.command = Gtk.Entry(text=data["command"], hexpand=True, activates_default=True)
        self.command.set_placeholder_text("google-chrome-stable   or   flatpak run org.telegram.desktop")
        browse = Gtk.Button(label="Browse...")
        browse.connect("clicked", self.on_browse)
        self.match = Gtk.Entry(text=data.get("match", ""), hexpand=True)
        self.match.set_placeholder_text("Automatic")
        self.profile = Gtk.ComboBoxText()
        self.profile.append_text("Use the default profile")
        for name in core.list_profiles():
            self.profile.append_text(name)
        names = core.list_profiles()
        self.profile.set_active(names.index(data["profile"]) + 1 if data.get("profile") in names else 0)
        self.in_all = Gtk.CheckButton(label="Include when I press \"Start all\"")
        self.in_all.set_active(bool(data.get("in_start_all", True)))

        rows = [("Name", self.name, None), ("Command", self.command, browse),
                ("VPN profile", self.profile, None), ("Process match", self.match, None)]
        for i, (title, widget, extra) in enumerate(rows):
            grid.attach(label(title), 0, i, 1, 1)
            grid.attach(widget, 1, i, 1, 1)
            if extra:
                grid.attach(extra, 2, i, 1, 1)
        grid.attach(label("Used to tell whether the app is already running. Leave empty unless AppTunnel misses it.",
                          css="hint", wrap=True), 1, 4, 2, 1)
        grid.attach(self.in_all, 1, 5, 2, 1)
        self.show_all()

    def on_browse(self, _btn):
        dlg = Gtk.FileChooserDialog(title="Choose a program", transient_for=self, action=Gtk.FileChooserAction.OPEN)
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Choose", Gtk.ResponseType.OK)
        if dlg.run() == Gtk.ResponseType.OK:
            self.command.set_text(dlg.get_filename())
        dlg.destroy()

    def result(self):
        while self.run() == Gtk.ResponseType.OK:
            name, command = self.name.get_text().strip(), self.command.get_text().strip()
            problem = None
            clash = self.cfg.find(name)
            if not name:
                problem = "Give the app a name."
            elif clash is not None and clash is not self.original:
                problem = f"There is already an app called '{name}'."
            else:
                try:
                    core.resolve_command(command, wrap=False)
                except ValueError as exc:
                    problem = str(exc)
            if problem:
                message(self, problem, kind=Gtk.MessageType.WARNING)
                continue
            choice = self.profile.get_active()
            return {"name": name, "command": command, "match": self.match.get_text().strip(),
                    "profile": "" if choice <= 0 else self.profile.get_active_text(),
                    "in_start_all": self.in_all.get_active()}
        return None


class PickAppDialog(Gtk.Dialog):
    """Choose from the apps installed on this computer."""

    def __init__(self, parent):
        super().__init__(title="Add from installed apps", transient_for=parent, modal=True)
        self.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Add", Gtk.ResponseType.OK)
        self.set_default_size(460, 520)
        store = Gtk.ListStore(str, str, Gio.Icon)
        for info in sorted(Gio.AppInfo.get_all(), key=lambda a: a.get_name().lower()):
            if info.should_show() and info.get_commandline():
                store.append([info.get_name(), info.get_commandline(), info.get_icon()])
        self.search = Gtk.SearchEntry(placeholder_text="Search apps")
        self.filter = store.filter_new()
        self.filter.set_visible_func(lambda m, it, _d: self.search.get_text().lower() in m[it][0].lower())
        self.search.connect("search-changed", lambda _e: self.filter.refilter())
        self.tree = Gtk.TreeView(model=self.filter, headers_visible=False)
        icon_cell = Gtk.CellRendererPixbuf()
        icon_cell.set_property("stock-size", Gtk.IconSize.LARGE_TOOLBAR)
        column = Gtk.TreeViewColumn()
        column.pack_start(icon_cell, False)
        column.add_attribute(icon_cell, "gicon", 2)
        text_cell = Gtk.CellRendererText()
        column.pack_start(text_cell, True)
        column.add_attribute(text_cell, "text", 0)
        self.tree.append_column(column)
        self.tree.connect("row-activated", lambda *_a: self.response(Gtk.ResponseType.OK))
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.add(self.tree)
        area = self.get_content_area()
        area.set_spacing(8)
        area.set_border_width(12)
        area.pack_start(self.search, False, False, 0)
        area.pack_start(scroll, True, True, 0)
        self.show_all()

    def result(self):
        if self.run() != Gtk.ResponseType.OK:
            return None
        model, it = self.tree.get_selection().get_selected()
        if it is None:
            return None
        try:
            command = " ".join(shlex.quote(t) for t in core.clean_exec(model[it][1]))
        except ValueError:
            command = model[it][1]
        return {"name": model[it][0], "command": command, "match": "", "profile": "", "in_start_all": True}


# -------------------------------------------------------------- main window ----

class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, application, cfg=None):
        super().__init__(application=application)
        self.cfg = cfg or core.Config()
        self.tracked: dict[str, object] = {}     # app name -> Popen started from this window
        self.started_at: dict[str, float] = {}
        self.queue: list[str] = []               # apps waiting for "Start all"
        self.rows: dict[str, dict] = {}
        self.states: dict[str, dict] = {}
        self.busy = False
        self.loading = False
        self.snapshot_namespaces: dict = {}
        self.set_default_size(780, 580)
        self.set_icon_name("apptunnel")

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(self.get_screen(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        header = Gtk.HeaderBar(show_close_button=True, title=APP_NAME, subtitle=f"v{VERSION} (build {BUILD_DATE})")
        self.set_titlebar(header)
        header.pack_end(self.build_menu())

        root = box(vertical=True, spacing=10, margin=12)
        self.add(root)
        root.pack_start(self.build_topbar(), False, False, 0)
        self.notebook = Gtk.Notebook()
        self.notebook.append_page(self.build_apps_page(), Gtk.Label(label="Apps"))
        self.notebook.append_page(self.build_profiles_page(), Gtk.Label(label="Profiles"))
        self.notebook.append_page(self.build_settings_page(), Gtk.Label(label="Settings"))
        root.pack_start(self.notebook, True, True, 0)
        self.note = label("", css="hint")
        self.note.set_ellipsize(Pango.EllipsizeMode.END)
        root.pack_start(self.note, False, False, 0)

        self.reload_profiles()
        self.rebuild_apps()
        if not core.list_profiles():
            self.notebook.set_current_page(1)
            self.say("Start by importing your WireGuard profile.")
        self.show_all()
        GLib.timeout_add_seconds(2, self.tick)
        self.tick()

    # -- building blocks
    def build_menu(self):
        menu = Gtk.Menu()
        for text, handler in (("Remove stuck tunnels...", self.on_cleanup),
                              ("Open log folder", self.on_open_logs), ("About", self.on_about)):
            item = Gtk.MenuItem(label=text)
            item.connect("activate", handler)
            menu.append(item)
        menu.show_all()
        button = Gtk.MenuButton(popup=menu)
        button.add(Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON))
        return button

    def build_topbar(self):
        bar = box(spacing=8)
        bar.pack_start(label("VPN profile"), False, False, 0)
        self.profile_combo = Gtk.ComboBoxText()
        self.profile_combo.connect("changed", self.on_default_changed)
        bar.pack_start(self.profile_combo, False, False, 0)
        self.summary = label("", css="hint")
        bar.pack_start(self.summary, True, True, 8)
        self.stop_all_btn = Gtk.Button(label="Stop all")
        self.stop_all_btn.connect("clicked", self.on_stop_all)
        self.start_all_btn = Gtk.Button(label="Start all")
        self.start_all_btn.get_style_context().add_class("suggested-action")
        self.start_all_btn.connect("clicked", self.on_start_all)
        bar.pack_end(self.start_all_btn, False, False, 0)
        bar.pack_end(self.stop_all_btn, False, False, 0)
        return bar

    def build_apps_page(self):
        page = box(vertical=True, spacing=8, margin=10)
        self.empty = label("No apps yet. Add the apps you want to send through the VPN.\n"
                           "Everything not listed here keeps using your normal connection.",
                           css="hint", wrap=True, xalign=0.5)
        self.empty.set_no_show_all(True)
        page.pack_start(self.empty, False, False, 12)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        scroll = Gtk.ScrolledWindow(vexpand=True, shadow_type=Gtk.ShadowType.IN)
        scroll.add(self.listbox)
        page.pack_start(scroll, True, True, 0)
        buttons = box()
        add = Gtk.Button(label="Add app...")
        add.connect("clicked", self.on_add)
        pick = Gtk.Button(label="Add from installed apps...")
        pick.connect("clicked", self.on_pick)
        buttons.pack_start(add, False, False, 0)
        buttons.pack_start(pick, False, False, 0)
        page.pack_start(buttons, False, False, 0)
        return page

    def build_profiles_page(self):
        page = box(spacing=12, margin=10)
        self.profile_store = Gtk.ListStore(str, str, str)
        self.profile_tree = Gtk.TreeView(model=self.profile_store)
        for i, title in enumerate(("Profile", "Server", "")):
            column = Gtk.TreeViewColumn(title, Gtk.CellRendererText(), text=i)
            column.set_expand(i == 1)
            column.set_min_width(110 if i == 0 else 90 if i == 2 else 0)
            self.profile_tree.append_column(column)
        self.profile_tree.get_selection().connect("changed", self.on_profile_selected)
        scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True, shadow_type=Gtk.ShadowType.IN)
        scroll.add(self.profile_tree)
        page.pack_start(scroll, True, True, 0)
        side = box(vertical=True, spacing=8)
        self.details = label("", wrap=True)
        self.details.set_max_width_chars(30)
        self.details.set_selectable(True)
        side.pack_start(self.details, False, False, 0)
        for text, handler in (("Import file...", self.on_import_file),
                              ("Import from /etc/wireguard...", self.on_import_system),
                              ("Use as default", self.on_set_default), ("Remove", self.on_remove_profile)):
            btn = Gtk.Button(label=text)
            btn.connect("clicked", handler)
            side.pack_start(btn, False, False, 0)
        keys = label("Keys stay in ~/.config/apptunnel/profiles and are never shown here.", css="hint", wrap=True)
        keys.set_max_width_chars(30)
        side.pack_start(keys, False, False, 8)
        page.pack_start(side, False, False, 0)
        return page

    def build_settings_page(self):
        page = box(vertical=True, spacing=12, margin=16)
        grid = Gtk.Grid(column_spacing=16, row_spacing=14)
        page.pack_start(grid, False, False, 0)
        self.switches = {}
        rows = (
            ("killswitch", "Kill switch", "Block an app's internet if the VPN drops. Recommended."),
            ("keep_alive", "Keep tunnel open", "Leave the tunnel up after the last app closes."),
            ("disable_ipv6", "Block IPv6", "Stop apps from using IPv6 outside the tunnel."),
            ("allow_host_access", "Reach this computer", "Let tunneled apps talk to services on this PC (printers, local servers)."),
        )
        for i, (key, title, hint) in enumerate(rows):
            text = box(vertical=True, spacing=2)
            text.set_hexpand(True)
            text.pack_start(label(title, css="app-name"), False, False, 0)
            text.pack_start(label(hint, css="hint", wrap=True), False, False, 0)
            switch = Gtk.Switch(valign=Gtk.Align.CENTER, halign=Gtk.Align.END)
            switch.set_active(self.cfg.settings[key])
            switch.connect("notify::active", self.on_switch, key)
            self.switches[key] = switch
            grid.attach(text, 0, i, 1, 1)
            grid.attach(switch, 1, i, 1, 1)
        n = len(rows)
        self.dns = Gtk.Entry(text=self.cfg.settings["dns"], width_chars=24)
        self.dns.set_placeholder_text("Use the profile's DNS")
        self.dns.connect("changed", self.on_dns)
        dns_box = box(vertical=True, spacing=2)
        dns_box.pack_start(label("DNS servers", css="app-name"), False, False, 0)
        dns_box.pack_start(label("Optional. Separate several with commas, for example 10.6.0.1, 1.1.1.1.",
                                 css="hint", wrap=True), False, False, 0)
        grid.attach(dns_box, 0, n, 1, 1)
        grid.attach(self.dns, 1, n, 1, 1)
        self.firewall = Gtk.ComboBoxText()
        for text in ("Automatic", "nftables", "iptables"):
            self.firewall.append_text(text)
        self.firewall.set_active(("auto", "nftables", "iptables").index(self.cfg.settings["firewall"]))
        self.firewall.connect("changed", self.on_firewall)
        fw_box = box(vertical=True, spacing=2)
        fw_box.pack_start(label("Firewall", css="app-name"), False, False, 0)
        fw_box.pack_start(label("Change only if tunnels fail to start.", css="hint"), False, False, 0)
        grid.attach(fw_box, 0, n + 1, 1, 1)
        grid.attach(self.firewall, 1, n + 1, 1, 1)

        test = box(spacing=10)
        self.test_btn = Gtk.Button(label="Test the tunnel")
        self.test_btn.connect("clicked", self.on_test)
        self.test_result = label("Compares your normal IP address with the one apps get through the VPN.", css="hint", wrap=True)
        test.pack_start(self.test_btn, False, False, 0)
        test.pack_start(self.test_result, True, True, 0)
        page.pack_start(test, False, False, 8)
        return page

    # -- helpers
    def say(self, text):
        self.note.set_text(text)

    def app_by_name(self, name):
        return self.cfg.find(name)

    def reload_profiles(self):
        names = core.list_profiles()
        if self.cfg.default_profile not in names:
            self.cfg.default_profile = names[0] if names else ""
            self.cfg.save()
        self.loading = True
        self.profile_combo.remove_all()
        for name in names:
            self.profile_combo.append_text(name)
        if names:
            self.profile_combo.set_active(names.index(self.cfg.default_profile))
        self.profile_store.clear()
        for name in names:
            self.profile_store.append([name, core.parse_profile(name)["Endpoint"],
                                       "default" if name == self.cfg.default_profile else ""])
        self.loading = False
        if names:
            self.profile_tree.set_cursor(Gtk.TreePath.new_from_string(str(names.index(self.cfg.default_profile))))
        self.on_profile_selected(self.profile_tree.get_selection())

    # -- apps list
    def rebuild_apps(self):
        for child in self.listbox.get_children():
            self.listbox.remove(child)
        self.rows.clear()
        for app in self.cfg.apps:
            self.listbox.add(self.make_row(app))
        self.empty.set_visible(not self.cfg.apps)
        self.listbox.show_all()
        self.empty.set_visible(not self.cfg.apps)
        self.apply_states()

    def make_row(self, app):
        row = Gtk.ListBoxRow(activatable=False)
        line = box(spacing=10, margin=8)
        text = box(vertical=True, spacing=2)
        text.pack_start(label(app["name"], css="app-name"), False, False, 0)
        cmd = label(app["command"], css="app-cmd")
        cmd.set_ellipsize(Pango.EllipsizeMode.END)
        text.pack_start(cmd, False, False, 0)
        line.pack_start(text, True, True, 0)
        status = label("", xalign=1.0)
        line.pack_start(status, False, False, 8)
        start = Gtk.Button(label="Start")
        start.connect("clicked", lambda _b, n=app["name"]: self.on_start(n))
        stop = Gtk.Button(label="Stop")
        stop.connect("clicked", lambda _b, n=app["name"]: self.on_stop(n))
        edit = icon_button("document-edit-symbolic", "Edit")
        edit.connect("clicked", lambda _b, n=app["name"]: self.on_edit(n))
        remove = icon_button("user-trash-symbolic", "Remove from the list")
        remove.connect("clicked", lambda _b, n=app["name"]: self.on_remove_app(n))
        for widget in (start, stop, edit, remove):
            line.pack_start(widget, False, False, 0)
        row.add(line)
        self.rows[app["name"]] = {"status": status, "start": start, "stop": stop}
        return row

    def apply_states(self):
        inside = 0
        for name, widgets in self.rows.items():
            info = self.states.get(name, {"state": "stopped"})
            state = info["state"]
            inside += state == "vpn"
            ctx = widgets["status"].get_style_context()
            for cls in ("status-vpn", "status-direct", "status-starting", "status-stopped"):
                ctx.remove_class(cls)
            ctx.add_class(f"status-{state}")
            widgets["status"].set_text(STATE_TEXT[state])
            widgets["start"].set_label("Quit and restart in VPN" if state == "direct" else "Start")
            widgets["start"].set_sensitive(state in ("stopped", "direct"))
            widgets["stop"].set_sensitive(state != "stopped")
        profile_ok = bool(core.list_profiles())
        self.start_all_btn.set_sensitive(profile_ok and bool(self.cfg.apps))
        self.stop_all_btn.set_sensitive(any(s["state"] != "stopped" for s in self.states.values()))
        namespaces = len(self.snapshot_namespaces)
        if not profile_ok:
            self.summary.set_text("No profile yet")
        elif inside:
            self.summary.set_text(f"{inside} app{'s' if inside != 1 else ''} in the VPN")
        else:
            self.summary.set_text("Nothing running in the VPN" + (f" ({namespaces} tunnel open)" if namespaces else ""))

    # -- periodic scan (runs off the GTK thread)
    def tick(self):
        if self.busy:
            return True
        self.busy = True
        alive = {n for n, p in self.tracked.items() if p.poll() is None}
        threading.Thread(target=self.scan, args=(list(self.cfg.apps), alive), daemon=True).start()
        return True

    def scan(self, apps, alive):
        try:
            snap = core.Snapshot(with_flatpak=any(core.flatpak_id(shlex.split(a["command"]))
                                                  for a in apps if a["command"]))
            states = {a["name"]: core.app_state(a, snap, a["name"] in alive) for a in apps}
            GLib.idle_add(self.scanned, states, snap.vns)
        except Exception as exc:  # keep the timer alive whatever happens
            GLib.idle_add(self.scan_failed, str(exc))

    def scan_failed(self, text):
        self.busy = False
        self.say(f"Status check failed: {text}")

    def scanned(self, states, namespaces):
        self.busy = False
        self.states, self.snapshot_namespaces = states, namespaces
        for name, process in list(self.tracked.items()):
            code = process.poll()
            if code is None:
                continue
            del self.tracked[name]
            if code != 0 and time.time() - self.started_at.get(name, 0) < 30:
                self.start_failed(name, code)
        if self.queue and not any(s["state"] == "starting" for s in states.values()):
            self.launch_now(self.queue.pop(0))
        self.apply_states()

    def start_failed(self, name, code):
        if code in (126, 127):
            self.say(f"{name}: authorization was cancelled.")
            return
        tail = core.log_tail(name)
        if "File exists" in tail or "veth pair" in tail:
            if confirm(self, f"A leftover tunnel is blocking {name}",
                       "A tunnel from an earlier run was not cleaned up. AppTunnel can remove it and try again.",
                       "Clean up and try again"):
                self.say("Removing the leftover tunnel...")
                threading.Thread(target=self.cleanup_then_start, args=(name,), daemon=True).start()
            return
        message(self, f"{name} could not start through the VPN",
                tail or f"Exit code {code}. See the log folder in the menu.", Gtk.MessageType.ERROR)

    def cleanup_then_start(self, name):
        try:
            core.cleanup_stale()
        except (ValueError, OSError) as exc:
            GLib.idle_add(message, self, "Could not clean up", str(exc), Gtk.MessageType.WARNING)
            return
        GLib.idle_add(self.launch_now, name)

    # -- actions on apps
    def launch_now(self, name):
        app = self.app_by_name(name)
        if app is None:
            return
        try:
            self.tracked[name] = core.launch(self.cfg, app)
        except ValueError as exc:
            self.queue.clear()
            message(self, f"Cannot start {name}", str(exc), Gtk.MessageType.WARNING)
            return
        self.started_at[name] = time.time()
        self.states[name] = {"state": "starting"}
        self.say(f"Starting {name} through the VPN... (enter your password if asked)")
        self.apply_states()
        GLib.timeout_add(800, lambda: self.tick() and False)

    def on_start(self, name):
        if self.states.get(name, {}).get("state") == "direct":
            if not confirm(self, f"{name} is already running outside the VPN",
                           "A running app cannot be moved into the tunnel. Quit it and start it again through the VPN? "
                           "Unsaved work in that app may be lost.", "Quit and restart"):
                return
            self.say(f"Closing {name}...")
            threading.Thread(target=self.stop_then_start, args=(name,), daemon=True).start()
        else:
            self.launch_now(name)

    def stop_then_start(self, name):
        self.stop_worker(name)
        GLib.idle_add(self.launch_now, name)

    def stop_worker(self, name):
        app = self.app_by_name(name)
        if app:
            core.stop_app(app, core.Snapshot(with_flatpak=bool(core.flatpak_id(shlex.split(app["command"])))),
                          self.tracked.get(name))

    def on_stop(self, name):
        self.say(f"Stopping {name}...")
        self.queue = [n for n in self.queue if n != name]

        def work():
            self.stop_worker(name)
            GLib.idle_add(self.after_stop, name)
        threading.Thread(target=work, daemon=True).start()

    def after_stop(self, name):
        self.say(f"{name} stopped.")
        self.tick()

    def on_start_all(self, _btn):
        wanted = [a["name"] for a in self.cfg.apps if a["in_start_all"]
                  and self.states.get(a["name"], {}).get("state") == "stopped"]
        outside = [a["name"] for a in self.cfg.apps if self.states.get(a["name"], {}).get("state") == "direct"]
        if outside:
            message(self, "Some apps are running outside the VPN",
                    "Close them first, or press \"Quit and restart in VPN\" on their rows:\n\n" + "\n".join(outside),
                    Gtk.MessageType.WARNING)
        self.queue = wanted
        self.tick()
        if wanted:
            self.say("Starting " + ", ".join(wanted) + "...")

    def on_stop_all(self, _btn):
        self.queue.clear()
        names = [n for n, s in self.states.items() if s["state"] != "stopped"]
        self.say("Stopping " + ", ".join(names) + "...")

        def work():
            for name in names:
                self.stop_worker(name)
            GLib.idle_add(self.after_stop, "All apps")
        threading.Thread(target=work, daemon=True).start()

    def on_add(self, _btn):
        self.save_app(AppDialog(self, self.cfg).result())

    def on_pick(self, _btn):
        preset = PickAppDialog(self).result()
        if preset:
            self.save_app(AppDialog(self, self.cfg, preset=preset).result())

    def save_app(self, data):
        if data:
            self.cfg.apps.append(data)
            self.cfg.save()
            self.rebuild_apps()

    def on_edit(self, name):
        app = self.app_by_name(name)
        data = AppDialog(self, self.cfg, app=app).result()
        if data:
            app.update(data)
            self.cfg.save()
            self.rebuild_apps()

    def on_remove_app(self, name):
        if confirm(self, f"Remove {name} from the list?", "The app itself is not uninstalled.", "Remove"):
            self.cfg.apps = [a for a in self.cfg.apps if a["name"] != name]
            self.cfg.save()
            self.rebuild_apps()

    # -- profiles
    def selected_profile(self):
        model, it = self.profile_tree.get_selection().get_selected()
        return model[it][0] if it else None

    def on_default_changed(self, combo):
        if self.loading or not combo.get_active_text():
            return
        self.cfg.default_profile = combo.get_active_text()
        self.cfg.save()
        self.reload_profiles()
        self.say(f"Default profile: {self.cfg.default_profile}. Apps already running keep their old tunnel until restarted.")

    def on_profile_selected(self, selection):
        model, it = selection.get_selected()
        if not it:
            self.details.set_text("Select a profile to see its details.")
            return
        info = core.parse_profile(model[it][0])
        lines = [f"<b>{esc(model[it][0])}</b>"] + [f"{k}: {esc(v)}" for k, v in info.items() if v]
        self.details.set_markup("\n".join(lines))

    def on_import_file(self, _btn):
        dlg = Gtk.FileChooserDialog(title="Import a WireGuard profile", transient_for=self, action=Gtk.FileChooserAction.OPEN)
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Import", Gtk.ResponseType.OK)
        flt = Gtk.FileFilter()
        flt.set_name("WireGuard configs (*.conf)")
        flt.add_pattern("*.conf")
        dlg.add_filter(flt)
        path = dlg.get_filename() if dlg.run() == Gtk.ResponseType.OK else None
        dlg.destroy()
        if path:
            try:
                name = core.import_profile(path)
            except (ValueError, OSError) as exc:
                return message(self, "Could not import that file", str(exc), Gtk.MessageType.WARNING)
            self.after_import(name)

    def on_import_system(self, _btn):
        self.say("Waiting for authorization to read /etc/wireguard...")
        threading.Thread(target=self.list_system_worker, daemon=True).start()

    def list_system_worker(self):
        try:
            names = [n for n in core.system_profiles() if n not in core.list_profiles()]
            GLib.idle_add(self.choose_system, names, None)
        except (ValueError, OSError) as exc:
            GLib.idle_add(self.choose_system, [], str(exc))

    def choose_system(self, names, error):
        self.say("")
        if error:
            return message(self, "Cannot read /etc/wireguard", error, Gtk.MessageType.WARNING)
        if not names:
            return message(self, "Nothing new to import", "There are no profiles in /etc/wireguard that are not already imported.")
        dlg = Gtk.Dialog(title="Import from /etc/wireguard", transient_for=self, modal=True)
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Import", Gtk.ResponseType.OK)
        combo = Gtk.ComboBoxText()
        for name in names:
            combo.append_text(name)
        combo.set_active(0)
        area = dlg.get_content_area()
        area.set_border_width(16)
        area.set_spacing(8)
        area.add(label("Choose a profile to copy into AppTunnel:"))
        area.add(combo)
        dlg.show_all()
        chosen = combo.get_active_text() if dlg.run() == Gtk.ResponseType.OK else None
        dlg.destroy()
        if chosen:
            threading.Thread(target=self.import_system_worker, args=(chosen,), daemon=True).start()

    def import_system_worker(self, name):
        try:
            core.import_system_profile(name)
            GLib.idle_add(self.after_import, name)
        except (ValueError, OSError) as exc:
            GLib.idle_add(message, self, "Could not import the profile", str(exc), Gtk.MessageType.WARNING)

    def after_import(self, name):
        if not self.cfg.default_profile:
            self.cfg.default_profile = name
            self.cfg.save()
        self.reload_profiles()
        self.apply_states()
        self.say(f"Imported {name}.")

    def on_set_default(self, _btn):
        name = self.selected_profile()
        if name:
            self.cfg.default_profile = name
            self.cfg.save()
            self.reload_profiles()

    def on_remove_profile(self, _btn):
        name = self.selected_profile()
        if name and confirm(self, f"Remove the profile '{name}'?",
                            "Its file in ~/.config/apptunnel/profiles is deleted. Apps that used it fall back to the default.", "Remove"):
            core.remove_profile(name)
            self.reload_profiles()
            self.apply_states()

    # -- settings
    def on_switch(self, switch, _param, key):
        self.cfg.settings[key] = switch.get_active()
        self.cfg.save()
        self.say("Saved. New settings apply the next time an app starts through the VPN.")

    def on_dns(self, entry):
        text = entry.get_text()
        ok = core.valid_dns(text)
        entry.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, None if ok else "dialog-warning-symbolic")
        entry.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, None if ok else "Enter IP addresses separated by commas")
        if ok:
            self.cfg.settings["dns"] = text.strip()
            self.cfg.save()

    def on_firewall(self, combo):
        self.cfg.settings["firewall"] = ("auto", "nftables", "iptables")[combo.get_active()]
        self.cfg.save()

    def on_test(self, _btn):
        profile = self.cfg.default_profile
        if not profile:
            return message(self, "No profile", "Import a WireGuard profile first.", Gtk.MessageType.WARNING)
        self.test_btn.set_sensitive(False)
        self.test_result.set_text("Testing... (enter your password if asked)")

        def work():
            try:
                normal = core.direct_ip()
            except Exception:
                normal = "unavailable"
            try:
                tunnel = core.tunnel_ip(self.cfg, profile)
                same = tunnel == normal
                text = f"Normal: {normal}    Through the VPN: {tunnel}    " + \
                       ("The addresses match, so the tunnel is not hiding your IP." if same else "Different addresses: the tunnel works.")
            except Exception as exc:
                text = f"Normal: {normal}    VPN test failed: {exc}"
            GLib.idle_add(self.test_done, text)
        threading.Thread(target=work, daemon=True).start()

    def test_done(self, text):
        self.test_btn.set_sensitive(True)
        self.test_result.set_text(text)

    # -- menu
    def on_cleanup(self, _item):
        names = sorted(self.snapshot_namespaces.values())
        text = "This closes every app inside an open tunnel and removes tunnel pieces left over from earlier runs."
        if names:
            text += "\n\nOpen tunnels:\n" + "\n".join(names)
        if not confirm(self, "Remove stuck tunnels?", text + "\n\nUse it only if apps fail to start through the VPN.", "Remove"):
            return

        def work():
            try:
                core.cleanup_tunnels(names)
                core.cleanup_stale()
            except (ValueError, OSError) as exc:
                GLib.idle_add(self.say, str(exc))
            GLib.idle_add(self.after_stop, "Tunnels")
        threading.Thread(target=work, daemon=True).start()

    def on_open_logs(self, _item):
        core.LOG_DIR.mkdir(parents=True, exist_ok=True)
        Gio.AppInfo.launch_default_for_uri(core.LOG_DIR.as_uri(), None)

    def on_about(self, _item):
        dlg = Gtk.AboutDialog(transient_for=self, modal=True)
        dlg.set_program_name(APP_NAME)
        dlg.set_version(f"{VERSION} (build {BUILD_DATE})")
        dlg.set_comments("Send chosen apps through a WireGuard VPN and leave everything else on your normal connection.")
        dlg.set_authors([AUTHOR])
        dlg.set_copyright(f"Copyright 2026 {AUTHOR}")
        dlg.set_license_type(Gtk.License.MIT_X11)
        dlg.set_logo_icon_name("apptunnel")
        dlg.run()
        dlg.destroy()


class Application(Gtk.Application):
    def __init__(self, unique=True):
        flags = Gio.ApplicationFlags.FLAGS_NONE if unique else Gio.ApplicationFlags.NON_UNIQUE
        super().__init__(application_id="org.apptunnel.AppTunnel", flags=flags)
        self.window = None

    def do_activate(self):
        if self.window is None:
            self.window = MainWindow(self)
        self.window.present()


def run() -> int:
    return Application().run(sys.argv[:1])
