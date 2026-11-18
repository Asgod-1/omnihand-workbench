"""USB telemetry plus explicit validated position writes; no implicit motion."""

from __future__ import annotations

import gc
import importlib.metadata
import math
import time

REGIONS = ("THUMB", "INDEX", "MIDDLE", "RING", "LITTLE", "PALM", "DORSUM")
REGION_NAMES = dict(zip(REGIONS, ("拇指", "食指", "中指", "无名指", "小指", "手心", "手背")))
JOINT_NAMES = ("拇指旋转", "拇指侧摆", "拇指屈伸", "食指侧摆", "食指屈伸", "中指屈伸", "无名指侧摆", "无名指屈伸", "小指侧摆", "小指屈伸")
ERROR_FIELDS = ("stalled", "overheat", "over_current", "motor_except", "commu_except")


def integer_vector(values, *, expected=None, tactile=False):
    """Reject missing/malformed replies, without clamping real position feedback."""
    result = list(values)
    if not result or (expected is not None and len(result) != expected):
        raise ValueError(f"Expected {expected or 'nonempty'} values, received {len(result)}")
    if any(isinstance(v, bool) or not isinstance(v, int) for v in result):
        raise ValueError("Reply contains non-integer values")
    if tactile and (len(result) > 512 or any(v < 0 or v > 255 for v in result)):
        raise ValueError("Tactile reply is outside uint8 array bounds")
    return result


def list_usb_ports():
    from serial.tools import list_ports

    ports = []
    for p in list_ports.comports():
        bridge = p.vid == 0x34B7 and p.pid in (0x6632, 0x6877, 0x4543)
        ports.append({"port": p.device, "description": p.description, "usb": p.vid is not None, "can_bridge_uart": bridge})
    return sorted(ports, key=lambda p: (not p["usb"], p["port"]))


def resolve_port(port):
    if port and port != "auto":
        if not isinstance(port, str) or len(port) > 128 or "\x00" in port:
            raise ValueError("Invalid serial port")
        if any(p["port"].lower() == port.lower() and p.get("can_bridge_uart") for p in list_usb_ports()):
            raise ValueError(f"{port} 是达妙模块的 USB2UART 串口，请选择达妙 CAN FD 连接方式")
        return port
    candidates = [p["port"] for p in list_usb_ports() if p["usb"] and not p.get("can_bridge_uart")]
    if len(candidates) != 1:
        raise RuntimeError("需要选择 USB 串口：" + (", ".join(candidates) if candidates else "未检测到 USB 串口"))
    return candidates[0]


class USBHand:
    """One exclusive SDK USB connection; position writes are explicit methods."""

    mode = "usb"
    control_capabilities = ('position','hold')

    def __init__(self, port, hand_type="right", device_id=1):
        if hand_type not in ("left", "right") or not 1 <= device_id <= 254:
            raise ValueError("Use left/right hand and a non-broadcast device ID (1..254)")
        self.port = resolve_port(port)
        self.hand_type = hand_type
        self.device_id = device_id
        self._hand = None
        self.info = {}

    def open(self):
        try:
            from omnihand import Finger, HandType, OmniHand2025
        except (ImportError, OSError) as exc:
            raise RuntimeError("当前环境未正确安装官方 SDK，请先运行 scripts/bootstrap.py") from exc
        self._fingers = Finger
        self._hand = OmniHand2025.create_hand_by_usb(
            hand_type=HandType.RIGHT if self.hand_type == "right" else HandType.LEFT,
            hand_device_id=self.device_id,
            uart_port=self.port,
            baudrate=460800,
        )
        try:
            if not self._hand or not self._hand.init():
                raise RuntimeError("SDK USB 初始化状态检查失败")
            v = self._hand.get_vendor_info()
            model = str(getattr(v, "product_model", ""))
            if "".join(model.lower().split()) != "omnihand2025":
                raise RuntimeError(f"未识别到 OmniHand 2025 O10，设备回复型号：{model!r}")
            self.positions()  # Require an actual ten-axis reply before declaring connected.
            voltage = int(getattr(v, "voltage", 0) or 0)
            self.info = {
                "model": model, "port": self.port, "hand": self.hand_type,
                "device_id": self.device_id, "transport": "USB", "dof": 10,
                "hardware_version": str(getattr(v, "hardware_version", "")),
                "firmware_version": str(getattr(v, "software_version", "")),
                "voltage_mv": voltage if voltage > 0 else None,
                "sdk_version": importlib.metadata.version("omnihand"),
            }
            return self.info
        except Exception:
            self.close()
            raise

    def positions(self):
        return integer_vector(self._hand.get_all_joint_positions(), expected=10)

    def set_positions(self, positions):
        from .motion_protocol import target_vector
        actual=integer_vector(self._hand.set_all_joint_positions(target_vector(positions)),expected=10)
        return {'positions':actual,'faults':None}

    def tactile(self, region):
        if region not in REGIONS:
            raise ValueError("Unknown tactile region")
        return integer_vector(self._hand.get_tactile_sensor_data(getattr(self._fingers, region)), tactile=True)

    def diagnostics(self):
        result, errors = {}, []
        for key, method in (("current_ma", "get_all_current_reports"), ("temperature_c", "get_all_temperature_reports")):
            try:
                result[key] = integer_vector(getattr(self._hand, method)(), expected=10)
            except Exception as exc:
                errors.append(f"{key}: {exc}")
        try:
            reports = list(self._hand.get_all_error_reports())
            if len(reports) != 10:
                raise ValueError(f"Expected 10 error reports, received {len(reports)}")
            result["motor_errors"] = [{f: bool(getattr(r, f)) for f in ERROR_FIELDS} for r in reports]
        except Exception as exc:
            errors.append(f"motor_errors: {exc}")
        return result, errors

    def close(self):
        hand, self._hand = self._hand, None
        del hand
        gc.collect()


class DemoHand:
    """Clearly labelled synthetic data for UI development without hardware."""

    mode = "demo"
    port = "DEMO"
    control_capabilities = ('position','power','hold')

    def __init__(self, *args, **kwargs):
        self.info = {"model": "演示设备（非实机）", "port": "DEMO", "hand": "right", "transport": "DEMO", "dof": 10, "hardware_version": "demo", "firmware_version": "demo", "voltage_mv": None, "sdk_version": "not required"}
        self._positions=[2000,1800,3800,2000,3800,3800,2050,3800,2050,3800]
        self._target=list(self._positions)
        self._enabled=False

    def open(self):
        return self.info

    def positions(self):
        return list(self._positions)

    def set_positions(self, positions):
        from .motion_protocol import target_vector
        self._target=target_vector(positions)
        if self._enabled:self._positions=list(self._target)
        return {'positions':list(self._positions),'faults':[0]*10}

    def get_power_state(self):
        return int(self._enabled)

    def set_enabled(self, enabled):
        if type(enabled) is not bool:raise ValueError('enabled must be boolean')
        self._enabled=enabled
        if enabled:self._positions=list(self._target)
        return {'power_state':int(enabled),'enabled':enabled}

    def tactile(self, region):
        count = 25 if region in ("PALM", "DORSUM") else 16
        return [max(0, int(75 * math.sin(time.monotonic() * .5 + i * .4 + REGIONS.index(region)) - 15)) for i in range(count)]

    def diagnostics(self):
        return {"current_ma": [12 + i * 3 for i in range(10)], "temperature_c": [30 + i % 5 for i in range(10)], "motor_errors": [{k: False for k in ERROR_FIELDS} for _ in range(10)]}, []

    def close(self):
        pass
