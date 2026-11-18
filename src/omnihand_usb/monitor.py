"""Single-owner sampling thread and snapshot storage for the USB dashboard."""

from __future__ import annotations

import copy
from datetime import datetime
import json
from pathlib import Path
import threading
import time

from .device import DemoHand, JOINT_NAMES, REGIONS, REGION_NAMES, USBHand
from .control import MotionController


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def read_sample(device, *, tactile=True, diagnostics=True, pose_solver=None, between_reads=None):
    """Each reply keeps a timestamp; failures are not replaced with zero arrays."""
    sample = {"sampled_at": now_iso(), "errors": [], "tactile": {}}
    try:
        sample["positions"] = device.positions()
        sample['positions_at'] = now_iso()
        if pose_solver is not None:
            try:
                sample['pose'] = pose_solver.solve(sample['positions'])
            except Exception as exc:
                sample['pose'] = {'joints_rad':{},'error':str(exc)}
    except Exception as exc:
        sample["positions"] = None
        sample["errors"].append(f"positions: {exc}")
    if between_reads:between_reads(sample.get('positions'))
    if diagnostics:
        sample["diagnostics"], problems = device.diagnostics()
        sample["errors"].extend(problems)
        sample["diagnostics"]["sampled_at"] = now_iso()
        if between_reads:between_reads(diagnostics=sample['diagnostics'],diagnostic_errors=problems)
    if tactile:
        if hasattr(device, 'raw_tactile'):
            try:
                sample['raw_tactile'] = device.raw_tactile()
            except Exception as exc:
                sample['raw_tactile'] = {'status':'error','regions':{},'error':str(exc),'sampled_at':now_iso()}
        else:
            sample['raw_tactile'] = {'status':'unavailable','regions':{},'error':'当前连接后端未提供原始触觉读取；普通触觉独立显示。'}
        if between_reads:between_reads()
        for region in REGIONS:
            entry = {"name": REGION_NAMES[region], "sampled_at": now_iso()}
            try:
                entry["values"] = device.tactile(region)
                entry["error"] = None
            except Exception as exc:
                entry["values"] = None
                entry["error"] = str(exc)
                sample["errors"].append(f"{region}: {exc}")
            sample["tactile"][region] = entry
            if between_reads:between_reads()
    sample["completed_at"] = now_iso()
    return sample


