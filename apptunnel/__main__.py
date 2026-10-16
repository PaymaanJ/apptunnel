"""AppTunnel command line and GUI entry point.

Author: Paymaan Jafari
"""
import argparse
import sys

from . import APP_NAME, BUILD_DATE, VERSION, core


def find_app(cfg, name):
    app = cfg.find(name)
    if app is None:
        known = ", ".join(a["name"] for a in cfg.apps) or "none yet"
        sys.exit(f"No app called '{name}'. Known apps: {known}")
    return app


def main(argv=None):
    parser = argparse.ArgumentParser(prog="apptunnel", description=f"{APP_NAME}: per-app WireGuard VPN for Linux.")
    parser.add_argument("--version", action="store_true", help="show the version and build date")
    parser.add_argument("--list", action="store_true", help="list the apps and whether they run in the VPN")
    parser.add_argument("--launch", metavar="APP", help="start one app through the VPN (for menu entries and shortcuts)")
    parser.add_argument("--stop", metavar="APP", help="stop one app")
    args = parser.parse_args(argv)

    if args.version:
        print(f"apptunnel v{VERSION} (build {BUILD_DATE})")
        return 0
    if args.list or args.stop or args.launch:
        cfg = core.Config()
        if args.list:
            snap = core.Snapshot(with_flatpak=True)
            for app in cfg.apps:
                print(f"{app['name']:<24} {core.app_state(app, snap)['state']}")
            return 0
        if args.stop:
            app = find_app(cfg, args.stop)
            core.stop_app(app, core.Snapshot(with_flatpak=True))
            return 0
        app = find_app(cfg, args.launch)
        try:
            process = core.launch(cfg, app)
        except ValueError as exc:
            sys.exit(str(exc))
        return process.wait()
    from .gui import run
    return run()


if __name__ == "__main__":
    sys.exit(main())
