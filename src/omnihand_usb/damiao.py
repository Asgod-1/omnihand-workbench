"""O10 telemetry and explicit position/power control over the Damiao C API.

This is a project adapter, independent of the Agibot SDK's ZLG/HCAN backends.
"""

from __future__ import annotations

import ctypes
import importlib
from pathlib import Path
import queue
import sys
import threading
import time

from . import canfd_protocol as protocol
from .damiao_runtime import checked_runtime, load_linux_library, check_abi


class _NativeDamiaoHand:
    mode = "canfd"
    control_capabilities = ('position','power','hold','joint','pt','pvt','mixed_joint')

    def __init__(self, root, *, channel=1, node_id=9, hand_type="right", timeout=.6):
        if type(channel) is not int or channel not in (1, 2):
            raise ValueError("物理 CAN 通道只能选 1 或 2")
        if type(node_id) is not int or not 1 <= node_id < 0x7FF:
            raise ValueError("使用已知的非广播 CAN 节点 ID，范围 1..2046")
        if hand_type not in ("right", "left"):
            raise ValueError("Hand side must be right or left")
        self.root = Path(root)
        self.channel, self.node_id, self.hand_type = channel - 1, node_id, hand_type
        self.timeout = timeout
        self.port = f"达妙 CAN{channel} / 0x{node_id:03X}"
        self.info = {}
        self._context = self._device = None
        self._dll_directory = self._usb = None
        self._native_library = None
        self._rx = queue.Queue(maxsize=2048)
        self._query_lock = threading.Lock()
        self._touch_cache = {}
        self._enabled = False
        self._latest_error = None
        self._raw_reader = None
        self._raw_result = None
        self._raw_retry_at = 0.0
        self._raw_has_succeeded = False
        self._raw_failures = 0
        self._owner_thread = None

    def open(self):
        self._owner_thread = threading.get_ident()
        folder = self.root / "vendor/damiao_device_sdk"
        runtime = checked_runtime(folder)
        try:
            if runtime.key == 'windows-x64':
                self._dll_directory = __import__('os').add_dll_directory(str(runtime.folder))
                self._usb = ctypes.CDLL(str(runtime.folder / 'libusb-1.0.dll'))
            else:
                self._native_library = load_linux_library(runtime)
            context_module = importlib.import_module("dmcan.dmcan_context")
            context_module.find_backend_dll_path = lambda: str(runtime.library)
            check_abi(importlib.import_module('dmcan.dmcan_def'))
            from dmcan import DmCanContext, dmcan_channel_can_info, dmcan_device_type

            self._context = DmCanContext()
            count = self._context.find_devices(dmcan_device_type.USB2CANFD_DUAL)
            if count != 1:
                help_text = '；Ubuntu请先运行doctor检查USB权限，首次安装执行 sudo bash scripts/setup_udev.sh 并重新登录' if runtime.key.startswith('linux') else ''
                raise RuntimeError(f"需要恰好一个达妙双路模块，当前发现 {count} 个" + help_text)
            self._device = d = self._context.get_device(0)
            if not d.open():
                raise RuntimeError("无法打开达妙模块，请关闭其他正在使用它的程序")
            adapter_firmware = d.get_version()
            for name in ("dmcan_device_enable_channel", "dmcan_device_disable_channel"):
                getattr(d.dll, name).restype = ctypes.c_bool
            d.enable_channel(self.channel, False)
            info = dmcan_channel_can_info()
            info.channel, info.canfd = self.channel, True
            info.can_baudrate, info.canfd_baudrate = 1000000, 5000000
            info.can_sp, info.canfd_sp = .8, .75
            if not d.set_channel_baudrate(self.channel, info):
                raise RuntimeError("无法配置达妙 CAN FD 通道")
            actual = d.get_channel_baudrate(self.channel)
            if actual is None or not actual.canfd or actual.can_baudrate != 1000000 or actual.canfd_baudrate != 5000000 or abs(actual.can_sp - .8) > .02 or abs(actual.canfd_sp - .75) > .02:
                raise RuntimeError("CAN FD 通道参数回读校验失败")
            d.hook_recv_callback(self._receive)
            d.hook_err_callback(self._error)
            if not d.enable_channel(self.channel, True):
                raise RuntimeError("无法启用适配器 CAN 通道")
            self._enabled = True
            info = protocol.vendor_info(self._query(0xCD))
            self.positions()
            self.info = {**info, "port": self.port, "hand": self.hand_type, "device_id": self.node_id, "transport": "CAN FD", "can_channel": self.channel + 1, "can_id": self.node_id, "arbitration_bitrate": 1000000, "data_bitrate": 5000000, "adapter_firmware": adapter_firmware, "sdk_version": "DM 1.1.0 / Py 1.0.4", "voltage_mv": None, "current_display": "raw", "current_unit_note": "CAN 0x0A 原始值；手册标注 0.01A，尚未与 USB 读数核对", "error_scope": "hand"}
            return self.info
        except Exception:
            self.close()
            raise

    def _receive(self, device, frame):
        h = frame.head
        if h.channel == self.channel and h.dir == 0 and h.canfd and h.ext and not h.rtr and self._raw_reader is not None:
            self._raw_reader.receive(h.can_id, bytes(frame.payload[:protocol.DLC_LENGTHS[h.dlc]]))
        if h.channel != self.channel or h.dir != 0 or not h.canfd or h.ext or h.rtr or h.can_id != self.node_id:
            return
        # Copy in the callback; the native frame buffer is reused after return.
        data = bytes(frame.payload[:protocol.DLC_LENGTHS[h.dlc]])
        try:
            self._rx.put_nowait((time.monotonic(), data))
        except queue.Full:
            self._latest_error = "CAN 接收缓存已满"

    def _error(self, device, frame):
        if frame.head.channel == self.channel:
            self._latest_error = "达妙适配器报告总线错误；检查连接、终端电阻和位时序"

    def _query(self, command):
        payload = protocol.query_payload(command)
        return self._exchange(payload)

    def _exchange(self, payload):
        if self._owner_thread is not None and threading.get_ident()!=self._owner_thread:
            raise RuntimeError('设备读写必须由同一个通信线程串行执行')
        command=payload[0]
        with self._query_lock:
            while True:
                try:
                    self._rx.get_nowait()
                except queue.Empty:
                    break
            self._latest_error = None
            started = time.monotonic()
            if not self._device.send_can(self.channel, self.node_id, len(payload), payload, True, False, False, True):
                raise RuntimeError(f"CAN 查询 0x{command:02X} 未提交成功")
            until = started + self.timeout
            while time.monotonic() < until:
                try:
                    received, data = self._rx.get(timeout=max(.001, until - time.monotonic()))
                except queue.Empty:
                    break
                if received >= started and data and data[0] == command:
                    return data
            reason = self._latest_error or "未收到匹配回复"
            raise TimeoutError(f"CAN{self.channel + 1} 节点 0x{self.node_id:03X} 查询 0x{command:02X} 超时：{reason}")

    def set_positions(self, positions):
        from .motion_protocol import position_payload,position_reply
        result=position_reply(self._exchange(position_payload(positions)))
        # FW1.2.14 returns nonzero trailing status bytes even with 0x0D=0.
        # Keep those bytes as evidence, not independently verified axis faults.
        # Always require a fresh documented aggregate-error reply after the ACK.
        try:code=protocol.error_code(self._query(0x0D))
        except Exception as exc:raise RuntimeError(f'位置应答已收到，但设备故障复核失败：{exc}') from exc
        result.update(faults=None,hand_error_code=code,fault_check='private_0x0d')
        return result

    def get_power_state(self):
        from .motion_protocol import power_state
        return power_state(self._query(0x02))

    def set_joint_position(self,index,position):
        from .motion_protocol import joint_payload,joint_reply
        reply=self._exchange(joint_payload(index,position));joint_reply(reply,index)
        code=protocol.error_code(self._query(0x0D))
        return {'positions':self.positions(),'hand_error_code':code,'faults':None,
                'fault_check':'private_0x0d','response_hex':reply.hex()}

    def set_mixed(self,positions,profile,index=None):
        from .control_profile import mixed_can_frames,joint_target
        if index is not None:joint_target(index,positions[index-1] if type(index) is int and 1<=index<=10 else 0)
        if self._owner_thread is not None and threading.get_ident()!=self._owner_thread:
            raise RuntimeError('混合控制必须由设备通信线程执行')
        settings_path=self.root/'config.local.json'
        settings=__import__('json').loads(settings_path.read_text(encoding='utf-8-sig')) if settings_path.is_file() else {}
        public_id=settings.get('raw_public_hand_id')
        if type(public_id) is not int or not 1<=public_id<=254:raise ValueError('混合控制需要配置明确的公有raw_public_hand_id（1–254）')
        frames=mixed_can_frames(positions,profile,None if index is None else [index-1])
        can_id=(0x14<<16)|(public_id<<8)|0x81
        for payload in frames:
            if not self._device.send_can(self.channel,can_id,len(payload),payload,True,True,False,True):
                raise RuntimeError('混合目标未提交成功；不自动重发')
        # Public mixed writes are asynchronous. Do not label readback as a write ACK.
        code=protocol.error_code(self._query(0x0D));actual=self.positions()
        return {'positions':actual,'faults':None,'hand_error_code':code,'fault_check':'private_0x0d',
                'delivery':'transport_submitted_with_readback','submitted_frames':len(frames),
                'request_hex':[p.hex() for p in frames]}

    def set_enabled(self, enabled):
        from .motion_protocol import power_payload,power_reply
        power_reply(self._exchange(power_payload(enabled)))
        actual=self.get_power_state()
        if actual!=int(enabled):raise RuntimeError('电机状态回读与请求不一致')
        return {'power_state':actual,'enabled':actual==1}

    def positions(self):
        self._touch_cache.clear()
        return protocol.positions(self._query(0x09))

    def tactile(self, region):
        if region not in protocol.TACTILE_SLICES:
            raise ValueError("Unknown tactile region")
        command, length, begin, end = protocol.TACTILE_SLICES[region]
        if command not in self._touch_cache:
            self._touch_cache[command] = protocol.response_body(self._query(command), command, length)
        return list(self._touch_cache[command][begin:end])

    def diagnostics(self):
        result, errors = {}, []
        for key, command, decoder in (("current_raw", 0x0A, protocol.current_raw), ("temperature_c", 0x0C, protocol.temperatures), ("hand_error_code", 0x0D, protocol.error_code)):
            try:
                result[key] = decoder(self._query(command))
            except Exception as exc:
                errors.append(f"{key}: {exc}")
        return result, errors

    def raw_tactile(self):
        from .raw_tactile import DirectRawReader as RawReader
        from .raw_bridge import EXPECTED_COUNTS
        from datetime import datetime
        import json
        settings_path = self.root / 'config.local.json'
        settings = json.loads(settings_path.read_text(encoding='utf-8-sig')) if settings_path.is_file() else {}
        if settings.get('raw_tactile_probe_enabled') is not True:
            report_path = self.root / 'data/raw_tactile_compatibility.json'
            report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.is_file() else {}
            if report.get('firmware_version') == self.info.get('firmware_version') and report.get('hardware_version') == self.info.get('hardware_version') and report.get('can_id') == self.node_id:
                return {'status':'disabled', 'regions':{}, 'expected_counts':EXPECTED_COUNTS,
                    'probe_enabled':False, 'last_probe_status':report.get('status'),
                    'error':'本机已关闭原始采集；历史兼容性记录不作为实时数据。'}
            return {'status':'disabled','regions':{},'expected_counts':EXPECTED_COUNTS,'error':'原始公有协议尚未验证，当前保留普通触觉采集；核对兼容协议后可启用独立探测。','probe_enabled':False}
        public_id = settings.get('raw_public_hand_id')
        if type(public_id) is not int or not 1 <= public_id <= 254:
            return {'status':'disabled','regions':{},'expected_counts':EXPECTED_COUNTS,'error':'先确认公有协议手 ID 并填写 raw_public_hand_id；不能直接代用私有 CAN 节点 ID。','probe_enabled':False}
        if self._raw_result is not None and time.monotonic() < self._raw_retry_at:
            return self._raw_result
        if self._raw_reader is None:
            self._raw_reader = RawReader(self._device, self.channel, public_id)
        elif self._raw_reader.node_id != public_id:
            raise RuntimeError('公有手 ID 设置已变化，请重新连接后读取')
        try:
            result = self._raw_reader.read()
        except (OSError,RuntimeError,TimeoutError) as exc:
            result = {'status':'error','regions':{},'expected_counts':EXPECTED_COUNTS,'error':str(exc)}
        result['public_hand_id'] = public_id
        result['sampled_at'] = datetime.now().astimezone().isoformat(timespec='milliseconds')
        if result['status'] == 'ready':
            self._raw_has_succeeded = True
            self._raw_failures = 0
        elif self._raw_has_succeeded:
            # A transient miss cannot erase an already verified capability.
            # Keep missing data empty, and retry on a later sampling cycle.
            self._raw_failures += 1
            result['consecutive_failures'] = self._raw_failures
            result['error'] = '本次原始阵列未完整返回，将自动重试；未用旧数据或补零代替。'
            if self._raw_failures >= 3 or result['status'] == 'error':
                self._raw_reader.close()
                self._raw_reader = None
            self._raw_retry_at = time.monotonic()+min(2.0,.5*self._raw_failures)
            self._raw_result = result
            return result
        if result['status'] != 'ready':
            self._raw_reader.close()
            self._raw_reader = None
            # Some legacy O10 firmware does not acknowledge the public extended
            # frames. Recover the ADAPTER after the one read-only capability test.
            # No motor enable, device configuration or flash write is sent.
            self._device.enable_channel(self.channel, False)
            from dmcan import dmcan_channel_can_info
            info = dmcan_channel_can_info()
            info.channel, info.canfd = self.channel, True
            info.can_baudrate, info.canfd_baudrate = 1000000, 5000000
            info.can_sp, info.canfd_sp = .8, .75
            if not self._device.set_channel_baudrate(self.channel, info) or not self._device.enable_channel(self.channel, True):
                raise RuntimeError('原始接口未返回，适配器通道恢复失败，请重新连接')
            self.positions()  # Ordinary telemetry must work before returning.
            result['ordinary_channel_recovered'] = True
        # Never repeat a silent public-interface probe in this connection.
        self._raw_retry_at = 0 if result['status'] == 'ready' else float('inf')
        self._raw_result = result
        if result['status'] != 'ready':
            report = {**result,'firmware_version':self.info.get('firmware_version'),'hardware_version':self.info.get('hardware_version'),'can_id':self.node_id}
            folder = self.root / 'data'
            folder.mkdir(exist_ok=True)
            (folder/'raw_tactile_compatibility.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        return result

    def close(self):
        if self._raw_reader is not None:
            self._raw_reader.close()
            self._raw_reader = None
        if self._device is not None and self._enabled:
            self._device.enable_channel(self.channel, False)  # Adapter channel only.
            self._enabled = False
        if self._context is not None:
            # Context destruction owns native device cleanup. Calling device.close()
            # first causes a duplicate close in the vendor 1.1.0 lifecycle.
            self._context.destroy()
            self._context = None
        self._device = None
        self._usb = None
        self._native_library = None
        if self._dll_directory is not None:
            self._dll_directory.close()
            self._dll_directory = None


def _worker_main(connection, root, channel, node_id, hand_type, timeout):
    """One native SDK lifetime per OS process; never expose raw CAN writes."""
    import os
    hand = _NativeDamiaoHand(root, channel=channel, node_id=node_id, hand_type=hand_type, timeout=timeout)
    try:
        while True:
            request = connection.recv()
            operation = request.get("operation")
            try:
                if operation == "open":
                    result = hand.open()
                elif operation == "positions":
                    result = hand.positions()
                elif operation == "tactile":
                    result = hand.tactile(request["region"])
                elif operation == "diagnostics":
                    result = hand.diagnostics()
                elif operation == "raw_tactile":
                    result = hand.raw_tactile()
                elif operation == 'set_positions':
                    result = hand.set_positions(request['positions'])
                elif operation == 'set_joint_position':
                    result = hand.set_joint_position(request['index'],request['position'])
                elif operation == 'set_mixed':
                    result = hand.set_mixed(request['positions'],request['profile'],request.get('index'))
                elif operation == 'get_power_state':
                    result = hand.get_power_state()
                elif operation == 'set_enabled':
                    result = hand.set_enabled(request['enabled'])
                elif operation == "close":
                    hand.close()
                    connection.send({"ok": True, "result": None})
                    break
                else:
                    raise ValueError("Unsupported CAN worker operation")
                connection.send({"ok": True, "result": result})
            except Exception as exc:
                connection.send({"ok": False, "error": str(exc)})
    except (EOFError, OSError):
        pass
    finally:
        # Native cleanup can block on some vendor runtime/firmware combinations.
        # The owning parent bounds shutdown and terminates only this worker.
        hand.close()
        connection.close()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)


