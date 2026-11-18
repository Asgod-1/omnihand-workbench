"""Capture three ordinary USB tactile snapshots, with no raw/CAN operation."""
import sys
from omnihand_usb.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main(["sample", "--count", "3", "--tactile", *sys.argv[1:]]))
