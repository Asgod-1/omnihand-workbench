"""Read-only probes and a dashboard with explicit, initially locked position control."""

import argparse
import importlib.metadata
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
import webbrowser

from . import SDK_VERSION, __version__
from .device import DemoHand, list_usb_ports, USBHand
from .monitor import Monitor, now_iso, read_sample


def project_root():
    cwd = Path.cwd()
    if (cwd / "pyproject.toml").exists():
        return cwd
    return Path(__file__).resolve().parents[2]


def load_config(root):
    path = root / "config.local.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("transport", "usb") not in ("usb", "canfd") or data.get("http_host", "127.0.0.1") != "127.0.0.1":
        raise ValueError("Local config must use USB/CAN FD transport and a 127.0.0.1 web host")
    return data


def add_usb_options(parser, config):
    parser.add_argument("--transport", choices=("usb", "canfd"), default=config.get("transport", "usb"))
    parser.add_argument("--port", default=config.get("port", "auto"), help="Actual USB COM port or auto when exactly one USB serial device exists")
    parser.add_argument("--hand", choices=("right", "left"), default=config.get("hand", "right"))
    parser.add_argument("--device-id", type=int, default=config.get("device_id", 1))
    parser.add_argument("--can-channel", type=int, choices=(1, 2), default=config.get("can_channel", 1), help="Physical Damiao CAN port 1/2, SDK channel 0/1")
    parser.add_argument("--can-id", type=lambda x: int(x, 0), default=config.get("can_id", 9), help="Known O10 CAN node ID, such as 0x09; separate from USB --device-id")


def make_device(args, root):
    if args.transport == "canfd":
        from .damiao import DamiaoHand
        return DamiaoHand(root, channel=args.can_channel, node_id=args.can_id, hand_type=args.hand)
    return USBHand(args.port, args.hand, args.device_id)


def dashboard_health(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=.5) as response:
            value = json.load(response)
        return value if isinstance(value, dict) and value.get("app") in ("omnihand-workbench", "omnihand-usb-lab") else None
    except (OSError, ValueError):
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description="OmniHand O10 telemetry and explicit position control: USB or Damiao CAN FD")
    root = project_root()
    try:
        config = load_config(root)
    except (OSError, ValueError) as exc:
        parser.error(f"Invalid config.local.json: {exc}")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="Check Python and SDK without opening a device")
    sub.add_parser("ports", help="List serial port metadata without opening devices")
    p = sub.add_parser('doctor', help='Check SDK, native libraries and Linux USB access without opening hardware')
    p.add_argument('--transport', choices=('usb','canfd'), default=config.get('transport','canfd'))
    p = sub.add_parser("probe", help="Read device info and one state snapshot")
    add_usb_options(p, config)
    p = sub.add_parser("sample", help="Collect a bounded read-only session")
    add_usb_options(p, config)
    p.add_argument("--count", type=int, default=10)
    p.add_argument("--interval", type=float, default=.5)
    p.add_argument("--tactile", action="store_true")
    p.add_argument("--output", type=Path)
    p = sub.add_parser("dashboard", help="Start the local USB/CAN FD dashboard")
    add_usb_options(p, config)
    p.add_argument("--http-port", type=int, default=config.get("http_port", 8765))
    p.add_argument("--interval", type=float, default=config.get("position_interval_seconds", .4))
    p.add_argument("--demo", action="store_true", help="Use clearly labelled synthetic data, no SDK or hardware needed")
    p.add_argument("--open", action="store_true", help="Open the browser")
    p = sub.add_parser("stop", help="Stop this project's local dashboard and release its USB connection")
    p.add_argument("--http-port", type=int, default=config.get("http_port", 8765))
    args = parser.parse_args(argv)
    try:
        if args.command == 'doctor':
            from .doctor import diagnose
            report = diagnose(root,args.transport)
            print(json.dumps(report,ensure_ascii=True,indent=2))
            return 0 if report['ok'] else 1
        if args.command == "check":
            import omnihand
            version = importlib.metadata.version("omnihand")
            print(json.dumps({"python": sys.executable, "python_version": sys.version.split()[0], "sdk_version": version, "expected_sdk": SDK_VERSION, "sdk_location": omnihand.__file__, "project_version": __version__, "device_opened": False}, ensure_ascii=True, indent=2))
            return 0 if version == SDK_VERSION else 1
        if args.command == "ports":
            print(json.dumps(list_usb_ports(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "dashboard":
            from .server import make_server
            existing = dashboard_health(args.http_port)
            if existing:
                if existing["mode"] != ("demo" if args.demo else args.transport):
                    raise RuntimeError("This port runs another mode. Stop it or change the connection in its web page.")
                url = f"http://127.0.0.1:{args.http_port}/"
                print(f"Dashboard already running: {url}")
                if args.open:
                    webbrowser.open(url)
                return 0
            monitor = Monitor(root, args.interval, config.get("tactile_interval_seconds", 1.0), config.get("diagnostics_interval_seconds", 3.0))
            server = make_server(monitor, args.http_port)
            try:
                monitor.connect(args.port, demo=args.demo, hand_type=args.hand, device_id=args.device_id, transport=args.transport, can_channel=args.can_channel, can_id=args.can_id)
            except Exception as exc:
                print(f"Initial connection: {exc}. You can select a connection in the web page.", file=sys.stderr)
            url = f"http://127.0.0.1:{server.server_port}/"
            print(f"Dashboard: {url} | control initially locked | press Ctrl+C to stop", flush=True)
            if args.open:
                webbrowser.open(url)
            try:
                server.serve_forever(poll_interval=.2)
            finally:
                server.server_close()
                monitor.disconnect()
            return 0
        if args.command == "stop":
            if not dashboard_health(args.http_port):
                print("No matching dashboard is running.")
                return 0
            request = urllib.request.Request(f"http://127.0.0.1:{args.http_port}/api/shutdown", data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request, timeout=5) as response:
                print(json.load(response)["message"])
            return 0
        device = make_device(args, root)
        try:
            info = device.open()
            if args.command == "probe":
                sample = read_sample(device)
                print(json.dumps({"device": info, "sample": sample}, ensure_ascii=False, indent=2))
                return 0 if sample["positions"] is not None and not sample["errors"] else 1
            if args.count < 1 or args.interval < .1:
                parser.error("Use --count >= 1 and --interval >= 0.1")
            filename = args.transport + "_" + now_iso().replace(":", "").replace("+", "_") + ".jsonl"
            output = args.output or root / "data" / filename
            output.parent.mkdir(parents=True, exist_ok=True)
            valid = 0
            with output.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps({"record_type": "session", "device": info, "started_at": now_iso(), "read_only": True}, ensure_ascii=False) + "\n")
                for i in range(args.count):
                    started = time.monotonic()
                    sample = read_sample(device, tactile=args.tactile, diagnostics=i == 0 or i % 6 == 0)
                    good = sample["positions"] is not None and not sample["errors"]
                    valid += int(good)
                    stream.write(json.dumps({"record_type": "sample", "sequence": i + 1, **sample}, ensure_ascii=False) + "\n")
                    stream.flush()
                    print(f"{i+1}/{args.count}: valid={good} positions={sample['positions']}", flush=True)
                    if i + 1 < args.count:
                        time.sleep(max(0, args.interval - (time.monotonic() - started)))
            print(f"Saved {valid}/{args.count} valid samples: {output}")
            return 0 if valid == args.count else 1
        finally:
            device.close()
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
