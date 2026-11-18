"""Read USB data through the separately running official SDK web server."""

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request

from omnihand_usb.device import REGIONS, resolve_port


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="auto", help="USB serial port, such as COM5")
    parser.add_argument("--server-port", type=int, default=8766)
    args = parser.parse_args()
    base = f"http://127.0.0.1:{args.server_port}"

    def request(path, body=None, method=None):
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=8) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Official API {exc.code}: {exc.read().decode(errors='replace')}") from exc

    request("/health")
    # Do not reuse/release a session owned by the official web UI or another client.
    sessions = request("/v1/hands")
    if isinstance(sessions, dict):
        sessions = sessions.get("hands")
    if not isinstance(sessions, list):
        raise ValueError("Official server did not return a session list")
    if sessions:
        raise RuntimeError("Official server already has a hand session; release it in that UI first")
    created = request("/v1/hands", {"hand_type": "omnihand_2025", "hand_side": "right", "conn_method": "usb", "conn_config": {"uart_port": resolve_port(args.port), "hand_device_id": 1, "baudrate": 460800}})
    hand_id = created["hand_id"]
    if created.get("already_created"):
        raise RuntimeError("Existing session was returned; it will not be queried or released")
    try:
        from omnihand import Finger

        def getter(method, body=None):
            value = request(f"/v1/hands/{hand_id}/methods?" + urllib.parse.urlencode({"method": method}), {} if body is None else body)
            # The pinned native server returns position arrays directly but tactile
            # data as {"data": [...]}; some documented examples use {"result": ...}.
            if isinstance(value, dict) and "result" in value:
                value = value["result"]
            return value["data"] if isinstance(value, dict) and "data" in value else value

        positions = getter("get_all_joint_positions")
        if not isinstance(positions, list) or len(positions) != 10:
            raise ValueError("No complete ten-axis position reply")
        tactile = {region: getter("get_tactile_sensor_data", {"finger_index": int(getattr(Finger, region))}) for region in REGIONS}
        if any(not isinstance(values, list) or not values for values in tactile.values()):
            raise ValueError("A tactile region did not return data")
        print(json.dumps({"read_only": True, "transport": "USB", "positions": positions, "tactile": tactile}, ensure_ascii=False, indent=2))
    finally:
        request(f"/v1/hands/{hand_id}", method="DELETE")


if __name__ == "__main__":
    main()