class Monitor:
    def __init__(self, root: Path, interval=.4, tactile_interval=1.0, diagnostics_interval=3.0):
        if min(interval, tactile_interval, diagnostics_interval) < .1:
            raise ValueError("Sampling intervals must be at least 0.1 seconds")
        self.root, self.interval = Path(root), interval
        self.tactile_interval, self.diagnostics_interval = tactile_interval, diagnostics_interval
        self._lifecycle = threading.RLock()
        self._state_lock = threading.Lock()
        self._record_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._device = None
        self._record = None
        self._pose_solver = None
        self.control = MotionController(self.root)
        self._published_control_feedback = 0.0
        self._state = {"connected": False, "read_only": True, "mode": "usb", "sequence": 0, "valid_position_samples": 0, "device": {}, "positions": None, "positions_at": None, "pose":None,"raw_tactile":{},"diagnostics": {}, "tactile": {}, "errors": [], "recording": False, "record_path": None, "joint_names": list(JOINT_NAMES), "nominal_position_range": [0, 4096]}

    def snapshot(self):
        with self._state_lock:
            state = copy.deepcopy(self._state)
        state["server_time"] = now_iso()
        state['control'] = self.control.snapshot()
        state['read_only'] = not state['control']['armed']
        return state

    def connect(self, port="auto", *, demo=False, hand_type="right", device_id=1, transport="usb", can_channel=1, can_id=9):
        if transport not in ("usb", "canfd"):
            raise ValueError("Unsupported transport")
        with self._lifecycle:
            self.disconnect()
            mode = "demo" if demo else transport
            with self._state_lock:
                self._state.update(mode=mode, device={}, sequence=0, valid_position_samples=0, positions=None, positions_at=None, pose=None,raw_tactile={},diagnostics={}, tactile={}, errors=[])
            if demo:
                device = DemoHand()
            elif transport == "canfd":
                from .damiao import DamiaoHand
                device = DamiaoHand(self.root, channel=can_channel, node_id=can_id, hand_type=hand_type)
            else:
                device = USBHand(port, hand_type, device_id)
            try:
                info = device.open()
            except Exception as exc:
                with self._state_lock:
                    self._state["errors"] = [str(exc)]
                raise
            self._device = device
            try:
                from .kinematics import PoseSolver
                self._pose_solver = PoseSolver(hand_type)
            except (ImportError,OSError,ValueError):
                self._pose_solver = None
            self._stop = threading.Event()
            self.control.bind(device,device.mode,hand_type)
            self._published_control_feedback = 0.0
            with self._state_lock:
                self._state.update(connected=True, mode=device.mode, device=info, sequence=0, valid_position_samples=0, positions=None, positions_at=None, pose=None,raw_tactile={},diagnostics={}, tactile={}, errors=[])
            self._thread = threading.Thread(target=self._run, args=(device, self._stop), name="usb-hand-sampler", daemon=True)
            self._thread.start()
            return self.snapshot()

    def disconnect(self):
        with self._lifecycle:
            control = self.control.snapshot()
            if control['running'] and self._thread and self._thread.is_alive():
                # Ask the existing owner to cancel and hold before closing its transport.
                self.control.request('lock',{'session_id':control['session_id']})
            self._stop.set()
            if self._thread:
                self._thread.join(timeout=10)
                if self._thread.is_alive():
                    raise RuntimeError("SDK 查询仍在结束，请稍后重试断开；不会并行打开第二个连接")
                self._thread = None
            if self._device:
                self._device.close()
                self._device = None
            self.control.unbind()
            self.set_recording(False)
            with self._state_lock:
                self._state["connected"] = False

    def set_recording(self, enabled):
        with self._lifecycle, self._record_lock:
            if enabled and self._record is None:
                if not self.snapshot()["connected"]:
                    raise RuntimeError("请先连接设备再记录")
                folder = self.root / "data"
                folder.mkdir(parents=True, exist_ok=True)
                name = self.snapshot()["mode"] + "_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".jsonl"
                self._record = (folder / name).open("x", encoding="utf-8")
                self._record.write(json.dumps({"record_type": "session", "started_at": now_iso(), "device": self.snapshot()["device"], "timestamp_semantics": "host query time, not device acquisition time"}, ensure_ascii=False) + "\n")
                with self._state_lock:
                    self._state.update(recording=True, record_path="data/" + name)
            elif not enabled and self._record is not None:
                self._record.close()
                self._record = None
                with self._state_lock:
                    self._state["recording"] = False
        return self.snapshot()

    def _merge(self, sample):
        with self._state_lock:
            s = self._state
            s["sequence"] += 1
            s["sampled_at"] = sample["sampled_at"]
            s["errors"] = sample["errors"]
            if sample["positions"] is not None:
                s["valid_position_samples"] += 1
                position_at=sample.get('positions_at',sample['completed_at'])
                if not s['positions_at'] or position_at >= s['positions_at']:
                    s["positions"] = sample["positions"]
                    s["positions_at"] = position_at
                    s['pose'] = sample.get('pose')
                    if s['pose'] is not None:s['pose']['sampled_at'] = position_at
            if 'raw_tactile' in sample:
                s['raw_tactile'] = sample['raw_tactile']
            if "diagnostics" in sample:
                # Keep earlier successful fields but retain their own per-field timestamps.
                for key, value in sample["diagnostics"].items():
                    if key != "sampled_at":
                        s["diagnostics"][key] = value
                        s["diagnostics"][key + "_at"] = sample["diagnostics"]["sampled_at"]
            s["tactile"].update(sample["tactile"])

    def _run(self, device, stop):
        last_touch = last_diag = next_sample = 0.0
        def control_tick(feedback=None,diagnostics=None,diagnostic_errors=()):
            if stop.is_set():return
            if diagnostics is not None:self.control.check_diagnostics(diagnostics,diagnostic_errors)
            self.control.tick(device,feedback)
            positions,at=self.control.feedback()
            if positions is not None and at > self._published_control_feedback:
                self._published_control_feedback=at
                position_at=now_iso()
                try:pose=self._pose_solver.solve(positions) if self._pose_solver else None
                except Exception as exc:pose={'joints_rad':{},'error':str(exc)}
                if pose is not None:pose['sampled_at']=position_at
                with self._state_lock:self._state.update(positions=positions,positions_at=position_at,pose=pose)
        while not stop.is_set():
            started = time.monotonic()
            try:
                control_tick()
                if started >= next_sample:
                    touch, diag = started - last_touch >= self.tactile_interval, started - last_diag >= self.diagnostics_interval
                    sample = read_sample(device, tactile=touch, diagnostics=diag, pose_solver=self._pose_solver,between_reads=control_tick)
                    if diag:self.control.check_diagnostics(sample.get('diagnostics',{}))
                    self._merge(sample)
                    next_sample=started+self.interval
                    if touch:last_touch=started
                    if diag:last_diag=started
                    with self._record_lock:
                        if self._record:
                            self._record.write(json.dumps({"record_type": "sample", **sample}, ensure_ascii=False, allow_nan=False) + "\n")
                            self._record.flush()
            except Exception as exc:
                with self._state_lock:
                    self._state["errors"] = [str(exc)]
            stop.wait(.02)
