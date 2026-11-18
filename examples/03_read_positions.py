"""Capture ten USB position samples, with no motor write."""
import sys
from omnihand_usb.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main(["sample", "--count", "10", *sys.argv[1:]]))
