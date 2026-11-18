#!/usr/bin/env bash
# Run once on Ubuntu: sudo bash scripts/setup_udev.sh
set -euo pipefail
if [[ "$(uname -s)" != Linux ]]; then
    printf '%s\n' 'This USB permission setup is for Linux only.' >&2
    exit 1
fi
if [[ "$EUID" -ne 0 ]]; then
    printf '%s\n' 'Run: sudo bash scripts/setup_udev.sh' >&2
    exit 1
fi
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_USER="${SUDO_USER:-}"
if [[ -z "$TARGET_USER" || "$TARGET_USER" == root ]]; then
    printf '%s\n' 'Run sudo from the normal account that will develop this project.' >&2
    exit 1
fi
id "$TARGET_USER" >/dev/null
getent group plugdev >/dev/null || groupadd --system plugdev
usermod --append --groups plugdev "$TARGET_USER"
install -m 0644 "$PROJECT_DIR/scripts/70-omnihand-damiao.rules" /etc/udev/rules.d/70-omnihand-damiao.rules
udevadm control --reload-rules
udevadm trigger --subsystem-match=usb --attr-match=idVendor=34b7 --attr-match=idProduct=6632
printf '%s\n' 'USB2CANFD DUAL permission rule installed. Log out/in once for the group change; then reconnect adapter USB.'
printf '%s\n' 'Only the adapter USB permissions were changed. No CAN frame, motor action or firmware write was sent.'
