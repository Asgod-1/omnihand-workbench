"""USB-only info and initial state; default is the single detected USB port."""
import sys
from omnihand_usb.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main(["probe", *sys.argv[1:]]))