class DamiaoHand:
    """IPC facade: fresh official USB driver process for every connection."""

    mode = "canfd"
    control_capabilities = ('position','power','hold','joint','pt','pvt','mixed_joint')

    def __init__(self, root, *, channel=1, node_id=9, hand_type="right", timeout=.6):
        # Validate before creating a process; this constructor opens no hardware.
        _NativeDamiaoHand(root, channel=channel, node_id=node_id, hand_type=hand_type, timeout=timeout)
        self.root, self.channel, self.node_id = Path(root), channel, node_id
        self.hand_type, self.timeout = hand_type, timeout
        self._process = self._connection = None
        self._rpc_lock = threading.Lock()
        self.info = {}

    def open(self):
        import multiprocessing
        self.close()
        checked_runtime(self.root / "vendor/damiao_device_sdk")
        context = multiprocessing.get_context("spawn")
        self._connection, child = context.Pipe()
        self._process = context.Process(target=_worker_main, args=(child, str(self.root), self.channel, self.node_id, self.hand_type, self.timeout), name="omnihand-canfd-driver", daemon=True)
        try:
            self._process.start()
            child.close()
            self.info = self._rpc("open", wait=12)
            self.port = self.info["port"]
            return self.info
        except Exception:
            child.close()
            self.close()
            raise

    def _terminate_worker(self):
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        process, self._process = self._process, None
        if process is not None and process.pid is not None:
            process.join(timeout=.1)
            if process.is_alive():
                process.terminate()
                process.join(timeout=3)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)

    def _rpc(self, operation, *, wait=4, **params):
        with self._rpc_lock:
            if self._connection is None or self._process is None or not self._process.is_alive():
                raise RuntimeError("达妙通信进程未运行，请重新连接")
            try:
                self._connection.send({"operation": operation, **params})
                if not self._connection.poll(wait):
                    raise TimeoutError("达妙通信进程未及时回复，已释放会话，请重新连接")
                reply = self._connection.recv()
            except (OSError, EOFError, TimeoutError):
                self._terminate_worker()
                raise
            if not reply["ok"]:
                raise RuntimeError(reply["error"])
            return reply["result"]

    def positions(self):
        return self._rpc("positions")

    def tactile(self, region):
        return self._rpc("tactile", region=region)

    def diagnostics(self):
        return self._rpc("diagnostics")

    def raw_tactile(self):
        return self._rpc("raw_tactile")

    def set_positions(self, positions):
        from .motion_protocol import target_vector
        return self._rpc('set_positions',positions=target_vector(positions))

    def set_joint_position(self,index,position):
        from .control_profile import joint_target
        joint_target(index,position)
        return self._rpc('set_joint_position',index=index,position=position)

    def set_mixed(self,positions,profile,index=None):
        from .motion_protocol import target_vector
        from .control_profile import control_profile
        return self._rpc('set_mixed',positions=target_vector(positions),profile=control_profile(profile),index=index)

    def get_power_state(self):
        return self._rpc('get_power_state')

    def set_enabled(self, enabled):
        if type(enabled) is not bool:raise ValueError('enabled must be boolean')
        return self._rpc('set_enabled',enabled=enabled)

    def close(self):
        if self._process is not None:
            try:
                self._rpc("close", wait=4)
            except (OSError, EOFError, TimeoutError, RuntimeError):
                pass
            if self._process is not None:
                self._process.join(timeout=1)
        self._terminate_worker()
