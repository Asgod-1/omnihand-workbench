"""Run without connecting a device."""
from omnihand_usb.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main(["check"]))
