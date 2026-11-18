"""Query the O10 over physical CAN1 of a Damiao dual adapter; no motor write."""
import sys
from omnihand_usb.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main(["probe", "--transport", "canfd", "--can-channel", "1", "--can-id", "0x09", *sys.argv[1:]]))
