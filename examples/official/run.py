"""Run a selected unmodified official read-only demo, always in USB mode."""

import argparse
from pathlib import Path
import subprocess
import sys

from omnihand_usb.device import resolve_port

SCRIPTS = {"info": "demo_get_hardware_info.py", "tactile": "demo_tactile_sensor.py", "current": "demo_monitor_current.py", "temperature": "demo_monitor_temperature.py", "errors": "demo_monitor_error.py"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("example", choices=SCRIPTS)
    parser.add_argument("--port", default="auto")
    parser.add_argument("--hand", choices=("left", "right"), default="right")
    parser.add_argument("--device-id", type=int, default=1)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    script = root / "vendor" / "official_examples" / SCRIPTS[args.example]
    if not script.is_file():
        parser.error("Official examples are not cached. Run scripts/bootstrap.py --download-sdk first.")
    return subprocess.call([sys.executable, "-X", "utf8", str(script), "-d", "usb", "-t", args.hand, "--uart-port", resolve_port(args.port), "--hand-id", str(args.device_id)], cwd=root)


if __name__ == "__main__":
    raise SystemExit(main())
