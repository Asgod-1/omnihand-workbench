"""O10 CAN FD query codec. No motor-write command can be constructed here."""

import struct

READ_COMMANDS = frozenset((0x02, 0x09, 0x0A, 0x0C, 0x0D, 0x12, 0x13, 0x14, 0x81, 0xCD))
DLC_LENGTHS = (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64)
TACTILE_SLICES = {
    "THUMB": (0x12, 48, 0, 16), "INDEX": (0x12, 48, 16, 32),
    "MIDDLE": (0x12, 48, 32, 48), "RING": (0x13, 32, 0, 16),
    "LITTLE": (0x13, 32, 16, 32), "PALM": (0x14, 50, 0, 25),
    "DORSUM": (0x14, 50, 25, 50),
}


def query_payload(command):
    if type(command) is not int or command not in READ_COMMANDS:
        raise ValueError("Only documented O10 read commands are permitted")
    return bytes((command,))


def response_body(payload, command, length):
    payload = bytes(payload)
    if len(payload) < length + 1 or payload[0] != command:
        raise ValueError(f"Incomplete or mismatched CAN FD reply for 0x{command:02X}")
    # CAN FD DLC rounds the payload up to 12/16/20/24/32/48/64 bytes.
    return payload[1:length + 1]


def positions(payload):
    return list(struct.unpack("<10H", response_body(payload, 0x09, 20)))


def current_raw(payload):
    return list(struct.unpack("<10H", response_body(payload, 0x0A, 20)))


def temperatures(payload):
    return list(struct.unpack("<10b", response_body(payload, 0x0C, 10)))


def error_code(payload):
    return struct.unpack("<H", response_body(payload, 0x0D, 2))[0]


def vendor_info(payload):
    data = response_body(payload, 0xCD, 10)
    if data[0] != 2 or data[9] != 10:
        raise ValueError(f"CAN reply is not an O10 ten-axis hand: type={data[0]}, dof={data[9]}")
    return {"model": "OmniHand 2025", "product_variant": data[1:3].decode("ascii", errors="replace"), "firmware_version": ".".join(map(str, data[3:6])), "hardware_version": ".".join(map(str, data[6:9])), "dof": data[9]}
